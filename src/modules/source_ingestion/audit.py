"""Policy-aware reuse and audit of completed source snapshots."""

from __future__ import annotations

from collections import defaultdict
import gzip
import json
from pathlib import Path

from modules.raw_snapshot.schema import cohort_key as _cohort_key
from modules.raw_snapshot.schema import parse_nonnegative as _number
from modules.raw_snapshot.storage import (read_part_rows as _read_part_rows,
                                          snapshot_dir as _snapshot_dir)
from modules.raw_snapshot.validation import verify_snapshot

from .selection import _aliases, _codes, _read_cohort, iter_periods

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
