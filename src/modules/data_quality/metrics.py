"""Shared result shape and stable snapshot selection fingerprint."""

from __future__ import annotations

from datetime import date
import hashlib
import json


WARNING_CODES = (
    "MISSING_CREATOR", "MISSING_ISBN", "MISSING_SUBJECTS",
    "MISSING_PUBLISHER", "MISSING_PUBLICATION_YEAR",
)


def selection_sha256(selection: list[dict]) -> str:
    """Hash the ordered, effective selection for a recorded DQ run."""
    keys = ("stream", "scope_id", "month_start", "snapshot_id",
            "selection_status", "manifest_sha256")
    rows = [[str(row[key]) if row[key] is not None else None for key in keys]
            for row in selection]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def record_metric(results: list[dict], code: str, severity: str, passed: bool = True,
                  *, stream: str | None = None, month_start: date | None = None,
                  snapshot_id: str | None = None, format_key: str | None = None,
                  expected: int | None = None, observed: int | None = None,
                  details: dict | None = None) -> None:
    results.append({
        "metric_code": code, "severity": severity,
        "status": ("CONTEXT" if severity == "CONTEXT" else
                   "PASS" if passed else severity),
        "stream": stream, "month_start": month_start,
        "snapshot_id": snapshot_id, "format_key": format_key,
        "expected_value": expected, "observed_value": observed,
        "details": details or {},
    })
