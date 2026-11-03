"""Register immutable Raw snapshot metadata before loading rows."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
from pathlib import Path
import re

import psycopg


SNAPSHOT_COLUMNS = (
    "snapshot_id, stream, scope_id, period, source_url, dataset_id, "
    "manifest_path, manifest_sha256, config_sha256, extractor_version, "
    "mapping_version, extracted_at, completed_at, row_count, "
    "source_row_count, checkouts"
)


def snapshot_values(path: Path, manifest: dict) -> tuple:
    config_hash = manifest["config_sha256"]
    if not isinstance(config_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", config_hash):
        raise ValueError("manifest config_sha256 must be a lowercase SHA-256 digest")
    extracted = datetime.fromisoformat(manifest["extracted_at_utc"])
    completed = datetime.fromisoformat(manifest["completed_at_utc"])
    if extracted.tzinfo is None or completed.tzinfo is None:
        raise ValueError("manifest timestamps must include a timezone")
    return (
        manifest["snapshot_id"], manifest["stream"], manifest["scope_id"],
        date.fromisoformat(manifest["period"] + "-01"), manifest["source_url"],
        manifest["dataset_id"], str((path / "manifest.json").resolve()),
        hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
        config_hash, manifest["extractor_version"], manifest["mapping_version"],
        extracted, completed, manifest["row_count"],
        manifest["source_row_count"] if manifest["stream"] == "monthly_overview" else None,
        manifest["checkouts"],
    )


def register_snapshot(conn: psycopg.Connection, values: tuple,
                      run_id: str | None) -> None:
    with conn.transaction():
        conn.execute(
            f"INSERT INTO ops.data_snapshots ({SNAPSHOT_COLUMNS}, run_id) "
            "VALUES (" + ", ".join(["%s"] * 17) + ") "
            "ON CONFLICT (snapshot_id) DO NOTHING",
            (*values, run_id),
        )
        stored = conn.execute(
            f"SELECT {SNAPSHOT_COLUMNS} FROM ops.data_snapshots WHERE snapshot_id = %s",
            (values[0],),
        ).fetchone()
        if stored != values:
            raise ValueError("snapshot_id is already registered with different manifest metadata")
