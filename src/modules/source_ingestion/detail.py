"""Extract selected monthly detail rows and reconcile title batches."""

from collections import defaultdict
from pathlib import Path

from modules.raw_snapshot.schema import cohort_key as _cohort_key
from modules.raw_snapshot.schema import parse_nonnegative as _number
from modules.raw_snapshot.schema import validate_source_row as _validate_row
from modules.raw_snapshot.storage import write_manifest as _write_manifest
from modules.raw_snapshot.storage import write_part as _part

from .client import SodaClient
from .selection import _aliases, _total, _total_query, _where


def _pages(client: SodaClient, query: str, page_size: int, expected: dict,
           period: str, codes: list[str], path: Path, manifest: dict,
           *, titles: set[str], keys: set[tuple], batch_number: int,
           format_map: dict[str, str], aliases: dict[str, str]) -> tuple[int, int, dict, int, int]:
    count = total_checkouts = 0
    selected_count = selected_sum = 0
    alias_count = alias_sum = 0
    format_totals = defaultdict(lambda: {"row_count": 0, "checkouts": 0})
    page = 1
    seen_ids = set()
    # Source total fixes the number of pages; an extra page is never silently accepted.
    while count < expected["row_count"]:
        rows = client.query(query, page=page, page_size=page_size)
        if not rows or len(rows) > page_size or count + len(rows) > expected["row_count"]:
            raise ValueError(f"{period}: missing/extra page {page}")
        ids = [row.get(":id") for row in rows]
        if any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{period}: missing or duplicate Socrata row ID on page {page}")
        if seen_ids.intersection(ids):
            raise ValueError(f"{period}: repeated source ID across pages")
        seen_ids.update(ids)
        for row in rows:
            total_checkouts += _validate_row(row, period, codes)
            if row.get("title") not in titles:
                raise ValueError(f"{period}: row outside title batch")
        selected = [row for row in rows if _cohort_key(row, aliases) in keys]
        part_name = None
        if selected:
            part = _part(path, len(manifest["parts"]) + 1, selected)
            manifest["parts"].append(part)
            part_name = part["path"]
        manifest["pages"].append({"batch": batch_number, "page": page, "row_count": len(rows),
                                  "first_source_id": ids[0], "last_source_id": ids[-1],
                                  "stored_rows": len(selected), "part": part_name})
        count += len(rows)
        selected_count += len(selected)
        for row in selected:
            amount = _number(row["checkouts"], "checkouts")
            selected_sum += amount
            if row["materialtype"] in aliases:
                alias_count += 1
                alias_sum += amount
            bucket = format_totals[format_map[row["materialtype"]]]
            bucket["row_count"] += 1
            bucket["checkouts"] += amount
        manifest["row_count"] += len(selected)
        manifest["checkouts"] += sum(_number(row["checkouts"], "checkouts") for row in selected)
        _write_manifest(path, manifest)
        page += 1
    if count != expected["row_count"] or total_checkouts != expected["checkouts"]:
        raise ValueError(f"{period}: page total disagrees with source aggregate")
    return selected_count, selected_sum, dict(format_totals), alias_count, alias_sum


def extract_detail(client: SodaClient, config: dict, period: str, path: Path,
                   manifest: dict, codes: list[str], keys: set[tuple],
                   cohort_sha: str, cohort_path: Path, page_size: int,
                   title_batch_size: int, overview_manifest: dict | None) -> None:
    manifest["cohort_sha256"] = cohort_sha
    manifest["cohort_path"] = str(cohort_path)
    titles = sorted({key[0] for key in keys})
    selected_count = selected_sum = 0
    alias_count = alias_sum = 0
    formats = defaultdict(lambda: {"row_count": 0, "checkouts": 0})
    manifest["batches"] = []
    for start in range(0, len(titles), title_batch_size):
        batch = titles[start:start + title_batch_size]
        where = _where(period, codes, batch)
        total_query = _total_query(where)
        expected = _total(client, total_query)
        query = f"SELECT *, :id WHERE {where} ORDER BY :id"
        manifest["queries"].extend([total_query, query])
        batch_number = start // title_batch_size + 1
        batch_selected = batch_sum = 0
        batch_alias_count = batch_alias_sum = 0
        batch_formats = {}
        if expected["row_count"]:
            # Each title batch is reconciled before retaining exact cohort
            # tuples; only one page is held in memory at a time.
            batch_selected, batch_sum, batch_formats, batch_alias_count, batch_alias_sum = _pages(
                client, query, page_size, expected, period, codes,
                path, manifest, titles=set(batch), keys=keys,
                batch_number=batch_number, format_map=config["book_material_type_map"],
                aliases=_aliases(config))
        selected_count += batch_selected
        selected_sum += batch_sum
        alias_count += batch_alias_count
        alias_sum += batch_alias_sum
        for format_key, totals in batch_formats.items():
            formats[format_key]["row_count"] += totals["row_count"]
            formats[format_key]["checkouts"] += totals["checkouts"]
        manifest["batches"].append({"batch": batch_number, "titles": len(batch),
            "source_totals": expected, "selected_rows": batch_selected,
            "selected_checkouts": batch_sum})
    manifest["selected_row_count"] = selected_count
    manifest["selected_checkouts"] = selected_sum
    manifest["aliased_material_row_count"] = alias_count
    manifest["aliased_material_checkouts"] = alias_sum
    manifest["format_totals"] = dict(formats)
    manifest["selection_rule"] = "exact cohort tuple membership after title batch extraction"
    if overview_manifest is not None:
        if (overview_manifest["period"] != period
                or overview_manifest["status"] != "COMPLETE"
                or overview_manifest["mapping_version"] != config["mapping_version"]):
            raise ValueError("detail coverage needs a matching COMPLETE overview")
        denominator = overview_manifest["checkouts"]
        if selected_sum > denominator:
            raise ValueError("detail checkouts exceed eligible overview")
        manifest["coverage_percent"] = (round(100 * selected_sum / denominator, 4)
                                         if denominator else None)
        manifest["coverage_by_format_percent"] = {}
        for format_key, source in overview_manifest["format_totals"].items():
            numerator = manifest["format_totals"].get(format_key, {}).get("checkouts", 0)
            if numerator > source["checkouts"]:
                raise ValueError(f"detail exceeds overview for {format_key}")
            manifest["coverage_by_format_percent"][format_key] = (
                round(100 * numerator / source["checkouts"], 4)
                if source["checkouts"] else None)
        manifest["overview_snapshot_id"] = overview_manifest["snapshot_id"]
