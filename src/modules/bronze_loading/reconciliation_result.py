"""Read the database gate for a registered Bronze snapshot."""

from __future__ import annotations

import psycopg


def reconciliation(conn: psycopg.Connection, snapshot_id: str) -> dict:
    row = conn.execute(
        "SELECT completed_batch_id, expected_rows, loaded_rows, "
        "expected_checkouts, loaded_checkouts, expected_source_row_count, "
        "loaded_source_row_count, non_complete_batch_rows, ready_for_transform "
        "FROM bronze.snapshot_load_reconciliation WHERE snapshot_id = %s",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("snapshot reconciliation row is missing")
    keys = ("batch_id", "expected_rows", "loaded_rows", "expected_checkouts",
            "loaded_checkouts", "expected_source_row_count", "loaded_source_row_count",
            "non_complete_batch_rows", "ready_for_transform")
    result = dict(zip(keys, row))
    result["loaded_checkouts"] = int(result["loaded_checkouts"])
    result["loaded_source_row_count"] = int(result["loaded_source_row_count"])
    return result
