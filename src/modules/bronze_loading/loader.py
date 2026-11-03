"""Coordinate verified Raw loading and atomic Bronze batch completion."""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from modules.raw_snapshot import verify_snapshot

from .records import insert_records
from .reconciliation_result import reconciliation
from .registry import register_snapshot, snapshot_values


def _record_failure(conn: psycopg.Connection, snapshot_id: str,
                    run_id: str | None, error: Exception) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO bronze.load_batches "
            "(snapshot_id, run_id, status, finished_at, error_message) "
            "VALUES (%s, %s, 'FAILED', now(), %s)",
            (snapshot_id, run_id, f"{type(error).__name__}: {error}"[:4000]),
        )


def load_snapshot(conn: psycopg.Connection, path: Path, *, run_id: str | None = None,
                  chunk_size: int = 1000) -> dict:
    """Verify and load one snapshot; retries reuse a reconciled COMPLETE batch.

    Snapshot registration is committed first so a failed load can be logged.
    Rows and the COMPLETE status commit together only after the SQL gate passes.
    """
    if not conn.autocommit:
        raise ValueError("load_snapshot requires an autocommit connection")
    if type(chunk_size) is not int or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    path = Path(path).resolve()
    manifest = verify_snapshot(path)
    values = snapshot_values(path, manifest)
    snapshot_id = manifest["snapshot_id"]
    register_snapshot(conn, values, run_id)
    started_attempt = False
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (snapshot_id,))
            existing = reconciliation(conn, snapshot_id)
            if existing["batch_id"] is not None:
                if not existing["ready_for_transform"]:
                    raise ValueError("existing COMPLETE batch fails Bronze reconciliation")
                return {"snapshot_id": snapshot_id, "status": "REUSED", **existing}
            batch_id = conn.execute(
                "INSERT INTO bronze.load_batches (snapshot_id, run_id, status) "
                "VALUES (%s, %s, 'STARTED') RETURNING batch_id",
                (snapshot_id, run_id),
            ).fetchone()[0]
            started_attempt = True
            count = insert_records(conn, path, snapshot_id, batch_id, chunk_size)
            if count != manifest["row_count"]:
                raise ValueError("Raw row count changed during load")
            if hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest() != values[7]:
                raise ValueError("Raw manifest changed during load")
            verify_snapshot(path)
            conn.execute(
                "UPDATE bronze.load_batches SET status = 'COMPLETE', "
                "finished_at = now(), inserted_rows = %s WHERE batch_id = %s",
                (count, batch_id),
            )
            result = reconciliation(conn, snapshot_id)
            if result["batch_id"] != batch_id or not result["ready_for_transform"]:
                raise ValueError(f"Bronze reconciliation failed: {result}")
        return {"snapshot_id": snapshot_id, "status": "COMPLETE", **result}
    except Exception as error:
        if started_attempt:
            try:
                _record_failure(conn, snapshot_id, run_id, error)
            except Exception as log_error:
                error.add_note(f"Could not record FAILED batch: {log_error}")
        raise
