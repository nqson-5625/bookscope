"""Build, replay, and inspect versioned Gold analytics marts."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import sys
import time

from dotenv import load_dotenv
import psycopg

from modules.bronze_loading import connect_database
from .dbt import artifact_dir, create_stage, drop_stage, run_dbt, stage_schema
from .digest import code_sha256, data_version, output_sha256
from .gate import read_pin, save_pin
from .paths import (BUILD_LOCK_ID, GOLD_DATA_TEST_IDS_SHA256,
                    GOLD_UNIT_TEST_IDS, MIGRATION, MODELS, ROOT)
from .publish import mark_failed, mark_tested, publish
from .validation import validate_stage


def migrate(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(MIGRATION.read_text(encoding="utf-8"))


def _dbt_evidence(pin) -> dict:
    target = artifact_dir(pin)
    run_bytes = (target / "run_results.json").read_bytes()
    manifest_bytes = (target / "manifest.json").read_bytes()
    run = json.loads(run_bytes)
    manifest = json.loads(manifest_bytes)
    if run["metadata"]["invocation_id"] != manifest["metadata"]["invocation_id"]:
        raise ValueError("dbt artifacts have different invocation IDs")
    actual = {r["unique_id"]: r for r in run["results"]}
    expected_models = {uid for uid, node in manifest["nodes"].items()
                       if node.get("resource_type") == "model"
                       and node.get("original_file_path", "").replace("\\", "/").startswith("models/gold/")}
    expected_tests = {uid for uid, node in manifest["nodes"].items()
                      if node.get("resource_type") == "test"
                      and (node.get("original_file_path", "").replace("\\", "/").startswith("tests/gold/")
                           or node.get("original_file_path", "").replace("\\", "/").startswith("models/gold/"))}
    expected_units = {uid for uid, node in manifest.get("unit_tests", {}).items()
                      if node.get("original_file_path", "").replace("\\", "/").startswith("models/gold/")}
    inventory_hash = sha256("\n".join(sorted(expected_tests)).encode()).hexdigest()
    required_models = {f"model.dbt_bookscope.{name}" for name in MODELS}
    if (expected_models != required_models or len(expected_tests) != 31
            or inventory_hash != GOLD_DATA_TEST_IDS_SHA256
            or expected_units != GOLD_UNIT_TEST_IDS
            or any(manifest["nodes"][uid]["schema"] != stage_schema(pin)
                   for uid in expected_models)):
        raise ValueError(f"Gold test inventory incomplete: {len(expected_models)} models, {len(expected_tests)} data tests, {len(expected_units)} unit tests")
    required = expected_models | expected_tests | expected_units
    if (set(actual) != required
            or any(r["status"] not in ("success", "pass") for r in actual.values())):
        missing = sorted(required - set(actual))
        extra = sorted(set(actual) - required)
        failed = sorted(k for k, r in actual.items()
                        if r["status"] not in ("success", "pass"))
        raise ValueError(f"Gold dbt evidence incomplete: missing={missing}, extra={extra}, failed={failed}")
    return {
        "invocation_id": run["metadata"]["invocation_id"],
        "artifact_dir": str(target),
        "run_results_sha256": sha256(run_bytes).hexdigest(),
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "model_count": len(expected_models), "test_count": len(expected_tests),
        "unit_test_count": len(expected_units),
        "model_ids": sorted(expected_models), "test_ids": sorted(expected_tests),
        "unit_test_ids": sorted(expected_units),
        "statuses": {uid: r["status"] for uid, r in actual.items()},
        "elapsed_time": run.get("elapsed_time"),
    }


def _status(conn: psycopg.Connection) -> dict:
    gate = conn.execute("""SELECT run_id::text,status,expected_period_scopes,
        matched_period_scopes,block_count,source_state_matches
        FROM ops.dq_gate""").fetchone()
    current = conn.execute("""SELECT p.data_version,p.published_at,
        p.output_sha256,p.dq_run_id::text FROM ops.current_publication c
        JOIN ops.data_publications p USING(data_version) WHERE c.id=1""").fetchone()
    versions = conn.execute("SELECT count(*) FROM ops.data_publications WHERE status='PUBLISHED'").fetchone()[0]
    return {"dq_gate": {"run_id": gate[0], "status": gate[1],
                        "expected": gate[2], "matched": gate[3],
                        "blocks": gate[4], "source_state_matches": gate[5]},
            "current_publication": ({"data_version": current[0].strip(),
                                     "published_at": current[1].isoformat(),
                                     "output_sha256": current[2].strip(),
                                     "dq_run_id": current[3]} if current else None),
            "published_versions": versions}


def _build_locked(conn: psycopg.Connection, replay: bool) -> dict:
    previous = _status(conn)["current_publication"]
    if replay and previous is None:
        raise ValueError("no published Gold version to replay")
    pin = read_pin(conn)
    save_pin(conn, pin)
    started = time.monotonic()
    stage = None
    try:
        stage = create_stage(conn, pin)
        dbt_run = run_dbt(pin)
        evidence = _dbt_evidence(pin)
        counts = validate_stage(conn, pin, stage)
        output_hash, digest_counts = output_sha256(conn, stage)
        if counts != digest_counts:
            raise ValueError("digest row counts disagree with Gold validation")
        code_hash = code_sha256()
        version = data_version(pin, code_hash, output_hash)
        if replay and version != previous["data_version"]:
            raise ValueError("replay produced a different data_version")
        mark_tested(conn, pin, code_hash, output_hash, version,
                    evidence, {"counts": counts, "reconciled": True})
        outcome = publish(conn, pin, stage, version, code_hash, output_hash)
        if replay and (outcome != "REUSED" or version != previous["data_version"]):
            raise ValueError("replay did not reproduce the current publication")
        return {"build_id": str(pin.build_id), "outcome": outcome,
                "data_version": version, "output_sha256": output_hash,
                "selection_sha256": pin.selection_sha256,
                "dq_run_id": str(pin.dq_run_id), "counts": counts,
                "dbt_models": evidence["model_count"],
                "dbt_tests": evidence["test_count"],
                "dbt_unit_tests": evidence["unit_test_count"],
                "artifact_dir": evidence["artifact_dir"],
                "dbt_seconds": dbt_run["elapsed_seconds"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "database_bytes": conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]}
    except Exception as exc:
        mark_failed(conn, pin, str(exc))
        raise
    finally:
        if stage is not None:
            drop_stage(conn, pin)


def _build(conn: psycopg.Connection, replay: bool) -> dict:
    """Serialize Gold builds so two dbt processes cannot contend for RAM."""
    conn.execute("SELECT pg_advisory_lock(%s)", (BUILD_LOCK_ID,))
    try:
        return _build_locked(conn, replay)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (BUILD_LOCK_ID,))


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "build", "replay", "status"))
    args = parser.parse_args(argv)
    with connect_database() as conn:
        if args.command == "migrate":
            migrate(conn)
            report = {"migration": str(MIGRATION), "status": "PASS"}
        elif args.command == "status":
            report = _status(conn)
        else:
            report = _build(conn, args.command == "replay")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, psycopg.Error) as exc:
        print(f"gold_analytics_marts: {exc}", file=sys.stderr)
        raise SystemExit(1)
