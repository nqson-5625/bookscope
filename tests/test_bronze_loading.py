"""PostgreSQL integration checks; enable with BOOKSCOPE_TEST_DB=1."""

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dotenv import load_dotenv

from modules.bronze_loading import connect_database, load_snapshot
from modules.raw_snapshot import complete_snapshot, start_snapshot, write_part


METADATA = {
    "source_url": "https://example.test/query.json",
    "dataset_id": "test-dataset",
    "config_sha256": "a" * 64,
    "extractor_version": "bronze-test",
    "mapping_version": "v1",
}


def make_snapshot(root: Path, stream: str) -> tuple[Path, dict]:
    path, manifest = start_snapshot(root, stream, "2023-01", METADATA)
    if stream == "monthly_overview":
        rows = [
            {"materialtype": "BOOK", "usageclass": "Physical",
             "row_count": "2", "checkouts": "1"},
            {"materialtype": "EBOOK", "usageclass": "Digital",
             "row_count": "3", "checkouts": "2"},
        ]
        manifest.update({"source_row_count": 5,
                         "source_totals": {"row_count": 5, "checkouts": 3}})
    else:
        rows = [{":id": "test-source-id", "checkoutyear": "2023",
                 "checkoutmonth": "1", "checkouts": "3"}]
        manifest.update({"selected_row_count": 1, "selected_checkouts": 3})
    part = write_part(path, 1, rows)
    manifest.update({"parts": [part], "row_count": len(rows), "checkouts": 3,
                     "pages": [{"part": part["path"], "row_count": len(rows),
                                "stored_rows": len(rows)}]})
    complete_snapshot(path, manifest)
    return path, manifest


@unittest.skipUnless(os.environ.get("BOOKSCOPE_TEST_DB") == "1",
                     "set BOOKSCOPE_TEST_DB=1 to run PostgreSQL integration tests")
class BronzeLoadingIntegrationTests(unittest.TestCase):
    def setUp(self):
        load_dotenv()
        self.directory = tempfile.TemporaryDirectory()
        self.conn = connect_database()
        self.snapshot_ids = []

    def tearDown(self):
        try:
            with self.conn.transaction():
                for snapshot_id in self.snapshot_ids:
                    self.conn.execute("DELETE FROM bronze.checkout_records WHERE snapshot_id = %s",
                                      (snapshot_id,))
                    self.conn.execute("DELETE FROM bronze.load_batches WHERE snapshot_id = %s",
                                      (snapshot_id,))
                    self.conn.execute("DELETE FROM ops.data_snapshots WHERE snapshot_id = %s",
                                      (snapshot_id,))
        finally:
            self.conn.close()
            self.directory.cleanup()

    def snapshot(self, stream="monthly_overview"):
        path, manifest = make_snapshot(Path(self.directory.name), stream)
        self.snapshot_ids.append(manifest["snapshot_id"])
        return path, manifest

    def test_overview_load_and_reuse(self):
        path, manifest = self.snapshot()
        result = load_snapshot(self.conn, path, chunk_size=1)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(result["ready_for_transform"])
        self.assertEqual(result["loaded_rows"], 2)
        self.assertEqual(result["loaded_source_row_count"], 5)
        reused = load_snapshot(self.conn, path)
        self.assertEqual(reused["status"], "REUSED")
        self.assertEqual(reused["batch_id"], result["batch_id"])
        counts = self.conn.execute(
            "SELECT (SELECT count(*) FROM bronze.load_batches WHERE snapshot_id = %s), "
            "(SELECT count(*) FROM bronze.checkout_records WHERE snapshot_id = %s)",
            (manifest["snapshot_id"], manifest["snapshot_id"]),
        ).fetchone()
        self.assertEqual(counts, (1, 2))

    def test_detail_load(self):
        path, _ = self.snapshot("book_details")
        result = load_snapshot(self.conn, path)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["loaded_rows"], 1)
        self.assertIsNone(result["expected_source_row_count"])

    def test_failed_reconciliation_rolls_back_rows_and_retry_succeeds(self):
        path, manifest = self.snapshot()
        from modules.raw_snapshot import iter_rows

        def wrong_rows(snapshot_path):
            for record in iter_rows(snapshot_path):
                yield replace(record, source_payload={**record.source_payload,
                                                      "checkouts": "99"})

        with patch("modules.bronze_loading.records.iter_rows", wrong_rows):
            with self.assertRaisesRegex(ValueError, "reconciliation failed"):
                load_snapshot(self.conn, path)
        counts = self.conn.execute(
            "SELECT (SELECT count(*) FROM bronze.checkout_records WHERE snapshot_id = %s), "
            "(SELECT count(*) FROM bronze.load_batches "
            " WHERE snapshot_id = %s AND status = 'FAILED')",
            (manifest["snapshot_id"], manifest["snapshot_id"]),
        ).fetchone()
        self.assertEqual(counts, (0, 1))
        result = load_snapshot(self.conn, path)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(result["ready_for_transform"])

    def test_overview_source_count_mismatch_rolls_back(self):
        path, manifest = self.snapshot()
        from modules.raw_snapshot import iter_rows

        def wrong_rows(snapshot_path):
            for record in iter_rows(snapshot_path):
                yield replace(record, source_payload={**record.source_payload,
                                                      "row_count": "99"})

        with patch("modules.bronze_loading.records.iter_rows", wrong_rows):
            with self.assertRaisesRegex(ValueError, "reconciliation failed"):
                load_snapshot(self.conn, path)
        row = self.conn.execute(
            "SELECT loaded_rows, non_complete_batch_rows, ready_for_transform "
            "FROM bronze.snapshot_load_reconciliation WHERE snapshot_id = %s",
            (manifest["snapshot_id"],),
        ).fetchone()
        self.assertEqual(row, (0, 0, False))

    def test_noncomplete_rows_block_reuse(self):
        path, manifest = self.snapshot()
        load_snapshot(self.conn, path)
        batch_id = self.conn.execute(
            "INSERT INTO bronze.load_batches (snapshot_id, status, finished_at) "
            "VALUES (%s, 'FAILED', now()) RETURNING batch_id",
            (manifest["snapshot_id"],),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO bronze.checkout_records "
            "(snapshot_id, source_row_number, load_batch_id, raw_path, source_payload) "
            "VALUES (%s, 3, %s, %s, %s::jsonb)",
            (manifest["snapshot_id"], batch_id, "test/stale-part.jsonl.gz",
             '{"row_count":"100","checkouts":"100"}'),
        )
        with self.assertRaisesRegex(ValueError, "existing COMPLETE batch fails"):
            load_snapshot(self.conn, path)
        row = self.conn.execute(
            "SELECT loaded_rows, loaded_source_row_count, non_complete_batch_rows, "
            "ready_for_transform FROM bronze.snapshot_load_reconciliation "
            "WHERE snapshot_id = %s",
            (manifest["snapshot_id"],),
        ).fetchone()
        self.assertEqual(row, (2, 5, 1, False))

    def test_changed_manifest_is_rejected_for_registered_snapshot(self):
        path, _ = self.snapshot()
        load_snapshot(self.conn, path)
        manifest_file = path / "manifest.json"
        manifest_file.write_bytes(manifest_file.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "different manifest metadata"):
            load_snapshot(self.conn, path)


if __name__ == "__main__":
    unittest.main()
