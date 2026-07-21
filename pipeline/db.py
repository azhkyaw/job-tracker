"""Thin database helpers around psycopg 3. No ORM — the schema is the model."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .config import DATABASE_URL


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def connect_scoped(user_id) -> psycopg.Connection:
    """Request-serving connection: runs as the RLS role with app.user_id set,
    so every data query is tenant-scoped at the database layer regardless of
    what the application-level WHERE clauses remember to do."""
    conn = connect()
    conn.execute("SET ROLE tracker_app")
    conn.execute("SELECT set_config('app.user_id', %s, false)", (str(user_id),))
    return conn


def single_user_id(conn: psycopg.Connection):
    """v1 has exactly one user; every entry point resolves it here so the
    user_id plumbing is already in place for multi-tenant later."""
    row = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError(
            "no user row — run: psql tracker -c \"INSERT INTO users (email) VALUES ('you@example.com');\""
        )
    return row["id"]


def enqueue(conn: psycopg.Connection, user_id, job_type: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO job_queue (user_id, type, payload) VALUES (%s, %s, %s)",
        (user_id, job_type, Json(payload)),
    )
