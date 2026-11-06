"""Independent, calendar-indexed KPI reference for tests and reconciliation."""

from __future__ import annotations

from decimal import Decimal


def rate(current: int | None, previous: int | None) -> tuple[Decimal | None, str]:
    if current is None or previous is None:
        return None, "INSUFFICIENT_HISTORY"
    if previous == 0:
        return None, "NEW_ACTIVITY" if current > 0 else "NO_ACTIVITY"
    return (Decimal(current) - previous) / previous, "OK"


def rolling_three(values: list[int | None]) -> tuple[int | None, Decimal | None, str]:
    if len(values) != 3 or any(v is None for v in values):
        return None, None, "INSUFFICIENT_HISTORY"
    total = sum(values)
    return total, Decimal(total) / 3, "OK"


def growth_three(values: list[int | None]) -> tuple[Decimal | None, str]:
    if len(values) != 6 or any(v is None for v in values):
        return None, "INSUFFICIENT_HISTORY"
    previous, recent = sum(values[:3]), sum(values[3:])
    if previous == 0:
        return None, "NEW_ACTIVITY" if recent > 0 else "NO_ACTIVITY"
    return (Decimal(recent) - previous) / previous, "OK"


def share(part: int, whole: int | None) -> tuple[Decimal | None, str]:
    if whole is None:
        return None, "MISSING_MONTH"
    if whole == 0:
        return None, "ZERO_DENOMINATOR"
    return Decimal(part) / whole, "OK"
