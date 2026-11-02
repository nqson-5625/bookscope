"""Coordinate monthly source extraction into Raw snapshots."""

from __future__ import annotations

from pathlib import Path

from modules.raw_snapshot.storage import (complete_snapshot as _finish,
                                          fail_snapshot as _failed,
                                          start_snapshot as _start_snapshot)

from .client import SodaClient
from .detail import extract_detail
from .overview import extract_overview
from .selection import _codes, _read_cohort, iter_periods

EXTRACTOR_VERSION = "source_ingestion_v3"


def _snapshot_metadata(config: dict, config_sha256: str) -> dict:
    """Freeze source and policy settings in the Raw manifest."""
    return {
        "source_url": config["source_url"], "dataset_id": config["dataset_id"],
        "extractor_version": EXTRACTOR_VERSION, "config_sha256": config_sha256,
        "config_sources": config.get("config_sources", {}),
        "book_material_type_map": config["book_material_type_map"],
        "reviewed_excluded_material_types": config.get("reviewed_excluded_material_types", []),
        "material_review_version": config.get("material_review_version", "unversioned"),
        "cohort_material_aliases": config.get("cohort_material_aliases", {}),
        "cohort_match_version": config.get("cohort_match_version", "exact_source_tuple"),
        "mapping_version": config["mapping_version"],
    }


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
    path, manifest = _start_snapshot(root, stream, period,
                                     _snapshot_metadata(config, config_sha256))
    manifest["page_size"] = page_size
    manifest["title_batch_size"] = title_batch_size
    try:
        if stream == "monthly_overview":
            extract_overview(client, config, period, path, manifest, codes)
        elif stream == "book_details":
            extract_detail(client, config, period, path, manifest, codes, keys,
                           cohort_sha, cohort_path, page_size, title_batch_size,
                           overview_manifest)
        else:
            raise ValueError("unknown stream")
        return _finish(path, manifest)
    except Exception as exc:
        _failed(path, manifest, exc)
        raise
