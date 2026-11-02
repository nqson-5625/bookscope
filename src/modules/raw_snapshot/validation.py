"""Offline integrity and stream-specific reconciliation of Raw snapshots."""

from __future__ import annotations

from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re

from .schema import cohort_key, parse_nonnegative, validate_source_row
from .storage import read_part_rows

def verify_snapshot(path: Path, *, _allow_writing: bool = False,
                    cohort_keys: set[tuple] | None = None) -> dict:
    """Offline integrity check; return a COMPLETE manifest only after full scan."""
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "COMPLETE" and not (_allow_writing and manifest["status"] == "WRITING"):
        raise ValueError("snapshot is not COMPLETE")
    stream_type = manifest["stream"]
    if path.name != f"snapshot={manifest['snapshot_id']}":
        raise ValueError("manifest path does not match snapshot_id")
    if manifest["scope_id"] != ("ALL_ELIGIBLE_AGG" if stream_type == "monthly_overview"
                                else "SELECTED_DETAIL" if stream_type == "book_details" else None):
        raise ValueError("invalid snapshot stream/scope")
    mapping = manifest.get("book_material_type_map", {})
    count = 0
    total = 0
    source_rows = 0
    seen_ids = set()
    seen_groups = set()
    format_totals = defaultdict(lambda: {"row_count": 0, "checkouts": 0})
    alias_count = alias_sum = 0
    part_names = set()
    for part in manifest["parts"]:
        if not isinstance(part.get("path"), str) or not re.fullmatch(r"part-[0-9]{4,}\.jsonl\.gz", part["path"]):
            raise ValueError("invalid part path")
        if part["path"] in part_names:
            raise ValueError("duplicate part path")
        part_names.add(part["path"])
        file = path / part["path"]
        if file.name != part["path"] or hashlib.sha256(file.read_bytes()).hexdigest() != part["sha256"]:
            raise ValueError(f"part checksum mismatch: {part['path']}")
        part_count = 0
        with gzip.open(file, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Raw row must be an object")
                part_count += 1
                if stream_type == "monthly_overview":
                    key = (row.get("materialtype"), row.get("usageclass"))
                    if key in seen_groups or not all(isinstance(x, str) and x for x in key):
                        raise ValueError("duplicate or invalid overview group")
                    seen_groups.add(key)
                    source_rows += parse_nonnegative(row.get("row_count"), "row_count")
                    amount = parse_nonnegative(row.get("checkouts"), "checkouts")
                    total += amount
                    if mapping:
                        if key[0] not in mapping:
                            raise ValueError("overview includes excluded material type")
                        bucket = format_totals[mapping[key[0]]]
                        bucket["row_count"] += parse_nonnegative(row["row_count"], "row_count")
                        bucket["checkouts"] += amount
                else:
                    amount = validate_source_row(row, manifest["period"])
                    total += amount
                    if (cohort_keys is not None
                            and cohort_key(row, manifest.get("cohort_material_aliases", {}))
                            not in cohort_keys):
                        raise ValueError("detail row outside selected cohort")
                    source_id = row.get(":id")
                    if not isinstance(source_id, str) or not source_id or source_id in seen_ids:
                        raise ValueError("missing or duplicate source ID in detail snapshot")
                    seen_ids.add(source_id)
                    if mapping:
                        material = row.get("materialtype")
                        if material not in mapping:
                            raise ValueError("detail includes excluded material type")
                        if material in manifest.get("cohort_material_aliases", {}):
                            alias_count += 1
                            alias_sum += amount
                        bucket = format_totals[mapping[material]]
                        bucket["row_count"] += 1
                        bucket["checkouts"] += amount
        if part_count != part["row_count"]:
            raise ValueError(f"part row_count mismatch: {part['path']}")
        count += part_count
    if count != manifest["row_count"] or total != manifest["checkouts"]:
        raise ValueError("snapshot row_count/checkouts mismatch")
    if mapping and dict(format_totals) != manifest["format_totals"]:
        raise ValueError("format totals mismatch")
    if stream_type == "book_details":
        if (manifest["selected_row_count"] != count
                or manifest["selected_checkouts"] != total
                or sum(page["stored_rows"] for page in manifest["pages"]) != count):
            raise ValueError("detail selection counters mismatch")
        if mapping and (alias_count != manifest["aliased_material_row_count"]
                        or alias_sum != manifest["aliased_material_checkouts"]):
            raise ValueError("cohort alias counters mismatch")
        if mapping:
            page_parts = [page["part"] for page in manifest["pages"] if page["part"] is not None]
            if len(page_parts) != len(set(page_parts)) or set(page_parts) != part_names:
                raise ValueError("detail page/part mismatch")
            batches = {batch["batch"]: batch for batch in manifest["batches"]}
            if len(batches) != len(manifest["batches"]):
                raise ValueError("duplicate detail batch")
            for number, batch in batches.items():
                pages = [page for page in manifest["pages"] if page["batch"] == number]
                if (sum(page["row_count"] for page in pages) != batch["source_totals"]["row_count"]
                        or sum(page["stored_rows"] for page in pages) != batch["selected_rows"]):
                    raise ValueError("detail batch/page mismatch")
            if any(page["batch"] not in batches for page in manifest["pages"]):
                raise ValueError("page without detail batch")
    elif (manifest["source_totals"]["row_count"] != manifest["source_row_count"]
          or manifest["source_totals"]["checkouts"] != total):
        raise ValueError("overview source total mismatch")
    elif mapping:
        if source_rows != manifest["source_row_count"]:
            raise ValueError("overview source row_count mismatch")
        groups = manifest["source_material_distribution"]
        mapped = [row for row in groups if row["materialtype"] in mapping]
        if mapped != [row for part in manifest["parts"]
                      for row in read_part_rows(path / part["path"])]:
            raise ValueError("overview distribution and Raw groups mismatch")
        codes = {row["materialtype"] for row in groups}
        unreviewed = sorted(codes - set(mapping) - set(manifest["reviewed_excluded_material_types"]))
        if unreviewed != manifest["unreviewed_material_types"]:
            raise ValueError("unreviewed material types mismatch")
    return manifest
