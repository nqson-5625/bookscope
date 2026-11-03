"""PostgreSQL connection settings for Bronze loading."""

from __future__ import annotations

import os

import psycopg


def connect_database() -> psycopg.Connection:
    """Connect using the local Docker settings, with environment overrides."""
    password = os.environ.get("BOOKSCOPE_DB_PASSWORD")
    if not password:
        raise ValueError("BOOKSCOPE_DB_PASSWORD is required")
    return psycopg.connect(
        host=os.environ.get("BOOKSCOPE_DB_HOST", "127.0.0.1"),
        port=os.environ.get("BOOKSCOPE_DB_PORT", "5433"),
        dbname=os.environ.get("BOOKSCOPE_DB_NAME", "bookscope"),
        user=os.environ.get("BOOKSCOPE_DB_USER", "bookscope"),
        password=password,
        autocommit=True,
    )
