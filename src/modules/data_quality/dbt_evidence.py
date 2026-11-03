"""Match dbt build results to the Silver selection invocation."""

from __future__ import annotations

import json
from pathlib import Path

from psycopg import Connection

from .dbt_contract import REQUIRED_MODELS, REQUIRED_TESTS
from .metrics import record_metric


ROOT = Path(__file__).resolve().parents[3]


def assess_build(manifest: dict, artifact: dict, selected_invocation: str | None,
                 selection_count: int) -> tuple[bool, dict, list[dict]]:
    """Require every Silver model and test from the project manifest exactly once."""
    nodes = [node for node in manifest["nodes"].values()
             if node.get("package_name") == "dbt_bookscope"
             and node.get("resource_type") in {"model", "test"}
             and (path := node.get("original_file_path", "").replace("\\", "/"))
             and (path.startswith("models/silver/")
                  or path == "models/sources.yml"
                  or path.startswith("tests/silver_"))]
    models = {node["name"]: node["unique_id"] for node in nodes
              if node["resource_type"] == "model"}
    tests = {node["name"]: node["unique_id"] for node in nodes
             if node["resource_type"] == "test"}
    expected = set(models.values()) | set(tests.values())
    records = artifact["results"]
    by_id = {record["unique_id"]: record for record in records}
    missing_models = sorted(REQUIRED_MODELS - models.keys())
    missing_tests = sorted(REQUIRED_TESTS - tests.keys())
    missing_results = sorted(expected - by_id.keys())
    duplicate_results = sorted({record["unique_id"] for record in records
                                if sum(r["unique_id"] == record["unique_id"]
                                       for r in records) > 1})
    incorrect_status = sorted(uid for uid in expected & by_id.keys()
                              if by_id[uid]["status"] !=
                              ("pass" if uid.startswith("test.") else "success"))
    all_success = all(record["status"] in {"pass", "success"} for record in records)
    invocation = artifact["metadata"]["invocation_id"]
    details = {
        "artifact_invocation_id": invocation,
        "selected_invocation_id": selected_invocation,
        "manifest_invocation_id": manifest["metadata"]["invocation_id"],
        "expected_model_count": len(models),
        "expected_test_count": len(tests),
        "recorded_result_count": len(records),
        "missing_models": missing_models,
        "missing_tests": missing_tests,
        "missing_results": missing_results,
        "duplicate_results": duplicate_results,
        "incorrect_status": incorrect_status,
    }
    valid = (
        selected_invocation is not None and selection_count == 72
        and invocation == selected_invocation == manifest["metadata"]["invocation_id"]
        and manifest["metadata"]["project_name"] == "dbt_bookscope"
        and artifact["args"]["which"] == "build"
        and not artifact["args"].get("select")
        and not artifact["args"].get("exclude")
        and len(models) >= len(REQUIRED_MODELS)
        and len(tests) >= len(REQUIRED_TESTS)
        and not (missing_models or missing_tests or missing_results
                 or duplicate_results or incorrect_status)
        and all_success
    )
    return valid, details, [by_id[uid] for uid in sorted(tests.values()) if uid in by_id]


def record_dbt_build(conn: Connection, results: list[dict]) -> None:
    """Store the tests from the dbt build that produced the selected Silver run."""
    latest = conn.execute("""SELECT invocation_id, count(*)
        FROM silver.run_snapshot_selection GROUP BY invocation_id
        ORDER BY max(selected_at) DESC, invocation_id DESC LIMIT 1""").fetchone()
    try:
        manifest = json.loads((ROOT / "dbt_bookscope/target/manifest.json")
                              .read_text(encoding="utf-8"))
        artifact = json.loads((ROOT / "dbt_bookscope/target/run_results.json")
                              .read_text(encoding="utf-8"))
        valid, details, tests = assess_build(
            manifest, artifact, latest[0] if latest else None,
            int(latest[1]) if latest else 0)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record_metric(results, "DBT_BUILD", "BLOCK", False,
                      details={"error": str(exc)})
        return
    record_metric(results, "DBT_BUILD", "BLOCK", valid,
                  expected=72, observed=int(latest[1]) if latest else None,
            details=details)
    for record in tests:
        record_metric(results, "DBT_TEST", "BLOCK", record["status"] == "pass",
                      details={"unique_id": record["unique_id"],
                               "dbt_status": record["status"],
                         "invocation_id": details["artifact_invocation_id"]})
