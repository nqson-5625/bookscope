import json
from pathlib import Path
import tempfile
import unittest

from modules.raw_snapshot import (complete_snapshot, fail_snapshot, iter_rows,
                                  latest_complete_snapshot, start_snapshot,
                                  verify_snapshot, write_manifest, write_part)


CONFIG = {"source_url": "https://example.test/query.json", "dataset_id": "tmmm-ytt6",
          "book_material_type_map": {"BOOK": "PRINT_BOOK"}, "mapping_version": "v1",
          "reviewed_excluded_material_types": []}
METADATA = {**CONFIG, "config_sha256": "config-hash",
            "extractor_version": "test-extractor"}


def make_snapshot(root: Path, *, checkouts: int = 5):
    path, manifest = start_snapshot(root, "monthly_overview", "2023-01", METADATA)
    row = {"materialtype": "BOOK", "usageclass": "Physical",
           "row_count": "2", "checkouts": str(checkouts)}
    part = write_part(path, 1, [row])
    manifest.update({"parts": [part], "pages": [{"page": 1, "row_count": 1,
                                                 "part": part["path"]}],
                     "queries": ["SELECT ..."], "row_count": 1,
                     "source_row_count": 2, "checkouts": checkouts,
                     "source_totals": {"row_count": 2, "checkouts": checkouts},
                     "source_material_distribution": [row],
                     "unreviewed_material_types": [],
                     "format_totals": {"PRINT_BOOK": {"row_count": 2,
                                                      "checkouts": checkouts}}})
    complete_snapshot(path, manifest)
    return path, manifest


class RawSnapshotTests(unittest.TestCase):
    def test_offline_rows_have_stable_positions_and_raw_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path, manifest = make_snapshot(Path(directory))
            rows = list(iter_rows(path))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].snapshot_id, manifest["snapshot_id"])
            self.assertEqual(rows[0].source_row_number, 1)
            self.assertEqual(rows[0].raw_path.name, "part-0001.jsonl.gz")
            self.assertEqual(rows[0].source_payload["checkouts"], "5")
            self.assertEqual(verify_snapshot(path)["status"], "COMPLETE")

    def test_complete_snapshot_rejects_new_writes_and_reader_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path, manifest = make_snapshot(Path(directory))
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_manifest(path, manifest)
            with self.assertRaisesRegex(ValueError, "WRITING"):
                write_part(path, 2, [{"materialtype": "BOOK"}])
            part = path / manifest["parts"][0]["path"]
            part.write_bytes(part.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                list(iter_rows(path))

    def test_select_ignores_failed_but_rejects_corrupt_newest_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, _ = make_snapshot(root)
            failed, failed_manifest = start_snapshot(root, "monthly_overview", "2023-01",
                                                     METADATA)
            fail_snapshot(failed, failed_manifest, RuntimeError("source unavailable"))
            self.assertEqual(latest_complete_snapshot(root, "monthly_overview", "2023-01")[0], first)
            second, manifest = make_snapshot(root, checkouts=6)
            self.assertEqual(latest_complete_snapshot(root, "monthly_overview", "2023-01")[0], second)
            disk = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
            disk["snapshot_id"] = "incorrect-id"
            (second / "manifest.json").write_text(json.dumps(disk), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest path"):
                latest_complete_snapshot(root, "monthly_overview", "2023-01")
            with self.assertRaisesRegex(ValueError, "not COMPLETE"):
                list(iter_rows(failed))


if __name__ == "__main__":
    unittest.main()
