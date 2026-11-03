"""Coordinate Raw, Bronze, Silver and dbt quality evidence for one run."""

from __future__ import annotations

from pathlib import Path

from psycopg import Connection

from .coverage import record_coverage
from .dbt_evidence import record_dbt_build
from .metrics import record_metric, selection_sha256
from .raw_evidence import record_manifest, record_raw_audit
from .silver_evidence import load_silver_evidence
from .snapshot_checks import record_snapshot

__all__ = ["evaluate", "selection_sha256"]


def evaluate(conn: Connection, raw_root: Path, foundation_path: Path,
             ingestion_path: Path) -> tuple[list[dict], list[dict], str, str]:
    """Return all DQ results, selection, contract and mapping versions."""
    evidence = load_silver_evidence(conn)
    selection = evidence.selection
    contract_version = evidence.gate["contract_version"]
    results: list[dict] = []
    record_metric(results, "SILVER_GATE", "BLOCK",
                  evidence.gate["status"] == "PASS",
                  details={"status": evidence.gate["status"]})
    record_metric(results, "EXPECTED_PERIOD_SCOPES", "BLOCK",
                  len(selection) == 72, expected=72, observed=len(selection))
    record_metric(results, "SILVER_GRAIN_DUPLICATES", "BLOCK",
                  evidence.duplicates == 0, expected=0, observed=evidence.duplicates)
    record_dbt_build(conn, results)
    config = record_raw_audit(conn, results, raw_root, foundation_path,
                              ingestion_path)

    expected_formats: dict[tuple[str, str], dict] = {}
    for row in selection:
        record_snapshot(results, row, evidence)
        if row["snapshot_id"] is None:
            continue
        formats = record_manifest(results, row, evidence.bronze_stats,
                                  evidence.silver_stats)
        if formats is not None:
            expected_formats[(row["stream"], row["month_start"].strftime("%Y-%m"))] = formats

    record_coverage(results, selection, expected_formats,
                    evidence.silver_stats, config["period_end"])
    return results, selection, contract_version, config["mapping_version"]
