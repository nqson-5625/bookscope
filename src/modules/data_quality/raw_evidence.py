"""Verify Raw provenance and compare manifest totals with Bronze and Silver."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from psycopg import Connection

from modules.silver_transformation.__main__ import _audit_raw, _check_registry

from .metrics import record_metric


def record_raw_audit(conn: Connection, results: list[dict], raw_root: Path,
                     foundation_path: Path, ingestion_path: Path) -> dict:
    try:
        audit, config = _audit_raw(raw_root, foundation_path, ingestion_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        audit = None
        config = json.loads(foundation_path.read_text(encoding="utf-8"))
        config.update(json.loads(ingestion_path.read_text(encoding="utf-8")))
        record_metric(results, "RAW_AUDIT", "BLOCK", False,
                      details={"error": str(exc)})
    else:
        record_metric(results, "RAW_AUDIT", "BLOCK",
                      len(audit["periods"]) == 36,
                      expected=36, observed=len(audit["periods"]))
    if audit is not None:
        try:
            _check_registry(conn, audit, config)
        except (OSError, ValueError, KeyError) as exc:
            record_metric(results, "REGISTRY_PROVENANCE", "BLOCK", False,
                          details={"error": str(exc)})
        else:
            record_metric(results, "REGISTRY_PROVENANCE", "BLOCK")
    return config


def _compare_format(results: list[dict], selection: dict, format_key: str,
                    expected: dict | None, bronze: dict | None,
                    silver: dict | None) -> None:
    scope = {"stream": selection["stream"], "month_start": selection["month_start"],
             "snapshot_id": selection["snapshot_id"], "format_key": format_key}
    expected_checkouts = int(expected["checkouts"]) if expected is not None else None
    expected_rows = int(expected["row_count"]) if expected is not None else None
    for layer, current in (("BRONZE", bronze), ("SILVER", silver)):
        checkouts = int(current["checkouts"]) if current is not None else None
        rows = (int(current["source_rows"] if selection["stream"] == "monthly_overview"
                    else current["rows"]) if current is not None else None)
        record_metric(results, f"RAW_{layer}_FORMAT_CHECKOUTS", "BLOCK",
                      expected_checkouts is not None and checkouts == expected_checkouts,
                      expected=expected_checkouts, observed=checkouts, **scope)
        record_metric(results, f"RAW_{layer}_FORMAT_ROWS", "BLOCK",
                      expected_rows is not None and rows == expected_rows,
                      expected=expected_rows, observed=rows, **scope)


def record_manifest(results: list[dict], row: dict, bronze_stats: dict,
                    silver_stats: dict) -> dict | None:
    stream, month, snapshot_id = row["stream"], row["month_start"], row["snapshot_id"]
    scope = {"stream": stream, "month_start": month, "snapshot_id": snapshot_id}
    try:
        manifest_bytes = Path(row["manifest_path"]).read_bytes()
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        manifest = json.loads(manifest_bytes)
        formats = manifest["format_totals"]
        if not isinstance(formats, dict):
            raise ValueError("format_totals is not an object")
        for totals in formats.values():
            if (not isinstance(totals, dict)
                    or int(totals["checkouts"]) < 0
                    or int(totals["row_count"]) < 0):
                raise ValueError("invalid format_totals values")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record_metric(results, "MANIFEST_FORMAT_TOTALS", "BLOCK", False,
                      details={"error": str(exc)}, **scope)
        return None
    record_metric(results, "MANIFEST_SHA256", "BLOCK",
                  digest == row["manifest_sha256"],
                  details={"registered_sha256": row["manifest_sha256"],
                           "observed_sha256": digest}, **scope)
    record_metric(results, "MANIFEST_IDENTITY", "BLOCK",
                  manifest.get("snapshot_id") == snapshot_id
                  and manifest.get("stream") == stream
                  and manifest.get("period") == month.strftime("%Y-%m"), **scope)
    observed_formats = {fmt for sid, fmt in bronze_stats if sid == snapshot_id}
    observed_formats.update(fmt for sid, fmt in silver_stats if sid == snapshot_id)
    for fmt in sorted(set(formats) | observed_formats, key=lambda x: x or ""):
        _compare_format(results, row, fmt, formats.get(fmt),
                        bronze_stats.get((snapshot_id, fmt)),
                        silver_stats.get((snapshot_id, fmt)))
    return formats
