import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modules.project_foundation.profile import (
    cohort_query, distribution_query, duplicate_tuple_query,
    eligible_year_total_query, fetch_soda3, month_query,
    profile_rows, read_jsonl, validate_distribution, validate_sample,
)


class FoundationProfileTests(unittest.TestCase):
    def test_committed_live_excerpt_has_valid_checksum_and_scope(self):
        root = Path(__file__).resolve().parents[1]
        excerpt = root / "data/fixtures/foundation_live_excerpt.jsonl"
        manifest = json.loads((root / "data/fixtures/foundation_live_excerpt_manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(excerpt.read_bytes()).hexdigest(),
                         manifest["excerpt_sha256"])
        self.assertEqual(hashlib.sha256((root / "configs/project_foundation.json").read_bytes())
                         .hexdigest(), manifest["config_sha256"])
        rows = read_jsonl(excerpt)
        self.assertEqual(len(rows), manifest["excerpt_rows"])
        self.assertEqual({f"{row['checkoutyear']}-{int(row['checkoutmonth']):02d}"
                          for row in rows}, set(manifest["periods"]))

    def test_invalid_values_are_counted_without_turning_missing_into_zero(self):
        rows = [
            {"checkoutyear": "2025", "checkoutmonth": "1", "checkouts": "0",
             "materialtype": "BOOK", "usageclass": "Physical", "title": "A"},
            {"checkoutyear": "2025", "checkoutmonth": "13", "checkouts": "-2",
             "materialtype": "EBOOK", "usageclass": "Digital", "title": ""},
            {"checkoutyear": "bad", "checkoutmonth": "2", "checkouts": None,
             "materialtype": "BOOK", "title": "C"},
        ]
        report = profile_rows(rows, source="fixture")
        self.assertEqual(report["sample_size"], 3)
        self.assertEqual(report["sample_checkouts_sum"], 0)
        self.assertEqual(report["invalid_rows_by_field"], {
            "checkoutmonth": 1, "checkoutyear": 1, "checkouts": 2, "title": 1,
        })
        self.assertEqual(report["missing_rows_by_field"]["checkouts"], 1)
        self.assertEqual(report["observed_values"]["materialtype"], {"BOOK": 2, "EBOOK": 1})
        self.assertEqual(report["sample_sha256"], profile_rows(rows, source="fixture")["sample_sha256"])

    def test_jsonl_rejects_non_object_and_empty_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text(json.dumps([1, 2]) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_jsonl(path)
        with self.assertRaises(ValueError):
            profile_rows([], source="fixture")

    def test_query_is_bounded_and_scoped(self):
        query = month_query("2025-01", 100)
        self.assertIn("checkoutyear = 2025 AND checkoutmonth = 1", query)
        self.assertIn("LIMIT 100", query)
        self.assertIn("GROUP BY materialtype, usageclass", distribution_query("2024-06"))
        self.assertIn("materialtype = 'Ebook'", month_query("2024-06", 30, "Ebook"))
        self.assertIn("HAVING count(*) > 1", duplicate_tuple_query("2025-01", ["BOOK", "Ebook"]))
        self.assertIn("LIMIT 1000", cohort_query(2023, ["BOOK", "Ebook"], 1000))
        self.assertIn("sum(checkouts)", eligible_year_total_query(2023, ["BOOK"]))
        with self.assertRaises(ValueError):
            month_query("2025-13", 100)
        with self.assertRaises(ValueError):
            month_query("2025-1", 100)
        with self.assertRaises(ValueError):
            month_query("2025-01", 1001)
        with self.assertRaises(ValueError):
            month_query("2025-01", 100, "BOOK' OR 1=1")

    def test_distribution_rejects_missing_or_duplicate_aggregate_groups(self):
        good = [{"materialtype": "BOOK", "usageclass": "Physical",
                 "row_count": "2", "checkouts": "0"}]
        self.assertEqual(validate_distribution(good, "2025-01"),
                         {"row_count": 2, "checkouts": 0, "group_count": 1})
        for rows in ([], good + good, [{**good[0], "row_count": "bad"}],
                     [{**good[0], "checkouts": "-1"}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_distribution(rows, "2025-01")

    def test_sample_must_match_requested_month_and_material(self):
        row = {"checkoutyear": "2024", "checkoutmonth": "6", "materialtype": "Ebook"}
        validate_sample([row], "2024-06", 1, "Ebook")
        for altered in ({**row, "checkoutmonth": "7"},
                        {**row, "materialtype": "EBOOK"}):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                validate_sample([altered], "2024-06", 1, "Ebook")
        with self.assertRaises(ValueError):
            validate_sample([row, row], "2024-06", 1, "Ebook")

    def test_soda3_request_carries_query_and_token(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return b'[{"materialtype":"BOOK"}]'

        with patch("modules.project_foundation.profile.urlopen", return_value=Response()) as open_url:
            rows = fetch_soda3("https://example.test/query.json", "token", "SELECT * LIMIT 1")
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-app-token"), "token")
        self.assertEqual(json.loads(request.data)["query"], "SELECT * LIMIT 1")
        self.assertEqual(rows, [{"materialtype": "BOOK"}])


if __name__ == "__main__":
    unittest.main()
