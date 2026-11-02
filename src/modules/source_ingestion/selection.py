"""Source filters, cohort selection and cohort-file validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from modules.project_foundation.profile import cohort_query
from modules.raw_snapshot.schema import cohort_key as _cohort_key
from modules.raw_snapshot.schema import parse_nonnegative as _number
from modules.raw_snapshot.schema import parse_period as _period

from .client import SodaClient

DETAIL_FIELDS = ("title", "creator", "materialtype", "checkouttype")

def iter_periods(start: str, end: str):
    first, last = _period(start), _period(end)
    if first > last:
        raise ValueError("period_start is after period_end")
    year, month = first
    while (year, month) <= last:
        yield f"{year:04d}-{month:02d}"
        year, month = year + (month == 12), month % 12 + 1


def _literal(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("query literal must be text")
    return "'" + value.replace("'", "''") + "'"


def _codes(config: dict) -> list[str]:
    mapping = config["book_material_type_map"]
    if (not isinstance(mapping, dict) or not mapping
            or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", x)
                   or not isinstance(format_key, str) or not format_key
                   for x, format_key in mapping.items())):
        raise ValueError("invalid book_material_type_map")
    excluded = config.get("reviewed_excluded_material_types", [])
    if (not isinstance(excluded, list) or any(not isinstance(x, str) or not x for x in excluded)
            or len(excluded) != len(set(excluded))):
        raise ValueError("invalid reviewed_excluded_material_types")
    codes = list(mapping)
    if set(codes) & set(excluded):
        raise ValueError("mapped and excluded material types overlap")
    _aliases(config)
    return codes


def _aliases(config: dict) -> dict[str, str]:
    aliases = config.get("cohort_material_aliases", {})
    mapping = config["book_material_type_map"]
    if not isinstance(aliases, dict) or any(
        not isinstance(source, str) or not isinstance(target, str)
        or source == target or source not in mapping or target not in mapping
        or mapping[source] != mapping[target] or target in aliases
        for source, target in aliases.items()
    ):
        raise ValueError("invalid cohort_material_aliases")
    return aliases


def _where(period: str, codes: list[str] | None = None,
           titles: list[str] | None = None) -> str:
    year, month = _period(period)
    result = f"checkoutyear = {year} AND checkoutmonth = {month}"
    if codes is not None:
        result += " AND materialtype IN (" + ", ".join(_literal(x) for x in codes) + ")"
    if titles is not None:
        result += " AND title IN (" + ", ".join(_literal(x) for x in titles) + ")"
    return result


def _total_query(where: str) -> str:
    return f"SELECT count(*) AS row_count, sum(checkouts) AS checkouts WHERE {where}"


def _total(client: SodaClient, query: str) -> dict[str, int]:
    rows = client.query(query)
    if len(rows) != 1:
        raise ValueError("expected one source total row")
    count = _number(rows[0].get("row_count"), "row_count")
    checkouts = (0 if count == 0 and rows[0].get("checkouts") is None
                 else _number(rows[0].get("checkouts"), "checkouts"))
    return {"row_count": count, "checkouts": checkouts}


def ingest_cohort(client: SodaClient, config: dict, output: Path) -> dict:
    """Select and persist the fixed 2023 tuple cohort with its source query."""
    selection = config["detail_selection"]
    year = int(selection["selection_start"][:4])
    if (selection["selection_start"] != f"{year}-01"
            or selection["selection_end"] != f"{year}-12"
            or selection["group_by"] != list(DETAIL_FIELDS)):
        raise ValueError("cohort must cover one calendar year and use the declared tuple")
    query = cohort_query(year, _codes(config), selection["max_tuples"])
    rows = client.query(query)
    if not rows or len(rows) > selection["max_tuples"]:
        raise ValueError("invalid cohort size")
    seen = set()
    for row in rows:
        key = tuple(row.get(x) for x in DETAIL_FIELDS)
        if not isinstance(key[0], str) or not key[0].strip() or key in seen:
            raise ValueError("invalid or duplicate cohort tuple")
        _number(row.get("annual_checkouts"), "annual_checkouts")
        seen.add(key)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"cohort already exists: {output}")
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    meta = {"query": query, "row_count": len(rows), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "mapping_version": config["mapping_version"]}
    output.with_suffix(output.suffix + ".manifest.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta


def _read_cohort(path: Path, config: dict) -> tuple[set[tuple], str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    meta = json.loads(path.with_suffix(path.suffix + ".manifest.json").read_text(encoding="utf-8"))
    if meta["sha256"] != digest or meta["mapping_version"] != config["mapping_version"]:
        raise ValueError("cohort checksum or mapping version mismatch")
    selection = config["detail_selection"]
    expected_query = cohort_query(int(selection["selection_start"][:4]), _codes(config),
                                  selection["max_tuples"])
    if meta["query"] != expected_query or meta["row_count"] > selection["max_tuples"]:
        raise ValueError("cohort selection query changed")
    keys = set()
    aliases = _aliases(config)
    for line in data.decode("utf-8").splitlines():
        row = json.loads(line)
        key = _cohort_key(row, aliases)
        if (len(key) != 4 or not isinstance(key[0], str) or not key[0].strip()
                or key[2] not in config["book_material_type_map"] or key in keys):
            raise ValueError("invalid cohort row")
        _number(row.get("annual_checkouts"), "annual_checkouts")
        keys.add(key)
    if not keys or len(keys) != meta["row_count"]:
        raise ValueError("cohort row_count mismatch")
    return keys, digest
