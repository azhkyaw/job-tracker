"""Web UI test against a live database seeded by tests/test_integration.py.

Run AFTER the integration test on the same database:
  TRACKER_DATABASE_URL=postgresql:///tracker_test python3 tests/test_web.py

Covers: applications table + funnel render, detail page, focused toggle,
manual event logging, triage listing, and all three resolve actions
(link appends an event with provenance; create builds a full record; ignore).
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from psycopg.types.json import Json

from pipeline import db
from pipeline.web import app

client = TestClient(app, follow_redirects=False)

# Phase 4: pages require a session — bootstrap one for the seeded user.
def _bootstrap_session():
    from pipeline import auth as _auth
    with db.connect() as conn, conn.transaction():
        u = conn.execute(
            "SELECT id, password_hash FROM users ORDER BY created_at LIMIT 1").fetchone()
        if not u["password_hash"]:
            conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                         (_auth.hash_password("testpass123"), u["id"]))
        sid = _auth.create_session(conn, u["id"])
    client.cookies.set("session", sid)


_bootstrap_session()



def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


with db.connect() as conn:
    user_id = db.single_user_id(conn)
    northwind_app = conn.execute(
        "SELECT a.id FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE j.company_norm = 'northwind labs'").fetchone()["id"]
    # A pending status_update email to resolve by hand-linking.
    linkme = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text,
                               received_at, classification, extraction, triage_state)
           VALUES (%s, 'gm-linkme', 'noreply@linkedin.com', 'Your application was viewed',
                   'body', %s, 'status_update',
                   %s, 'pending')
           ON CONFLICT (user_id, gmail_message_id)
             DO UPDATE SET triage_state = 'pending', matched_application_id = NULL
           RETURNING id""",
        (user_id, datetime(2026, 7, 20, tzinfo=timezone.utc),
         Json({"company": "Northwind Labs Inc", "role_title": None,
               "platform": "linkedin", "ats": None, "event_date": None,
               "status_detail": "viewed", "recruiter": None, "notes": None})),
    ).fetchone()["id"]
    conn.commit()

print("pages render")
r = client.get("/")
check("applications table renders", r.status_code == 200 and "northwind labs" in r.text, r.status_code)
check("funnel strip present", 'class="funnel"' in r.text)
check("triage count pill shows", 'class="pill"' in r.text)

r = client.get(f"/applications/{northwind_app}")
check("detail renders timeline", r.status_code == 200 and "rejected" in r.text, r.status_code)
check("404 on bad id", client.get("/applications/not-a-uuid").status_code == 404)

print("detail actions")
r = client.post(f"/applications/{northwind_app}/focused", data={"focused": "yes"})
check("focused toggle redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    check("focused persisted", conn.execute(
        "SELECT focused FROM applications WHERE id = %s", (northwind_app,)).fetchone()["focused"] is True)
r = client.post(f"/applications/{northwind_app}/events",
                data={"type": "follow_up_sent", "note": "pinged recruiter"})
check("manual event redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    ev = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s AND type = 'follow_up_sent' "
        "AND source = 'manual'", (northwind_app,)).fetchone()
    check("manual follow-up logged with note", ev and ev["payload"].get("note") == "pinged recruiter", ev)

print("triage: link")
r = client.get("/triage")
check("pending email listed", "Your application was viewed" in r.text)
r = client.post(f"/triage/{linkme}", data={"action": "link", "application_id": str(northwind_app)})
check("link redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    row = conn.execute("SELECT triage_state, matched_application_id FROM emails WHERE id = %s",
                       (linkme,)).fetchone()
    check("email resolved to application",
          row["triage_state"] == "resolved" and str(row["matched_application_id"]) == str(northwind_app), row)
    ev = conn.execute(
        "SELECT id FROM events WHERE application_id = %s AND type = 'viewed' "
        "AND source_email_id = %s", (northwind_app, linkme)).fetchone()
    check("viewed event appended with provenance", ev is not None)

print("triage: create + ignore")
with db.connect() as conn:
    mystery = conn.execute(
        "SELECT id FROM emails WHERE subject = 'mystery-rejection' AND triage_state = 'pending'"
    ).fetchone()["id"]
r = client.post(f"/triage/{mystery}", data={"action": "create"})
check("create redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    row = conn.execute("SELECT matched_application_id FROM emails WHERE id = %s",
                       (mystery,)).fetchone()
    check("application created from triage", row["matched_application_id"] is not None)
    evs = {e["type"] for e in conn.execute(
        "SELECT type FROM events WHERE application_id = %s",
        (row["matched_application_id"],)).fetchall()}
    check("applied + rejected recorded on created app", {"applied", "rejected"} <= evs, evs)
    ign = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, received_at, triage_state)
           VALUES (%s, 'gm-ignoreme', 'x@y.example', 'spamish', now(), 'pending')
           ON CONFLICT (user_id, gmail_message_id)
             DO UPDATE SET triage_state = 'pending'
           RETURNING id""", (user_id,)).fetchone()["id"]
    conn.commit()
r = client.post(f"/triage/{ign}", data={"action": "ignore"})
with db.connect() as conn:
    check("ignore works", conn.execute(
        "SELECT triage_state FROM emails WHERE id = %s", (ign,)).fetchone()["triage_state"] == "ignored")
check("resolved emails leave the queue",
      "Your application was viewed" not in client.get("/triage").text)

print("\nALL WEB PATHS PASS")
