"""Bounded SODA3 extraction with source reconciliation and immutable snapshots."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from modules.project_foundation.profile import cohort_query, distribution_query

EXTRACTOR_VERSION = "source_ingestion_v3"
DETAIL_FIELDS = ("title", "creator", "materialtype", "checkouttype")


def _number(value: object, name: str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"invalid {name}: {value!r}")
    return int(str(value))


def _period(period: str) -> tuple[int, int]:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
        raise ValueError(f"invalid period: {period!r}")
    return int(period[:4]), int(period[5:])


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


def _cohort_key(row: dict, aliases: dict[str, str]) -> tuple:
    return (row.get("title"), row.get("creator"),
            aliases.get(row.get("materialtype"), row.get("materialtype")),
            row.get("checkouttype"))


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


class SodaClient:
    """POST JSON SODA3 client; retry transient failures only."""

    def __init__(self, url: str, token: str, *, attempts: int = 4,
                 timeout: int = 120, backoff: float = 1.0):
        if not url.startswith("https://") or not token:
            raise ValueError("HTTPS source URL and SOCRATA_APP_TOKEN are required")
        if attempts < 1 or timeout < 1 or backoff < 0:
            raise ValueError("invalid client retry settings")
        self.url, self.token = url, token
        self.attempts, self.timeout, self.backoff = attempts, timeout, backoff

    def query(self, query: str, *, page: int | None = None,
              page_size: int | None = None) -> list[dict]:
        body = {"query": query, "includeSystem": True, "includeSynthetic": False}
        if page is not None:
            if page < 1 or page_size is None or not 1 <= page_size <= 50000:
                raise ValueError("invalid SODA3 page")
            body["page"] = {"pageNumber": page, "pageSize": page_size}
        request = Request(self.url, data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json",
                                   "X-App-Token": self.token,
                                   "User-Agent": "BookScope-ingestion/1.0"}, method="POST")
        for attempt in range(self.attempts):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    rows = json.load(response)
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("SODA3 response must be an array of objects")
                return rows
            except HTTPError as exc:
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt + 1 == self.attempts:
                    detail = exc.read(1000).decode("utf-8", errors="replace")
                    raise RuntimeError(f"SODA3 HTTP {exc.code}: {detail}") from exc
            except (URLError, TimeoutError) as exc:
                if attempt + 1 == self.attempts:
                    raise RuntimeError(f"SODA3 unavailable: {exc}") from exc
            time.sleep(self.backoff * 2 ** attempt)
        raise AssertionError("unreachable")


def _snapshot_dir(root: Path, stream: str, period: str, snapshot_id: str) -> Path:
    year, month = _period(period)
    if stream not in ("monthly_overview", "book_details"):
        raise ValueError("unknown stream")
    return root / stream / f"year={year}" / f"month={month:02d}" / f"snapshot={snapshot_id}"


def _start_snapshot(root: Path, stream: str, period: str, config: dict,
                    config_sha256: str) -> tuple[Path, dict]:
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    path = _snapshot_dir(root, stream, period, snapshot_id)
    path.mkdir(parents=True, exist_ok=False)
    manifest = {
        "snapshot_id": snapshot_id, "status": "WRITING", "stream": stream,
        "period": period, "scope_id": "ALL_ELIGIBLE_AGG" if stream == "monthly_overview" else "SELECTED_DETAIL",
        "source_url": config["source_url"], "dataset_id": config["dataset_id"],
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "extractor_version": EXTRACTOR_VERSION, "config_sha256": config_sha256,
        "config_sources": config.get("config_sources", {}),
        "book_material_type_map": config["book_material_type_map"],
        "reviewed_excluded_material_types": config.get("reviewed_excluded_material_types", []),
        "material_review_version": config.get("material_review_version", "unversioned"),
        "cohort_material_aliases": config.get("cohort_material_aliases", {}),
        "cohort_match_version": config.get("cohort_match_version", "exact_source_tuple"),
        "mapping_version": config["mapping_version"], "queries": [], "pages": [],
        "parts": [], "row_count": 0, "checkouts": 0,
    }
    _write_manifest(path, manifest)
    return path, manifest


def _write_manifest(path: Path, manifest: dict) -> None:
    target = path / "manifest.json"
    temporary = path / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _part(path: Path, number: int, rows: list[dict]) -> dict:
    name = f"part-{number:04d}.jsonl.gz"
    target = path / name
    with target.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for row in rows:
                compressed.write((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")) + "\n").encode("utf-8"))
    return {"path": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "row_count": len(rows)}


def _finish(path: Path, manifest: dict) -> dict:
    _write_manifest(path, manifest)
    verify_snapshot(path, _allow_writing=True)
    manifest["status"] = "COMPLETE"
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_manifest(path, manifest)
    return manifest


def _failed(path: Path, manifest: dict, exc: Exception) -> None:
    manifest["status"] = "FAILED"
    manifest["error"] = f"{type(exc).__name__}: {exc}"
    _write_manifest(path, manifest)


def _validate_row(row: dict, period: str, codes: list[str] | None = None) -> int:
    year, month = _period(period)
    if (_number(row.get("checkoutyear"), "checkoutyear") != year
            or _number(row.get("checkoutmonth"), "checkoutmonth") != month
            or (codes is not None and row.get("materialtype") not in codes)):
        raise ValueError(f"row outside requested period/material scope: {period}")
    return _number(row.get("checkouts"), "checkouts")


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


def ingest_month(client: SodaClient, config: dict, period: str, root: Path,
                 config_sha256: str, *, stream: str, cohort_path: Path | None = None,
                 page_size: int = 1000, title_batch_size: int = 100,
                 overview_manifest: dict | None = None) -> dict:
    """Write a monthly snapshot; errors leave a FAILED manifest and never COMPLETE."""
    if period not in set(iter_periods(config["period_start"], config["period_end"])):
        raise ValueError("period outside configured range")
    if not 1 <= page_size <= 50000 or not 1 <= title_batch_size <= 500:
        raise ValueError("invalid batch size")
    codes = _codes(config)
    keys, cohort_sha = (None, None)
    if stream == "book_details":
        if cohort_path is None:
            raise ValueError("detail stream needs a cohort file")
        keys, cohort_sha = _read_cohort(cohort_path, config)
    path, manifest = _start_snapshot(root, stream, period, config, config_sha256)
    manifest["page_size"] = page_size
    manifest["title_batch_size"] = title_batch_size
    try:
        if stream == "monthly_overview":
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
        elif stream == "book_details":
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
        else:
            raise ValueError("unknown stream")
        return _finish(path, manifest)
    except Exception as exc:
        _failed(path, manifest, exc)
        raise


def verify_snapshot(path: Path, *, _allow_writing: bool = False,
                    cohort_keys: set[tuple] | None = None) -> dict:
    """Offline integrity check; return a COMPLETE manifest only after full scan."""
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "COMPLETE" and not (_allow_writing and manifest["status"] == "WRITING"):
        raise ValueError("snapshot is not COMPLETE")
    stream_type = manifest["stream"]
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
                    source_rows += _number(row.get("row_count"), "row_count")
                    amount = _number(row.get("checkouts"), "checkouts")
                    total += amount
                    if mapping:
                        if key[0] not in mapping:
                            raise ValueError("overview includes excluded material type")
                        bucket = format_totals[mapping[key[0]]]
                        bucket["row_count"] += _number(row["row_count"], "row_count")
                        bucket["checkouts"] += amount
                else:
                    amount = _validate_row(row, manifest["period"])
                    total += amount
                    if (cohort_keys is not None
                            and _cohort_key(row, manifest.get("cohort_material_aliases", {}))
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
                      for row in _read_part_rows(path / part["path"])]:
            raise ValueError("overview distribution and Raw groups mismatch")
        codes = {row["materialtype"] for row in groups}
        unreviewed = sorted(codes - set(mapping) - set(manifest["reviewed_excluded_material_types"]))
        if unreviewed != manifest["unreviewed_material_types"]:
            raise ValueError("unreviewed material types mismatch")
    return manifest


def _read_part_rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def logical_sha256(path: Path) -> str:
    """Order/part-independent digest of the verified Raw record multiset."""
    manifest = verify_snapshot(path)
    row_digests = []
    for part in manifest["parts"]:
        for row in _read_part_rows(path / part["path"]):
            canonical = json.dumps(row, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8")
            row_digests.append(hashlib.sha256(canonical).digest())
    digest = hashlib.sha256()
    for row_digest in sorted(row_digests):
        digest.update(row_digest)
    return digest.hexdigest()


def compare_snapshots(first: Path, second: Path) -> dict:
    """Compare two snapshots of the same period and scope for offline replay."""
    left, right = verify_snapshot(first), verify_snapshot(second)
    if (left["period"], left["scope_id"], left["stream"]) != (
            right["period"], right["scope_id"], right["stream"]):
        raise ValueError("snapshots must have the same period and scope")
    first_hash, second_hash = logical_sha256(first), logical_sha256(second)
    return {"period": left["period"], "scope_id": left["scope_id"],
            "first_snapshot_id": left["snapshot_id"],
            "second_snapshot_id": right["snapshot_id"],
            "first_logical_sha256": first_hash, "second_logical_sha256": second_hash,
            "same_content": first_hash == second_hash,
            "first_row_count": left["row_count"], "second_row_count": right["row_count"],
            "first_checkouts": left["checkouts"], "second_checkouts": right["checkouts"]}


def latest_usable_snapshot(root: Path, config: dict, stream: str, period: str,
                           cohort_path: Path | None = None) -> dict | None:
    """Find the latest verified snapshot for resuming an unchanged extraction."""
    cohort_digest = None
    if stream == "book_details" and cohort_path is not None:
        _, cohort_digest = _read_cohort(cohort_path, config)
    folder = _snapshot_dir(root, stream, period, "unused").parent
    for path in sorted(folder.glob("snapshot=*"), key=lambda item: item.name, reverse=True):
        try:
            manifest = verify_snapshot(path)
            if (manifest["period"] == period and manifest["stream"] == stream
                    and manifest["source_url"] == config["source_url"]
                    and manifest["dataset_id"] == config["dataset_id"]
                    and manifest.get("book_material_type_map") == config["book_material_type_map"]
                    and manifest["mapping_version"] == config["mapping_version"]
                    and (stream == "monthly_overview" or (
                        (cohort_digest is None or manifest["cohort_sha256"] == cohort_digest)
                        and
                        manifest.get("cohort_material_aliases", {}) == config.get("cohort_material_aliases", {})
                        and manifest.get("cohort_match_version", "exact_source_tuple")
                        == config.get("cohort_match_version", "exact_source_tuple")))):
                return manifest
        except (OSError, ValueError, KeyError, EOFError, gzip.BadGzipFile):
            continue
    return None


def audit_range(root: Path, config: dict, config_sha256: str | None = None,
                cohort_path: Path | None = None) -> dict:
    """Verify latest COMPLETE snapshots for every configured month and both streams."""
    _codes(config)
    months = {}
    issues = []
    cohort_hashes = set()
    material_summary = defaultdict(lambda: {"months": set(), "row_count": 0, "checkouts": 0,
                                            "usageclasses": set()})
    try:
        cohort_keys, cohort_digest = _read_cohort(
            cohort_path or root / "cohort_2023.jsonl", config)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        cohort_keys = None
        cohort_digest = None
        issues.append(f"cohort: {exc}")
    for period in iter_periods(config["period_start"], config["period_end"]):
        monthly = {}
        for stream in ("monthly_overview", "book_details"):
            folder = _snapshot_dir(root, stream, period, "unused").parent
            candidates = []
            for path in folder.glob("snapshot=*"):
                try:
                    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
                    if manifest.get("status") == "COMPLETE":
                        candidates.append(path)
                except (OSError, ValueError):
                    issues.append(f"{period} {stream}: unreadable manifest at {path}")
            if not candidates:
                issues.append(f"{period} {stream}: missing COMPLETE snapshot")
                continue
            latest = max(candidates, key=lambda path: path.name)
            try:
                manifest = verify_snapshot(
                    latest, cohort_keys=cohort_keys if stream == "book_details" else None)
                if manifest["period"] != period or manifest["stream"] != stream:
                    raise ValueError("manifest path/scope mismatch")
                if manifest["mapping_version"] != config["mapping_version"]:
                    raise ValueError("mapping version mismatch")
                if manifest["source_url"] != config["source_url"] or manifest["dataset_id"] != config["dataset_id"]:
                    raise ValueError("source identity mismatch")
                if manifest.get("book_material_type_map") != config["book_material_type_map"]:
                    raise ValueError("book mapping mismatch; re-extract with current config")
                if stream == "book_details" and (
                    manifest.get("cohort_material_aliases", {}) != config.get("cohort_material_aliases", {})
                    or manifest.get("cohort_match_version", "exact_source_tuple")
                    != config.get("cohort_match_version", "exact_source_tuple")
                ):
                    raise ValueError("cohort matching policy mismatch")
                monthly[stream] = manifest
            except (OSError, ValueError, KeyError, EOFError, gzip.BadGzipFile) as exc:
                issues.append(f"{period} {stream}: {exc}")
        overview = monthly.get("monthly_overview")
        detail = monthly.get("book_details")
        current_unreviewed = []
        if overview:
            for row in overview.get("source_material_distribution", []):
                bucket = material_summary[row["materialtype"]]
                bucket["months"].add(period)
                bucket["row_count"] += _number(row["row_count"], "row_count")
                bucket["checkouts"] += _number(row["checkouts"], "checkouts")
                bucket["usageclasses"].add(row["usageclass"])
            current_unreviewed = sorted(
                {row["materialtype"] for row in overview.get("source_material_distribution", [])}
                - set(config["book_material_type_map"])
                - set(config.get("reviewed_excluded_material_types", [])))
            for code in current_unreviewed:
                issues.append(f"{period}: unreviewed materialtype {code!r}")
        if detail:
            cohort_hashes.add(detail["cohort_sha256"])
            if cohort_keys is not None:
                path = _snapshot_dir(root, "book_details", period, detail["snapshot_id"])
                observed_keys = {
                    _cohort_key(row, _aliases(config))
                    for part in detail["parts"] for row in _read_part_rows(path / part["path"])
                }
                monthly["observed_cohort_tuples"] = len(observed_keys)
                monthly["missing_cohort_tuples"] = len(cohort_keys - observed_keys)
        if overview and detail:
            if detail["checkouts"] > overview["checkouts"]:
                issues.append(f"{period}: selected detail exceeds eligible overview")
            monthly["coverage_percent"] = (round(100 * detail["checkouts"] / overview["checkouts"], 4)
                                           if overview["checkouts"] else None)
            monthly["coverage_by_format_percent"] = {}
            for format_key, source in overview["format_totals"].items():
                selected = detail["format_totals"].get(format_key, {"checkouts": 0})["checkouts"]
                if selected > source["checkouts"]:
                    issues.append(f"{period}: detail exceeds overview for {format_key}")
                monthly["coverage_by_format_percent"][format_key] = (
                    round(100 * selected / source["checkouts"], 4) if source["checkouts"] else None)
        months[period] = {
            "overview_snapshot_id": overview["snapshot_id"] if overview else None,
            "detail_snapshot_id": detail["snapshot_id"] if detail else None,
            "current_mapping_review_status": (
                "REVIEWED" if overview and not current_unreviewed else "BLOCKED"),
            "coverage_percent": monthly.get("coverage_percent"),
            "coverage_by_format_percent": monthly.get("coverage_by_format_percent"),
            "observed_cohort_tuples": monthly.get("observed_cohort_tuples"),
            "missing_cohort_tuples": monthly.get("missing_cohort_tuples"),
        }
    if len(cohort_hashes) > 1:
        issues.append("detail snapshots use different cohort files")
    if cohort_hashes and cohort_hashes != {cohort_digest}:
        issues.append("detail snapshots do not match the current cohort file")
    material_review = {}
    for code, summary in sorted(material_summary.items()):
        status = ("INCLUDED" if code in config["book_material_type_map"]
                  else "EXCLUDED_REVIEWED" if code in config.get("reviewed_excluded_material_types", [])
                  else "UNREVIEWED")
        material_review[code] = {"status": status, "months": sorted(summary["months"]),
                                 "row_count": summary["row_count"],
                                 "checkouts": summary["checkouts"],
                                 "usageclasses": sorted(summary["usageclasses"])}
    return {"status": "PASS" if not issues else "BLOCKED", "periods": months,
            "audit_config_sha256": config_sha256,
            "material_review_version": config.get("material_review_version", "unversioned"),
            "cohort_tuples": len(cohort_keys) if cohort_keys is not None else None,
            "issues": issues, "cohort_sha256": next(iter(cohort_hashes)) if len(cohort_hashes) == 1 else None,
            "material_type_review": material_review}
