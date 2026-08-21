"""Web UI test against a live database seeded by tests/test_integration.py.

Run AFTER the integration test on the same database:
  TRACKER_DATABASE_URL=postgresql:///tracker_test python3 tests/test_web.py

Covers: applications table + funnel render, detail page,
manual event logging, triage listing, and all resolve actions (link appends
an event with provenance; create builds a full record; ignore; lead files a
recruiter_outreach email as an inbound application with origin='inbound' and
no fabricated applied event), plus the triage actionable/inbound lane split
and the applications-list origin filter.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from psycopg.types.json import Json

from pipeline import analytics, db, web
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
check("triage count pill shows",
      'class="pill"' in r.text.split('href="/triage"')[1].split("</a>")[0])
check("default theme renders data-theme=\"auto\"", 'data-theme="auto"' in r.text)
# Regression guard for the dark-mode retheme: every color-mix() tint must
# blend into var(--mix), not a literal white that would never adapt.
# The 'white' in 'white-space:nowrap' must not false-positive this check.
check("no hardcoded white color-mix tint remains",
      ",white)" not in r.text.replace("white-space", ""), r.text[:200])
check("password inputs are styled (not left to browser default black-on-dark)",
      "input[type=password]" in r.text, r.text[:200])
check("textarea has its own themed rule",
      "textarea{" in r.text or "textarea {" in r.text, r.text[:200])

r = client.get(f"/applications/{northwind_app}")
check("detail renders timeline", r.status_code == 200 and "rejected" in r.text, r.status_code)
check("404 on bad id", client.get("/applications/not-a-uuid").status_code == 404)

print("detail actions")
r = client.post(f"/applications/{northwind_app}/events",
                data={"type": "follow_up_sent", "note": "pinged recruiter"})
check("manual event redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    ev = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s AND type = 'follow_up_sent' "
        "AND source = 'manual'", (northwind_app,)).fetchone()
    check("manual follow-up logged with note", ev and ev["payload"].get("note") == "pinged recruiter", ev)
    manual_event_id = conn.execute(
        "SELECT id FROM events WHERE application_id = %s AND type = 'follow_up_sent' "
        "AND source = 'manual'", (northwind_app,)).fetchone()["id"]

print("timeline events: CRUD")
r = client.get(f"/applications/{northwind_app}")
check("manual event shows an edit link on the detail page",
      f"/events/{manual_event_id}/edit" in r.text, r.text)

r = client.get(f"/applications/{northwind_app}/events/{manual_event_id}/edit")
check("edit form renders and prefills the note", r.status_code == 200
      and 'value="pinged recruiter"' in r.text, r.text)

r = client.post(f"/applications/{northwind_app}/events/{manual_event_id}/edit",
                data={"type": "rejected", "reason": "salary", "channel": "phone",
                      "occurred_on": "2026-07-25", "note": "recruiter called"})
check("event edit redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    ev = conn.execute(
        "SELECT type, occurred_at, payload FROM events WHERE id = %s", (manual_event_id,)).fetchone()
    check("edit changed type, date and payload",
          ev["type"] == "rejected" and ev["payload"].get("reason") == "salary"
          and ev["payload"].get("channel") == "phone" and ev["payload"].get("note") == "recruiter called",
          ev)

r = client.post(f"/applications/{northwind_app}/events/{manual_event_id}/edit",
                data={"type": "not-a-real-type", "occurred_on": "2026-07-25"})
check("editing to an unsupported type is rejected", r.status_code == 400, r.status_code)

r = client.post(f"/applications/{northwind_app}/events/{manual_event_id}/edit",
                data={"type": "rejected", "occurred_on": ""})
check("blank date on edit is rejected rather than silently kept", r.status_code == 400, r.status_code)

r = client.get("/applications/{}/events/{}/edit".format(
    northwind_app, "00000000-0000-0000-0000-000000000000"))
check("unknown event 404s", r.status_code == 404, r.status_code)

with db.connect() as conn:
    applied_event_id = conn.execute(
        "SELECT id FROM events WHERE application_id = %s AND type = 'applied'",
        (northwind_app,)).fetchone()["id"]
r = client.get(f"/applications/{northwind_app}/events/{applied_event_id}/edit")
check("the 'applied' event is not editable through this route (it has its own correction path)",
      r.status_code == 404, r.status_code)

with db.connect() as conn, conn.transaction():
    other_app = conn.execute(
        "SELECT a.id, a.user_id FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE j.company_norm != 'northwind labs' LIMIT 1").fetchone()
    foreign_event = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'note', 'manual', now(), '{}') RETURNING id",
        (other_app["user_id"], other_app["id"])).fetchone()["id"]
r = client.get(f"/applications/{northwind_app}/events/{foreign_event}/edit")
check("an event belonging to a different application 404s, even under a valid application id",
      r.status_code == 404, r.status_code)

r = client.post(f"/applications/{northwind_app}/events/{manual_event_id}/delete")
check("event delete redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    gone = conn.execute("SELECT 1 FROM events WHERE id = %s", (manual_event_id,)).fetchone()
    check("deleted event is gone", gone is None, gone)

print("engaged event type")
with db.connect() as conn, conn.transaction():
    vd_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'vector dynamics', 'ml engineer') RETURNING id", (user_id,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, platform_job_id, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'LI-vector-1', 'extension')", (user_id, vd_job))
    engaged_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, vd_job)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', '2026-07-01 09:00+00', '{}')",
        (user_id, engaged_app))

r = client.post(f"/applications/{engaged_app}/events",
                data={"type": "viewed", "occurred_on": "2026-07-10"})
check("viewed event redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    st = conn.execute("SELECT status FROM application_status WHERE application_id = %s",
                      (engaged_app,)).fetchone()
    check("status is viewed", st["status"] == "viewed", st)

# Same occurred_on as the viewed event above: local-noon anchoring makes both
# instants identical, so this is a real precedence tie, not just "more recent".
r = client.post(f"/applications/{engaged_app}/events",
                data={"type": "engaged", "channel": "whatsapp",
                      "note": "follow-up screening questions", "occurred_on": "2026-07-10"})
check("engaged event redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    st = conn.execute("SELECT status FROM application_status WHERE application_id = %s",
                      (engaged_app,)).fetchone()
    check("engaged outranks viewed at the same instant", st["status"] == "engaged", st)
    ev = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s AND type = 'engaged'",
        (engaged_app,)).fetchone()
    check("engaged event records channel and note",
          ev["payload"].get("channel") == "whatsapp"
          and ev["payload"].get("note") == "follow-up screening questions", ev)

r = client.get(f"/applications/{engaged_app}")
check("detail badge renders the engaged status token",
      r.status_code == 200 and "var(--engaged)" in r.text and ">engaged<" in r.text, r.text)

r = client.get("/")
check("funnel shows an engaged segment", "engaged" in r.text, r.text)

# Same occurred_on again: interview_invite must outrank engaged at the same instant.
r = client.post(f"/applications/{engaged_app}/events",
                data={"type": "interview_invite", "occurred_on": "2026-07-10"})
with db.connect() as conn:
    st = conn.execute("SELECT status FROM application_status WHERE application_id = %s",
                      (engaged_app,)).fetchone()
    check("interview_invite outranks engaged at the same instant",
          st["status"] == "interview_invite", st)

r = client.post("/applications/new",
                data={"company": "Helix Analytics", "title": "Data Scientist",
                      "platform": "linkedin", "applied_date": "2026-07-01",
                      "outcome": "engaged", "outcome_date": "2026-07-15"})
check("manual entry accepts outcome=engaged redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    ev = conn.execute(
        "SELECT e.type FROM events e JOIN applications a ON a.id = e.application_id "
        "JOIN jobs j ON j.id = a.job_id WHERE j.company_norm = 'helix analytics'").fetchall()
    types = {r["type"] for r in ev}
    check("outcome=engaged stored as a status-driving event",
          "engaged" in types and "applied" in types, types)

print("contacts: CRUD")
r = client.post(f"/applications/{northwind_app}/contacts",
                data={"name": "Jane Recruiter", "role": "Talent Partner",
                      "url": "https://linkedin.com/in/janer", "notes": "met at meetup"})
check("add contact redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    contact = conn.execute(
        "SELECT id, name, role, url, source, approached, approached_at, notes "
        "FROM contacts WHERE job_id = (SELECT job_id FROM applications WHERE id = %s) "
        "AND name = 'Jane Recruiter'", (northwind_app,)).fetchone()
    check("contact stored with manual source, unapproached",
          contact and contact["source"] == "manual" and contact["approached"] is False
          and contact["approached_at"] is None, contact)
contact_id = contact["id"]

r = client.get(f"/applications/{northwind_app}")
check("contact appears on detail page with edit/delete controls",
      "Jane Recruiter" in r.text and f"/contacts/{contact_id}/edit" in r.text, r.text)

r = client.get(f"/applications/{northwind_app}/contacts/{contact_id}/edit")
check("edit form prefills", r.status_code == 200 and 'value="Jane Recruiter"' in r.text, r.text)

r = client.post(f"/applications/{northwind_app}/contacts/{contact_id}/edit",
                data={"name": "Jane R. Recruiter", "role": "Senior Talent Partner",
                      "url": "https://linkedin.com/in/janer", "approached": "yes",
                      "notes": "met at meetup, followed up"})
check("edit redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    contact = conn.execute(
        "SELECT name, role, approached, approached_at, notes FROM contacts WHERE id = %s",
        (contact_id,)).fetchone()
    check("edits persisted and approached_at stamped on first approach",
          contact["name"] == "Jane R. Recruiter" and contact["role"] == "Senior Talent Partner"
          and contact["approached"] is True and contact["approached_at"] is not None, contact)
    first_approached_at = contact["approached_at"]

r = client.post(f"/applications/{northwind_app}/contacts/{contact_id}/edit",
                data={"name": "Jane R. Recruiter", "role": "Senior Talent Partner",
                      "url": "https://linkedin.com/in/janer", "approached": "yes",
                      "notes": "met at meetup, followed up"})
with db.connect() as conn:
    contact = conn.execute(
        "SELECT approached_at FROM contacts WHERE id = %s", (contact_id,)).fetchone()
    check("re-saving an already-approached contact doesn't reset the timestamp",
          contact["approached_at"] == first_approached_at, contact)

r = client.post(f"/applications/{northwind_app}/contacts/{contact_id}/edit",
                data={"name": "", "role": "x", "url": "", "notes": ""})
check("blank name rejected", r.status_code == 400 and "name" in r.text, r.text)

r = client.post(f"/applications/{northwind_app}/contacts/{contact_id}/edit",
                data={"name": "Jane R. Recruiter", "role": "Senior Talent Partner",
                      "url": "https://linkedin.com/in/janer", "approached": "",
                      "notes": "no longer approached"})
with db.connect() as conn:
    contact = conn.execute(
        "SELECT approached, approached_at FROM contacts WHERE id = %s", (contact_id,)).fetchone()
    check("unchecking approached clears the timestamp",
          contact["approached"] is False and contact["approached_at"] is None, contact)

r = client.get(f"/applications/{northwind_app}/contacts/00000000-0000-0000-0000-000000000000/edit")
check("unknown contact 404s", r.status_code == 404, r.status_code)

with db.connect() as conn, conn.transaction():
    other_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'other co', 'Other Role') RETURNING id", (user_id,)).fetchone()["id"]
    other_contact = conn.execute(
        "INSERT INTO contacts (user_id, job_id, name, source) "
        "VALUES (%s, %s, 'Wrong Job Contact', 'manual') RETURNING id",
        (user_id, other_job)).fetchone()["id"]
r = client.get(f"/applications/{northwind_app}/contacts/{other_contact}/edit")
check("a contact belonging to a different job 404s, even under a valid application id",
      r.status_code == 404, r.status_code)

r = client.post(f"/applications/{northwind_app}/contacts/{contact_id}/delete")
check("delete redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    check("contact gone", conn.execute(
        "SELECT 1 FROM contacts WHERE id = %s", (contact_id,)).fetchone() is None)

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

print("triage: inbound lane (recruiter_outreach)")
with db.connect() as conn:
    # Null company — the agency-withholds-the-client case (a real agency
    # email in production had extraction.company = null).
    recruiter_email = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text,
                               received_at, classification, extraction, triage_state)
           VALUES (%s, 'gm-recruiter-agency', 'recruiter@beaconsearch.example',
                   'Hiring for Senior Software Engineer (AI and LLMOps)', 'body', now(),
                   'recruiter_outreach', %s, 'pending')
           ON CONFLICT (user_id, gmail_message_id)
             DO UPDATE SET triage_state = 'pending', matched_application_id = NULL
           RETURNING id""",
        (user_id,
         Json({"company": None, "role_title": "Senior Software Engineer (AI and LLMOps)",
               "platform": "linkedin", "ats": None, "event_date": None,
               "status_detail": None,
               "recruiter": {"name": "Mira Sen", "email": None}, "notes": None})),
    ).fetchone()["id"]
    conn.commit()

r = client.get("/triage")
check("actionable lane omits recruiter_outreach email",
      "Hiring for Senior Software Engineer" not in r.text, r.status_code)
r = client.get("/triage?lane=inbound")
check("inbound lane lists it", "Hiring for Senior Software Engineer" in r.text)

print("triage: nav badge excludes recruiter_outreach")
with db.connect() as conn:
    expected_pending = conn.execute(
        "SELECT (SELECT count(*) FROM emails WHERE triage_state = 'pending' "
        "        AND classification IS DISTINCT FROM 'recruiter_outreach') + "
        "       (SELECT count(*) FROM duplicate_candidates WHERE state = 'pending') AS n"
    ).fetchone()["n"]
r = client.get("/")
# Everything actionable was already resolved above, so the only pending item
# left is the recruiter_outreach email just seeded — the triage pill (which
# hides itself at 0, base.html's {% if pending %}) should therefore be gone
# entirely, proving the exclusion rather than just matching a nonzero count.
# Scoped to the TRIAGE link: the nav gained a second counted entry
# (follow-ups) on 21 Aug 2026, so "no pill anywhere on the page" stopped
# being the same claim as "the triage badge is hidden".
triage_link = r.text.split('href="/triage"')[1].split("</a>")[0]
if expected_pending:
    check(f"triage pill shows actionable-only count ({expected_pending})",
          f'class="pill">{expected_pending}<' in triage_link, triage_link)
else:
    check("triage pill hidden — only a recruiter_outreach email is pending",
          'class="pill"' not in triage_link, triage_link)

print("triage: track as lead (agency company override, null extraction.company)")
with db.connect() as conn:
    summary_before = analytics.summary(conn, user_id)
r = client.post(f"/triage/{recruiter_email}", data={
    "action": "lead", "company": "Beacon Search", "lane": "inbound"})
check("lead redirects to inbound lane",
      r.status_code == 303 and "lane=inbound" in r.headers["location"], r.headers.get("location"))
with db.connect() as conn:
    row = conn.execute("SELECT matched_application_id, triage_state FROM emails WHERE id = %s",
                       (recruiter_email,)).fetchone()
    check("email resolved", row["triage_state"] == "resolved")
    lead_app_id = row["matched_application_id"]
    a = conn.execute(
        "SELECT a.origin, j.company_norm, s.status FROM applications a "
        "JOIN jobs j ON j.id = a.job_id JOIN application_status s ON s.application_id = a.id "
        "WHERE a.id = %s", (lead_app_id,)).fetchone()
    check("origin is inbound", a["origin"] == "inbound", a)
    check("filed under the posted company override (agency, not null)",
          a["company_norm"] == "beacon search", a)
    check("derived status is interested — no applied event fabricated",
          a["status"] == "interested", a)
    has_applied = conn.execute(
        "SELECT 1 FROM events WHERE application_id = %s AND type = 'applied'",
        (lead_app_id,)).fetchone()
    check("no applied event written", has_applied is None)
    contact = conn.execute(
        "SELECT name FROM contacts WHERE job_id = "
        "(SELECT job_id FROM applications WHERE id = %s) AND name = 'Mira Sen'",
        (lead_app_id,)).fetchone()
    check("recruiter captured as contact", contact is not None)
    summary_after = analytics.summary(conn, user_id)
check("lead does not change applied count", summary_after["applied"] == summary_before["applied"],
      (summary_before, summary_after))
check("lead does not change response rate",
      summary_after["response_rate"] == summary_before["response_rate"],
      (summary_before, summary_after))

print("applications: origin filter")
r = client.get("/?origin=inbound")
check("inbound filter lists the lead", "Beacon Search" in r.text, r.status_code)
check("inbound badge shown", ">inbound</span>" in r.text)
r = client.get("/?origin=applied")
check("applied filter excludes the lead", "Beacon Search" not in r.text)

print("applications: default ordering")
# Built as a burst on one day, the way a real backfill arrives: local-noon
# anchoring gives every one of them the SAME last_activity to the second, which
# is what made the old unti-tiebroken ORDER BY undefined across 15 of 52 real
# rows.
with db.connect() as conn, conn.transaction():
    burst = []
    for name in ("sortalpha", "sortbravo", "sortcharlie"):
        jid = conn.execute(
            "INSERT INTO jobs (user_id, company_norm, title_canonical) "
            "VALUES (%s, %s, 'Engineer') RETURNING id", (user_id, name)).fetchone()["id"]
        aid = conn.execute(
            "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
            (user_id, jid)).fetchone()["id"]
        conn.execute(
            "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
            "VALUES (%s, %s, 'applied', 'manual', "
            "        date_trunc('day', now() - interval '9 days') + interval '12 hours', '{}')",
            (user_id, aid))
        burst.append(aid)
    # Applied a week later than the burst. The default sorts by submission date
    # DESC, so this one leads them — the burst alone can't test that, since all
    # three share an instant by construction.
    _jid = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'sortdelta', 'Engineer') RETURNING id", (user_id,)).fetchone()["id"]
    _aid = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, _jid)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', "
        "        date_trunc('day', now() - interval '2 days') + interval '12 hours', '{}')",
        (user_id, _aid))
    # One of the burst then hears back. Under the OLD default (most recent
    # activity) this alone put it on top; under the current one it must not,
    # which is the trade the default change made deliberately.
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'interview_invite', 'email', now() - interval '1 hour', '{}')",
        (user_id, burst[2]))

# Two more inbound rows. An inbound has no `applied` event by construction
# (invariant #9), so the date the default sorts it by is the date THEY
# approached. `sortecho` is a second pinned lead, approached long before Beacon
# Search (whose outreach event is now()) — the two of them are what proves the
# pinned block is ordered rather than left on insertion order. `sortfoxtrot` was
# approached 5 days ago and already rejected, so it is inbound but NOT pinned,
# and has to take its place among the applications by that same approach date
# instead of falling into the NULLS-LAST bucket at the bottom of the page.
with db.connect() as conn, conn.transaction():
    for name, evs in (("sortecho", (("recruiter_outreach", "12 days"),)),
                      ("sortfoxtrot", (("recruiter_outreach", "5 days"),
                                       ("rejected", "1 day")))):
        _jid = conn.execute(
            "INSERT INTO jobs (user_id, company_norm, title_canonical) "
            "VALUES (%s, %s, 'Engineer') RETURNING id", (user_id, name)).fetchone()["id"]
        _aid = conn.execute(
            "INSERT INTO applications (user_id, job_id, origin) "
            "VALUES (%s, %s, 'inbound') RETURNING id", (user_id, _jid)).fetchone()["id"]
        for _type, _ago in evs:
            conn.execute(
                "INSERT INTO events (user_id, application_id, type, source, occurred_at, "
                f"payload) VALUES (%s, %s, %s, 'email', now() - interval '{_ago}', '{{}}')",
                (user_id, _aid, _type))


def _order(path="/"):
    """Company names in the order the list renders them."""
    import re as _re
    html = client.get(path).text
    body = html.split('class="tl tl-head"')[1] if 'class="tl tl-head"' in html else html
    return _re.findall(r'<span class="co">([^<]*?)(?:\s*<|</span>)', body)


order = _order()
check("default is newest-applied-first: the later submission leads the burst",
      order.index("sortdelta") < order.index("sortalpha")
      and order.index("sortdelta") < order.index("sortcharlie"), order[:8])
check("the default does NOT reorder on activity — a reply no longer promotes a "
      "row past an application submitted after it",
      order.index("sortdelta") < order.index("sortcharlie"), order[:8])
check("the inbound lead is pinned above every application, whatever its date",
      order.index("Beacon Search") < order.index("sortdelta"), order[:8])
check("the pinned block is itself ordered newest-approach-first — the lead "
      "approached today leads the one approached 12 days ago",
      order.index("Beacon Search") < order.index("sortecho"), order[:8])
check("...and both stay above every application",
      order.index("sortecho") < order.index("sortdelta"), order[:8])
check("an inbound the user pursued isn't pinned, but is placed by the date of "
      "the approach rather than stranded below every application",
      order.index("sortdelta") < order.index("sortfoxtrot") < order.index("sortalpha"),
      order[:10])
# The pin matters MORE under this default than the last one: a lead has no
# `applied` event, so applied_at is NULL and NULLS LAST would otherwise drop it
# to the very bottom of the page rather than merely interleave it.
r = client.get("/")
check("and the group is labelled, so the pin isn't mysterious",
      "Inbound &middot; awaiting your call" in r.text or "Inbound · awaiting your call" in r.text)

# Identical timestamps, no tiebreaker = an order the planner picks. Asserting
# stability is the point; which of the two comes first is not.
check("a burst of identically-timed rows comes back in the same order every time",
      _order() == _order() == _order())

activity = _order("/?sort=activity")
check("sort=activity still surfaces the engaged thread — the old default is "
      "kept, demoted to an explicit choice",
      activity.index("sortcharlie") < activity.index("sortalpha")
      and activity.index("sortcharlie") < activity.index("sortbravo"), activity[:8])
# Position can't test this one: the lead's own outreach event may legitimately
# be the most recent activity on the page, so it can top this sort honestly.
# The divider is the observable — it renders only when leads_pinned is set.
_sep = "awaiting your call"
check("and as an explicit sort it is taken literally — no lead pinning",
      _sep not in client.get("/?sort=activity").text)
check("...while the default does render the divider",
      _sep in client.get("/").text)

silence = _order("/?sort=silence")
check("an explicit sort is taken literally — no lead pinning",
      silence.index("Beacon Search") > 0, silence[:6])
check("and it still means what it says: longest quiet first",
      silence.index("sortalpha") < silence.index("sortcharlie"), silence[:8])
check("an unknown sort falls back to the default, not an error",
      _order("/?sort=nonsense") == order)

# Every key in _SORTS, not the three that happened to have assertions. `company`
# 500ed from the day it was written — `lower(company_display)` wraps an OUTPUT
# ALIAS, which Postgres accepts in ORDER BY only as a bare name — and nothing
# noticed for weeks because the tests named their sorts one at a time. Looping
# the dict is what makes a new sort key testable by existing.
for _key in web._SORTS:
    _r = client.get(f"/?sort={_key}")
    check(f"sort={_key} renders", _r.status_code == 200, _r.status_code)
    # Same query, same ORDER BY, with each of the other filters layered on: the
    # ORDER BY is interpolated into one f-string shared by all of them, so a key
    # that only works unfiltered is a key that breaks on the next click.
    for _extra in ("origin=applied", "status=applied", "q=sort"):
        _r = client.get(f"/?sort={_key}&{_extra}")
        check(f"sort={_key} + {_extra} renders", _r.status_code == 200, _r.status_code)
check("the default is reachable by name and identical to the bare URL",
      _order("/?sort=applied") == order)

# The macro omits `sort` only when it equals DEFAULT_SORT. Hardcoding the old
# literal there would have made every filter click silently reset a chosen sort.
r = client.get("/?sort=activity&origin=applied")
check("a non-default sort survives a filter link (sort= carried in hrefs)",
      "sort=activity" in r.text, r.status_code)
r = client.get("/?origin=applied")
check("the default sort is omitted from hrefs rather than spelled out",
      "sort=applied" not in r.text, r.status_code)

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

print("manual entry: location")
r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Located Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "location": "Singapore, Remote",
    "after": "view"})
check("create with location redirects", r.status_code == 303, r.text)
focus_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    row = conn.execute(
        """SELECT p.location FROM applications a
           JOIN postings p ON p.id = a.applied_via_posting_id
           WHERE a.id = %s::uuid""", (focus_app,)).fetchone()
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

print("list: how-you-applied flag, three states kept three")
r = client.get("/")


def row_for(app_id, text):
    """The one list row for an application — the flag has to be read inside
    its own row, not anywhere on a page that holds 200 of them."""
    marker = f'href="/applications/{app_id}"'
    check(f"{text} has a row on the list", marker in r.text, marker)
    return r.text.split(marker)[1].split("</a>")[0]


check("an on-platform apply is labelled on-platform",
      "on-platform" in row_for(easy_app, "Easy Apply Role"))
check("an employer-site apply is labelled employer site",
      "employer site" in row_for(ext_app, "External Apply Role"))
# The whole point of reading it with `is sameas`: 111 real records predate the
# flag, and calling those "on-platform" would invent a fact about every one.
unset_row = row_for(unset_app, "Unspecified Apply Role")
check("an unrecorded apply is labelled NEITHER — unknown is not a no",
      "on-platform" not in unset_row and "employer site" not in unset_row, unset_row)
check("the flag is grey, not a new hue (UI rule 1 reserves chroma for the wait)",
      "--c:var(--interested)" in row_for(ext_app, "External Apply Role"))

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
        """SELECT e.occurred_at,
                  (e.occurred_at AT TIME ZONE 'Asia/Singapore')::date AS local_date
           FROM events e WHERE e.application_id = %s::uuid AND e.type = 'applied'""",
        (tz_app,)).fetchone()
    check("stored UTC instant lands on 2026-04-12 in Asia/Singapore",
          str(row["local_date"]) == "2026-04-12", row)
    # Blank time anchors to local noon, NOT the moment of submission — so the
    # instant is deterministic and asserting it exactly is meaningful.
    check("blank time anchors to noon Asia/Singapore (04:00 UTC)",
          row["occurred_at"] == datetime(2026, 4, 12, 4, 0, tzinfo=timezone.utc),
          row["occurred_at"])

r = client.post("/applications/new", data={
    "company": "Manual Entry Co", "title": "Noon Determinism Role", "platform": "linkedin",
    "applied_date": "2026-04-12", "after": "view"})
noon_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    other = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (noon_app,)).fetchone()["occurred_at"]
    check("a second same-date entry gets the identical instant (no submission-time drift)",
          other == datetime(2026, 4, 12, 4, 0, tzinfo=timezone.utc), other)

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

print("edit application: form prefills from the current record")
r = client.post("/applications/new", data={
    "company": "Edit Test Co", "title": "Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-10", "applied_time": "09:30", "location": "Singapore",
    "after": "view"})
edit_app = r.headers["location"].rsplit("/", 1)[1]
r = client.get(f"/applications/{edit_app}/edit")
check("edit form renders", r.status_code == 200, r.status_code)
check("company prefilled", 'value="Edit Test Co"' in r.text)
check("title prefilled", 'value="Backend Engineer"' in r.text)
check("location prefilled", 'value="Singapore"' in r.text)
check("applied date prefilled", 'value="2026-05-10"' in r.text)
check("applied time prefilled", 'value="09:30"' in r.text)

print("edit application: company/title sync across job AND every posting")
# A second posting on the same job — the case that makes a jobs-only write
# visibly wrong, since company_display prefers the newest posting's company_raw.
with db.connect() as conn, conn.transaction():
    edit_job = conn.execute(
        "SELECT job_id FROM applications WHERE id = %s::uuid", (edit_app,)).fetchone()["job_id"]
    second_posting = conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, company_raw, title, "
        "captured_via, captured_at) VALUES (%s, %s, 'jobstreet', 'Stale Name', "
        "'Stale Title', 'manual', now() + interval '1 day') RETURNING id",
        (user_id, edit_job)).fetchone()["id"]

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co Pte Ltd", "title": "Senior Backend Engineer",
    "platform": "linkedin", "applied_date": "2026-05-10", "applied_time": "09:30",
    "location": "Singapore"})
check("edit redirects", r.status_code == 303, r.status_code)
check("redirect flags the save", "saved=1" in r.headers["location"], r.headers["location"])
with db.connect() as conn:
    row = conn.execute("SELECT company_norm, title_canonical FROM jobs WHERE id = %s",
                       (edit_job,)).fetchone()
    check("job company_norm re-derived via norm_company (Pte Ltd stripped)",
          row["company_norm"] == "edit test co", row)
    check("job title updated", row["title_canonical"] == "Senior Backend Engineer", row)
    ps = conn.execute(
        "SELECT company_raw, company_norm, title FROM postings WHERE job_id = %s",
        (edit_job,)).fetchall()
    check("every posting carries the corrected company_raw",
          all(p["company_raw"] == "Edit Test Co Pte Ltd" for p in ps), ps)
    check("every posting carries the corrected title",
          all(p["title"] == "Senior Backend Engineer" for p in ps), ps)
    check("posting company_norm normalized too",
          all(p["company_norm"] == "edit test co" for p in ps), ps)
r = client.get(f"/applications/{edit_app}")
check("detail header shows the corrected company (not the stale posting)",
      "Edit Test Co Pte Ltd" in r.text and "Stale Name" not in r.text)
check("saved banner shown after redirect",
      "Saved" in client.get(f"/applications/{edit_app}?saved=1").text)

print("edit application: per-ad fields touch only the primary posting")
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co Pte Ltd", "title": "Senior Backend Engineer",
    "platform": "linkedin", "applied_date": "2026-05-10", "applied_time": "09:30",
    "location": "Remote"})
check("per-ad edit redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    primary_loc = conn.execute(
        "SELECT p.location FROM postings p JOIN applications a "
        "ON a.applied_via_posting_id = p.id WHERE a.id = %s::uuid", (edit_app,)
        ).fetchone()["location"]
    other_loc = conn.execute("SELECT location FROM postings WHERE id = %s",
                             (second_posting,)).fetchone()["location"]
    check("primary posting location updated", primary_loc == "Remote", primary_loc)
    check("other posting location untouched", other_loc is None, other_loc)

print("edit application: adding a URL retro-fits the dedup key")
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co Pte Ltd", "title": "Senior Backend Engineer",
    "platform": "linkedin", "applied_date": "2026-05-10", "applied_time": "09:30",
    "url": "https://www.linkedin.com/jobs/view/5544332211/?refId=zzz"})
check("url edit redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    p = conn.execute(
        "SELECT p.platform_job_id, p.url FROM postings p JOIN applications a "
        "ON a.applied_via_posting_id = p.id WHERE a.id = %s::uuid", (edit_app,)).fetchone()
    check("platform_job_id derived on edit", p["platform_job_id"] == "5544332211", p)
    check("url canonicalized on edit",
          p["url"] == "https://www.linkedin.com/jobs/view/5544332211/", p)

print("edit application: applied date is corrected in place, not appended")
with db.connect() as conn:
    n_before_edit = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (edit_app,)).fetchone()["n"]
# The form is a full-state submission (every field posts back, blank clears) —
# so a realistic edit carries the URL it already has, exactly as the browser does.
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co Pte Ltd", "title": "Senior Backend Engineer",
    "platform": "linkedin", "applied_date": "2026-05-08", "applied_time": "14:45",
    "url": "https://www.linkedin.com/jobs/view/5544332211/"})
check("date edit redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    evs = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (edit_app,)).fetchall()
    check("still exactly one applied event (corrected, not appended)",
          len(evs) == n_before_edit == 1, evs)
    check("applied event moved to the new instant",
          evs[0]["occurred_at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
          == "2026-05-08 14:45", evs[0])

print("edit application: blank time anchors to local noon")
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co Pte Ltd", "title": "Senior Backend Engineer",
    "platform": "linkedin", "applied_date": "2026-05-09", "applied_time": "",
    "url": "https://www.linkedin.com/jobs/view/5544332211/"})
check("blank time edit redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    # The user's timezone was reset to NULL above, so local == UTC here.
    occurred = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (edit_app,)).fetchone()["occurred_at"]
    check("edit with blank time lands at noon, not the submission moment",
          occurred == datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc), occurred)

print("edit application: validation")
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "", "title": "Role", "platform": "linkedin", "applied_date": "2026-05-08"})
check("blank company rejected", r.status_code == 400, r.status_code)
check("blank company error shown", "Enter the company name" in r.text)

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Pte Ltd", "title": "Role", "platform": "linkedin", "applied_date": "2026-05-08"})
check("company normalizing to nothing rejected", r.status_code == 400, r.status_code)

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Preserve Co", "title": "", "platform": "linkedin",
    "applied_date": "2026-05-08"})
check("blank title rejected", r.status_code == 400, r.status_code)
check("submitted value preserved on error", 'value="Edit Preserve Co"' in r.text)

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Role", "platform": "linkedin",
    "applied_date": "2099-01-01"})
check("future applied date rejected", r.status_code == 400, r.status_code)

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Role", "platform": "linkedin",
    "applied_date": "2026-05-08", "url": "https://sg.indeed.com/viewjob?jk=abc123"})
check("url/platform mismatch rejected", r.status_code == 400, r.status_code)
check("mismatch names the detected platform", "indeed" in r.text)

with db.connect() as conn:
    unchanged = conn.execute("SELECT company_norm, title_canonical FROM jobs WHERE id = %s",
                             (edit_job,)).fetchone()
    check("no rejected edit was persisted",
          unchanged["company_norm"] == "edit test co"
          and unchanged["title_canonical"] == "Senior Backend Engineer", unchanged)

print("edit application: URL already owned by another record is refused")
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-08",
    "url": "https://www.linkedin.com/jobs/view/9988776655/"})
check("colliding url rejected", r.status_code == 400, r.status_code)
check("collision names the other record", "Manual Entry Co" in r.text, r.text[:400])
with db.connect() as conn:
    still = conn.execute(
        "SELECT p.platform_job_id FROM postings p JOIN applications a "
        "ON a.applied_via_posting_id = p.id WHERE a.id = %s::uuid", (edit_app,)).fetchone()
    check("posting kept its own platform_job_id after the refused edit",
          still["platform_job_id"] == "5544332211", still)

print("edit application: applied date can't jump past an existing outcome")
with db.connect() as conn, conn.transaction():
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s::uuid, 'rejected', 'manual', %s, '{}')",
        (user_id, edit_app, datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)))
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-06-01"})
check("applied date after an outcome rejected", r.status_code == 400, r.status_code)
check("error names the blocking event", "rejected" in r.text)
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "applied_time": "08:00",
    "url": "https://www.linkedin.com/jobs/view/5544332211/"})
check("applied date before the outcome still allowed", r.status_code == 303, r.status_code)

print("edit application: blanking the URL clears the dedup key")
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "applied_time": "08:00", "url": ""})
check("blank url accepted", r.status_code == 303, r.status_code)
with db.connect() as conn:
    p = conn.execute(
        "SELECT p.url, p.platform_job_id FROM postings p JOIN applications a "
        "ON a.applied_via_posting_id = p.id WHERE a.id = %s::uuid", (edit_app,)).fetchone()
    check("url and platform_job_id both cleared",
          p["url"] is None and p["platform_job_id"] is None, p)

print("edit application: how you applied (external) is settable, changeable and clearable")
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "external": "yes"})
check("external=yes accepted", r.status_code == 303, r.status_code)
with db.connect() as conn:
    ev = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (edit_app,)).fetchone()
    check("payload.external stored as true", ev["payload"].get("external") is True, ev)
check("external prefilled as yes on the form",
      '<option value="yes" selected>' in client.get(f"/applications/{edit_app}/edit").text)

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "external": "no"})
with db.connect() as conn:
    ev = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (edit_app,)).fetchone()
    check("changed to false", ev["payload"].get("external") is False, ev)

# Blank means "not set" — matches manual entry's three-state semantics.
r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "external": ""})
with db.connect() as conn:
    ev = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s::uuid AND type = 'applied'",
        (edit_app,)).fetchone()
    check("blank external clears back to not-set", "external" not in ev["payload"], ev)

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "external": "maybe"})
check("unknown external value rejected", r.status_code == 400, r.status_code)

print("edit application: JD text re-runs extraction and drops the stale embedding")
with db.connect() as conn, conn.transaction():
    jd_posting = conn.execute(
        "SELECT applied_via_posting_id FROM applications WHERE id = %s::uuid",
        (edit_app,)).fetchone()["applied_via_posting_id"]
    # Seed an embedding so we can prove the edit invalidates it — otherwise a
    # stale vector keeps driving dedup against text that no longer exists.
    conn.execute("UPDATE postings SET jd_embedding = %s::vector WHERE id = %s",
                 ("[" + ",".join(["0.1"] * 1024) + "]", jd_posting))

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "jd_text": "Build data pipelines. Go and Postgres."})
check("jd edit redirects", r.status_code == 303, r.status_code)
with db.connect() as conn, conn.transaction():
    p = conn.execute("SELECT jd_text, jd_embedding FROM postings WHERE id = %s",
                     (jd_posting,)).fetchone()
    check("jd_text saved", p["jd_text"] == "Build data pipelines. Go and Postgres.", p["jd_text"])
    check("stale embedding cleared so embed_jd can re-run", p["jd_embedding"] is None)
    q = conn.execute(
        "SELECT count(*) AS n FROM job_queue WHERE type = 'extract_jd' "
        "AND payload->>'posting_id' = %s", (str(jd_posting),)).fetchone()["n"]
    check("extract_jd enqueued once", q == 1, q)
    # Same reason as the manual-entry jd case above: test_phase3.py drains the
    # shared queue with an embedding stub that rejects unstubbed text.
    conn.execute("DELETE FROM job_queue WHERE type = 'extract_jd' "
                 "AND payload->>'posting_id' = %s", (str(jd_posting),))

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "jd_text": "Build data pipelines. Go and Postgres."})
check("resubmitting identical jd redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    q = conn.execute(
        "SELECT count(*) AS n FROM job_queue WHERE type = 'extract_jd' "
        "AND payload->>'posting_id' = %s", (str(jd_posting),)).fetchone()["n"]
    check("unchanged jd does not re-enqueue extraction", q == 0, q)

r = client.post(f"/applications/{edit_app}/edit", data={
    "company": "Edit Test Co", "title": "Senior Backend Engineer", "platform": "linkedin",
    "applied_date": "2026-05-12", "jd_text": ""})
check("blanking jd redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    p = conn.execute("SELECT jd_text FROM postings WHERE id = %s", (jd_posting,)).fetchone()
    check("jd_text cleared", p["jd_text"] is None, p)
    q = conn.execute(
        "SELECT count(*) AS n FROM job_queue WHERE type = 'extract_jd' "
        "AND payload->>'posting_id' = %s", (str(jd_posting),)).fetchone()["n"]
    check("no extraction enqueued for an emptied jd", q == 0, q)

print("edit application: unknown id 404s")
check("edit form 404s for a bogus id",
      client.get("/applications/not-a-uuid/edit").status_code == 404)
check("edit post 404s for a bogus id",
      client.post("/applications/not-a-uuid/edit", data={
          "company": "X Co", "title": "Role", "platform": "linkedin",
          "applied_date": "2026-05-08"}).status_code == 404)

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

print("refile email: ATS-branding wrong-company match, corrected after the fact")
with db.connect() as conn, conn.transaction():
    # The "wrong" application — exists ONLY because of one email, mirroring
    # matcher._create_application's backfill path: job + email_only posting +
    # application + applied + confirmation events, all from a single email.
    wrong_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'wrongco', 'unknown role') RETURNING id", (user_id,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, captured_via) "
        "VALUES (%s, %s, 'other', 'email_only')", (user_id, wrong_job))
    wrong_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, wrong_job)).fetchone()["id"]
    refile_email_id = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, received_at,
                               classification, extraction, matched_application_id, triage_state)
           VALUES (%s, 'gm-refile-test', 'no-reply@ashbyhq.com', 'Thanks for applying', now(),
                   'confirmation', %s, %s, 'auto_matched')
           RETURNING id""",
        (user_id, Json({"company": "WrongCo", "platform": "ats"}), wrong_app)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, source_email_id, payload) "
        "VALUES (%s, %s, 'applied', 'email', now(), %s, '{}')",
        (user_id, wrong_app, refile_email_id))
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, source_email_id, payload) "
        "VALUES (%s, %s, 'confirmation', 'email', now(), %s, '{}')",
        (user_id, wrong_app, refile_email_id))

    # The real target application — its own genuine history that must survive untouched.
    right_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'rightco', 'Real Role') RETURNING id", (user_id,)).fetchone()["id"]
    right_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, right_job)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now(), '{}')", (user_id, right_app))

r = client.post(f"/emails/{refile_email_id}/refile",
                data={"action": "link", "application_id": str(right_app),
                      "redirect_to": f"/applications/{wrong_app}"})
check("refile redirects", r.status_code == 303, r.text)
check("wrong application was empty after the undo, so it's cleaned up too — "
      "redirect falls back to the delete banner instead of the now-gone page",
      "deleted=" in r.headers["location"], r.headers["location"])

with db.connect() as conn:
    check("wrong job gone (existed only because of this email)", conn.execute(
        "SELECT 1 FROM jobs WHERE id = %s", (wrong_job,)).fetchone() is None)
    right_events = conn.execute(
        "SELECT type, source_email_id FROM events WHERE application_id = %s "
        "ORDER BY occurred_at", (right_app,)).fetchall()
    check("right application kept its own event and gained exactly the confirmation",
          len(right_events) == 2
          and {(e["type"], e["source_email_id"] and str(e["source_email_id"])) for e in right_events}
              == {("applied", None), ("confirmation", str(refile_email_id))},
          right_events)
    email_row = conn.execute(
        "SELECT matched_application_id, triage_state FROM emails WHERE id = %s",
        (refile_email_id,)).fetchone()
    check("email now points at the right application, resolved",
          str(email_row["matched_application_id"]) == str(right_app)
          and email_row["triage_state"] == "resolved", email_row)

print("refile email: only THIS email's events are undone, not the rest of the application's history")
with db.connect() as conn, conn.transaction():
    busy_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'busyco', 'Busy Role') RETURNING id", (user_id,)).fetchone()["id"]
    busy_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, busy_job)).fetchone()["id"]
    stray_email_id = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, received_at,
                               classification, extraction, matched_application_id, triage_state)
           VALUES (%s, 'gm-refile-test-2', 'x@y.example', 'status update', now(),
                   'status_update', %s, %s, 'auto_matched')
           RETURNING id""",
        (user_id, Json({"company": "BusyCo", "status_detail": "viewed"}), busy_app)).fetchone()["id"]
    conn.execute(  # the application's own, unrelated history
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now(), '{}')", (user_id, busy_app))
    conn.execute(  # this email's contribution — a 'viewed' event
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, source_email_id, payload) "
        "VALUES (%s, %s, 'viewed', 'email', now(), %s, '{}')",
        (user_id, busy_app, stray_email_id))

r = client.post(f"/emails/{stray_email_id}/refile",
                data={"action": "pending", "redirect_to": f"/applications/{busy_app}"})
check("send-to-pending redirects back to the (still-existing) application page",
      r.status_code == 303 and r.headers["location"] == f"/applications/{busy_app}", r.text)

with db.connect() as conn:
    check("application NOT deleted — it has its own unrelated history", conn.execute(
        "SELECT 1 FROM jobs WHERE id = %s", (busy_job,)).fetchone() is not None)
    remaining = conn.execute(
        "SELECT type FROM events WHERE application_id = %s", (busy_app,)).fetchall()
    check("only the email's own event was undone; the manual one survives",
          [e["type"] for e in remaining] == ["applied"], remaining)
    email_row = conn.execute(
        "SELECT matched_application_id, triage_state FROM emails WHERE id = %s",
        (stray_email_id,)).fetchone()
    check("email unmatched and back in triage as pending",
          email_row["matched_application_id"] is None
          and email_row["triage_state"] == "pending", email_row)

r = client.post(f"/emails/{stray_email_id}/refile", data={"action": "bogus"})
check("unknown action rejected", r.status_code == 400, r.status_code)

print("needs-follow-up block: acts from the list and clears the row")
with db.connect() as conn, conn.transaction():
    stale_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'quietcorp', 'Staff Engineer') RETURNING id", (user_id,)).fetchone()["id"]
    stale_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, stale_job)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now() - interval '30 days', '{}')",
        (user_id, stale_app))

r = client.get("/")
check("the list no longer carries the queue at all",
      r.status_code == 200 and "Needs follow-up" not in r.text
      and 'class="card fu"' not in r.text
      and 'value="follow_up_sent"' not in r.text, r.status_code)
check("it carries a counted link to them instead",
      'href="/follow-ups"' in r.text and "need follow-up" in r.text, r.status_code)

r = client.get("/follow-ups")
check("the queue page lists the stale thread with its wait length",
      r.status_code == 200 and "Needs follow-up" in r.text
      and "quietcorp" in r.text and "30d" in r.text, r.status_code)
check("it offers the action, not just a link", 'value="follow_up_sent"' in r.text)
check("its buttons return to the queue, so clearing one shortens the page "
      "you are still looking at", 'name="redirect_to" value="/follow-ups"' in r.text)

r = client.post(f"/applications/{stale_app}/events",
                data={"type": "note", "note": "still waiting", "redirect_to": "/follow-ups"})
check("acting from the queue returns to the queue",
      r.status_code == 303 and r.headers["location"] == "/follow-ups",
      r.headers.get("location"))
r = client.post(f"/applications/{stale_app}/events",
                data={"type": "note", "note": "x", "redirect_to": "/?fu=1"})
check("the retired fu=1 destination is no longer accepted",
      r.status_code == 303 and r.headers["location"].startswith("/applications/"),
      r.headers.get("location"))

r = client.post(f"/applications/{stale_app}/events",
                data={"type": "follow_up_sent", "redirect_to": "/"})
check("acting from the list returns to the list",
      r.status_code == 303 and r.headers["location"] == "/", r.headers.get("location"))
with db.connect() as conn:
    check("follow-up recorded", conn.execute(
        "SELECT 1 FROM events WHERE application_id = %s AND type = 'follow_up_sent'",
        (stale_app,)).fetchone() is not None)
r = client.get("/follow-ups")
check("row is gone from the queue once followed up", "quietcorp" not in r.text)

r = client.post(f"/applications/{stale_app}/events",
                data={"type": "note", "note": "from detail", "redirect_to": "/evil"})
check("an unknown redirect falls back to the detail page, never followed",
      r.headers["location"] == f"/applications/{stale_app}", r.headers.get("location"))

print("news that arrives off the ingest paths: manual status events")
# A recruiter rings, or messages on WhatsApp, and the status genuinely changed
# with nothing for the system to parse. Before this the only honest option was a
# note, which leaves status at 'applied' — the row never leaves the follow-up
# queue and analytics count it as never answered.
with db.connect() as conn, conn.transaction():
    wa_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'sponsorless', 'AI Engineer') RETURNING id", (user_id,)).fetchone()["id"]
    wa_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, wa_job)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now() - interval '20 days', '{}')",
        (user_id, wa_app))

r = client.get("/follow-ups")
check("before: the untouched thread is in the follow-up queue",
      "sponsorless" in r.text, r.status_code)

r = client.post(f"/applications/{wa_app}/events",
                data={"type": "rejected", "reason": "visa", "channel": "whatsapp",
                      "note": "they can't sponsor", "occurred_on": "2026-07-26"})
check("a manual rejection is accepted", r.status_code == 303, r.text)
with db.connect() as conn:
    ev = conn.execute(
        "SELECT type, source, occurred_at, payload FROM events "
        "WHERE application_id = %s AND type = 'rejected'", (wa_app,)).fetchone()
    check("stored as a manual rejected event", ev and ev["source"] == "manual", ev)
    check("reason and channel land in the payload, note alongside them",
          ev["payload"] == {"reason": "visa", "channel": "whatsapp",
                            "note": "they can't sponsor"}, ev["payload"])
    check("backdated to the day it happened, at local noon — not now, not midnight",
          ev["occurred_at"].date().isoformat() == "2026-07-26"
          and ev["occurred_at"].time() != datetime.min.time(), ev["occurred_at"])
    check("derived status follows (invariant #2 — no status column was written)",
          conn.execute("SELECT status FROM application_status WHERE application_id = %s",
                       (wa_app,)).fetchone()["status"] == "rejected")

r = client.get("/follow-ups")
check("the row leaves the follow-up queue, because it is genuinely answered now",
      "sponsorless" not in r.text)

r = client.get(f"/applications/{wa_app}")
check("the timeline names the reason and the channel it came through",
      "visa / sponsorship" in r.text and "WhatsApp" in r.text, r.status_code)

# A reason is only meaningful on a rejection; there's no JS to hide the select.
r = client.post(f"/applications/{wa_app}/events",
                data={"type": "note", "reason": "salary", "channel": "phone",
                      "note": "called to ask"})
with db.connect() as conn:
    p = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s AND type = 'note' "
        "ORDER BY created_at DESC LIMIT 1", (wa_app,)).fetchone()["payload"]
    check("reason dropped on a non-rejection, channel kept",
          p == {"channel": "phone", "note": "called to ask"}, p)

r = client.post(f"/applications/{wa_app}/events",
                data={"type": "rejected", "channel": "carrier pigeon"})
with db.connect() as conn:
    n = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s AND type = 'rejected'",
        (wa_app,)).fetchone()["n"]
    check("an unknown channel is dropped, not fatal — the event still lands", n == 2, n)

r = client.post(f"/applications/{wa_app}/events",
                data={"type": "interview_invite", "occurred_on": "2099-01-01"})
check("a future date is refused with a message, not a 422",
      r.status_code == 303 and "event_error" in r.headers["location"], r.headers.get("location"))
r = client.post(f"/applications/{wa_app}/events",
                data={"type": "interview_invite", "occurred_on": "not-a-date"})
check("so is an unparseable one",
      r.status_code == 303 and "event_error" in r.headers["location"], r.headers.get("location"))
with db.connect() as conn:
    check("neither stored anything", conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s "
        "AND type = 'interview_invite'", (wa_app,)).fetchone()["n"] == 0)
check("the message renders as a banner on the way back",
      "Not recorded" in client.get(
          f"/applications/{wa_app}?event_error=That+date+is+in+the+future.").text)

r = client.post(f"/applications/{wa_app}/events", data={"type": "confirmation"})
check("an event type the ingest paths own is still refused", r.status_code == 400, r.status_code)
r = client.post(f"/applications/{wa_app}/events", data={"type": "recruiter_outreach"})
check("so is recruiter_outreach — invariant #9 reserves it for triage",
      r.status_code == 400, r.status_code)

print("form answers: detail page + answer bank")
with db.connect() as conn, conn.transaction():
    other_app = conn.execute(
        "SELECT a.id FROM applications a WHERE a.id <> %s LIMIT 1",
        (northwind_app,)).fetchone()["id"]
    for app_id, notice, extra in (
            (northwind_app, "1 month", ("Sponsorship needed?", "No")),
            (other_app, "2 months", ("Preferred start date", "Immediately"))):
        conn.execute(
            "INSERT INTO application_answers (user_id, application_id, question, "
            "question_norm, answer, field_type, ordinal) "
            "VALUES (%s, %s, 'What is your notice period?', 'what is your notice period', "
            "%s, 'text', 0)", (user_id, app_id, notice))
        conn.execute(
            "INSERT INTO application_answers (user_id, application_id, question, "
            "question_norm, answer, field_type, ordinal) VALUES (%s, %s, %s, %s, %s, "
            "'radio', 1)",
            (user_id, app_id, extra[0],
             extra[0].lower().replace("?", "").replace(" ", " "), extra[1]))

r = client.get(f"/applications/{northwind_app}")
check("detail page shows the form Q&A", r.status_code == 200
      and "What the form asked" in r.text
      and "What is your notice period?" in r.text and "1 month" in r.text, r.status_code)
check("detail page does not leak the other application's answer",
      "2 months" not in r.text, r.text[:200])

r = client.get("/answers")
check("answer bank renders", r.status_code == 200 and "What you've told them" in r.text,
      r.status_code)
check("the repeated question is grouped once, newest answer shown",
      r.text.count("What is your notice period?") == 1 and "2 differe" in r.text, r.text[:200])
check("a question answered two ways offers its history",
      "What you said each time" in r.text, r.text[:200])
check("questions asked once show no history toggle for themselves",
      "Sponsorship needed?" in r.text, r.text[:200])
check("answers nav entry is active on its own page",
      '<a href="/answers" class="active"' in r.text, r.text[:200])

print("\nALL WEB PATHS PASS")
