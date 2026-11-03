"""Evaluate DQ, persist historical results, and report the current gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg.types.json import Jsonb

from modules.bronze_loading import connect_database
from .checks import evaluate
from .metrics import selection_sha256
from .source_state import read_source_state, record_source_tracking


ROOT = Path(__file__).resolve().parents[3]
DDL = ROOT / "sql/data_quality/001_results.sql"
DQ_VERSION = "v1_2026_11_03"


def _install(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(DDL.read_text(encoding="utf-8"))


def _gate_status(conn: psycopg.Connection) -> dict:
    gate = conn.execute("""SELECT run_id, status, expected_period_scopes,
        matched_period_scopes, block_count, warning_count, finished_at,
        source_state_matches
        FROM ops.dq_gate""").fetchone()
    return {
        "run_id": str(gate[0]) if gate[0] else None,
        "gate": gate[1], "expected_period_scopes": gate[2],
        "matched_period_scopes": gate[3], "block_count": gate[4],
        "warning_count": gate[5],
        "finished_at": gate[6].isoformat() if gate[6] else None,
        "source_state_matches": gate[7],
    }


def _run(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    source_state = read_source_state(conn)
    results, selection, contract_version, mapping_version = evaluate(
        conn, args.raw_root, args.foundation_config, args.ingestion_config)
    record_source_tracking(results, source_state)
    run_id = uuid4()
    blocked = sum(r["status"] == "BLOCK" for r in results)
    warned = sum(r["status"] == "WARN" for r in results)
    status = "BLOCK" if blocked else "PASS"
    config_sha = hashlib.sha256(args.foundation_config.read_bytes() + b"\n"
                                + args.ingestion_config.read_bytes()).hexdigest()
    code_hash = hashlib.sha256()
    for source in sorted(Path(__file__).parent.glob("*.py")):
        code_hash.update(source.name.encode("utf-8") + b"\0")
        code_hash.update(source.read_bytes() + b"\0")
    code_hash.update(DDL.read_bytes())
    code_sha = code_hash.hexdigest()
    with conn.transaction():
        conn.execute("""INSERT INTO ops.dq_runs
            (run_id, finished_at, status, contract_version, mapping_version,
             selection_sha256, result_count, block_count, warning_count,
             dq_version, code_sha256, config_sha256, source_state)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (run_id, datetime.now(timezone.utc), status, contract_version,
             mapping_version, selection_sha256(selection), len(results), blocked,
             warned, DQ_VERSION, code_sha, config_sha, Jsonb(source_state)))
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO ops.dq_results
                (run_id, result_no, metric_code, severity, status, stream,
                 month_start, snapshot_id, format_key, expected_value,
                 observed_value, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [(run_id, i, r["metric_code"], r["severity"], r["status"],
                  r["stream"], r["month_start"], r["snapshot_id"],
                  r["format_key"], r["expected_value"], r["observed_value"],
                  Jsonb(r["details"])) for i, r in enumerate(results, 1)])
    return {"run_id": str(run_id), "status": status, "result_count": len(results),
            "block_count": blocked, "warning_count": warned,
            "selection_sha256": selection_sha256(selection),
            "code_sha256": code_sha, **_gate_status(conn)}


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
    with connect_database() as conn:
        if args.command == "run":
            _install(conn)
            report = _run(conn, args)
        else:
            report = _gate_status(conn)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["gate"] == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, psycopg.Error) as exc:
        print(f"data_quality: {exc}", file=sys.stderr)
        raise SystemExit(1)
