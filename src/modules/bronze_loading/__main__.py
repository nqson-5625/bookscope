"""CLI for loading verified Raw snapshots into PostgreSQL Bronze."""

import argparse
import json
from pathlib import Path
import sys

from dotenv import load_dotenv
import psycopg

from modules.raw_snapshot import latest_complete_snapshot
from modules.source_ingestion.selection import iter_periods

from . import connect_database, load_snapshot


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--run-id", help="Existing ops.pipeline_runs run_id")
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("load", help="Load one verified snapshot directory")
    one.add_argument("snapshot_dir", type=Path)
    many = commands.add_parser("range", help="Load latest COMPLETE snapshots by month")
    many.add_argument("--raw-root", type=Path, default=Path("data/raw/seattle_checkouts"))
    many.add_argument("--config", type=Path, default=Path("configs/project_foundation.json"))
    many.add_argument("--start", help="First month, YYYY-MM; defaults to config")
    many.add_argument("--end", help="Last month, YYYY-MM; defaults to config")
    many.add_argument("--stream", choices=("overview", "detail", "both"), default="both")
    args = parser.parse_args(argv)
    if args.chunk_size < 1:
        parser.error("--chunk-size must be positive")
    if args.command == "load":
        paths = [args.snapshot_dir]
    else:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        start, end = args.start or config["period_start"], args.end or config["period_end"]
        if start < config["period_start"] or end > config["period_end"]:
            raise ValueError("requested range outside configured range")
        streams = (("monthly_overview", "book_details") if args.stream == "both"
                   else ("monthly_overview",) if args.stream == "overview"
                   else ("book_details",))
        paths = [latest_complete_snapshot(args.raw_root, stream, period)[0]
                 for period in iter_periods(start, end) for stream in streams]
    with connect_database() as conn:
        for path in paths:
            result = load_snapshot(conn, path, run_id=args.run_id,
                                   chunk_size=args.chunk_size)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError, psycopg.Error) as exc:
        print(f"bronze_loading: {exc}", file=sys.stderr)
        sys.exit(1)
