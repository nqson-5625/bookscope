"""Seattle Raw row and period validation shared by extraction and verification."""

from __future__ import annotations

import re

def parse_nonnegative(value: object, name: str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"invalid {name}: {value!r}")
    return int(str(value))


def parse_period(period: str) -> tuple[int, int]:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
        raise ValueError(f"invalid period: {period!r}")
    return int(period[:4]), int(period[5:])


def cohort_key(row: dict, aliases: dict[str, str]) -> tuple:
    return (row.get("title"), row.get("creator"),
            aliases.get(row.get("materialtype"), row.get("materialtype")),
            row.get("checkouttype"))


def validate_source_row(row: dict, period: str, codes: list[str] | None = None) -> int:
    year, month = parse_period(period)
    if (parse_nonnegative(row.get("checkoutyear"), "checkoutyear") != year
            or parse_nonnegative(row.get("checkoutmonth"), "checkoutmonth") != month
            or (codes is not None and row.get("materialtype") not in codes)):
        raise ValueError(f"row outside requested period/material scope: {period}")
    return parse_nonnegative(row.get("checkouts"), "checkouts")
