"""Stream verified Raw rows into their Bronze load batch."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from modules.raw_snapshot import iter_rows


INSERT_RECORD = (
    "INSERT INTO bronze.checkout_records "
    "(snapshot_id, source_row_number, load_batch_id, raw_path, source_payload) "
    "VALUES (%s, %s, %s, %s, %s)"
)


def insert_records(conn: psycopg.Connection, path: Path, snapshot_id: str,
                   batch_id: int, chunk_size: int) -> int:
    count = 0
    pending = []
    with conn.cursor() as cursor:
        for record in iter_rows(path):
            if record.snapshot_id != snapshot_id or record.source_row_number != count + 1:
                raise ValueError("Raw row sequence changed during load")
            count += 1
            pending.append((snapshot_id, count, batch_id,
                            str(record.raw_path.resolve()), Jsonb(record.source_payload)))
            if len(pending) >= chunk_size:
                cursor.executemany(INSERT_RECORD, pending)
                pending.clear()
        if pending:
            cursor.executemany(INSERT_RECORD, pending)
    return count
