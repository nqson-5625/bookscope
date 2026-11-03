"""Run the repo-local dbt project with the existing BookScope .env settings."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "dbt_bookscope"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_dbt.py <dbt-command> [args...]", file=sys.stderr)
        return 2
    env = os.environ.copy()
    for key, value in dotenv_values(ROOT / ".env").items():
        if value is not None:
            env.setdefault(key, value)
    if not env.get("BOOKSCOPE_DB_PASSWORD"):
        print("BOOKSCOPE_DB_PASSWORD is required in the environment or root .env", file=sys.stderr)
        return 2
    local_dbt = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    dbt = str(local_dbt) if local_dbt.exists() else shutil.which("dbt")
    if not dbt:
        print("dbt executable not found; install requirements.txt", file=sys.stderr)
        return 2
    command = [dbt, sys.argv[1], "--project-dir", str(PROJECT),
               "--profiles-dir", str(PROJECT), *sys.argv[2:]]
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
