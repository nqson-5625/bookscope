"""Atomically copy tested staging tables into immutable Gold publications."""

from __future__ import annotations

from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .digest import output_sha256
from .gate import Pin, metadata, recheck_pin
from .paths import MODELS, VERSIONED_MODELS


PUBLICATION_FIELDS = (
    "selection_sha256", "cohort_sha256", "silver_contract_version",
    "mapping_version", "dq_version", "dq_code_sha256", "dq_config_sha256",
    "dq_source_state", "gold_contract_version", "identity_version",
    "code_sha256", "output_sha256",
)


def mark_tested(conn: Connection, pin: Pin, code_hash: str, output_hash: str,
                version: str, dbt_results: dict, validation_results: dict) -> None:
    with conn.transaction():
        conn.execute("""UPDATE ops.gold_builds SET status='TESTED',
            data_version=%s, code_sha256=%s, output_sha256=%s,
            dbt_results=%s, validation_results=%s
            WHERE build_id=%s AND status='PINNED'""",
            (version, code_hash, output_hash, Jsonb(dbt_results),
             Jsonb(validation_results), pin.build_id))


def mark_failed(conn: Connection, pin: Pin, error: str) -> None:
    with conn.transaction():
        conn.execute("""UPDATE ops.gold_builds SET status='FAILED',
            finished_at=now(), error_text=%s WHERE build_id=%s
            AND status IN ('PINNED','TESTED')""", (error[:4000], pin.build_id))


def _columns(conn: Connection, stage: str, name: str) -> list[str]:
    return [r[0] for r in conn.execute("""SELECT column_name
        FROM information_schema.columns WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position""", (stage, name)).fetchall()]


def _copy_dimension(conn: Connection, stage: str, name: str) -> None:
    columns = _columns(conn, stage, name)
    if not columns:
        raise ValueError(f"staging dimension missing: {name}")
    names = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    conn.execute(sql.SQL("INSERT INTO gold.{} ({}) SELECT {} FROM {}.{} "
                         "ON CONFLICT DO NOTHING").format(
        sql.Identifier(name), names, names, sql.Identifier(stage),
        sql.Identifier(name)))
    mismatched = conn.execute(sql.SQL("SELECT count(*) FROM (SELECT {} FROM {}.{} "
        "EXCEPT SELECT {} FROM gold.{}) AS diff").format(
        names, sql.Identifier(stage), sql.Identifier(name), names,
        sql.Identifier(name))).fetchone()[0]
    if mismatched:
        raise ValueError(f"existing dimension {name} disagrees with staging")


def _copy_versioned(conn: Connection, stage: str, name: str, version: str) -> None:
    columns = [c for c in _columns(conn, stage, name) if c != "data_version"]
    if not columns:
        raise ValueError(f"staging model missing: {name}")
    names = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    conn.execute(sql.SQL("INSERT INTO gold.{} (data_version, {}) "
                         "SELECT %s, {} FROM {}.{}").format(
        sql.Identifier(name), names, names, sql.Identifier(stage),
        sql.Identifier(name)), (version,))


def _published_matches(conn: Connection, pin: Pin, stage: str, version: str,
                       code_hash: str, output_hash: str) -> bool:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM ops.data_publications WHERE data_version=%s",
                    (version,))
        old = cur.fetchone()
    if old is None:
        return False
    expected = metadata(pin) | {"code_sha256": code_hash,
                                "output_sha256": output_hash}
    if old["status"] != "PUBLISHED" or any(old[k] != expected[k]
                                           for k in PUBLICATION_FIELDS):
        raise ValueError("data_version collision: provenance differs")
    selected = conn.execute("""SELECT stream,scope_id,month_start,snapshot_id,
        manifest_sha256 FROM ops.publication_snapshots WHERE data_version=%s
        ORDER BY month_start,stream""", (version,)).fetchall()
    wanted = [(r["stream"], r["scope_id"], r["month_start"],
               r["snapshot_id"], r["manifest_sha256"]) for r in pin.rows]
    if selected != wanted:
        raise ValueError("data_version collision: pinned snapshots differ")
    actual_hash, _ = output_sha256(conn, "gold", version)
    if actual_hash != output_hash:
        raise ValueError("published Gold rows differ from recorded digest")
    return True


def publish(conn: Connection, pin: Pin, stage: str, version: str,
            code_hash: str, output_hash: str, *, failpoint: str | None = None) -> str:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(87261104)")
        conn.execute("LOCK TABLE ops.dq_runs IN SHARE MODE")
        conn.execute("SELECT source_table FROM ops.dq_source_revisions FOR SHARE").fetchall()
        recheck_pin(conn, pin)
        if _published_matches(conn, pin, stage, version, code_hash, output_hash):
            conn.execute("""UPDATE ops.gold_builds SET status='REUSED',
                finished_at=now() WHERE build_id=%s""", (pin.build_id,))
            return "REUSED"
        meta = metadata(pin)
        conn.execute("""INSERT INTO ops.data_publications (
            data_version,status,dq_run_id,selection_sha256,cohort_sha256,
            silver_contract_version,mapping_version,dq_version,dq_code_sha256,
            dq_config_sha256,dq_source_state,gold_contract_version,
            identity_version,code_sha256,output_sha256)
            VALUES (%s,'STAGED',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (version, pin.dq_run_id, meta["selection_sha256"],
             meta["cohort_sha256"], meta["silver_contract_version"],
             meta["mapping_version"], meta["dq_version"],
             meta["dq_code_sha256"], meta["dq_config_sha256"],
             Jsonb(meta["dq_source_state"]), meta["gold_contract_version"],
             meta["identity_version"], code_hash, output_hash))
        conn.execute("""INSERT INTO ops.publication_snapshots
            (data_version,stream,scope_id,month_start,snapshot_id,manifest_sha256)
            SELECT %s,stream,scope_id,month_start,snapshot_id,manifest_sha256
            FROM ops.gold_build_pins WHERE build_id=%s""", (version, pin.build_id))
        for name in MODELS[:3]:
            _copy_dimension(conn, stage, name)
        for name in VERSIONED_MODELS:
            _copy_versioned(conn, stage, name, version)
        physical_hash, _ = output_sha256(conn, "gold", version)
        if physical_hash != output_hash:
            raise ValueError("published Gold digest differs from tested staging")
        if failpoint == "after_storage":
            raise RuntimeError("injected failure after Gold storage")
        conn.execute("""UPDATE ops.data_publications SET status='PUBLISHED',
            published_at=now() WHERE data_version=%s AND status='STAGED'""", (version,))
        if failpoint == "before_pointer":
            raise RuntimeError("injected failure before pointer promotion")
        conn.execute("""INSERT INTO ops.current_publication (id,data_version)
            VALUES (1,%s) ON CONFLICT (id) DO UPDATE SET
            data_version=excluded.data_version, switched_at=now()""", (version,))
        conn.execute("""UPDATE ops.gold_builds SET status='PUBLISHED',
            finished_at=now() WHERE build_id=%s""", (pin.build_id,))
    return "PUBLISHED"
