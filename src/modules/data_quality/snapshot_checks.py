"""Validity and metadata completeness results for one selected snapshot."""

from __future__ import annotations

from .metrics import WARNING_CODES, record_metric
from .silver_evidence import SilverEvidence


def record_snapshot(results: list[dict], row: dict,
                    evidence: SilverEvidence) -> None:
    stream, month, snapshot_id = row["stream"], row["month_start"], row["snapshot_id"]
    scope = {"stream": stream, "month_start": month, "snapshot_id": snapshot_id}
    record_metric(results, "SNAPSHOT_SELECTION", "BLOCK",
                  row["selection_status"] == "READY" and snapshot_id is not None,
                  details={"selection_status": row["selection_status"],
                           "manifest_sha256": row["manifest_sha256"],
                           "scope_id": row["scope_id"]}, **scope)
    if snapshot_id is None:
        return
    q = evidence.quality.get(snapshot_id)
    live = evidence.live_validation.get(snapshot_id)
    record_metric(results, "BRONZE_VALIDATION", "BLOCK",
                  row["selection_status"] == "READY" and (
                      (live is not None and int(live["blocked_rows"]) == 0)
                      or (live is None and q is not None and int(q["bronze_rows"]) == 0)),
                  expected=0, observed=int(live["blocked_rows"]) if live else None,
                  details={"reason_codes": live["reason_codes"] if live else []}, **scope)
    record_metric(results, "SILVER_SNAPSHOT_QUALITY", "BLOCK",
                  q is not None and q["status"] == "PASS",
                  expected=0, observed=int(q["rejected_rows"]) if q else None,
                  details={"block_reasons": q["block_reasons"] if q else ["MISSING_QUALITY"]},
                  **scope)
    if q is None:
        return
    total = int(q["bronze_rows"])
    rejected = int(q["rejected_rows"])
    record_metric(results, "VALIDITY_ROWS", "CONTEXT", observed=total - rejected,
                  expected=total, details={"rejected_rows": rejected,
                                           "valid_percent": round(100 * (total - rejected) / total, 4)
                                           if total else None}, **scope)
    if stream == "book_details":
        blank_titles = int(evidence.title_missing.get(snapshot_id, 0))
        record_metric(results, "BLANK_TITLE", "BLOCK", blank_titles == 0,
                      expected=0, observed=blank_titles,
                      details={"rate_percent": round(100 * blank_titles / total, 4)
                               if total else None}, **scope)
        for warning_code in WARNING_CODES:
            n = int(evidence.warnings.get((snapshot_id, warning_code), 0))
            record_metric(results, warning_code, "WARN", n == 0,
                          expected=0, observed=n,
                          details={"rate_percent": round(100 * n / total, 4)
                                   if total else None}, **scope)
