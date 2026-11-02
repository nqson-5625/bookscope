"""CLI for Seattle monthly overview and selected-detail extraction."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from .ingest import (SodaClient, audit_range, ingest_cohort, ingest_month,
                     iter_periods, latest_usable_snapshot)


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/project_foundation.json"))
    parser.add_argument("--ingestion-config", type=Path,
                        default=Path("configs/source_ingestion.json"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/seattle_checkouts"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("cohort", help="Fetch and freeze the 2023 selected cohort")
    sub.add_parser("audit", help="Verify all 36 configured months offline")
    run = sub.add_parser("run", help="Extract one or more months")
    run.add_argument("--start", help="First month, YYYY-MM")
    run.add_argument("--end", help="Last month, YYYY-MM")
    run.add_argument("--stream", choices=("overview", "detail", "both"), default="both")
    run.add_argument("--page-size", type=int)
    run.add_argument("--title-batch-size", type=int)
    run.add_argument("--resume", action="store_true",
                     help="Reuse verified snapshots with the same config; retry missing/failed months")
    args = parser.parse_args(argv)
    config_bytes = args.config.read_bytes()
    config = json.loads(config_bytes)
    ingestion_bytes = args.ingestion_config.read_bytes()
    ingestion_config = json.loads(ingestion_bytes)
    allowed = {"material_review_version", "reviewed_excluded_material_types", "cohort_material_aliases",
               "cohort_match_version", "page_size", "title_batch_size"}
    if not isinstance(ingestion_config, dict) or set(ingestion_config) - allowed:
        raise ValueError("ingestion config may only set review codes and batch sizes")
    if (type(ingestion_config.get("page_size")) is not int
            or type(ingestion_config.get("title_batch_size")) is not int):
        raise ValueError("ingestion batch sizes must be integers")
    config.update(ingestion_config)
    config["config_sources"] = {
        "foundation_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "ingestion_sha256": hashlib.sha256(ingestion_bytes).hexdigest(),
    }
    config_sha = hashlib.sha256(config_bytes + b"\n" + ingestion_bytes).hexdigest()
    cohort_path = args.raw_root / "cohort_2023.jsonl"
    if args.command == "audit":
        result = audit_range(args.raw_root, config, config_sha)
        output = args.raw_root / "ingestion_audit.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "issues": len(result["issues"]),
                          "report": str(output)}, ensure_ascii=False))
        return 0 if result["status"] == "PASS" else 2
    client = SodaClient(config["source_url"], os.environ.get("SOCRATA_APP_TOKEN", ""))
    if args.command == "cohort":
        print(json.dumps(ingest_cohort(client, config, cohort_path), ensure_ascii=False))
        return 0
    start = args.start or config["period_start"]
    end = args.end or config["period_end"]
    periods = list(iter_periods(start, end))
    if periods[0] < config["period_start"] or periods[-1] > config["period_end"]:
        raise ValueError("requested range outside configured range")
    if args.stream in ("detail", "both") and not cohort_path.exists():
        ingest_cohort(client, config, cohort_path)
    for period in periods:
        overview = None
        for stream in (("monthly_overview", "book_details") if args.stream == "both"
                       else ("monthly_overview",) if args.stream == "overview"
                       else ("book_details",)):
            existing = (latest_usable_snapshot(args.raw_root, config, stream, period,
                                               cohort_path=cohort_path)
                        if args.resume else None)
            if existing is not None:
                if stream == "monthly_overview":
                    overview = existing
                print(json.dumps({"period": period, "stream": stream,
                                  "snapshot_id": existing["snapshot_id"], "status": "REUSED"},
                                 ensure_ascii=False), flush=True)
                continue
            print(f"Extracting {period} {stream}", file=sys.stderr, flush=True)
            result = ingest_month(client, config, period, args.raw_root, config_sha,
                                  stream=stream, cohort_path=cohort_path,
                                  page_size=(args.page_size if args.page_size is not None
                                             else config["page_size"]),
                                  title_batch_size=(args.title_batch_size if args.title_batch_size is not None
                                                    else config["title_batch_size"]),
                                  overview_manifest=overview)
            if stream == "monthly_overview":
                overview = result
            print(json.dumps({"period": period, "stream": stream,
                              "snapshot_id": result["snapshot_id"],
                              "row_count": result["row_count"],
                              "mapping_review_status": result.get("mapping_review_status"),
                              "coverage_percent": result.get("coverage_percent")},
                             ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"source_ingestion: {exc}", file=sys.stderr)
        sys.exit(1)
