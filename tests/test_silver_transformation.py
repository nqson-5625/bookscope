"""Transactional Silver integration checks; enable with BOOKSCOPE_TEST_DB=1."""

import os
from pathlib import Path
import unittest
import uuid

from dotenv import load_dotenv

from modules.bronze_loading import connect_database


@unittest.skipUnless(os.environ.get("BOOKSCOPE_TEST_DB") == "1",
                     "set BOOKSCOPE_TEST_DB=1 to run PostgreSQL integration tests")
class SilverTransformationIntegrationTests(unittest.TestCase):
    def setUp(self):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        self.conn = connect_database()
        self.conn.autocommit = False
        self.snapshot_id = self.conn.execute(
            "SELECT snapshot_id FROM ops.data_snapshots"
            " WHERE stream = 'book_details' AND period = '2025-01-01'"
            " ORDER BY completed_at DESC, extracted_at DESC, snapshot_id DESC LIMIT 1"
        ).fetchone()[0]

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_invalid_metadata_gets_blocking_reasons(self):
        source_row_number = self.conn.execute(
            "SELECT min(source_row_number) FROM bronze.checkout_records"
            " WHERE snapshot_id = %s", (self.snapshot_id,)
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE bronze.checkout_records SET source_payload ="
            " jsonb_set(jsonb_set(source_payload, '{materialtype}',"
            " to_jsonb('UNKNOWN_BOOK_CODE'::text)), '{title}', to_jsonb(' '::text))"
            " WHERE snapshot_id = %s AND source_row_number = %s",
            (self.snapshot_id, source_row_number),
        )
        result = self.conn.execute(
            "SELECT v.block_reasons, s.selection_status"
            " FROM silver.int_validated_rows v"
            " JOIN silver.int_snapshot_selection s USING (snapshot_id)"
            " WHERE v.snapshot_id = %s AND v.source_row_number = %s",
            (self.snapshot_id, source_row_number),
        ).fetchone()
        self.assertEqual(result[1], "READY")
        self.assertIn("UNKNOWN_MATERIAL", result[0])
        self.assertIn("BLANK_TITLE", result[0])

    def test_newest_unready_snapshot_blocks_current_views(self):
        fake_id = "silver-test-" + uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO ops.data_snapshots (snapshot_id, run_id, stream, scope_id,"
            " period, status, source_url, dataset_id, manifest_path, manifest_sha256,"
            " config_sha256, extractor_version, mapping_version, extracted_at,"
            " completed_at, row_count, source_row_count, checkouts)"
            " SELECT %s, run_id, stream, scope_id, period, status, source_url,"
            " dataset_id, %s, manifest_sha256, config_sha256, extractor_version,"
            " mapping_version, extracted_at + interval '2 days',"
            " completed_at + interval '2 days', row_count, source_row_count, checkouts"
            " FROM ops.data_snapshots WHERE snapshot_id = %s",
            (fake_id, fake_id + "/manifest.json", self.snapshot_id),
        )
        status = self.conn.execute(
            "SELECT selection_status FROM silver.int_snapshot_selection"
            " WHERE stream = 'book_details' AND month_start = '2025-01-01'"
        ).fetchone()[0]
        gate = self.conn.execute("SELECT status FROM silver.pipeline_gate").fetchone()[0]
        active = self.conn.execute("SELECT count(*) FROM silver.active_detail_records").fetchone()[0]
        self.assertEqual(status, "SNAPSHOT_NOT_READY")
        self.assertEqual(gate, "BLOCK")
        self.assertEqual(active, 0)


if __name__ == "__main__":
    unittest.main()
