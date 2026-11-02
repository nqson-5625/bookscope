"""Inspect and replay verified BookScope Raw snapshots offline."""

import argparse
import json
from pathlib import Path
import sys

from .snapshot import compare_snapshots, latest_complete_snapshot, verify_snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="Validate a COMPLETE snapshot and its parts")
    verify.add_argument("snapshot_dir", type=Path)
    compare = commands.add_parser("compare", help="Compare two snapshots by logical content")
    compare.add_argument("first", type=Path)
    compare.add_argument("second", type=Path)
    select = commands.add_parser("select", help="Find the newest verified COMPLETE snapshot")
    select.add_argument("stream", choices=("monthly_overview", "book_details"))
    select.add_argument("period", help="YYYY-MM")
    select.add_argument("--raw-root", type=Path, default=Path("data/raw/seattle_checkouts"))
    args = parser.parse_args(argv)
    if args.command == "verify":
        manifest = verify_snapshot(args.snapshot_dir)
        result = {"snapshot_id": manifest["snapshot_id"], "status": manifest["status"],
                  "row_count": manifest["row_count"], "checkouts": manifest["checkouts"]}
    elif args.command == "compare":
        result = compare_snapshots(args.first, args.second)
    else:
        path, manifest = latest_complete_snapshot(args.raw_root, args.stream, args.period)
        result = {"path": str(path), "snapshot_id": manifest["snapshot_id"],
                  "row_count": manifest["row_count"]}
    print(json.dumps(result, ensure_ascii=False))
    return 3 if args.command == "compare" and not result["same_content"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, EOFError) as exc:
        print(f"raw_snapshot: {exc}", file=sys.stderr)
        sys.exit(1)
