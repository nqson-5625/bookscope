"""Raw snapshot directory, manifest and compressed-part lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .schema import parse_period

def snapshot_dir(root: Path, stream: str, period: str, snapshot_id: str) -> Path:
    year, month = parse_period(period)
    if stream not in ("monthly_overview", "book_details"):
        raise ValueError("unknown stream")
    return root / stream / f"year={year}" / f"month={month:02d}" / f"snapshot={snapshot_id}"


def start_snapshot(root: Path, stream: str, period: str,
                   metadata: dict) -> tuple[Path, dict]:
    """Create a WRITING snapshot without interpreting extraction metadata."""
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    manifest = {
        "snapshot_id": snapshot_id, "status": "WRITING", "stream": stream,
        "period": period, "scope_id": "ALL_ELIGIBLE_AGG" if stream == "monthly_overview" else "SELECTED_DETAIL",
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "queries": [], "pages": [], "parts": [], "row_count": 0, "checkouts": 0,
    }
    if not isinstance(metadata, dict) or set(metadata) & set(manifest):
        raise ValueError("metadata cannot override snapshot identity or lifecycle fields")
    manifest.update(metadata)
    path = snapshot_dir(root, stream, period, snapshot_id)
    path.mkdir(parents=True, exist_ok=False)
    write_manifest(path, manifest)
    return path, manifest


def write_manifest(path: Path, manifest: dict) -> None:
    target = path / "manifest.json"
    if target.exists() and json.loads(target.read_text(encoding="utf-8"))["status"] == "COMPLETE":
        raise ValueError("COMPLETE snapshot is immutable")
    temporary = path / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def write_part(path: Path, number: int, rows: list[dict]) -> dict:
    if json.loads((path / "manifest.json").read_text(encoding="utf-8"))["status"] != "WRITING":
        raise ValueError("parts can only be written to a WRITING snapshot")
    name = f"part-{number:04d}.jsonl.gz"
    target = path / name
    with target.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for row in rows:
                compressed.write((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")) + "\n").encode("utf-8"))
    return {"path": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "row_count": len(rows)}


def complete_snapshot(path: Path, manifest: dict) -> dict:
    if manifest["status"] != "WRITING":
        raise ValueError("only a WRITING snapshot can be completed")
    write_manifest(path, manifest)
    from .validation import verify_snapshot
    verify_snapshot(path, _allow_writing=True)
    manifest["status"] = "COMPLETE"
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        write_manifest(path, manifest)
    except Exception:
        manifest["status"] = "WRITING"
        del manifest["completed_at_utc"]
        raise
    return manifest


def fail_snapshot(path: Path, manifest: dict, exc: Exception) -> None:
    if manifest["status"] != "WRITING":
        raise ValueError("only a WRITING snapshot can fail")
    manifest["status"] = "FAILED"
    manifest["error"] = f"{type(exc).__name__}: {exc}"
    write_manifest(path, manifest)


def read_part_rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)
