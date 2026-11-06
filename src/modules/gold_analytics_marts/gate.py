"""Pin DQ-approved source snapshots and reject any later source drift."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from modules.data_quality.metrics import selection_sha256
from .paths import COHORT, GOLD_CONTRACT_VERSION, IDENTITY_VERSION


@dataclass(frozen=True)
class Pin:
    build_id: UUID
    dq_run_id: UUID
    selection_sha256: str
    source_state: dict
    rows: list[dict]
    cohort_sha256: str
    silver_contract_version: str
    mapping_version: str
    dq_version: str
    dq_code_sha256: str
    dq_config_sha256: str


def _selection(conn: Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT stream, scope_id, month_start, snapshot_id,
            selection_status, manifest_path, manifest_sha256, mapping_version
            FROM silver.int_snapshot_selection ORDER BY month_start, stream""")
        return cur.fetchall()


def _verify_manifests(rows: list[dict], cohort_sha: str) -> None:
    for row in rows:
        data = Path(row["manifest_path"]).read_bytes()
        if sha256(data).hexdigest() != row["manifest_sha256"]:
            raise ValueError(f"manifest changed: {row['snapshot_id']}")
        manifest = json.loads(data)
        if (manifest.get("snapshot_id") != row["snapshot_id"]
                or manifest.get("stream") != row["stream"]
                or manifest.get("period") != row["month_start"].strftime("%Y-%m")):
            raise ValueError(f"manifest identity mismatch: {row['snapshot_id']}")
        if row["stream"] == "book_details" and manifest.get("cohort_sha256") != cohort_sha:
            raise ValueError(f"cohort hash mismatch: {row['snapshot_id']}")


def read_pin(conn: Connection) -> Pin:
    gate = conn.execute("""SELECT run_id, status, expected_period_scopes,
        matched_period_scopes, block_count, source_state_matches, contract_version
        FROM ops.dq_gate""").fetchone()
    if (gate is None or gate[1] != "PASS" or gate[2:5] != (72, 72, 0)
            or gate[5] is not True):
        raise ValueError(f"DQ gate is not FULL PASS: {gate}")
    rows = _selection(conn)
    if (len(rows) != 72 or len({(r["stream"], r["scope_id"], r["month_start"])
                                for r in rows}) != 72
            or any(r["selection_status"] != "READY" or not r["snapshot_id"] for r in rows)):
        raise ValueError("snapshot selection is incomplete")
    digest = selection_sha256(rows)
    dq = conn.execute("""SELECT selection_sha256, contract_version, mapping_version,
        dq_version, code_sha256, config_sha256, source_state, status, block_count
        FROM ops.dq_runs WHERE run_id = %s""", (gate[0],)).fetchone()
    if (dq is None or dq[0] != digest or dq[1] != gate[6]
            or dq[7] != "PASS" or dq[8] != 0
            or len({r["mapping_version"] for r in rows}) != 1
            or rows[0]["mapping_version"] != dq[2]):
        raise ValueError("DQ run and selected snapshots disagree")
    cohort_sha = sha256(COHORT.read_bytes()).hexdigest()
    _verify_manifests(rows, cohort_sha)
    return Pin(uuid4(), gate[0], digest, dq[6], rows, cohort_sha,
               dq[1], dq[2], dq[3], dq[4], dq[5])


def save_pin(conn: Connection, pin: Pin) -> None:
    with conn.transaction():
        conn.execute("""INSERT INTO ops.gold_builds
            (build_id, dq_run_id, selection_sha256, source_state, status)
            VALUES (%s,%s,%s,%s,'PINNED')""",
            (pin.build_id, pin.dq_run_id, pin.selection_sha256,
             Jsonb(pin.source_state)))
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO ops.gold_build_pins
                (build_id, stream, scope_id, month_start, snapshot_id, manifest_sha256)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                [(pin.build_id, r["stream"], r["scope_id"], r["month_start"],
                  r["snapshot_id"], r["manifest_sha256"]) for r in pin.rows])


def recheck_pin(conn: Connection, pin: Pin) -> None:
    gate = conn.execute("""SELECT run_id, status, expected_period_scopes,
        matched_period_scopes, block_count, source_state_matches
        FROM ops.dq_gate""").fetchone()
    if (gate is None or gate != (pin.dq_run_id, "PASS", 72, 72, 0, True)
            or conn.execute("SELECT ops.dq_current_source_state()").fetchone()[0]
            != pin.source_state):
        raise ValueError("DQ gate, run, or source revision changed during Gold build")
    rows = _selection(conn)
    if selection_sha256(rows) != pin.selection_sha256 or len(rows) != 72:
        raise ValueError("snapshot selection changed during Gold build")
    if [(r["snapshot_id"], r["manifest_sha256"]) for r in rows] != [
            (r["snapshot_id"], r["manifest_sha256"]) for r in pin.rows]:
        raise ValueError("snapshot IDs or manifests changed during Gold build")
    persisted = conn.execute("""SELECT stream,scope_id,month_start,snapshot_id,
        manifest_sha256 FROM ops.gold_build_pins WHERE build_id=%s
        ORDER BY month_start,stream""", (pin.build_id,)).fetchall()
    expected = [(r["stream"], r["scope_id"], r["month_start"],
                 r["snapshot_id"], r["manifest_sha256"]) for r in pin.rows]
    if persisted != expected:
        raise ValueError("persisted Gold snapshot pin is incomplete or changed")
    if sha256(COHORT.read_bytes()).hexdigest() != pin.cohort_sha256:
        raise ValueError("cohort file changed during Gold build")
    _verify_manifests(rows, pin.cohort_sha256)


def metadata(pin: Pin) -> dict:
    return {
        "selection_sha256": pin.selection_sha256,
        "cohort_sha256": pin.cohort_sha256,
        "silver_contract_version": pin.silver_contract_version,
        "mapping_version": pin.mapping_version,
        "dq_version": pin.dq_version,
        "dq_code_sha256": pin.dq_code_sha256,
        "dq_config_sha256": pin.dq_config_sha256,
        "dq_source_state": pin.source_state,
        "gold_contract_version": GOLD_CONTRACT_VERSION,
        "identity_version": IDENTITY_VERSION,
    }
