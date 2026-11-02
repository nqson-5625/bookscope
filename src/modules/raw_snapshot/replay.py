"""Verified offline reader, snapshot selection and content comparison."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .storage import read_part_rows, snapshot_dir
from .validation import verify_snapshot

def logical_sha256(path: Path) -> str:
    """Order/part-independent digest of the verified Raw record multiset."""
    manifest = verify_snapshot(path)
    row_digests = []
    for part in manifest["parts"]:
        for row in read_part_rows(path / part["path"]):
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


@dataclass(frozen=True)
class RawRecord:
    """One verified Raw row and its stable position within a snapshot."""

    snapshot_id: str
    source_row_number: int
    raw_path: Path
    source_payload: dict


def iter_rows(path: Path, *, cohort_keys: set[tuple] | None = None):
    """Verify once, then stream rows in manifest part order without network access."""
    manifest = verify_snapshot(path, cohort_keys=cohort_keys)
    number = 0
    for part in manifest["parts"]:
        part_path = path / part["path"]
        for row in read_part_rows(part_path):
            number += 1
            yield RawRecord(manifest["snapshot_id"], number, part_path, row)


def latest_complete_snapshot(root: Path, stream: str, period: str) -> tuple[Path, dict]:
    """Choose the newest COMPLETE snapshot; a corrupt newest snapshot is an error."""
    folder = snapshot_dir(root, stream, period, "unused").parent
    for path in sorted(folder.glob("snapshot=*"), key=lambda item: item.name, reverse=True):
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"unreadable snapshot manifest: {path}") from exc
        if not isinstance(manifest, dict) or manifest.get("status") not in (
                "WRITING", "FAILED", "COMPLETE"):
            raise ValueError(f"invalid snapshot status: {path}")
        if manifest["status"] == "COMPLETE":
            verified = verify_snapshot(path)
            if verified["stream"] != stream or verified["period"] != period:
                raise ValueError("manifest stream/period does not match snapshot directory")
            return path, verified
    raise FileNotFoundError(f"no COMPLETE snapshot for {stream} {period}")
