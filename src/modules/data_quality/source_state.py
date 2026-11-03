"""Fail closed when monitored PostgreSQL source tables change after DQ."""

from __future__ import annotations

from psycopg import Connection

from .metrics import record_metric


SOURCE_TABLES = frozenset({
    "ops.data_snapshots", "bronze.load_batches", "bronze.checkout_records",
    "silver.format_map", "silver.overview_groups", "silver.detail_records",
    "silver.quarantine_records", "silver.snapshot_quality",
    "silver.run_snapshot_selection",
})


def read_source_state(conn: Connection) -> dict:
    return conn.execute("SELECT ops.dq_current_source_state()").fetchone()[0]


def record_source_tracking(results: list[dict], state: dict) -> None:
    missing = sorted(SOURCE_TABLES - state.keys())
    unexpected = sorted(state.keys() - SOURCE_TABLES)
    disabled = sorted(name for name, entry in state.items()
                      if entry["relation_oid"] is None
                      or entry["relation_file"] is None
                      or entry["trigger_enabled"] is not True)
    record_metric(results, "SOURCE_CHANGE_TRACKING", "BLOCK",
                  not (missing or unexpected or disabled),
                  expected=len(SOURCE_TABLES), observed=len(state),
                  details={"missing_tables": missing,
                           "unexpected_tables": unexpected,
                           "untracked_tables": disabled})
