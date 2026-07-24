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
    check("focused left unset when omitted (matches /captures semantics)",
          conn.execute("SELECT focused FROM applications WHERE id = %s::uuid",
                      (manual_app_1,)).fetchone()["focused"] is None)

print("manual entry: focused and location")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Focused Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "location": "Singapore, Remote", "focused": "yes",
    "after": "view"})
check("create with focused+location redirects", r.status_code == 303, r.text)
focus_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    row = conn.execute(
        """SELECT a.focused, p.location FROM applications a
           JOIN postings p ON p.id = a.applied_via_posting_id
           WHERE a.id = %s::uuid""", (focus_app,)).fetchone()
    check("focused=yes stored as True", row["focused"] is True, row)
    check("location stored on the posting", row["location"] == "Singapore, Remote", row)

print("manual entry: how you applied (external / Easy Apply)")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Easy Apply Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "external": "no", "after": "view"})
easy_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    payload = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (easy_app,)).fetchone()["payload"]
    check("external=no stored as False", payload.get("external") is False, payload)
check("detail page shows on-platform copy",
      "Easy Apply" in client.get(f"/applications/{easy_app}").text)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "External Apply Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "external": "yes", "after": "view"})
ext_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    payload = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (ext_app,)).fetchone()["payload"]
    check("external=yes stored as True", payload.get("external") is True, payload)
check("detail page shows employer-website copy",
      "employer" in client.get(f"/applications/{ext_app}").text)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Unspecified Apply Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "after": "view"})
unset_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    payload = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (unset_app,)).fetchone()["payload"]
    check("external omitted when not set", "external" not in payload, payload)

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

print("manual entry: explicit time input")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Explicit Time Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "applied_time": "14:30", "after": "view"})
check("create with explicit time redirects", r.status_code == 303, r.text)
time_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    occurred_at = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (time_app,)).fetchone()["occurred_at"]
    check("14:30 Asia/Singapore stored as the exact matching UTC instant",
          occurred_at == datetime(2026, 4, 12, 6, 30, tzinfo=timezone.utc), occurred_at)

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Bad Time Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "applied_time": "not-a-time"})
check("invalid time format rejected", r.status_code == 400, r.status_code)
check("invalid time error shown", "valid applied time" in r.text)

with db.connect() as conn, conn.transaction():
    conn.execute("UPDATE users SET timezone = NULL WHERE id = %s", (user_id,))

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

print("delete application: confirmation page + full cleanup")
with db.connect() as conn, conn.transaction():
    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'delete test co', 'Doomed Role') RETURNING id",
        (user_id,)).fetchone()
    del_job = job["id"]
    del_posting = conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'manual') RETURNING id",
        (user_id, del_job)).fetchone()["id"]
    del_posting2 = conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, captured_via) "
        "VALUES (%s, %s, 'jobstreet', 'manual') RETURNING id",
        (user_id, del_job)).fetchone()["id"]
    del_app = conn.execute(
        "INSERT INTO applications (user_id, job_id, applied_via_posting_id) "
        "VALUES (%s, %s, %s) RETURNING id",
        (user_id, del_job, del_posting)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now(), '{}')", (user_id, del_app))
    conn.execute(
        "INSERT INTO contacts (user_id, job_id, name, source) "
        "VALUES (%s, %s, 'Some Recruiter', 'manual')", (user_id, del_job))
    conn.execute(
        "INSERT INTO artifacts (user_id, application_id, kind, content) "
        "VALUES (%s, %s, 'cover_letter', 'Dear Hiring Team...')", (user_id, del_app))
    conn.execute("INSERT INTO extractions (user_id, posting_id) VALUES (%s, %s)",
                 (user_id, del_posting))
    conn.execute(
        "INSERT INTO duplicate_candidates (user_id, posting_a, posting_b, state) "
        "VALUES (%s, LEAST(%s::uuid, %s::uuid), GREATEST(%s::uuid, %s::uuid), 'pending')",
        (user_id, del_posting, del_posting2, del_posting, del_posting2))
    del_email = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, received_at,
                               triage_state, matched_application_id)
           VALUES (%s, 'gm-delete-test', 'x@y.example', 'doomed subject', now(),
                   'resolved', %s)
           RETURNING id""", (user_id, del_app)).fetchone()["id"]
    conn.execute(
        "INSERT INTO job_queue (user_id, type, payload) "
        "VALUES (%s, 'extract_jd', jsonb_build_object('posting_id', %s::text))",
        (user_id, del_posting))
    conn.execute(
        "INSERT INTO job_queue (user_id, type, payload) "
        "VALUES (%s, 'generate_cover_letter', jsonb_build_object('application_id', %s::text))",
        (user_id, del_app))

r = client.get(f"/applications/{del_app}/delete")
check("confirm page renders", r.status_code == 200, r.status_code)
check("confirm page shows posting count", "2 postings" in r.text, r.text)
check("confirm page shows event/contact/artifact counts",
      "1 timeline event" in r.text and "1 contact" in r.text
      and "1 generated artifact" in r.text, r.text)
check("confirm page warns about the linked email",
      "1 linked email" in r.text, r.text)

r = client.post(f"/applications/{del_app}/delete")
check("delete redirects to / with a banner", r.status_code == 303 and
      "deleted=" in r.headers["location"], r.text)
banner_page = client.get(r.headers["location"])
check("banner shows the company and title",
      "delete test co" in banner_page.text and "Doomed Role" in banner_page.text,
      banner_page.text)

with db.connect() as conn:
    check("job gone", conn.execute(
        "SELECT 1 FROM jobs WHERE id = %s", (del_job,)).fetchone() is None)
    check("postings gone", conn.execute(
        "SELECT count(*) AS n FROM postings WHERE job_id = %s", (del_job,)
        ).fetchone()["n"] == 0)
    check("application gone", conn.execute(
        "SELECT 1 FROM applications WHERE id = %s", (del_app,)).fetchone() is None)
    check("events gone", conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s", (del_app,)
        ).fetchone()["n"] == 0)
    check("contacts gone", conn.execute(
        "SELECT count(*) AS n FROM contacts WHERE job_id = %s", (del_job,)
        ).fetchone()["n"] == 0)
    check("artifacts gone", conn.execute(
        "SELECT count(*) AS n FROM artifacts WHERE application_id = %s", (del_app,)
        ).fetchone()["n"] == 0)
    check("extractions gone", conn.execute(
        "SELECT count(*) AS n FROM extractions WHERE posting_id = %s", (del_posting,)
        ).fetchone()["n"] == 0)
    check("duplicate_candidates gone", conn.execute(
        "SELECT count(*) AS n FROM duplicate_candidates WHERE posting_a = %s OR posting_b = %s",
        (del_posting, del_posting)).fetchone()["n"] == 0)
    check("queued jobs for the deleted posting/application gone", conn.execute(
        "SELECT count(*) AS n FROM job_queue WHERE payload->>'posting_id' = %s "
        "OR payload->>'application_id' = %s",
        (str(del_posting), str(del_app))).fetchone()["n"] == 0)
    email_row = conn.execute(
        "SELECT matched_application_id, triage_state FROM emails WHERE id = %s",
        (del_email,)).fetchone()
    check("matched email unlinked, not deleted", email_row is not None, email_row)
    check("email put back in triage",
          email_row["matched_application_id"] is None and
          email_row["triage_state"] == "pending", email_row)

print("delete application: gone means gone")
check("confirm page 404s for the now-deleted application",
      client.get(f"/applications/{del_app}/delete").status_code == 404)
check("delete route 404s for the now-deleted application",
      client.post(f"/applications/{del_app}/delete").status_code == 404)

print("\nALL WEB PATHS PASS")
