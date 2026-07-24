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

print("manual entry: form + route-ordering guard")
r = client.get("/applications/new")
# If /applications/new were ever declared after /applications/{app_id}, this
# would 404 silently (_get_application swallows the UUID-parse error) rather
# than error loudly — this assertion is what catches that regression.
check("form renders", r.status_code == 200 and 'name="applied_date"' in r.text, r.status_code)

print("manual entry: applied only")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-04-12", "after": "another"})
check("create redirects to ?added=", r.status_code == 303 and "added=" in r.headers["location"], r.text)
manual_app_1 = r.headers["location"].split("added=")[1].split("&")[0]
with db.connect() as conn:
    row = conn.execute(
        """SELECT j.company_norm, p.captured_via, e.occurred_at, e.source
           FROM applications a JOIN jobs j ON j.id = a.job_id
           JOIN postings p ON p.id = a.applied_via_posting_id
           JOIN events e ON e.application_id = a.id AND e.type = 'applied'
           WHERE a.id = %s::uuid""", (manual_app_1,)).fetchone()
    check("company normalized", row["company_norm"] == "manual entry co", row)
    check("captured_via is manual", row["captured_via"] == "manual", row)
    check("event source is manual", row["source"] == "manual", row)
    check("applied event not midnight-collapsed",
          row["occurred_at"].time() != datetime.min.time(), row["occurred_at"])
    n_applied = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (manual_app_1,)).fetchone()["n"]
    check("exactly one applied event", n_applied == 1, n_applied)

print("manual entry: applied + outcome, including same-day ordering")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Data Platform Engineer", "platform": "linkedin",
    "applied_date": "2026-04-12", "outcome": "rejected", "outcome_date": "2026-04-12",
    "after": "view"})
check("create + outcome redirects to detail", r.status_code == 303, r.text)
manual_app_2 = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    status = conn.execute(
        "SELECT status FROM application_status WHERE application_id = %s::uuid",
        (manual_app_2,)).fetchone()["status"]
    check("status is the outcome, not applied", status == "rejected", status)
    evs = {e["type"]: e["occurred_at"] for e in conn.execute(
        "SELECT type, occurred_at FROM events WHERE application_id = %s::uuid",
        (manual_app_2,)).fetchall()}
    check("both events present", {"applied", "rejected"} <= evs.keys(), evs)
    check("same-day outcome nudged strictly after applied",
          evs["rejected"] > evs["applied"], evs)

print("manual entry: timezone anchoring")
with db.connect() as conn, conn.transaction():
    conn.execute("UPDATE users SET timezone = 'Asia/Singapore' WHERE id = %s", (user_id,))
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "SRE", "platform": "linkedin",
    "applied_date": "2026-04-12", "after": "view"})
tz_app = r.headers["location"].rsplit("/", 1)[1]
r = client.get(f"/applications/{tz_app}")
check("detail page shows the entered date in the user's timezone",
      "12 Apr 2026" in r.text, r.text[:200])
with db.connect() as conn:
    row = conn.execute(
        """SELECT (e.occurred_at AT TIME ZONE 'Asia/Singapore')::date AS local_date
           FROM events e WHERE e.application_id = %s::uuid AND e.type = 'applied'""",
        (tz_app,)).fetchone()
    check("stored UTC instant lands on 2026-04-12 in Asia/Singapore",
          str(row["local_date"]) == "2026-04-12", row)
    conn.execute("UPDATE users SET timezone = NULL WHERE id = %s", (user_id,))
    conn.commit()

print("manual entry: linkedin url derives platform_job_id, resubmit merges")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Platform Engineer", "platform": "linkedin",
    "applied_date": "2026-04-12",
    "url": "https://www.linkedin.com/jobs/view/9988776655/?refId=abc&trackingId=xyz",
    "after": "view"})
url_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    posting = conn.execute(
        "SELECT platform_job_id, url FROM postings WHERE platform_job_id = '9988776655'"
    ).fetchone()
    check("platform_job_id derived", posting["platform_job_id"] == "9988776655", posting)
    check("url canonicalized (tracking params stripped)",
          posting["url"] == "https://www.linkedin.com/jobs/view/9988776655/", posting)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Senior Platform Engineer", "platform": "linkedin",
    "applied_date": "2026-04-13",
    "url": "https://www.linkedin.com/jobs/view/9988776655/", "after": "another"})
check("resubmit reports merged", r.status_code == 303 and "merged=1" in r.headers["location"], r.text)
check("resubmit attaches to the same application",
      f"added={url_app}" in r.headers["location"], r.headers["location"])
with db.connect() as conn:
    n_applied = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (url_app,)).fetchone()["n"]
    check("still exactly one applied event after merge", n_applied == 1, n_applied)
    n_postings = conn.execute(
        "SELECT count(*) AS n FROM postings WHERE job_id = "
        "(SELECT job_id FROM applications WHERE id = %s::uuid)", (url_app,)).fetchone()["n"]
    check("still exactly one posting after merge", n_postings == 1, n_postings)

print("manual entry: validation rejects bad input and preserves values")
with db.connect() as conn:
    n_before = conn.execute("SELECT count(*) AS n FROM applications").fetchone()["n"]

r = client.post("/applications/new", data={
    "company": "", "title": "Role", "platform": "linkedin", "applied_date": "2026-04-12"})
check("blank company rejected", r.status_code == 400, r.status_code)
check("blank company error shown", "Enter the company name" in r.text)

r = client.post("/applications/new", data={
    "company": "Pte Ltd", "title": "Role", "platform": "linkedin", "applied_date": "2026-04-12"})
check("company normalizing to nothing rejected", r.status_code == 400, r.status_code)

r = client.post("/applications/new", data={
    "company": "Validation Preserve Co", "title": "", "platform": "linkedin",
    "applied_date": "2026-04-12"})
check("blank title rejected", r.status_code == 400, r.status_code)
check("submitted company value preserved on error",
      'value="Validation Preserve Co"' in r.text, r.text)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Role", "platform": "linkedin",
    "applied_date": "2099-01-01"})
check("future applied date rejected", r.status_code == 400, r.status_code)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "outcome": "rejected", "outcome_date": ""})
check("outcome without a date rejected", r.status_code == 400, r.status_code)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Role", "platform": "linkedin",
    "applied_date": "2026-05-01", "outcome": "rejected", "outcome_date": "2026-04-01"})
check("outcome dated before applied rejected", r.status_code == 400, r.status_code)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "url": "https://sg.indeed.com/viewjob?jk=abc123"})
check("url/platform mismatch rejected", r.status_code == 400, r.status_code)
check("mismatch names the detected platform", "indeed" in r.text)

with db.connect() as conn:
    n_after = conn.execute("SELECT count(*) AS n FROM applications").fetchone()["n"]
    check("no applications created by any rejected submission", n_after == n_before, (n_before, n_after))

print("manual entry: jd text enqueues extraction")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "AI Engineer", "platform": "linkedin",
    "applied_date": "2026-04-12", "jd_text": "Build LLM systems. Python required.",
    "after": "view"})
jd_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn, conn.transaction():
    posting_id = conn.execute(
        "SELECT applied_via_posting_id FROM applications WHERE id = %s::uuid",
        (jd_app,)).fetchone()["applied_via_posting_id"]
    q = conn.execute(
        "SELECT id FROM job_queue WHERE type = 'extract_jd' AND payload->>'posting_id' = %s",
        (str(posting_id),)).fetchone()
    check("extract_jd enqueued", q is not None, q)
    # Delete it so test_phase3.py's drain() — which runs the real worker over
    # the shared queue — doesn't pick up this leftover row; its embedding
    # stub raises AssertionError for any text without a stub key.
    conn.execute("DELETE FROM job_queue WHERE id = %s", (q["id"],))

print("\nALL WEB PATHS PASS")
