import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from modules.raw_snapshot import compare_snapshots, iter_rows, verify_snapshot
from modules.source_ingestion.ingest import (SodaClient, audit_range, ingest_cohort,
                                             ingest_month, iter_periods, latest_usable_snapshot)


CONFIG = {
    "dataset_id": "tmmm-ytt6", "source_url": "https://example.test/query.json",
    "period_start": "2023-01", "period_end": "2023-02",
    "book_material_type_map": {"BOOK": "PRINT_BOOK", "Ebook": "EBOOK"},
    "reviewed_excluded_material_types": ["ATLAS"],
    "mapping_version": "v1", "detail_selection": {
        "selection_start": "2023-01", "selection_end": "2023-12",
        "group_by": ["title", "creator", "materialtype", "checkouttype"],
        "max_tuples": 1000,
    },
}


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def query(self, query, *, page=None, page_size=None):
        self.calls.append((query, page, page_size))
        return self.handler(query, page)


def row(identifier, title, creator, count):
    return {":id": identifier, "checkoutyear": "2023", "checkoutmonth": "1",
            "materialtype": "BOOK", "title": title, "creator": creator,
            "checkouttype": "Horizon", "checkouts": str(count)}


class SourceIngestionTests(unittest.TestCase):
    def test_periods_and_client_retry(self):
        self.assertEqual(list(iter_periods("2023-12", "2024-02")),
                         ["2023-12", "2024-01", "2024-02"])
        with self.assertRaises(ValueError):
            list(iter_periods("2024-02", "2024-01"))

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                return b'[{":id":"a"}]'

        error = HTTPError("https://example.test", 503, "busy", {}, io.BytesIO())
        with patch("modules.source_ingestion.client.urlopen",
                   side_effect=[error, Response()]) as request, \
                patch("modules.source_ingestion.client.time.sleep") as sleep:
            client = SodaClient(CONFIG["source_url"], "secret", backoff=0)
            self.assertEqual(client.query("SELECT :id, * ORDER BY :id", page=2,
                                          page_size=10), [{":id": "a"}])
        self.assertEqual(request.call_count, 2)
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["page"], {"pageNumber": 2, "pageSize": 10})
        self.assertEqual(request.call_args.args[0].get_header("X-app-token"), "secret")
        self.assertEqual(sleep.call_count, 1)

    def test_overview_is_reconciled_and_flags_new_codes(self):
        def handle(query, page):
            if "GROUP BY" in query:
                return [{"materialtype": "BOOK", "usageclass": "Physical",
                         "row_count": "2", "checkouts": "7"},
                        {"materialtype": "ATLAS", "usageclass": "Physical",
                         "row_count": "1", "checkouts": "1"},
                        {"materialtype": "NEWCODE", "usageclass": "Digital",
                         "row_count": "1", "checkouts": "2"}]
            return [{"row_count": "2", "checkouts": "7"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = ingest_month(FakeClient(handle), CONFIG, "2023-01", root, "sha",
                                    stream="monthly_overview")
            path = root / "monthly_overview/year=2023/month=01" / f"snapshot={manifest['snapshot_id']}"
            self.assertEqual(manifest["status"], "COMPLETE")
            self.assertEqual(manifest["mapping_review_status"], "BLOCKED")
            self.assertEqual(manifest["unreviewed_material_types"], ["NEWCODE"])
            self.assertEqual(manifest["source_row_count"], 2)
            self.assertEqual(manifest["row_count"], 1)
            self.assertEqual(manifest["unreviewed_material_distribution"][0]["materialtype"],
                             "NEWCODE")
            self.assertEqual(verify_snapshot(path)["checkouts"], 7)
            disk_manifest = json.loads((path / "manifest.json").read_text())
            disk_manifest["format_totals"]["PRINT_BOOK"]["checkouts"] = 6
            (path / "manifest.json").write_text(json.dumps(disk_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "format totals mismatch"):
                verify_snapshot(path)

    def test_detail_pages_keep_exact_cohort_and_reconcile_source(self):
        def handle(query, page):
            if "annual_checkouts" in query:
                return [{"title": "O'Brien", "creator": None, "materialtype": "BOOK",
                         "checkouttype": "Horizon", "annual_checkouts": "8"}]
            if "count(*)" in query:
                return [{"row_count": "3", "checkouts": "13"}]
            self.assertIn("O''Brien", query)
            self.assertIn("ORDER BY :id", query)
            return ({1: [row("a", "O'Brien", None, 2),
                         row("b", "O'Brien", "Other", 4)],
                     2: [row("c", "O'Brien", None, 7)]})[page]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeClient(handle)
            cohort = root / "cohort.jsonl"
            ingest_cohort(client, CONFIG, cohort)
            manifest = ingest_month(client, CONFIG, "2023-01", root, "sha",
                                    stream="book_details", cohort_path=cohort,
                                    page_size=2)
            path = root / "book_details/year=2023/month=01" / f"snapshot={manifest['snapshot_id']}"
            self.assertEqual(manifest["row_count"], 2)
            self.assertEqual(manifest["checkouts"], 9)
            self.assertEqual(manifest["selected_row_count"], 2)
            self.assertEqual([part["row_count"] for part in manifest["parts"]], [1, 1])
            self.assertEqual(verify_snapshot(path)["row_count"], 2)
            data = []
            for part in manifest["parts"]:
                with gzip.open(path / part["path"], "rt", encoding="utf-8") as stream:
                    data.extend(json.loads(line) for line in stream)
            self.assertEqual([item[":id"] for item in data], ["a", "c"])
            replay = list(iter_rows(path))
            self.assertEqual([item.source_row_number for item in replay], [1, 2])
            self.assertEqual([item.source_payload[":id"] for item in replay], ["a", "c"])
            first = path / manifest["parts"][0]["path"]
            first.write_bytes(first.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_snapshot(path)

    def test_failed_page_never_completes(self):
        def handle(query, page):
            if "annual_checkouts" in query:
                return [{"title": "A", "creator": "X", "materialtype": "BOOK",
                         "checkouttype": "Horizon", "annual_checkouts": "1"}]
            if "count(*)" in query:
                return [{"row_count": "2", "checkouts": "3"}]
            return [row("a", "A", "X", 1)] if page == 1 else []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeClient(handle)
            cohort = root / "cohort.jsonl"
            ingest_cohort(client, CONFIG, cohort)
            with self.assertRaisesRegex(ValueError, "missing/extra page"):
                ingest_month(client, CONFIG, "2023-01", root, "sha",
                             stream="book_details", cohort_path=cohort, page_size=1)
            path = next((root / "book_details/year=2023/month=01").iterdir())
            self.assertEqual(json.loads((path / "manifest.json").read_text())["status"], "FAILED")
            with self.assertRaisesRegex(ValueError, "not COMPLETE"):
                verify_snapshot(path)

    def test_repeated_source_id_across_pages_marks_snapshot_failed(self):
        def handle(query, page):
            if "annual_checkouts" in query:
                return [{"title": "A", "creator": "X", "materialtype": "BOOK",
                         "checkouttype": "Horizon", "annual_checkouts": "2"}]
            if "count(*)" in query:
                return [{"row_count": "2", "checkouts": "2"}]
            return [row("same-id", "A", "X", 1)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeClient(handle)
            cohort = root / "cohort.jsonl"
            ingest_cohort(client, CONFIG, cohort)
            with self.assertRaisesRegex(ValueError, "repeated source ID"):
                ingest_month(client, CONFIG, "2023-01", root, "sha",
                             stream="book_details", cohort_path=cohort, page_size=1)
            snapshot = next((root / "book_details/year=2023/month=01").iterdir())
            self.assertEqual(json.loads((snapshot / "manifest.json").read_text())["status"],
                             "FAILED")

    def test_verification_failure_never_commits_complete(self):
        def handle(query, page):
            if "GROUP BY" in query:
                return [{"materialtype": "BOOK", "usageclass": "Physical",
                         "row_count": "1", "checkouts": "2"}]
            return [{"row_count": "1", "checkouts": "2"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("modules.raw_snapshot.validation.verify_snapshot",
                       side_effect=ValueError("forced verification failure")):
                with self.assertRaisesRegex(ValueError, "forced verification failure"):
                    ingest_month(FakeClient(handle), CONFIG, "2023-01", root, "sha",
                                 stream="monthly_overview")
            snapshot = next((root / "monthly_overview/year=2023/month=01").iterdir())
            self.assertEqual(json.loads((snapshot / "manifest.json").read_text())["status"],
                             "FAILED")

    def test_ebook_source_alias_recovers_cohort_without_changing_raw_code(self):
        config = {**CONFIG,
                  "book_material_type_map": {**CONFIG["book_material_type_map"], "EBOOK": "EBOOK"},
                  "cohort_material_aliases": {"Ebook": "EBOOK"},
                  "cohort_match_version": "v2_ebook_case_alias"}
        def handle(query, page):
            if "annual_checkouts" in query:
                return [{"title": "A", "creator": "X", "materialtype": "EBOOK",
                         "checkouttype": "OverDrive", "annual_checkouts": "4"}]
            if "count(*)" in query:
                return [{"row_count": "1", "checkouts": "3"}]
            return [{":id": "row-1", "checkoutyear": "2023", "checkoutmonth": "1",
                     "materialtype": "Ebook", "title": "A", "creator": "X",
                     "checkouttype": "OverDrive", "checkouts": "3"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cohort = root / "cohort.jsonl"
            client = FakeClient(handle)
            ingest_cohort(client, config, cohort)
            manifest = ingest_month(client, config, "2023-01", root, "sha",
                                    stream="book_details", cohort_path=cohort)
            self.assertEqual(manifest["selected_checkouts"], 3)
            self.assertEqual(manifest["aliased_material_row_count"], 1)
            self.assertEqual(manifest["format_totals"],
                             {"EBOOK": {"row_count": 1, "checkouts": 3}})
            path = root / "book_details/year=2023/month=01" / f"snapshot={manifest['snapshot_id']}"
            with gzip.open(path / manifest["parts"][0]["path"], "rt", encoding="utf-8") as stream:
                self.assertEqual(json.loads(next(stream))["materialtype"], "Ebook")
            self.assertEqual(verify_snapshot(path)["aliased_material_checkouts"], 3)
            with self.assertRaisesRegex(ValueError, "outside selected cohort"):
                verify_snapshot(path, cohort_keys={("Different", "X", "EBOOK", "OverDrive")})

    def test_audit_requires_both_streams_and_reviewed_mapping(self):
        config = {**CONFIG, "period_end": "2023-01"}
        def handle(query, page):
            if "annual_checkouts" in query:
                return [{"title": "A", "creator": "X", "materialtype": "BOOK",
                         "checkouttype": "Horizon", "annual_checkouts": "2"}]
            if "GROUP BY materialtype" in query:
                return [{"materialtype": "BOOK", "usageclass": "Physical",
                         "row_count": "1", "checkouts": "2"},
                        {"materialtype": "ATLAS", "usageclass": "Physical",
                         "row_count": "1", "checkouts": "1"}]
            if "count(*)" in query:
                return [{"row_count": "1", "checkouts": "2"}]
            return [row("a", "A", "X", 2)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeClient(handle)
            cohort = root / "cohort.jsonl"
            ingest_cohort(client, config, cohort)
            self.assertEqual(audit_range(root, config, cohort_path=cohort)["status"], "BLOCKED")
            pending = {**config, "reviewed_excluded_material_types": []}
            overview = ingest_month(client, pending, "2023-01", root, "sha",
                                    stream="monthly_overview")
            self.assertEqual(overview["mapping_review_status"], "BLOCKED")
            ingest_month(client, config, "2023-01", root, "sha", stream="book_details",
                         cohort_path=cohort, overview_manifest=overview)
            report = audit_range(root, config, cohort_path=cohort)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["periods"]["2023-01"]["coverage_percent"], 100.0)
            self.assertEqual(report["periods"]["2023-01"]["coverage_by_format_percent"],
                             {"PRINT_BOOK": 100.0})
            self.assertEqual(report["periods"]["2023-01"]["observed_cohort_tuples"], 1)
            self.assertEqual(report["periods"]["2023-01"]["missing_cohort_tuples"], 0)
            self.assertEqual(report["material_type_review"]["BOOK"]["checkouts"], 2)
            self.assertEqual(report["material_type_review"]["ATLAS"]["status"],
                             "EXCLUDED_REVIEWED")
            self.assertEqual(audit_range(root, pending, cohort_path=cohort)["status"], "BLOCKED")
            self.assertIsNotNone(latest_usable_snapshot(root, config, "book_details",
                                                        "2023-01"))
            altered = {**config, "book_material_type_map": {"BOOK": "OTHER", "Ebook": "EBOOK"}}
            self.assertIsNone(latest_usable_snapshot(root, altered, "book_details", "2023-01"))
            self.assertEqual(audit_range(root, altered, cohort_path=cohort)["status"], "BLOCKED")
            repeated = ingest_month(client, config, "2023-01", root, "sha",
                                    stream="monthly_overview")
            first = root / "monthly_overview/year=2023/month=01" / f"snapshot={overview['snapshot_id']}"
            second = root / "monthly_overview/year=2023/month=01" / f"snapshot={repeated['snapshot_id']}"
            self.assertTrue(compare_snapshots(first, second)["same_content"])


if __name__ == "__main__":
    unittest.main()
