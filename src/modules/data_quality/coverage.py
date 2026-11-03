"""Selected cohort coverage and configured period freshness metrics."""

from __future__ import annotations

from collections import defaultdict

from .metrics import record_metric


def record_coverage(results: list[dict], selection: list[dict],
                    expected_formats: dict, silver_stats: dict,
                    period_end: str) -> None:
    # Cohort coverage is contextual. A selected subset larger than its overview
    # universe is a consistency failure, evaluated within the same month/format.
    by_month = defaultdict(dict)
    for row in selection:
        by_month[row["month_start"]][row["stream"]] = row["snapshot_id"]
    for month, streams in by_month.items():
        overview_id, detail_id = (streams.get("monthly_overview"),
                                  streams.get("book_details"))
        if not overview_id or not detail_id:
            continue
        formats = set(expected_formats.get(("monthly_overview", month.strftime("%Y-%m")), {}))
        formats.update(expected_formats.get(("book_details", month.strftime("%Y-%m")), {}))
        for fmt in sorted(formats):
            overview = silver_stats.get((overview_id, fmt))
            detail = silver_stats.get((detail_id, fmt))
            universe = int(overview["checkouts"]) if overview else 0
            selected = int(detail["checkouts"]) if detail else 0
            record_metric(results, "COHORT_COVERAGE_BOUND", "BLOCK", selected <= universe,
                          stream="book_details", month_start=month, snapshot_id=detail_id,
                          format_key=fmt, expected=universe, observed=selected)
            record_metric(results, "COHORT_COVERAGE_PERCENT", "CONTEXT",
                          stream="book_details", month_start=month, snapshot_id=detail_id,
                          format_key=fmt, details={"percent": round(100 * selected / universe, 4)
                                                          if universe else None})

    newest = max((r["month_start"] for r in selection if r["snapshot_id"]), default=None)
    end_year, end_month = map(int, period_end.split("-"))
    lag_months = ((end_year - newest.year) * 12 + end_month - newest.month
                  if newest else None)
    record_metric(results, "FRESHNESS", "CONTEXT",
                  expected=0, observed=lag_months,
                  details={"latest_period": newest.isoformat() if newest else None,
                           "expected_period_end": period_end,
                           "lag_months": lag_months})
