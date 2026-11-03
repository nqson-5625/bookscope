"""Read the selected Silver rows and live validation metrics from PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection
from psycopg.rows import dict_row


@dataclass
class SilverEvidence:
    selection: list[dict]
    gate: dict
    quality: dict
    warnings: dict
    live_validation: dict
    title_missing: dict
    bronze_stats: dict
    silver_stats: dict
    duplicates: int


def _format_stats(conn: Connection, contract_version: str) -> tuple[dict, dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT snapshot_id, format_key, count(*)::bigint AS rows,
                coalesce(sum(checkouts), 0) AS checkouts,
                coalesce(sum(source_row_count), 0) AS source_rows
            FROM silver.int_validated_rows GROUP BY 1, 2
        """)
        bronze = {(r["snapshot_id"], r["format_key"]): r for r in cur.fetchall()}
        cur.execute("""
            SELECT snapshot_id, format_key, sum(rows)::bigint AS rows,
                sum(checkouts) AS checkouts, sum(source_rows) AS source_rows
            FROM (
                SELECT snapshot_id, format_key, count(*)::bigint AS rows,
                    coalesce(sum(checkouts), 0) AS checkouts,
                    coalesce(sum(source_row_count), 0) AS source_rows
                FROM silver.overview_groups WHERE contract_version = %s
                GROUP BY 1, 2
                UNION ALL
                SELECT snapshot_id, format_key, count(*)::bigint,
                    coalesce(sum(checkouts), 0), 0::numeric
                FROM silver.detail_records WHERE contract_version = %s
                GROUP BY 1, 2
            ) AS grouped GROUP BY 1, 2
        """, (contract_version, contract_version))
        silver = {(r["snapshot_id"], r["format_key"]): r for r in cur.fetchall()}
    return bronze, silver


def _silver_duplicate_count(conn: Connection, contract_version: str) -> int:
    return conn.execute("""
        SELECT count(*) FROM (
            SELECT snapshot_id FROM silver.overview_groups
            WHERE contract_version = %s GROUP BY snapshot_id, source_row_number
            HAVING count(*) > 1
            UNION ALL
            SELECT snapshot_id FROM silver.overview_groups
            WHERE contract_version = %s
            GROUP BY snapshot_id, materialtype_raw, usageclass_raw
            HAVING count(*) > 1
            UNION ALL
            SELECT snapshot_id FROM silver.detail_records
            WHERE contract_version = %s GROUP BY snapshot_id, source_row_number
            HAVING count(*) > 1
            UNION ALL
            SELECT snapshot_id FROM silver.detail_records
            WHERE contract_version = %s GROUP BY snapshot_id, source_id
            HAVING count(*) > 1
        ) AS duplicates
    """, (contract_version,) * 4).fetchone()[0]


def load_silver_evidence(conn: Connection) -> SilverEvidence:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT stream, scope_id, month_start, snapshot_id,
            selection_status, manifest_path, manifest_sha256, mapping_version
            FROM silver.int_snapshot_selection ORDER BY month_start, stream""")
        selection = cur.fetchall()
        cur.execute("SELECT status, contract_version FROM silver.pipeline_gate")
        gate = cur.fetchone()
        cur.execute("""SELECT snapshot_id, status, bronze_rows, rejected_rows,
            warning_rows, block_reasons FROM silver.snapshot_quality
            WHERE contract_version = %s""", (gate["contract_version"],))
        quality = {r["snapshot_id"]: r for r in cur.fetchall()}
        cur.execute("""SELECT snapshot_id, warning_code, warning_rows
            FROM silver.warning_counts WHERE contract_version = %s""",
                    (gate["contract_version"],))
        warnings = {(r["snapshot_id"], r["warning_code"]): r["warning_rows"]
                    for r in cur.fetchall()}
        cur.execute("""SELECT v.snapshot_id,
            count(DISTINCT v.source_row_number) FILTER
                (WHERE cardinality(v.block_reasons) > 0)::bigint AS blocked_rows,
            array_agg(DISTINCT reason.rule_code) FILTER (WHERE reason.rule_code IS NOT NULL)
                AS reason_codes
            FROM silver.int_validated_rows AS v
            LEFT JOIN LATERAL unnest(v.block_reasons) AS reason(rule_code) ON true
            GROUP BY v.snapshot_id""")
        live_validation = {r["snapshot_id"]: r for r in cur.fetchall()}
        cur.execute("""SELECT snapshot_id,
            count(*) FILTER (WHERE 'BLANK_TITLE' = ANY(block_reasons))::bigint
                AS blank_titles
            FROM silver.int_validated_rows WHERE stream = 'book_details'
            GROUP BY snapshot_id""")
        title_missing = {r["snapshot_id"]: r["blank_titles"] for r in cur.fetchall()}
    contract_version = gate["contract_version"]
    bronze_stats, silver_stats = _format_stats(conn, contract_version)
    return SilverEvidence(selection, gate, quality, warnings, live_validation,
                          title_missing, bronze_stats, silver_stats,
                          _silver_duplicate_count(conn, contract_version))
