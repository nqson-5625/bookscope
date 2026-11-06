"""Independent source-to-stage reconciliation and Gold grain checks."""

from __future__ import annotations

from psycopg import Connection, sql

from .dbt import stage_token
from .gate import Pin, recheck_pin
from .paths import COHORT, MODELS, VERSIONED_MODELS


def _count(conn: Connection, stage: str, name: str) -> int:
    return conn.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(stage), sql.Identifier(name))).fetchone()[0]


def _check(conn: Connection, stage: str, name: str, template: str,
           params: tuple = ()) -> None:
    query = sql.SQL(template).format(s=sql.Identifier(stage))
    bad = conn.execute(query, params).fetchone()[0]
    if bad:
        raise ValueError(f"Gold validation {name}: {bad} bad rows")


def validate_stage(conn: Connection, pin: Pin, stage: str) -> dict[str, int]:
    recheck_pin(conn, pin)
    counts = {name: _count(conn, stage, name) for name in MODELS}
    expected = {
        "dim_month": 36,
        "dim_format": 2,
        "dim_book_record": len(COHORT.read_text(encoding="utf-8").splitlines()),
        "monthly_overview": 72,
    }
    expected["book_monthly_features"] = expected["dim_book_record"] * 36
    for name, required in expected.items():
        if counts[name] != required:
            raise ValueError(f"Gold {name}: expected {required}, found {counts[name]}")
    if min(counts["book_monthly_stats"], counts["overview_source_lineage"],
           counts["book_source_lineage"]) <= 0:
        raise ValueError("Gold fact or lineage is empty")
    for name in VERSIONED_MODELS:
        _check(conn, stage, f"{name} stage token",
               "SELECT count(*) FROM {s}." + name + " WHERE data_version IS DISTINCT FROM %s",
               (stage_token(pin),))
    _check(conn, stage, "overview lineage source", """
        WITH expected AS (
          SELECT o.snapshot_id,o.source_row_number,o.scope_id,o.month_start,
                 o.format_key,o.checkouts,o.source_row_count
          FROM silver.dq_approved_overview_groups o
          JOIN ops.gold_build_pins p ON p.snapshot_id=o.snapshot_id
            AND p.stream='monthly_overview' AND p.month_start=o.month_start
          WHERE p.build_id=%s
        ), diff AS (
          (SELECT * FROM expected EXCEPT ALL
           SELECT snapshot_id,source_row_number,scope_id,month_start,format_key,
                  checkouts,source_row_count FROM {s}.overview_source_lineage)
          UNION ALL
          (SELECT snapshot_id,source_row_number,scope_id,month_start,format_key,
                  checkouts,source_row_count FROM {s}.overview_source_lineage
           EXCEPT ALL SELECT * FROM expected)
        ) SELECT count(*) FROM diff""", (pin.build_id,))
    _check(conn, stage, "detail lineage source", """
        WITH expected AS (
          SELECT d.snapshot_id,d.source_row_number,d.scope_id,d.month_start,
                 d.format_key,
                 encode(sha256(convert_to(jsonb_build_array(
                   'cohort_tuple_v1',d.title_raw,d.creator_raw,
                   CASE WHEN d.materialtype_raw='Ebook' THEN 'EBOOK'
                        ELSE d.materialtype_raw END,d.checkouttype_raw
                 )::text,'UTF8')),'hex') AS book_record_key,
                 d.checkouts
          FROM silver.dq_approved_detail_records d
          JOIN ops.gold_build_pins p ON p.snapshot_id=d.snapshot_id
            AND p.stream='book_details' AND p.month_start=d.month_start
          WHERE p.build_id=%s
        ), diff AS (
          (SELECT * FROM expected EXCEPT ALL
           SELECT snapshot_id,source_row_number,scope_id,month_start,format_key,
                  book_record_key,checkouts FROM {s}.book_source_lineage)
          UNION ALL
          (SELECT snapshot_id,source_row_number,scope_id,month_start,format_key,
                  book_record_key,checkouts FROM {s}.book_source_lineage
           EXCEPT ALL SELECT * FROM expected)
        ) SELECT count(*) FROM diff""", (pin.build_id,))
    _check(conn, stage, "overview reconciliation", """
        SELECT count(*) FROM {s}.monthly_overview f
        LEFT JOIN LATERAL (
          SELECT sum(l.checkouts::numeric) AS checkouts,
                 sum(l.source_row_count::numeric) AS source_rows,
                 count(*) AS groups
          FROM {s}.overview_source_lineage l
          WHERE l.snapshot_id=f.source_snapshot_id AND l.month_start=f.month_start
            AND l.format_key=f.format_key AND l.scope_id=f.scope_id
        ) x ON true
        WHERE f.checkouts::numeric IS DISTINCT FROM x.checkouts
           OR f.source_row_count::numeric IS DISTINCT FROM x.source_rows
           OR f.source_group_count IS DISTINCT FROM x.groups
           OR f.scope_id <> 'ALL_ELIGIBLE_AGG'""")
    _check(conn, stage, "detail reconciliation", """
        SELECT count(*) FROM {s}.book_monthly_stats f
        LEFT JOIN LATERAL (
          SELECT sum(l.checkouts::numeric) AS checkouts, count(*) AS rows
          FROM {s}.book_source_lineage l
          WHERE l.snapshot_id=f.source_snapshot_id AND l.month_start=f.month_start
            AND l.format_key=f.format_key AND l.scope_id=f.scope_id
            AND l.book_record_key=f.book_record_key
        ) x ON true
        WHERE f.checkouts::numeric IS DISTINCT FROM x.checkouts
           OR f.source_record_count IS DISTINCT FROM x.rows
           OR f.scope_id <> 'SELECTED_DETAIL'""")
    _check(conn, stage, "feature observation", """
        SELECT count(*) FROM {s}.book_monthly_features f
        LEFT JOIN {s}.book_monthly_stats b
          ON b.book_record_key=f.book_record_key AND b.month_start=f.month_start
        WHERE f.observed IS DISTINCT FROM (b.book_record_key IS NOT NULL)
           OR f.checkouts IS DISTINCT FROM b.checkouts
           OR (b.book_record_key IS NOT NULL AND f.format_key <> b.format_key)""")
    _check(conn, stage, "coverage and format", """
        SELECT count(*) FROM {s}.monthly_overview
        WHERE cohort_coverage NOT BETWEEN 0 AND 1
           OR format_share NOT BETWEEN 0 AND 1""")
    return counts
