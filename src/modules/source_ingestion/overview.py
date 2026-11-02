"""Extract monthly material and usage aggregates."""

from collections import defaultdict
from pathlib import Path

from modules.project_foundation.profile import distribution_query
from modules.raw_snapshot.schema import parse_nonnegative as _number
from modules.raw_snapshot.storage import write_part as _part

from .client import SodaClient
from .selection import _total, _total_query, _where


def extract_overview(client: SodaClient, config: dict, period: str, path: Path,
                     manifest: dict, codes: list[str]) -> None:
    query = distribution_query(period)
    groups = client.query(query)
    if not groups:
        raise ValueError(f"{period}: empty material distribution")
    seen = set()
    source_codes = set()
    eligible = []
    for row in groups:
        key = (row.get("materialtype"), row.get("usageclass"))
        if (not all(isinstance(x, str) and x for x in key) or key in seen
                or _number(row.get("row_count"), "row_count") < 1):
            raise ValueError(f"{period}: invalid distribution group")
        _number(row.get("checkouts"), "checkouts")
        seen.add(key)
        source_codes.add(key[0])
        if key[0] in codes:
            eligible.append(row)
    if not eligible:
        raise ValueError(f"{period}: no eligible material groups")
    total_query = _total_query(_where(period, codes))
    total = _total(client, total_query)
    counted = sum(_number(row["row_count"], "row_count") for row in eligible)
    summed = sum(_number(row["checkouts"], "checkouts") for row in eligible)
    if (counted, summed) != (total["row_count"], total["checkouts"]):
        raise ValueError(f"{period}: overview disagrees with source total")
    manifest["queries"] = [query, total_query]
    manifest["source_totals"] = total
    manifest["source_material_types"] = sorted(source_codes)
    manifest["source_material_distribution"] = groups
    reviewed = set(config.get("reviewed_excluded_material_types", []))
    manifest["unreviewed_material_types"] = sorted(source_codes - set(codes) - reviewed)
    manifest["unreviewed_material_distribution"] = [
        row for row in groups if row["materialtype"] in manifest["unreviewed_material_types"]
    ]
    manifest["mapping_review_status"] = ("BLOCKED" if manifest["unreviewed_material_types"] else "REVIEWED")
    part = _part(path, 1, eligible)
    manifest["parts"].append(part)
    manifest["pages"].append({"page": 1, "row_count": len(eligible), "part": part["path"]})
    manifest["row_count"] = len(eligible)
    manifest["source_row_count"] = counted
    manifest["checkouts"] = summed
    manifest["aggregate_grain"] = ["materialtype", "usageclass"]
    formats = defaultdict(lambda: {"row_count": 0, "checkouts": 0})
    for row in eligible:
        bucket = formats[config["book_material_type_map"][row["materialtype"]]]
        bucket["row_count"] += _number(row["row_count"], "row_count")
        bucket["checkouts"] += _number(row["checkouts"], "checkouts")
    manifest["format_totals"] = dict(formats)
