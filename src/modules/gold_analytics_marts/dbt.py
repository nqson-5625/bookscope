"""Build Gold models in an isolated dbt schema without touching published data."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

from psycopg import Connection, sql

from .gate import Pin
from .paths import DBT_PROJECT, ROOT


def stage_schema(pin: Pin) -> str:
    return f"gold_stage_{pin.build_id.hex}"


def stage_token(pin: Pin) -> str:
    return sha256(pin.build_id.bytes).hexdigest()


def artifact_dir(pin: Pin) -> Path:
    """Keep dbt results for this build separate from every other invocation."""
    return DBT_PROJECT / "target_gold" / pin.build_id.hex


def create_stage(conn: Connection, pin: Pin) -> str:
    name = stage_schema(pin)
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    return name


def drop_stage(conn: Connection, pin: Pin) -> None:
    name = stage_schema(pin)
    if not name.startswith("gold_stage_") or len(name) != 43:
        raise ValueError("invalid generated stage schema")
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def run_dbt(pin: Pin) -> dict:
    dbt_exe = Path(sys.executable).with_name("dbt.exe")
    if not dbt_exe.is_file():
        raise FileNotFoundError(f"dbt executable unavailable: {dbt_exe}")
    target = artifact_dir(pin)
    target.mkdir(parents=True, exist_ok=False)
    vars_ = {
        "gold_build_id": str(pin.build_id),
        "gold_stage_token": stage_token(pin),
        "gold_stage_schema": stage_schema(pin),
        "gold_cohort_sha256": pin.cohort_sha256,
    }
    cmd = [str(dbt_exe), "build", "--project-dir", str(DBT_PROJECT),
           "--profiles-dir", str(DBT_PROJECT), "--target-path", str(target),
           "--log-path", str(target / "logs"),
           "--select", "path:models/gold",
           "tag:gold", "--vars", json.dumps(vars_, separators=(",", ":"))]
    started = __import__("time").monotonic()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(cmd, cwd=ROOT, env=env,
                               text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, check=False)
    report = {
        "returncode": completed.returncode,
        "elapsed_seconds": round(__import__("time").monotonic() - started, 3),
        "artifact_dir": str(target),
        "output": completed.stdout,
    }
    if completed.returncode:
        raise RuntimeError(f"dbt Gold build failed:\n{completed.stdout[-8000:]}")
    return report
