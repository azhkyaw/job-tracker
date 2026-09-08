"""Queue worker (design doc §6.4).

Claim pattern: the job row is locked with FOR UPDATE SKIP LOCKED inside one
transaction that also does the processing. A crash rolls the whole thing back
and the job stays 'pending' — no stale-'running' requeue logic needed. The
trade-off (a DB transaction held open across an LLM call, a few seconds) is
fine at single-user volume; revisit at multi-user.

Handler failures are isolated in a savepoint so partial writes roll back,
then the job is retried with exponential backoff up to MAX_ATTEMPTS, after
which it dead-letters (listed on Settings, with a requeue button).

That policy assumes a failure is ABOUT THE JOB. One that is about the
environment — the account out of credit, a revoked key, the network, the API
down — is an OUTAGE (`_outage`): the job is released untouched, no attempt is
charged, and `run()` pauses for OUTAGE_PAUSE_SECONDS instead of retrying the
whole queue into dead letters. `work --once` exits non-zero on one, the same
way `sync` does when a mailbox fails, so a cron run cannot fail quietly.
"""

from __future__ import annotations

import functools
import json
import sys
import time
import traceback

import anthropic
from psycopg.types.json import Json

from . import config, covers, db, dedup, email_classifier, embeddings, jd_extraction, matcher

_CLAIM_SQL = """
SELECT * FROM job_queue
WHERE state = 'pending' AND run_after <= now()
ORDER BY id
LIMIT 1
FOR UPDATE SKIP LOCKED
"""


@functools.cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _payload(job: dict) -> dict:
    p = job["payload"]
    return p if isinstance(p, dict) else json.loads(p)


def _get_email(conn, email_id: str) -> dict:
    row = conn.execute("SELECT * FROM emails WHERE id = %s", (email_id,)).fetchone()
    if row is None:
        raise ValueError(f"emails row {email_id} not found")
    return row


# --------------------------------------------------------------------------- handlers

def handle_classify_email(conn, job: dict) -> None:
    email = _get_email(conn, _payload(job)["email_id"])
    c = email_classifier.classify_email(
        _client(), email["sender"], email["subject"] or "",
        email["received_at"], email["body_text"] or "",
    )
    if c.job_related:
        conn.execute(
            """
            UPDATE emails SET classification = %s, classify_confidence = %s,
                              model = %s, prompt_version = %s
            WHERE id = %s
            """,
            (c.type, c.confidence, c.model, c.prompt_version, email["id"]),
        )
        db.enqueue(conn, job["user_id"], "extract_email", {"email_id": str(email["id"])})
    else:
        # INGEST_ALL removes the pre-filter, which was also the only thing
        # keeping non-job mail out of the DB entirely. Once the classifier has
        # ruled a message out, its body has no downstream use — nothing
        # re-reads body_text on a not_job_related row — so drop it rather than
        # retain personal mail indefinitely (design doc §14 q4). The row stays:
        # gmail_message_id is what makes the ingest idempotent, and keeping it
        # is what stops the message being re-fetched and re-classified forever.
        conn.execute(
            """
            UPDATE emails SET classification = 'not_job_related',
                              classify_confidence = %s, triage_state = 'ignored',
                              model = %s, prompt_version = %s, processed_at = now(),
                              body_text = CASE WHEN %s THEN '' ELSE body_text END
            WHERE id = %s
            """,
            (c.confidence, c.model, c.prompt_version, config.INGEST_ALL, email["id"]),
        )


def handle_extract_email(conn, job: dict) -> None:
    email = _get_email(conn, _payload(job)["email_id"])
    if not email["classification"] or email["classification"] == "not_job_related":
        raise ValueError(f"email {email['id']} has no job-related classification")
    x = email_classifier.extract_email(
        _client(), email["sender"], email["subject"] or "",
        email["received_at"], email["body_text"] or "", email["classification"],
    )
    conn.execute(
        "UPDATE emails SET extraction = %s WHERE id = %s",
        (Json(x.raw), email["id"]),
    )
    matcher.dispatch(conn, job["user_id"], email, email["classification"], x)


def _get_posting(conn, posting_id: str) -> dict:
    row = conn.execute("SELECT * FROM postings WHERE id = %s", (posting_id,)).fetchone()
    if row is None:
        raise ValueError(f"postings row {posting_id} not found")
    return row


def handle_extract_jd(conn, job: dict) -> None:
    posting = _get_posting(conn, _payload(job)["posting_id"])
    if not posting["jd_text"]:
        raise ValueError(f"posting {posting['id']} has no jd_text")
    x = jd_extraction.extract(_client(), posting["jd_text"], posting["title"])
    conn.execute(
        """
        INSERT INTO extractions (user_id, posting_id, languages, technologies,
                                 seniority, salary_min, salary_max, currency,
                                 work_mode, visa_signal, visa_notes,
                                 model, prompt_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (job["user_id"], posting["id"], x.languages, x.technologies, x.seniority,
         x.salary_min, x.salary_max, x.currency, x.work_mode, x.visa_signal,
         x.visa_notes, x.model, x.prompt_version))
    if embeddings.available() and posting["jd_embedding"] is None:
        db.enqueue(conn, job["user_id"], "embed_jd", {"posting_id": str(posting["id"])})


def handle_embed_jd(conn, job: dict) -> None:
    posting = _get_posting(conn, _payload(job)["posting_id"])
    if not posting["jd_text"]:
        raise ValueError(f"posting {posting['id']} has no jd_text")
    vec = embeddings.embed([posting["jd_text"]])[0]
    conn.execute("UPDATE postings SET jd_embedding = %s::vector WHERE id = %s",
                 (embeddings.to_pgvector(vec), posting["id"]))
    db.enqueue(conn, job["user_id"], "dedup_scan", {"posting_id": str(posting["id"])})


def handle_dedup_scan(conn, job: dict) -> None:
    dedup.scan_posting(conn, job["user_id"], _payload(job)["posting_id"])


def handle_generate_cover_letter(conn, job: dict) -> None:
    app_id = _payload(job)["application_id"]
    row = conn.execute(
        """
        SELECT a.id, j.company_norm, j.title_canonical,
               (SELECT p.jd_text FROM postings p
                 WHERE p.job_id = a.job_id AND p.jd_text IS NOT NULL
                 ORDER BY p.captured_at DESC LIMIT 1) AS jd_text,
               (SELECT p.company_raw FROM postings p
                 WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                 ORDER BY p.captured_at DESC LIMIT 1) AS company_raw
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = %s::uuid
        """, (app_id,)).fetchone()
    if row is None:
        raise ValueError(f"application {app_id} not found")
    prof_row = conn.execute("SELECT resume_profile FROM users WHERE id = %s",
                            (job["user_id"],)).fetchone()
    profile = covers.load_profile(prof_row and prof_row["resume_profile"])
    text = covers.generate(_client(), profile,
                           row["company_raw"] or row["company_norm"],
                           row["title_canonical"], row["jd_text"])
    conn.execute(
        "INSERT INTO artifacts (user_id, application_id, kind, content, model) "
        "VALUES (%s, %s, 'cover_letter', %s, %s)",
        (job["user_id"], row["id"], text, config.COVER_MODEL))


HANDLERS = {
    "classify_email": handle_classify_email,
    "extract_email": handle_extract_email,
    "extract_jd": handle_extract_jd,
    "embed_jd": handle_embed_jd,
    "dedup_scan": handle_dedup_scan,
    "generate_cover_letter": handle_generate_cover_letter,
}


# --------------------------------------------------------------------------- outages

class Outage(Exception):
    """Raised by process_one() AFTER it has released a job whose failure was
    about the environment rather than the job. str(exc) is the one-line
    reason, already stored in that job's last_error."""


def _api_message(exc: anthropic.APIStatusError) -> str:
    """The API's own sentence, without the SDK's `Error code: 400 - {...}`
    wrapper — this is what the Settings page and the header warning print."""
    body = exc.body if isinstance(exc.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    return err.get("message") or exc.message or str(exc)


def _outage(exc: BaseException) -> str | None:
    """A one-line reason when `exc` is about the ENVIRONMENT, None when it is
    about the job. The split follows the SDK's own classes: no response at all
    (network), or a status the API attributes to the account or itself — 401,
    403 (permission and billing_error both land here), 429, 5xx/529.

    The one case the classes cannot express: an exhausted credit balance is
    reported as a 400 `invalid_request_error`, identical in status and type to
    a malformed request, so it is told apart by its message. Measured on the
    real failure (3-7 Sep 2026, request ids req_011CepNv…): the text was
    "Your credit balance is too low to access the Anthropic API. Please go to
    Plans & Billing to upgrade or purchase credits." If Anthropic rewords it,
    this rule misses, the jobs dead-letter after MAX_ATTEMPTS, and Settings
    shows them with that new text — visible, and one requeue from recovered,
    which is the whole point of the dead-letter list."""
    if isinstance(exc, anthropic.APIConnectionError):
        return f"cannot reach the Anthropic API ({exc})"
    if isinstance(exc, anthropic.APIStatusError):
        status, msg = exc.status_code, _api_message(exc)
        if status in (401, 403, 429) or status >= 500:
            return f"Anthropic API {status}: {msg}"
        if status == 400 and "credit balance" in msg.lower():
            return f"Anthropic API {status}: {msg}"
    return None


# --------------------------------------------------------------------------- loop

def process_one(conn) -> bool:
    """Claim and process a single job. Returns False when the queue is idle.
    Raises Outage — after committing — when the job failed for a reason that
    is not the job's; the job is left exactly as claimed, so the next claim
    takes it again (oldest first, which keeps ingest order-sensitive work
    like confirmation-before-rejection intact — never defer it)."""
    outage = None
    with conn.transaction():
        job = conn.execute(_CLAIM_SQL).fetchone()
        if job is None:
            return False
        handler = HANDLERS.get(job["type"])
        try:
            if handler is None:
                raise ValueError(f"no Phase 1 handler for job type '{job['type']}'")
            with conn.transaction():        # savepoint: partial writes roll back
                handler(conn, job)
        except Exception as exc:
            outage = _outage(exc)
            if outage:
                # Not charged: attempts, state and run_after stay as they were.
                # The reason alone is stored — an outage has no stack worth
                # reading, and the UI prints last_error's final line.
                conn.execute(
                    "UPDATE job_queue SET last_error = %s WHERE id = %s",
                    (outage, job["id"]),
                )
            else:
                attempts = job["attempts"] + 1
                state = "dead" if attempts >= config.MAX_ATTEMPTS else "pending"
                backoff = config.BACKOFF_BASE_SECONDS * (2 ** job["attempts"])
                conn.execute(
                    """
                    UPDATE job_queue
                    SET state = %s, attempts = %s, last_error = %s,
                        run_after = now() + make_interval(secs => %s)
                    WHERE id = %s
                    """,
                    (state, attempts, traceback.format_exc(limit=8), backoff, job["id"]),
                )
        else:
            conn.execute(
                "UPDATE job_queue SET state = 'done', attempts = attempts + 1 WHERE id = %s",
                (job["id"],),
            )
    if outage:
        raise Outage(outage)
    return True


def run(poll_seconds: float = 2.0, once: bool = False) -> None:
    with db.connect() as conn:
        paused: str | None = None
        while True:
            try:
                worked = process_one(conn)
            except Outage as out:
                reason = str(out)
                if reason != paused:            # one line per outage, not per retry
                    print(f"worker paused — {reason}", file=sys.stderr)
                    paused = reason
                if once:
                    raise SystemExit(1)
                time.sleep(config.OUTAGE_PAUSE_SECONDS)
                continue
            if paused and worked:
                print("worker resumed", file=sys.stderr)
                paused = None
            if once and not worked:
                return
            if not worked:
                time.sleep(poll_seconds)
