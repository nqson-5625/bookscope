"""Audit Raw provenance, build dbt Silver, and report its quality gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv
import psycopg

from modules.bronze_loading import connect_database
from modules.source_ingestion.audit import audit_range
from modules.source_ingestion.selection import iter_periods


ROOT = Path(__file__).resolve().parents[3]


def _audit_raw(raw_root: Path, foundation_path: Path,
               ingestion_path: Path) -> tuple[dict, dict]:
    foundation = foundation_path.read_bytes()
    ingestion = ingestion_path.read_bytes()
    config = json.loads(foundation)
    config.update(json.loads(ingestion))
    config_sha = hashlib.sha256(foundation + b"\n" + ingestion).hexdigest()
    result = audit_range(raw_root, config, config_sha)
    if result["status"] != "PASS":
        raise ValueError("Raw audit blocked Silver: " + "; ".join(result["issues"][:10]))
    return result, config


def _check_registry(conn: psycopg.Connection, audit: dict, config: dict) -> None:
    rows = conn.execute(
        "WITH ranked AS ("
        " SELECT s.*, r.ready_for_transform, row_number() OVER ("
        " PARTITION BY s.stream, s.scope_id, s.period"
        " ORDER BY s.completed_at DESC, s.extracted_at DESC, s.snapshot_id DESC"
        " ) AS rn FROM ops.data_snapshots s"
        " LEFT JOIN bronze.snapshot_load_reconciliation r USING (snapshot_id)"
        " WHERE s.status = 'COMPLETE' AND s.period BETWEEN %s::date AND %s::date"
        ") SELECT snapshot_id, stream, scope_id, period, mapping_version,"
        " manifest_path, manifest_sha256, ready_for_transform"
        " FROM ranked WHERE rn = 1",
        (config["period_start"] + "-01", config["period_end"] + "-01"),
    ).fetchall()
    selected = {(row[1], row[3].strftime("%Y-%m")): row for row in rows}
    for month in iter_periods(config["period_start"], config["period_end"]):
        for stream, scope, audit_key in (
            ("monthly_overview", "ALL_ELIGIBLE_AGG", "overview_snapshot_id"),
            ("book_details", "SELECTED_DETAIL", "detail_snapshot_id"),
        ):
            row = selected.get((stream, month))
            if row is None:
                raise ValueError(f"{month} {stream}: no registered snapshot")
            snapshot_id, _, registered_scope, _, mapping_version, path, digest, ready = row
            if (snapshot_id != audit["periods"][month][audit_key]
                    or registered_scope != scope or mapping_version != config["mapping_version"]
                    or ready is not True):
                raise ValueError(f"{month} {stream}: Raw/registry/ready gate mismatch")
            manifest = Path(path)
            if not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest() != digest:
                raise ValueError(f"{month} {stream}: registered manifest SHA-256 mismatch")
    if len(rows) != len(audit["periods"]) * 2:
        raise ValueError("unexpected registered stream/period/scope combination")


def _status(conn: psycopg.Connection) -> dict:
    gate = conn.execute(
        "SELECT status, expected_period_scopes, passed_period_scopes,"
        " missing_snapshots, blocked_snapshots FROM silver.pipeline_gate"
    ).fetchone()
    quality = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE status = 'PASS'),"
        " coalesce(sum(rejected_rows), 0), coalesce(sum(warning_rows), 0)"
        " FROM silver.snapshot_quality WHERE contract_version ="
        " (SELECT contract_version FROM silver.pipeline_gate)"
    ).fetchone()
    overview = conn.execute(
        "SELECT count(*), coalesce(sum(checkouts), 0),"
        " coalesce(sum(source_row_count), 0) FROM silver.active_overview_groups"
    ).fetchone()
    detail = conn.execute(
        "SELECT count(*), coalesce(sum(checkouts), 0) FROM silver.active_detail_records"
    ).fetchone()
    return {
        "gate": gate[0], "expected_period_scopes": gate[1],
        "passed_period_scopes": gate[2], "missing_snapshots": gate[3],
        "blocked_snapshots": gate[4], "quality_rows": quality[0],
        "quality_pass": quality[1], "rejected_rows": int(quality[2]),
        "warning_rows": int(quality[3]),
        "overview": {"rows": overview[0], "checkouts": int(overview[1]),
                     "source_row_count": int(overview[2])},
        "detail": {"rows": detail[0], "checkouts": int(detail[1])},
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--raw-root", type=Path,
                        default=ROOT / "data/raw/seattle_checkouts")
    parser.add_argument("--foundation-config", type=Path,
                        default=ROOT / "configs/project_foundation.json")
    parser.add_argument("--ingestion-config", type=Path,
                        default=ROOT / "configs/source_ingestion.json")
    args = parser.parse_args(argv)
    if args.command == "run":
        audit, config = _audit_raw(args.raw_root, args.foundation_config,
                                   args.ingestion_config)
        with connect_database() as conn:
            _check_registry(conn, audit, config)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/run_dbt.py"), "build"],
            cwd=ROOT, check=False,
        )
        if result.returncode:
            return result.returncode
        with connect_database() as conn:
            _check_registry(conn, audit, config)
            status = _status(conn)
        print(json.dumps({"raw_audit": "PASS", **status}, ensure_ascii=False))
        return 0 if status["gate"] == "PASS" else 2
    with connect_database() as conn:
        status = _status(conn)
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["gate"] == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, psycopg.Error) as exc:
        print(f"silver_transformation: {exc}", file=sys.stderr)
        raise SystemExit(1)
