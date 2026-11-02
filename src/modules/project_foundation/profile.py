"""Bounded, auditable profiling of SPL Checkouts by Title source records."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import re

FIELDS = (
    "usageclass", "checkouttype", "materialtype", "checkoutyear",
    "checkoutmonth", "checkouts", "title", "isbn", "creator",
    "subjects", "publisher", "publicationyear",
)


def _period_parts(period: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise ValueError(f"invalid period: {period!r}; expected YYYY-MM")
    return int(period[:4]), int(period[5:])


def _material_literal(material_type: str) -> str:
    if not material_type or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", material_type):
        raise ValueError(f"invalid material type: {material_type!r}")
    return f"'{material_type}'"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{number}: expected JSON object")
            rows.append(row)
    return rows


def fetch_soda3(url: str, token: str, query: str, *, timeout: int = 90) -> list[dict]:
    if not token:
        raise ValueError("SOCRATA_APP_TOKEN is required for SODA3")
    body = json.dumps({"query": query, "includeSynthetic": False, "includeSystem": False}).encode()
    request = Request(url, data=body, headers={
        "Content-Type": "application/json", "X-App-Token": token,
        "User-Agent": "BookScope-foundation/1.0",
    }, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            rows = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"SODA3 HTTP {exc.code}; check app token, access and query") from exc
    except URLError as exc:
        raise RuntimeError(f"SODA3 unavailable: {exc.reason}") from exc
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("SODA3 returned an unexpected response shape")
    return rows


def month_query(period: str, limit: int, material_type: str | None = None) -> str:
    year, month = _period_parts(period)
    if not 1 <= limit <= 1000:
        raise ValueError("invalid profile limit")
    material_filter = (f" AND materialtype = {_material_literal(material_type)}"
                       if material_type is not None else "")
    return ("SELECT * WHERE checkoutyear = {year} AND checkoutmonth = {month}"
            "{material_filter} ORDER BY materialtype, title, creator LIMIT {limit}").format(
                year=year, month=month, material_filter=material_filter, limit=limit)


def distribution_query(period: str) -> str:
    year, month = _period_parts(period)
    return ("SELECT materialtype, usageclass, count(*) AS row_count, "
            "sum(checkouts) AS checkouts WHERE checkoutyear = {year} "
            "AND checkoutmonth = {month} "
            "GROUP BY materialtype, usageclass ORDER BY materialtype, usageclass").format(
                year=year, month=month)


def duplicate_tuple_query(period: str, material_types: list[str], limit: int = 10) -> str:
    year, month = _period_parts(period)
    if not material_types or not 1 <= limit <= 100:
        raise ValueError("invalid grain probe parameters")
    codes = ", ".join(_material_literal(code) for code in material_types)
    return ("SELECT title, creator, materialtype, checkouttype, count(*) AS source_rows, "
            "sum(checkouts) AS checkouts WHERE checkoutyear = {year} "
            "AND checkoutmonth = {month} AND materialtype IN ({codes}) "
            "GROUP BY title, creator, materialtype, checkouttype "
            "HAVING count(*) > 1 ORDER BY source_rows DESC LIMIT {limit}").format(
                year=year, month=month, codes=codes, limit=limit)


def cohort_query(year: int, material_types: list[str], limit: int) -> str:
    if not 2005 <= year <= 2100 or not material_types or not 1 <= limit <= 1000:
        raise ValueError("invalid cohort query parameters")
    codes = ", ".join(_material_literal(code) for code in material_types)
    return ("SELECT title, creator, materialtype, checkouttype, "
            "sum(checkouts) AS annual_checkouts WHERE checkoutyear = {year} "
            "AND materialtype IN ({codes}) "
            "GROUP BY title, creator, materialtype, checkouttype "
            "ORDER BY annual_checkouts DESC, title, creator, materialtype, checkouttype "
            "LIMIT {limit}").format(year=year, codes=codes, limit=limit)


def eligible_year_total_query(year: int, material_types: list[str]) -> str:
    if not 2005 <= year <= 2100 or not material_types:
        raise ValueError("invalid cohort total parameters")
    codes = ", ".join(_material_literal(code) for code in material_types)
    return ("SELECT count(*) AS row_count, sum(checkouts) AS checkouts "
            "WHERE checkoutyear = {year} AND materialtype IN ({codes})").format(
                year=year, codes=codes)


def validate_sample(rows: list[dict], period: str, limit: int,
                    material_type: str | None = None) -> None:
    year, month = _period_parts(period)
    if len(rows) > limit:
        raise ValueError(f"{period}: sample exceeds requested limit")
    for row in rows:
        if (_integer(row.get("checkoutyear")) != year
                or _integer(row.get("checkoutmonth")) != month
                or (material_type is not None and row.get("materialtype") != material_type)):
            raise ValueError(f"{period}: sample contains a row outside its query scope")


def validate_distribution(rows: list[dict], period: str) -> dict:
    """Validate the complete source aggregate and summarize its declared book scope."""
    if not rows:
        raise ValueError(f"{period}: empty material distribution")
    groups = set()
    total_rows = total_checkouts = 0
    for row in rows:
        key = (row.get("materialtype"), row.get("usageclass"))
        count, checkouts = _integer(row.get("row_count")), _integer(row.get("checkouts"))
        if (not all(isinstance(value, str) and value for value in key)
                or key in groups or count is None or count < 1 or checkouts is None):
            raise ValueError(f"{period}: invalid material distribution row: {row!r}")
        groups.add(key)
        total_rows += count
        total_checkouts += checkouts
    return {"row_count": total_rows, "checkouts": total_checkouts,
            "group_count": len(groups)}


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        text = str(value)
        if not text or not text.isascii() or not text.isdecimal():
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def profile_rows(rows: Iterable[dict], *, source: str, queries: list[str] | None = None) -> dict:
    records = list(rows)
    if not records:
        raise ValueError("empty sample cannot establish a data profile")
    field_presence = Counter()
    missing = Counter()
    values = {name: Counter() for name in ("materialtype", "usageclass", "checkouttype")}
    invalid = Counter()
    years = Counter()
    seen = set()
    exact_duplicates = 0
    total_checkouts = 0
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("sample contains a non-object record")
        for field in FIELDS:
            if field in row:
                field_presence[field] += 1
            if row.get(field) is None or str(row.get(field)).strip() == "":
                missing[field] += 1
        for field, counter in values.items():
            counter[str(row.get(field, "<missing>"))] += 1
        year, month, checkouts = (_integer(row.get(key)) for key in
                                  ("checkoutyear", "checkoutmonth", "checkouts"))
        if year is None or year < 2005 or year > 2100:
            invalid["checkoutyear"] += 1
        else:
            years[str(year)] += 1
        if month is None or not 1 <= month <= 12:
            invalid["checkoutmonth"] += 1
        if checkouts is None:
            invalid["checkouts"] += 1
        else:
            total_checkouts += checkouts
        if not str(row.get("title") or "").strip():
            invalid["title"] += 1
        canonical = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if canonical in seen:
            exact_duplicates += 1
        seen.add(canonical)
    return {
        "source": source,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(records),
        "sampling_warning": "Bounded ordered sample; missingness and frequencies are not population estimates",
        "queries": queries or [],
        "present_rows_by_field": dict(sorted(field_presence.items())),
        "missing_rows_by_field": dict(sorted(missing.items())),
        "invalid_rows_by_field": dict(sorted(invalid.items())),
        "observed_years": dict(sorted(years.items())),
        "observed_values": {key: dict(sorted(counter.items())) for key, counter in values.items()},
        "sample_checkouts_sum": total_checkouts,
        "exact_duplicate_rows": exact_duplicates,
        "sample_sha256": hashlib.sha256("\n".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False) for row in records
        ).encode()).hexdigest(),
    }
