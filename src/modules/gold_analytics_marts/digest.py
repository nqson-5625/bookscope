"""Stable hashes for Gold code, staged output, and publication identity."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json

from psycopg import Connection, sql

from .gate import Pin, metadata
from .paths import DBT_PROJECT, MIGRATION, MODELS, ROOT


def _json(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported digest value: {type(value).__name__}")


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=_json).encode("utf-8")


def code_sha256() -> str:
    files = [MIGRATION, DBT_PROJECT / "dbt_project.yml"]
    files += list((DBT_PROJECT / "models/gold").glob("*.sql"))
    files += list((DBT_PROJECT / "models/gold").glob("*.yml"))
    files += list((DBT_PROJECT / "tests/gold").glob("*.sql"))
    files += list((DBT_PROJECT / "macros").glob("*.sql"))
    files += list((ROOT / "src/modules/gold_analytics_marts").glob("*.py"))
    digest = sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def output_sha256(conn: Connection, stage: str,
                  version: str | None = None) -> tuple[str, dict[str, int]]:
    digest = sha256(b"gold-output-v1\n")
    counts = {}
    for name in MODELS:
        digest.update(canonical({"table": name}) + b"\n")
        with conn.cursor() as cur:
            query = sql.SQL("SELECT * FROM {}.{}").format(
                sql.Identifier(stage), sql.Identifier(name))
            params = ()
            if version is not None and name == "dim_book_record":
                query += sql.SQL(" WHERE book_record_key IN (SELECT book_record_key "
                                 "FROM gold.book_monthly_features WHERE data_version = %s)")
                params = (version,)
            elif version is not None and name in {
                    "monthly_overview", "book_monthly_stats", "book_monthly_features",
                    "overview_source_lineage", "book_source_lineage"}:
                query += sql.SQL(" WHERE data_version = %s")
                params = (version,)
            query += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(
                sql.Identifier(k) for k in {
                    "dim_month": ("month_start",),
                    "dim_format": ("format_key",),
                    "dim_book_record": ("book_record_key",),
                    "monthly_overview": ("scope_id", "month_start", "format_key"),
                    "book_monthly_stats": ("book_record_key", "month_start", "format_key"),
                    "book_monthly_features": ("book_record_key", "month_start"),
                    "overview_source_lineage": ("snapshot_id", "source_row_number"),
                    "book_source_lineage": ("snapshot_id", "source_row_number"),
                }[name])
            cur.execute(query, params)
            columns = [d.name for d in cur.description]
            count = 0
            for row in cur:
                record = {key: value for key, value in zip(columns, row)
                          if key != "data_version"}
                digest.update(canonical(record) + b"\n")
                count += 1
            counts[name] = count
    return digest.hexdigest(), counts


def data_version(pin: Pin, code_hash: str, output_hash: str) -> str:
    payload = {
        "serialization": "gold-version-v1",
        "metadata": metadata(pin),
        "snapshots": [(r["stream"], r["scope_id"], r["month_start"].isoformat(),
                       r["snapshot_id"], r["manifest_sha256"]) for r in pin.rows],
        "code_sha256": code_hash,
        "output_sha256": output_hash,
    }
    return sha256(canonical(payload)).hexdigest()
