"""End-to-end integration test against a live database (no external APIs).

Stubs the two LLM stage functions and drives the real queue, worker, matcher,
and event writes. Covers the paths that matter:

  1. auto-match  — rejection email matched to a seeded application, event appended
  2. create      — confirmation from an unseen company creates job/application/posting/events
  3. pending     — non-confirmation with no candidate lands in triage, writes nothing
  3c. recruiter_outreach — never auto-matches (even same-company) or auto-creates
  4. backoff     — a failing job retries with attempts+1 and a future run_after

Run:  TRACKER_DATABASE_URL=postgresql:///tracker python3 tests/test_integration.py
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psycopg.types.json import Json

from pipeline import db, email_classifier, worker
from pipeline.email_classifier import Classification, Extraction

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- LLM stubs

FAKE_CLASSIFY = {
    "northwind-rejection": Classification(True, "rejection", 0.95, "stub"),
    "acme-confirmation": Classification(True, "confirmation", 0.9, "stub"),
    "mystery-rejection": Classification(True, "rejection", 0.9, "stub"),
    "newsletter": Classification(False, None, 0.98, "stub"),
    "recruiter-pitch": Classification(True, "recruiter_outreach", 0.9, "stub"),
    "recruiter-unknown": Classification(True, "recruiter_outreach", 0.85, "stub"),
}
def _fake_extraction(**kw):
    """Mirror the real extract_email(): raw always carries the full payload."""
    d = {"company": None, "role_title": None, "platform": "unknown", "ats": None,
         "event_date": None, "status_detail": None, "recruiter": None, "notes": None, **kw}
    return Extraction(**d, raw=d)


FAKE_EXTRACT = {
    "northwind-rejection": _fake_extraction(company="Northwind Labs Inc",
                                       role_title="Senior AI Engineer",
                                       platform="linkedin"),
    "acme-confirmation": _fake_extraction(company="Acme Pte. Ltd.",
                                          role_title="AI Platform Engineer",
                                          platform="ats", ats="greenhouse",
                                          event_date="2026-07-15",
                                          recruiter={"name": "Jo Tan", "email": "jo@acme.example"}),
    "mystery-rejection": _fake_extraction(company="Totally Unknown Corp",
                                          role_title="Data Engineer"),
    # Same company as the seeded Northwind application, but a DIFFERENT role — the
    # regression case for "recruiter_outreach must never auto-match onto an
    # existing application's timeline", since by definition it's a role the
    # user did not apply to.
    "recruiter-pitch": _fake_extraction(company="Northwind Labs Inc",
                                        role_title="Staff ML Engineer",
                                        platform="linkedin",
                                        recruiter={"name": "Recruiter Ren", "email": None}),
    "recruiter-unknown": _fake_extraction(company="Totally New Agency",
                                          role_title="Backend Engineer"),
}

email_classifier.classify_email = lambda client, sender, subject, received, body: \
    FAKE_CLASSIFY[subject]
email_classifier.extract_email = lambda client, sender, subject, received, body, ctype: \
    FAKE_EXTRACT[subject]


# ---------------------------------------------------------------- helpers

def seed_email(conn, user_id, key, sender="noreply@linkedin.com"):
    row = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text, received_at)
           VALUES (%s, %s, %s, %s, 'body', %s) RETURNING id""",
        (user_id, f"gm-{key}", sender, key, NOW),
    ).fetchone()
    db.enqueue(conn, user_id, "classify_email", {"email_id": str(row["id"])})
    return row["id"]


def drain(conn):
    while worker.process_one(conn):
        pass


def email_state(conn, email_id):
    return conn.execute(
        "SELECT classification, triage_state, matched_application_id, match_score "
        "FROM emails WHERE id = %s", (email_id,)).fetchone()


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


# ---------------------------------------------------------------- test

with db.connect() as conn:
    user_id = db.single_user_id(conn)

    # Seed a known application: Northwind, Senior AI Engineer, applied 11 days ago via LinkedIn.
    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'northwind labs', 'senior ai engineer') RETURNING id", (user_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, platform_job_id, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'LI-northwind-1', 'extension')", (user_id, job["id"]))
    app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, job["id"])).fetchone()
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', %s, %s)",
        (user_id, app["id"], NOW.replace(day=10), Json({})))
    conn.commit()

    print("path 1: auto-match")
    e1 = seed_email(conn, user_id, "northwind-rejection")
    conn.commit()
    drain(conn)
    s1 = email_state(conn, e1)
    check("classified as rejection", s1["classification"] == "rejection", s1)
    check("auto_matched", s1["triage_state"] == "auto_matched", s1)
    check("matched to seeded application", str(s1["matched_application_id"]) == str(app["id"]), s1)
    check("score above threshold", s1["match_score"] and s1["match_score"] >= 0.75, s1)
    ev = conn.execute(
        "SELECT type, source_email_id FROM events WHERE application_id = %s "
        "AND type = 'rejected'", (app["id"],)).fetchone()
    check("rejected event appended with provenance", ev and str(ev["source_email_id"]) == str(e1), ev)
    st = conn.execute("SELECT status FROM application_status WHERE application_id = %s",
                      (app["id"],)).fetchone()
    check("derived status is rejected", st["status"] == "rejected", st)

    print("path 2: create (backfill)")
    e2 = seed_email(conn, user_id, "acme-confirmation", sender="no-reply@greenhouse.io")
    conn.commit()
    drain(conn)
    s2 = email_state(conn, e2)
    check("auto_matched via create", s2["triage_state"] == "auto_matched", s2)
    new_app = conn.execute(
        """SELECT a.id, a.applied_via_posting_id, j.company_norm, p.captured_via, p.jd_text
           FROM applications a JOIN jobs j ON j.id = a.job_id
           LEFT JOIN postings p ON p.id = a.applied_via_posting_id
           WHERE a.id = %s""", (s2["matched_application_id"],)).fetchone()
    check("job created with normalized company", new_app["company_norm"] == "acme", new_app)
    check("posting is email_only needing enrichment",
          new_app["captured_via"] == "email_only" and new_app["jd_text"] is None, new_app)
    evs = {r["type"] for r in conn.execute(
        "SELECT type FROM events WHERE application_id = %s", (new_app["id"],)).fetchall()}
    check("applied + confirmation events written", {"applied", "confirmation"} <= evs, evs)
    ev_date = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s AND type = 'applied'",
        (new_app["id"],)).fetchone()
    check("stated event_date used, not received_at", ev_date["occurred_at"].day == 15, ev_date)
    contact = conn.execute(
        "SELECT name FROM contacts WHERE user_id = %s AND name = 'Jo Tan'", (user_id,)).fetchone()
    check("recruiter contact captured", contact is not None)

    print("path 3: pending triage")
    e3 = seed_email(conn, user_id, "mystery-rejection", sender="hr@unknowncorp.example")
    conn.commit()
    drain(conn)
    s3 = email_state(conn, e3)
    check("lands in pending triage", s3["triage_state"] == "pending", s3)
    check("no application guessed", s3["matched_application_id"] is None, s3)

    print("path 3b: non-job mail ignored")
    e4 = seed_email(conn, user_id, "newsletter")
    conn.commit()
    drain(conn)
    s4 = email_state(conn, e4)
    check("marked not_job_related and ignored",
          s4["classification"] == "not_job_related" and s4["triage_state"] == "ignored", s4)

    print("path 3c: recruiter outreach never auto-matches or auto-creates")
    northwind_events_before = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s", (app["id"],)).fetchone()["n"]
    e5 = seed_email(conn, user_id, "recruiter-pitch")
    conn.commit()
    drain(conn)
    s5 = email_state(conn, e5)
    check("recruiter pitch lands pending, not auto-matched",
          s5["triage_state"] == "pending" and s5["matched_application_id"] is None, s5)
    check("no score recorded (never a silent guess)", s5["match_score"] is None, s5)
    northwind_events_after = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s", (app["id"],)).fetchone()["n"]
    check("Northwind application's timeline untouched despite company match",
          northwind_events_after == northwind_events_before, (northwind_events_before, northwind_events_after))

    e6 = seed_email(conn, user_id, "recruiter-unknown")
    conn.commit()
    drain(conn)
    s6 = email_state(conn, e6)
    check("recruiter pitch at unknown company also lands pending, no create",
          s6["triage_state"] == "pending" and s6["matched_application_id"] is None, s6)
    ghost = conn.execute(
        "SELECT 1 FROM jobs WHERE user_id = %s AND company_norm = 'totally new agency'",
        (user_id,)).fetchone()
    check("no job fabricated for the unknown recruiter pitch", ghost is None)

    print("path 4: failure backoff")
    db.enqueue(conn, user_id, "classify_email",
               {"email_id": "00000000-0000-0000-0000-000000000000"})
    conn.commit()
    worker.process_one(conn)
    jq = conn.execute(
        "SELECT state, attempts, run_after > now() AS deferred, last_error FROM job_queue "
        "ORDER BY id DESC LIMIT 1").fetchone()
    check("failed job requeued with backoff",
          jq["state"] == "pending" and jq["attempts"] == 1 and jq["deferred"], jq)
    check("error recorded", "not found" in (jq["last_error"] or ""), jq["last_error"])

print("\nALL PATHS PASS")
