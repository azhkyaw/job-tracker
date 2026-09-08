"""Thin database helpers around psycopg 3. No ORM — the schema is the model."""

from __future__ import annotations

import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .config import DATABASE_URL, QUEUE_STALL_SECONDS


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


# --------------------------------------------------------------------------- queue health
#
# The queue's read side lives next to its write side. Nothing in the UI used
# to look at job_queue except the cover-letter panel, so a worker that could
# not reach the API stalled for four days (3-7 Sep 2026) with 46 emails —
# one of them a rejection — stored and never read, while every page rendered
# as if nothing were wrong. `queue_health` is what base.html's header warning,
# the Settings page and `cli status` all read; one definition so the three
# cannot disagree about whether the pipeline is stuck.
#
# All three run on whatever connection they are given: scoped (one user's
# rows, via RLS) from a request, admin (every user) from the CLI.

_QUEUE_HEALTH_SQL = """
WITH oldest AS (
    SELECT min(created_at) AS since FROM job_queue
    WHERE state <> 'done' AND type IN ('classify_email', 'extract_email'))
SELECT (SELECT count(*) FROM emails WHERE processed_at IS NULL)     AS waiting,
       oldest.since                                                 AS waiting_since,
       -- age on the DATABASE clock: the dev DB is remote (Neon), and comparing
       -- its created_at against this machine's clock would make the stall
       -- threshold a function of clock skew.
       EXTRACT(EPOCH FROM now() - oldest.since)                     AS waiting_seconds,
       (SELECT count(*) FROM job_queue WHERE state = 'dead')         AS dead,
       (SELECT last_error FROM job_queue
         WHERE state <> 'done' AND last_error IS NOT NULL
         ORDER BY run_after DESC, id DESC LIMIT 1)                    AS last_error
FROM oldest
"""


_SDK_MESSAGE_RE = re.compile(r"""'message': (['"])(.*?)\1[,}]""")


def _last_line(text: str | None) -> str | None:
    """last_error is a full traceback for a job's own failure and a single
    line for an outage (worker._outage); either way the final line is the
    sentence meant for a human. When that line is the SDK's own repr —
    `anthropic.BadRequestError: Error code: 400 - {'type': 'error', 'error':
    {'message': '…'}, 'request_id': …}` — pull the API's sentence out of it."""
    lines = (text or "").strip().splitlines()
    if not lines:
        return None
    m = _SDK_MESSAGE_RE.search(lines[-1])
    return m.group(2) if m else lines[-1]


def queue_health(conn: psycopg.Connection) -> dict:
    """`waiting` is emails not yet fully processed (processed_at is set only
    once classify + extract + match have all run), `waiting_since` when the
    oldest of their jobs was queued, `dead` the dead-letter count and
    `reason` the newest recorded failure. `stalled` is the one bit the header
    acts on: work older than QUEUE_STALL_SECONDS, or anything dead. A job in
    ordinary retry backoff is neither, so a single transient failure that
    heals itself never shows."""
    row = conn.execute(_QUEUE_HEALTH_SQL).fetchone()
    age = row["waiting_seconds"]
    stalled = row["dead"] > 0 or (age is not None and age > QUEUE_STALL_SECONDS)
    return {"waiting": row["waiting"], "waiting_since": row["waiting_since"],
            "dead": row["dead"], "reason": _last_line(row["last_error"]), "stalled": stalled}


def dead_jobs(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, type, attempts, created_at, last_error FROM job_queue "
        "WHERE state = 'dead' ORDER BY created_at").fetchall()
    return [{**r, "reason": _last_line(r["last_error"])} for r in rows]


def requeue_dead(conn: psycopg.Connection) -> int:
    """Every dead job back to pending with a fresh attempt budget — a human
    saying "try again" after fixing what broke. Returns how many."""
    return conn.execute(
        "UPDATE job_queue SET state = 'pending', attempts = 0, run_after = now() "
        "WHERE state = 'dead'").rowcount
