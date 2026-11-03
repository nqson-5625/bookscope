"""Pure and transactional checks for the data quality gate."""

from argparse import Namespace
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
import uuid

from dotenv import load_dotenv
from psycopg.errors import UniqueViolation

from modules.bronze_loading import connect_database
from modules.data_quality.checks import evaluate, selection_sha256
from modules.data_quality.__main__ import _run
from modules.data_quality.dbt_evidence import assess_build


ROOT = Path(__file__).resolve().parents[1]


def has_block(results, code, snapshot_id=None):
    return any(r["metric_code"] == code and r["status"] == "BLOCK"
               and (snapshot_id is None or r["snapshot_id"] == snapshot_id)
               for r in results)


class DataQualityUnitTests(unittest.TestCase):
    def test_selection_digest_is_stable_and_sensitive_to_status(self):
        row = {"stream": "book_details", "scope_id": "SELECTED_DETAIL",
               "month_start": "2025-01-01", "snapshot_id": "one",
               "selection_status": "READY", "manifest_sha256": "abc"}
        original = selection_sha256([row])
        self.assertEqual(original, selection_sha256([dict(row)]))
        self.assertNotEqual(original, selection_sha256([{**row, "selection_status": "SNAPSHOT_NOT_READY"}]))

    def test_dbt_build_requires_every_model_and_test_once(self):
        manifest = json.loads((ROOT / "dbt_bookscope/target/manifest.json")
                              .read_text(encoding="utf-8"))
        artifact = json.loads((ROOT / "dbt_bookscope/target/run_results.json")
                              .read_text(encoding="utf-8"))
        invocation = artifact["metadata"]["invocation_id"]
        valid, details, tests = assess_build(manifest, artifact, invocation, 72)
        self.assertTrue(valid, details)
        self.assertEqual(len(tests), 36)
        for resource_type in ("model.", "test."):
            partial = deepcopy(artifact)
            partial["results"] = [r for r in partial["results"]
                                  if not r["unique_id"].startswith(resource_type)]
            valid, details, _ = assess_build(manifest, partial, invocation, 72)
            self.assertFalse(valid)
            self.assertTrue(details["missing_results"])
        duplicate = deepcopy(artifact)
        duplicate["results"].append(deepcopy(duplicate["results"][0]))
        valid, details, _ = assess_build(manifest, duplicate, invocation, 72)
        self.assertFalse(valid)
        self.assertTrue(details["duplicate_results"])
        trimmed = deepcopy(manifest)
        trimmed["nodes"] = {key: value for key, value in trimmed["nodes"].items()
                            if value.get("name") not in
                            {"silver_grain_unique", "pipeline_gate"}}
        valid, details, _ = assess_build(trimmed, artifact, invocation, 72)
        self.assertFalse(valid)
        self.assertIn("silver_grain_unique", details["missing_tests"])
        self.assertIn("pipeline_gate", details["missing_models"])


@unittest.skipUnless(os.environ.get("BOOKSCOPE_TEST_DB") == "1",
                     "set BOOKSCOPE_TEST_DB=1 to run PostgreSQL integration tests")
class DataQualityIntegrationTests(unittest.TestCase):
    def setUp(self):
        load_dotenv(ROOT / ".env")
        self.conn = connect_database()
        self.conn.autocommit = False
        self.overview_id = self.conn.execute("""SELECT snapshot_id
            FROM silver.int_snapshot_selection WHERE stream = 'monthly_overview'
            AND month_start = '2025-12-01'""").fetchone()[0]
        self.detail_id = self.conn.execute("""SELECT snapshot_id
            FROM silver.int_snapshot_selection WHERE stream = 'book_details'
            AND month_start = '2025-01-01'""").fetchone()[0]

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def assess(self):
        return evaluate(self.conn, ROOT / "data/raw/seattle_checkouts",
                        ROOT / "configs/project_foundation.json",
                        ROOT / "configs/source_ingestion.json")[0]

    def prepare_pass_gate(self):
        report = _run(self.conn, Namespace(
            raw_root=ROOT / "data/raw/seattle_checkouts",
            foundation_config=ROOT / "configs/project_foundation.json",
            ingestion_config=ROOT / "configs/source_ingestion.json"))
        self.assertEqual(report["gate"], "PASS")

    def test_balanced_total_but_wrong_format_blocks(self):
        source_row = self.conn.execute("""SELECT source_row_number
            FROM bronze.checkout_records WHERE snapshot_id = %s
              AND source_payload ->> 'materialtype' = 'BOOK'
              AND (source_payload ->> 'checkouts')::bigint > 0
            LIMIT 1""", (self.overview_id,)).fetchone()[0]
        self.conn.execute("""UPDATE bronze.checkout_records
            SET source_payload = jsonb_set(source_payload, '{materialtype}', '"EBOOK"')
            WHERE snapshot_id = %s AND source_row_number = %s""",
                          (self.overview_id, source_row))
        ready = self.conn.execute("""SELECT ready_for_transform
            FROM bronze.snapshot_load_reconciliation WHERE snapshot_id = %s""",
                                  (self.overview_id,)).fetchone()[0]
        self.assertTrue(ready)
        self.assertTrue(has_block(self.assess(), "RAW_BRONZE_FORMAT_CHECKOUTS",
                                  self.overview_id))
        report = _run(self.conn, Namespace(
            raw_root=ROOT / "data/raw/seattle_checkouts",
            foundation_config=ROOT / "configs/project_foundation.json",
            ingestion_config=ROOT / "configs/source_ingestion.json"))
        self.assertEqual(report["gate"], "BLOCK")
        stored = self.conn.execute("""SELECT count(*) FROM ops.dq_results
            WHERE run_id = %s AND metric_code = 'RAW_BRONZE_FORMAT_CHECKOUTS'
              AND status = 'BLOCK'""", (report["run_id"],)).fetchone()[0]
        self.assertGreater(stored, 0)

    def test_bad_month_blank_title_and_duplicate_id_block(self):
        first, second = self.conn.execute("""SELECT source_row_number,
            source_payload ->> ':id' FROM bronze.checkout_records
            WHERE snapshot_id = %s ORDER BY source_row_number LIMIT 2""",
                                          (self.detail_id,)).fetchall()
        self.conn.execute("""UPDATE bronze.checkout_records
            SET source_payload = jsonb_set(jsonb_set(jsonb_set(source_payload,
                '{checkoutmonth}', '"13"'), '{title}', '" "'),
                '{:id}', to_jsonb(%s::text))
            WHERE snapshot_id = %s AND source_row_number = %s""",
                          (first[1], self.detail_id, second[0]))
        reasons = self.conn.execute("""SELECT block_reasons
            FROM silver.int_validated_rows WHERE snapshot_id = %s
            AND source_row_number = %s""", (self.detail_id, second[0])).fetchone()[0]
        self.assertTrue({"DETAIL_PERIOD_MISMATCH", "BLANK_TITLE", "DUPLICATE_SOURCE_ID"}
                        <= set(reasons))
        self.assertTrue(has_block(self.assess(), "BRONZE_VALIDATION", self.detail_id))

    def test_negative_and_text_checkouts_block_selection(self):
        row = self.conn.execute("""SELECT source_row_number, source_payload
            FROM bronze.checkout_records WHERE snapshot_id = %s LIMIT 1""",
                                (self.detail_id,)).fetchone()
        for bad_value in ("-1", "not-a-number"):
            with self.subTest(checkouts=bad_value):
                self.conn.execute("""UPDATE bronze.checkout_records
                    SET source_payload = jsonb_set(source_payload, '{checkouts}',
                        to_jsonb(%s::text))
                    WHERE snapshot_id = %s AND source_row_number = %s""",
                                  (bad_value, self.detail_id, row[0]))
                status = self.conn.execute("""SELECT selection_status
                    FROM silver.int_snapshot_selection WHERE snapshot_id = %s""",
                                           (self.detail_id,)).fetchone()[0]
                self.assertEqual(status, "SNAPSHOT_NOT_READY")
                self.assertTrue(has_block(self.assess(), "SNAPSHOT_SELECTION",
                                          self.detail_id))

    def test_registered_manifest_hash_mismatch_blocks(self):
        self.conn.execute("""UPDATE ops.data_snapshots SET manifest_sha256 = %s
            WHERE snapshot_id = %s""", ("f" * 64, self.overview_id))
        results = self.assess()
        self.assertTrue(has_block(results, "MANIFEST_SHA256", self.overview_id))
        self.assertTrue(has_block(results, "REGISTRY_PROVENANCE"))

    def test_missing_period_blocks_and_invalidates_gate(self):
        self.conn.execute("""UPDATE ops.data_snapshots SET period = '2026-01-01'
            WHERE snapshot_id = %s""", (self.overview_id,))
        self.assertEqual(self.conn.execute("SELECT status FROM ops.dq_gate")
                         .fetchone()[0], "BLOCK")
        self.assertTrue(has_block(self.assess(), "SNAPSHOT_SELECTION"))

    def test_new_snapshot_invalidates_previous_dq_gate(self):
        self.prepare_pass_gate()
        fake_id = "dq-test-" + uuid.uuid4().hex
        self.conn.execute("""INSERT INTO ops.data_snapshots
            (snapshot_id, run_id, stream, scope_id, period, status, source_url,
             dataset_id, manifest_path, manifest_sha256, config_sha256,
             extractor_version, mapping_version, extracted_at, completed_at,
             row_count, source_row_count, checkouts)
            SELECT %s, run_id, stream, scope_id, period, status, source_url,
                dataset_id, %s, manifest_sha256, config_sha256, extractor_version,
                mapping_version, extracted_at + interval '2 days',
                completed_at + interval '2 days', row_count, source_row_count, checkouts
            FROM ops.data_snapshots WHERE snapshot_id = %s""",
                          (fake_id, fake_id + "/manifest.json", self.detail_id))
        gate = self.conn.execute("SELECT status FROM ops.dq_gate").fetchone()[0]
        self.assertEqual(gate, "BLOCK")
        active = self.conn.execute("""SELECT count(*)
            FROM silver.dq_approved_detail_records""").fetchone()[0]
        self.assertEqual(active, 0)

    def test_new_silver_build_invalidates_previous_dq_gate(self):
        self.prepare_pass_gate()
        invocation = str(uuid.uuid4())
        self.conn.execute("""INSERT INTO silver.run_snapshot_selection
            (invocation_id, contract_version, required_mapping_version,
             selected_at, stream, scope_id, month_start, snapshot_id,
             mapping_version, manifest_sha256, selection_status)
            SELECT %s, contract_version, required_mapping_version,
                (SELECT max(selected_at) + interval '1 hour'
                 FROM silver.run_snapshot_selection), stream, scope_id,
                month_start, snapshot_id, mapping_version, manifest_sha256,
                selection_status
            FROM silver.run_snapshot_selection
            WHERE invocation_id = (
                SELECT invocation_id FROM silver.run_snapshot_selection
                ORDER BY selected_at DESC, invocation_id DESC LIMIT 1)""",
                          (invocation,))
        self.assertEqual(self.conn.execute("SELECT status FROM ops.dq_gate")
                         .fetchone()[0], "BLOCK")

    def test_direct_bronze_and_silver_changes_invalidate_dq_gate(self):
        self.prepare_pass_gate()
        self.assertEqual(self.conn.execute("SELECT status FROM ops.dq_gate")
                         .fetchone()[0], "PASS")
        self.conn.execute("""UPDATE bronze.checkout_records
            SET source_payload = jsonb_set(source_payload, '{title}', '"changed"')
            WHERE snapshot_id = %s AND source_row_number = (
                SELECT min(source_row_number) FROM bronze.checkout_records
                WHERE snapshot_id = %s)""", (self.detail_id, self.detail_id))
        self.assertEqual(self.conn.execute("SELECT status FROM silver.pipeline_gate")
                         .fetchone()[0], "PASS")
        self.assertEqual(self.conn.execute("SELECT status FROM ops.dq_gate")
                         .fetchone()[0], "BLOCK")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM silver.dq_approved_detail_records")
                         .fetchone()[0], 0)

    def test_direct_silver_change_without_new_invocation_blocks(self):
        self.prepare_pass_gate()
        self.conn.execute("""UPDATE silver.detail_records SET checkouts = checkouts + 1
            WHERE snapshot_id = %s AND source_row_number = (
                SELECT min(source_row_number) FROM silver.detail_records
                WHERE snapshot_id = %s)""", (self.detail_id, self.detail_id))
        self.assertEqual(self.conn.execute("SELECT status FROM ops.dq_gate")
                         .fetchone()[0], "BLOCK")

    def test_disabled_change_tracking_blocks(self):
        self.prepare_pass_gate()
        self.conn.execute("""ALTER TABLE bronze.checkout_records
            DISABLE TRIGGER dq_source_revision""")
        self.assertEqual(self.conn.execute("SELECT status FROM ops.dq_gate")
                         .fetchone()[0], "BLOCK")

    def test_replay_preserves_results_and_run_history(self):
        args = Namespace(raw_root=ROOT / "data/raw/seattle_checkouts",
                         foundation_config=ROOT / "configs/project_foundation.json",
                         ingestion_config=ROOT / "configs/source_ingestion.json")
        before = self.conn.execute("SELECT count(*) FROM ops.dq_runs").fetchone()[0]
        first = _run(self.conn, args)
        second = _run(self.conn, args)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["gate"], second["gate"])
        self.assertEqual((first["result_count"], first["block_count"], first["warning_count"]),
                         (second["result_count"], second["block_count"], second["warning_count"]))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM ops.dq_runs")
                         .fetchone()[0], before + 2)
        mismatch = self.conn.execute("""SELECT count(*) FROM (
            (SELECT result_no, metric_code, severity, status, stream, month_start,
                snapshot_id, format_key, expected_value, observed_value, details
             FROM ops.dq_results WHERE run_id = %s
             EXCEPT ALL
             SELECT result_no, metric_code, severity, status, stream, month_start,
                snapshot_id, format_key, expected_value, observed_value, details
             FROM ops.dq_results WHERE run_id = %s)
            UNION ALL
            (SELECT result_no, metric_code, severity, status, stream, month_start,
                snapshot_id, format_key, expected_value, observed_value, details
             FROM ops.dq_results WHERE run_id = %s
             EXCEPT ALL
             SELECT result_no, metric_code, severity, status, stream, month_start,
                snapshot_id, format_key, expected_value, observed_value, details
             FROM ops.dq_results WHERE run_id = %s)
        ) AS differences""", (first["run_id"], second["run_id"],
                              second["run_id"], first["run_id"])).fetchone()[0]
        self.assertEqual(mismatch, 0)
        with self.assertRaises(UniqueViolation):
            with self.conn.transaction():
                self.conn.execute("""INSERT INTO ops.dq_results
                    (run_id, result_no, metric_code, severity, status)
                    VALUES (%s, 1, 'REPLAY', 'BLOCK', 'PASS')""",
                                  (first["run_id"],))
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM ops.dq_results
            WHERE run_id = %s""", (first["run_id"],)).fetchone()[0],
                         first["result_count"])


if __name__ == "__main__":
    unittest.main()
