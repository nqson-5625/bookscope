"""CLI: sample live SODA3 data or profile an existing JSONL file."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from .profile import (cohort_query, distribution_query, duplicate_tuple_query,
                      eligible_year_total_query, fetch_soda3, month_query,
                      profile_rows, read_jsonl,
                      validate_distribution, validate_sample)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/project_foundation.json"))
    parser.add_argument("--input", type=Path, help="Offline JSONL sample; skips API")
    parser.add_argument("--output", type=Path, default=Path("data/profiles/foundation_profile.json"))
    parser.add_argument("--sample-output", type=Path,
                        default=Path("data/profiles/foundation_sample.jsonl"),
                        help="Where to save rows fetched from the live API")
    parser.add_argument("--cohort-output", type=Path,
                        default=Path("data/profiles/foundation_cohort_2023.jsonl"),
                        help="Where to save the selected 2023 cohort probe")
    args = parser.parse_args()
    config_bytes = args.config.read_bytes()
    config = json.loads(config_bytes.decode("utf-8"))
    if args.input:
        rows = read_jsonl(args.input)
        report = profile_rows(rows, source=str(args.input))
        report["sample_file_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    else:
        token = os.environ.get("SOCRATA_APP_TOKEN", "")
        if not token:
            parser.error("SOCRATA_APP_TOKEN is required for live profiling")
        rows, queries, distributions, distribution_totals = [], [], {}, {}
        sample_strata, grain_probe = {}, {}
        material_types = list(config["book_material_type_map"])
        for period in config["profile_months"]:
            print(f"Profiling {period}: material distribution", file=sys.stderr, flush=True)
            aggregate_query = distribution_query(period)
            distributions[period] = fetch_soda3(config["source_url"], token, aggregate_query)
            distribution_totals[period] = validate_distribution(distributions[period], period)
            queries.append(aggregate_query)
            print(f"Profiling {period}: general sample", file=sys.stderr, flush=True)
            query = month_query(period, config["profile_rows_per_month"])
            general_rows = fetch_soda3(config["source_url"], token, query)
            validate_sample(general_rows, period, config["profile_rows_per_month"])
            if not general_rows:
                raise ValueError(f"{period}: empty general sample")
            rows.extend(general_rows)
            sample_strata[f"{period}:ALL"] = len(general_rows)
            queries.append(query)
            for material_type in material_types:
                source_count = sum(int(item["row_count"]) for item in distributions[period]
                                   if item["materialtype"] == material_type)
                if source_count == 0:
                    sample_strata[f"{period}:{material_type}"] = 0
                    continue
                print(f"Profiling {period}: {material_type} sample", file=sys.stderr, flush=True)
                query = month_query(period, config["profile_rows_per_material_type"],
                                    material_type)
                material_rows = fetch_soda3(config["source_url"], token, query)
                validate_sample(material_rows, period,
                                config["profile_rows_per_material_type"], material_type)
                rows.extend(material_rows)
                sample_strata[f"{period}:{material_type}"] = len(material_rows)
                queries.append(query)
            distribution_codes = {item["materialtype"] for item in distributions[period]}
            for material_type in material_types:
                source_count = sum(int(item["row_count"]) for item in distributions[period]
                                   if item["materialtype"] == material_type)
                sample_count = sample_strata[f"{period}:{material_type}"]
                if (material_type in distribution_codes) != (sample_count > 0):
                    raise ValueError(f"{period}: sample and distribution disagree for {material_type}")
                if sample_count > source_count:
                    raise ValueError(f"{period}: sample exceeds source count for {material_type}")
            print(f"Profiling {period}: duplicate tuple probe", file=sys.stderr, flush=True)
            grain_query = duplicate_tuple_query(period, material_types)
            grain_probe[period] = fetch_soda3(config["source_url"], token, grain_query)
            for item in grain_probe[period]:
                if (not isinstance(item.get("source_rows"), str)
                        or not item["source_rows"].isdigit()
                        or int(item["source_rows"]) < 2
                        or item.get("materialtype") not in material_types):
                    raise ValueError(f"{period}: invalid grain probe response")
            queries.append(grain_query)
        report = profile_rows(rows, source=config["source_url"], queries=queries)
        observed_codes = {item["materialtype"] for month in distributions.values()
                          for item in month}
        missing_codes = set(material_types) - observed_codes
        if missing_codes:
            raise ValueError(f"book mapping codes absent from live distributions: {sorted(missing_codes)}")
        report["monthly_material_distribution"] = distributions
        report["monthly_distribution_totals"] = distribution_totals
        report["sample_strata"] = sample_strata
        report["duplicate_tuple_examples"] = grain_probe
        report["material_mapping_check"] = {
            "book_material_type_map": config["book_material_type_map"],
            "observed_codes": sorted(observed_codes),
            "status": config["book_mapping_status"],
        }
        cohort_config = config["detail_selection"]
        cohort_year = int(cohort_config["selection_start"][:4])
        if cohort_config["selection_end"] != f"{cohort_year}-12":
            raise ValueError("cohort selection must cover a complete calendar year")
        print(f"Profiling {cohort_year}: cohort feasibility", file=sys.stderr, flush=True)
        selection_query = cohort_query(cohort_year, material_types,
                                       cohort_config["max_tuples"])
        cohort_rows = fetch_soda3(config["source_url"], token, selection_query, timeout=180)
        source_query = eligible_year_total_query(cohort_year, material_types)
        source_totals = fetch_soda3(config["source_url"], token, source_query, timeout=180)
        if len(source_totals) != 1 or not cohort_rows:
            raise ValueError("invalid cohort feasibility response")
        selected_sum = 0
        seen_tuples = set()
        for row in cohort_rows:
            key = tuple(row.get(field) for field in cohort_config["group_by"])
            score = str(row.get("annual_checkouts", ""))
            if key in seen_tuples or not score.isascii() or not score.isdecimal():
                raise ValueError("invalid or duplicate cohort tuple")
            seen_tuples.add(key)
            selected_sum += int(score)
        total_sum = str(source_totals[0].get("checkouts", ""))
        total_rows = str(source_totals[0].get("row_count", ""))
        if (not total_sum.isascii() or not total_sum.isdecimal()
                or not total_rows.isascii() or not total_rows.isdecimal()
                or int(total_sum) == 0 or int(total_sum) < selected_sum):
            raise ValueError("invalid cohort source total")
        args.cohort_output.parent.mkdir(parents=True, exist_ok=True)
        with args.cohort_output.open("w", encoding="utf-8", newline="\n") as stream:
            for row in cohort_rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        report["cohort_probe"] = {
            "query": selection_query,
            "source_total_query": source_query,
            "selected_tuples": len(cohort_rows),
            "selected_annual_checkouts": selected_sum,
            "eligible_annual_rows": int(total_rows),
            "eligible_annual_checkouts": int(total_sum),
            "coverage_percent_2023": round(100 * selected_sum / int(total_sum), 4),
            "minimum_selected_checkouts": int(cohort_rows[-1]["annual_checkouts"]),
            "cohort_path": str(args.cohort_output),
            "cohort_file_sha256": hashlib.sha256(args.cohort_output.read_bytes()).hexdigest(),
        }
        args.sample_output.parent.mkdir(parents=True, exist_ok=True)
        with args.sample_output.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        report["sample_path"] = str(args.sample_output)
        report["sample_file_sha256"] = hashlib.sha256(args.sample_output.read_bytes()).hexdigest()
    report["config"] = {
        "path": str(args.config),
        "sha256": hashlib.sha256(config_bytes).hexdigest(),
        "mapping_version": config["mapping_version"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({report['sample_size']} records)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"project_foundation: {exc}", file=sys.stderr)
        sys.exit(1)
