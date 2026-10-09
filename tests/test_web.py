"""Web UI test against a live database seeded by tests/test_integration.py.

Run AFTER the integration test on the same database:
  TRACKER_DATABASE_URL=postgresql:///tracker_test python3 tests/test_web.py

Covers: applications table + funnel render, detail page,
manual event logging, triage listing, and all resolve actions (link appends
an event with provenance; create builds a full record; ignore; lead files a
recruiter_outreach email as an inbound application with origin='inbound' and
no fabricated applied event), plus the triage actionable/inbound lane split
and the applications-list origin filter, and (9 Sep 2026) the rejection
reason on any rejected event whatever its source — the route, the row badge,
the why-chips + `reason` filter, and the analytics table.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from psycopg.types.json import Json

from pipeline import analytics, db, insights, web
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
# A date an email states shows on its line only when it is AHEAD of the day
# the email arrived (web._stated_ahead). Both emails are test_integration's:
# Northwind's rejection quotes the apply day, Alpine's invite names the
# interview day two weeks on.
check("a rejection does not repeat the apply day it quotes",
      "<small>for 10 Jul 2026</small>" not in r.text)
with db.connect() as conn:
    alpine_app = conn.execute(
        "SELECT a.id FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE j.company_norm = 'alpine ski house'").fetchone()["id"]
check("an invitation shows the interview day it names",
      "<small>for 4 Aug 2026</small>" in client.get(f"/applications/{alpine_app}").text)

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

print("triage: the link dropdown dates each record (25 Sep 2026)")
# A role reposted and applied to again leaves two records with one company and
# one title, identical in the dropdown until each carries its date. Noon UTC,
# so the local date is the same in any timezone the test user might have.
with db.connect() as conn, conn.transaction():
    twin_jobs = []
    for n, applied in (("1", "2026-07-03 12:00+00"), ("2", "2026-08-14 12:00+00")):
        job = conn.execute(
            "INSERT INTO jobs (user_id, company_norm, title_canonical) "
            "VALUES (%s, 'contoso markets', 'Platform Engineer') RETURNING id",
            (user_id,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO postings (user_id, job_id, platform, platform_job_id, company_raw, "
            "title, captured_via) VALUES (%s, %s, 'linkedin', %s, 'Contoso Markets', "
            "'Platform Engineer', 'extension')", (user_id, job, f"LI-twin-{n}"))
        twin = conn.execute(
            "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
            (user_id, job)).fetchone()["id"]
        conn.execute(
            "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
            "VALUES (%s, %s, 'applied', 'extension', %s, '{}')", (user_id, twin, applied))
        twin_jobs.append(job)
# Every pending email carries its own copy of the dropdown; read the first.
# Since 7 Oct 2026 it opens with the records nearest the email
# (triage.nearest), so read its "Every record" group, the whole list.
first_select = client.get("/triage").text.split("<select", 1)[1].split("</select>", 1)[0]
first_select = first_select.split('label="Every record"')[-1]
twins = re.findall(r'<option value="[^"]+">(Contoso Markets, Platform Engineer[^<]*)</option>',
                   first_select)
check("same-titled records are told apart by their applied dates, newest first",
      twins == ["Contoso Markets, Platform Engineer (applied 14 Aug 2026)",
                "Contoso Markets, Platform Engineer (applied 3 Jul 2026)"], twins)
with db.connect() as conn, conn.transaction():
    conn.execute("DELETE FROM events WHERE application_id IN "
                 "(SELECT id FROM applications WHERE job_id = ANY(%s))", (twin_jobs,))
    conn.execute("DELETE FROM applications WHERE job_id = ANY(%s)", (twin_jobs,))
    conn.execute("DELETE FROM postings WHERE job_id = ANY(%s)", (twin_jobs,))
    conn.execute("DELETE FROM jobs WHERE id = ANY(%s)", (twin_jobs,))

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
from pipeline import triage as _triage
with db.connect() as conn:
    # An application filed twice counts too (7 Oct 2026), by the rule the
    # page's band draws.
    expected_pending = conn.execute(
        "SELECT (SELECT count(*) FROM emails WHERE triage_state = 'pending' "
        "        AND classification IS DISTINCT FROM 'recruiter_outreach') + "
        "       (SELECT count(*) FROM duplicate_candidates WHERE state = 'pending') AS n"
    ).fetchone()["n"] + len(_triage.twins(conn, user_id))
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
# The re-file dropdown on its detail page shares triage's options. A lead has
# no applied event to date it by, so it is dated by the approach instead.
lead_opt = re.search(rf'<option value="{lead_app_id}">([^<]*)</option>',
                     client.get(f"/applications/{lead_app_id}").text)
check("a lead's option is dated by the approach, not left undated",
      lead_opt is not None and lead_opt.group(1).startswith("Beacon Search, ")
      and "(approached " in lead_opt.group(1), lead_opt and lead_opt.group(1))

print("inbound: its own page, split from the record by origin (24 Sep 2026)")
# Membership is by origin, never status: / shows what the user started,
# /inbound what a recruiter did, and the two partition the table. `?origin=`
# went with the tabs — it is ignored, not honoured, so an old bookmark can't
# quietly show a different population than the page it lands on.
r = client.get("/inbound")
check("/inbound lists the lead", "Beacon Search" in r.text, r.status_code)
check("the nav marks Inbound active, not Applications",
      'href="/inbound" class="active"' in r.text and 'href="/" class="active"' not in r.text)
check("its lede counts approaches awaiting the user, not applications and replies",
      "awaiting your call" in r.text and " application" not in r.text.split("<main>")[1].split('class="funnel')[0])
# Singular here: at this point the only inbound row is the lead just filed.
# In the table's head since 7 Oct 2026, where the column's name was.
check("and it states its own count in the noun the page is about",
      re.search(r'class="lab count">\d+ approach(es)?</span>', r.text) is not None)
r = client.get("/")
check("the record excludes the lead", "Beacon Search" not in r.text)
check("and never wears an inbound tag — nothing on it can be one", ">inbound</span>" not in r.text)
check("its funnel has no interested segment — empty segments are dropped, not zeroed",
      "--interested" not in r.text.split('class="funnel')[1].split("</div>")[0])
check("it states its count too (in the head, before the month index when there is one)",
      re.search(r'class="lab count">\d+ applications?(</span>|<span class="months">)', r.text) is not None)
r = client.get("/?origin=inbound")
check("?origin= on the record is ignored, not honoured", "Beacon Search" not in r.text)
with db.connect() as conn:
    expected_leads = analytics.lead_count(conn, user_id)
nav_link = r.text.split('href="/inbound"')[1].split("</a>")[0]
check(f"the nav pill counts the leads awaiting a decision ({expected_leads}), on every list page",
      expected_leads >= 1 and f'class="pill">{expected_leads}<' in nav_link
      and f'class="pill">{expected_leads}<' in client.get("/inbound").text, nav_link)
r = client.get(f"/applications/{lead_app_id}")
check("an inbound's detail page lights Inbound in the nav",
      'href="/inbound" class="active"' in r.text and 'href="/" class="active"' not in r.text)

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
# approached 5 days ago and already rejected, so it is inbound but NOT pinned:
# it sits under the second divider on /inbound, placed by that same approach
# date. (Until 24 Sep 2026 all three lived on / among the applications; the
# split is by origin, so none of them is on the record now.)
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
check("no inbound row is on the record at all — the split is by origin, not a pin",
      not {"Beacon Search", "sortecho", "sortfoxtrot"} & set(order), order[:10])
inbound_order = _order("/inbound")
check("on /inbound the undecided lead is pinned above the one the user pursued, "
      "whatever its date",
      inbound_order.index("Beacon Search") < inbound_order.index("sortfoxtrot"),
      inbound_order[:8])
check("the pinned block is itself ordered newest-approach-first — the lead "
      "approached today leads the one approached 12 days ago",
      inbound_order.index("Beacon Search") < inbound_order.index("sortecho"),
      inbound_order[:8])
check("...and both stay above the pursued one",
      inbound_order.index("sortecho") < inbound_order.index("sortfoxtrot"),
      inbound_order[:8])
# The pin matters MORE under this default than the last one: a lead has no
# `applied` event, so applied_at is NULL and NULLS LAST would otherwise drop it
# to the very bottom of the page rather than merely interleave it.
_sep = '<div class="tl-sep">Awaiting your call</div>'
_sep2 = '<div class="tl-sep">Underway or closed</div>'
r = client.get("/inbound")
check("and both groups are labelled, so the pin isn't mysterious",
      _sep in r.text and _sep2 in r.text)
check("the pursued one sits under the second divider, the leads under the first",
      r.text.index(_sep) < r.text.index("Beacon Search") < r.text.index(_sep2)
      < r.text.index("sortfoxtrot"))
# The ELEMENT, not the class name: base.html's stylesheet defines `.tl-sep`
# on every page, so the bare string is always present. The record has month
# dividers since 7 Oct 2026 (below), so this names the pin's two.
_rec = client.get("/").text
check("the record renders no lead divider — nothing on it can be pinned",
      ">Awaiting your call</div>" not in _rec and ">Underway or closed</div>" not in _rec)

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
check("and as an explicit sort it is taken literally — no lead pinning",
      _sep not in client.get("/inbound?sort=activity").text)
check("...while the default does render the divider",
      _sep in client.get("/inbound").text)

silence = _order("/inbound?sort=silence")
check("an explicit sort is taken literally — no lead pinning",
      silence.index("Beacon Search") > 0, silence[:6])
silence = _order("/?sort=silence")
check("and it still means what it says: longest quiet first",
      silence.index("sortalpha") < silence.index("sortcharlie"), silence[:8])
check("an unknown sort falls back to the default, not an error",
      _order("/?sort=nonsense") == order and _order("/inbound?sort=nonsense") == inbound_order)

# Every key in _SORTS, not the three that happened to have assertions. `company`
# 500ed from the day it was written — `lower(company_display)` wraps an OUTPUT
# ALIAS, which Postgres accepts in ORDER BY only as a bare name — and nothing
# noticed for weeks because the tests named their sorts one at a time. Looping
# the dict is what makes a new sort key testable by existing.
for _key in web._SORTS:
    # Both pages: they share one builder, so a key that renders on one renders
    # on the other — which is exactly what this loop is here to keep true.
    for _base in ("/", "/inbound"):
        _r = client.get(f"{_base}?sort={_key}")
        check(f"{_base} sort={_key} renders", _r.status_code == 200, _r.status_code)
        # Same query, same ORDER BY, with each of the other filters layered on:
        # the ORDER BY is interpolated into one f-string shared by all of them,
        # so a key that only works unfiltered is a key that breaks on the next
        # click.
        for _extra in ("status=applied", "q=sort",
                       "reason=visa", "status=rejected&reason=unrecorded",
                       "how=after_round", "status=rejected&how=no_round&reason=unrecorded"):
            _r = client.get(f"{_base}?sort={_key}&{_extra}")
            check(f"{_base} sort={_key} + {_extra} renders", _r.status_code == 200, _r.status_code)
check("the default is reachable by name and identical to the bare URL",
      _order("/?sort=applied") == order)

# The macro omits `sort` only when it equals DEFAULT_SORT. Hardcoding the old
# literal there would have made every filter click silently reset a chosen sort.
r = client.get("/?sort=activity&status=applied")
check("a non-default sort survives a filter link (sort= carried in hrefs)",
      "sort=activity" in r.text, r.status_code)
r = client.get("/?status=applied")
check("the default sort is omitted from hrefs rather than spelled out",
      "sort=applied" not in r.text, r.status_code)
r = client.get("/inbound?sort=activity&status=rejected")
check("and on /inbound every href the macro builds stays on /inbound",
      "sort=activity" in r.text and 'href="/?' not in r.text, r.status_code)

print("inbound: deleting one lands back on /inbound")
# The banner-redirect follows the record's own page (web._list_path), off the
# immutable origin — a throwaway lead, so the ordering fixtures above survive.
with db.connect() as conn, conn.transaction():
    _jid = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'delgolf', 'Engineer') RETURNING id", (user_id,)).fetchone()["id"]
    _del_lead = conn.execute(
        "INSERT INTO applications (user_id, job_id, origin) "
        "VALUES (%s, %s, 'inbound') RETURNING id", (user_id, _jid)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'recruiter_outreach', 'email', now(), '{}')", (user_id, _del_lead))
r = client.post(f"/applications/{_del_lead}/delete")
check("the redirect goes to /inbound, not the record",
      r.status_code == 303 and r.headers["location"].startswith("/inbound?deleted="),
      r.headers.get("location"))
r = client.get(r.headers["location"])
check("and the banner renders there", r.status_code == 200 and "Deleted <strong>delgolf" in r.text,
      r.status_code)

# The search box must echo what was searched. It read "None" from 8 Sep 2026,
# when base.html grew `{% set q = queue_alert() %}` for the stall band: a
# parent template's top-level set is visible in every child block and SHADOWS
# the render context, so `{{ q }}` on the list — and the `{% elif q %}` guarding
# the "nothing matches" state, and the Clear link — all read the band's value
# (None with a healthy queue, the health DICT when stalled, which list_url
# would then have urlencoded into every href on the page). Nothing asserted on
# the box's value until now, which is how it stayed unseen for a day.
r = client.get("/?q=zzqx-no-such-company")
check("the search box echoes the search",
      'name="q" value="zzqx-no-such-company"' in r.text, r.status_code)
check("a miss says so, and offers to clear",
      "Nothing matches" in r.text and "zzqx-no-such-company" in r.text
      and ">Clear</a>" in r.text, r.status_code)

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

print("manual entry: an employer's own site is platform 'other', with the extension's id")
# docs/career-sites.md §10. The form defaults to LinkedIn, so the likeliest slip
# is a career-site link left on it: refused with the fix named, rather than
# stored with no id — a record that could then never converge with a capture.
SITE_URL = "https://careers.contoso.com/job/Singapore-Engineer/1234567890/?locale=en_GB"
SITE = {"company": "Career Site Co", "title": "Platform Engineer", "url": SITE_URL,
        "applied_date": "2026-04-12", "after": "view"}
r = client.post("/applications/new", data={**SITE, "platform": "linkedin"})
check("a career-site link left on LinkedIn is refused, naming the fix",
      r.status_code == 400 and "Pick “other”" in r.text, r.status_code)
r = client.post("/applications/new", data={**SITE, "platform": "other"})
check("the same link on 'other' is created", r.status_code == 303, r.text)
site_app = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    p = conn.execute(
        "SELECT p.platform, p.platform_job_id, p.url FROM applications a "
        "JOIN postings p ON p.id = a.applied_via_posting_id WHERE a.id = %s::uuid",
        (site_app,)).fetchone()
    check("its id is the extension's <host>/<token>",
          (p["platform"], p["platform_job_id"]) == ("other", "careers.contoso.com/1234567890"), p)
# What the popup sends for the same page: it must land on the SAME record.
r = client.post("/captures", headers={"Authorization": f"Bearer {os.environ['TRACKER_API_TOKEN']}"},
                json={"platform": "other", "platform_job_id": "careers.contoso.com/1234567890",
                      "url": SITE_URL, "company": "Career Site Co", "title": "Platform Engineer",
                      "jd_text": "the JD, read off the page", "trigger": "apply", "external": True})
check("a capture of that page converges on the manual record",
      r.status_code == 200 and r.json()["application_id"] == site_app, r.text)

print("list: how-you-applied flag, three states kept three")
r = client.get("/")


def row_for(app_id, text):
    """The one list row for an application — the flag has to be read inside
    its own row, not anywhere on a page that holds 200 of them."""
    marker = f'href="/applications/{app_id}"'
    check(f"{text} has a row on the list", marker in r.text, marker)
    return r.text.split(marker)[1].split("</a>")[0]


# Since 7 Oct 2026 only the exception wears a tag: 242 of 351 real rows said
# "on-platform", the default. The detail page keeps all three states.
easy_row = row_for(easy_app, "Easy Apply Role")
check("an on-platform apply wears no tag — the default needs none",
      "on-platform" not in easy_row and "employer site" not in easy_row, easy_row)
check("...and its detail page still says how it went in",
      "on-platform (Easy Apply)" in client.get(f"/applications/{easy_app}").text)
check("an employer-site apply is labelled employer site",
      "employer site" in row_for(ext_app, "External Apply Role"))
# The whole point of reading it with `is sameas`: 111 real records predate the
# flag, and calling those "employer site" would invent a fact about every one.
unset_row = row_for(unset_app, "Unspecified Apply Role")
check("an unrecorded apply is labelled NEITHER — unknown is not a no",
      "on-platform" not in unset_row and "employer site" not in unset_row, unset_row)

print("list: the saved tag says nothing was sent, so it tests exactly that")
# It tested the record's ORIGIN until 24 Sep 2026, and a job captured as
# interested and then applied to, confirmed and rejected wore "saved" for a
# month. Origin is how a record started; the tag claims what has happened.
with db.connect() as conn, conn.transaction():
    _saved_ids = []
    for title, sent in (("Kept For Later", False), ("Applied After Saving", True)):
        job = conn.execute("INSERT INTO jobs (user_id, company_norm, title_canonical) "
                           "VALUES (%s, 'savedco', %s) RETURNING id", (user_id, title)).fetchone()["id"]
        a_id = conn.execute("INSERT INTO applications (user_id, job_id, origin) "
                            "VALUES (%s, %s, 'saved') RETURNING id", (user_id, job)).fetchone()["id"]
        conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
                     "VALUES (%s, %s, 'interested', 'extension', now() - interval '3 days', '{}')",
                     (user_id, a_id))
        if sent:
            conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, "
                         "payload) VALUES (%s, %s, 'applied', 'manual', now() - interval '2 days', '{}')",
                         (user_id, a_id))
        _saved_ids.append((a_id, job))
r = client.get("/")
check("a saved capture with nothing sent wears the tag",
      '<span class="tag">saved</span>' in row_for(_saved_ids[0][0], "Kept For Later"))
check("once an application is sent, it does not",
      '<span class="tag">saved</span>' not in row_for(_saved_ids[1][0], "Applied After Saving"))
with db.connect() as conn, conn.transaction():
    for a_id, job in _saved_ids:     # out of the way of every later count
        conn.execute("DELETE FROM events WHERE application_id = %s", (a_id,))
        conn.execute("DELETE FROM applications WHERE id = %s", (a_id,))
        conn.execute("DELETE FROM jobs WHERE id = %s", (job,))
check("the flag is grey, not a new hue (UI rule 1 reserves chroma for the wait)",
      # A .tag is grey by construction — it takes no --c token at all, unlike
      # a .badge, which is the status word and may.
      '<span class="tag">employer site</span>' in row_for(ext_app, "External Apply Role"))

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

print("edit application: a lead you haven't applied to edits with no applied date")
# 8 Oct 2026: the form refused any save without an applied date, so a lead
# made from a recruiter's request could not have its role or company set.
r = client.post("/applications/new", data={
    "company": "Lead Edit Co", "title": "unknown role", "platform": "linkedin",
    "started_by": "recruiter", "approach_date": "2026-08-03", "approach_channel": "phone",
    "after": "view"})
_lead = r.headers["location"].rsplit("/", 1)[1]
_lead_applied = lambda: db.connect().execute(  # noqa: E731
    "SELECT count(*) AS n FROM events WHERE application_id = %s::uuid AND type = 'applied'",
    (_lead,)).fetchone()["n"]
r = client.get(f"/applications/{_lead}/edit")
_date_input = r.text.split('name="applied_date"', 1)[1].split(">", 1)[0]
check("a lead's form asks for the applied date only if there is one",
      r.status_code == 200 and "Applied on, if you have" in r.text and "required" not in _date_input,
      _date_input)
r = client.get(f"/applications/{edit_app}/edit")
check("...while an application's form still requires it",
      "required" in r.text.split('name="applied_date"', 1)[1].split(">", 1)[0])
r = client.post(f"/applications/{_lead}/edit", data={
    "company": "Lead Edit Co", "title": "Platform Engineer", "platform": "linkedin",
    "url": "https://www.linkedin.com/jobs/view/5544332299/", "applied_date": "",
    "external": "yes"})
with db.connect() as conn:
    _lj = conn.execute(
        "SELECT j.title_canonical, p.platform_job_id FROM applications a JOIN jobs j ON j.id = a.job_id "
        "JOIN postings p ON p.id = a.applied_via_posting_id WHERE a.id = %s::uuid", (_lead,)).fetchone()
check("saving a lead with no applied date keeps it unapplied, and the rest of the form applies "
      "(the URL too: its parse no longer sits behind the date)",
      r.status_code == 303 and _lead_applied() == 0 and _lj["title_canonical"] == "Platform Engineer"
      and _lj["platform_job_id"] == "5544332299", (r.status_code, _lj))
r = client.post(f"/applications/{_lead}/edit", data={
    "company": "Lead Edit Co", "title": "Platform Engineer", "platform": "linkedin",
    "url": "https://www.linkedin.com/jobs/view/5544332299/", "applied_time": "09:30"})
check("a time with no date is refused", r.status_code == 400 and "needs its date" in r.text, r.status_code)
r = client.post(f"/applications/{_lead}/edit", data={
    "company": "Lead Edit Co", "title": "Platform Engineer", "platform": "linkedin",
    "url": "https://www.linkedin.com/jobs/view/5544332299/", "applied_date": "2026-08-05"})
check("...and a date, given later, files the application as before",
      r.status_code == 303 and _lead_applied() == 1, r.status_code)
r = client.post(f"/applications/{_lead}/edit", data={
    "company": "Lead Edit Co", "title": "Platform Engineer", "platform": "linkedin",
    "url": "https://www.linkedin.com/jobs/view/5544332299/", "applied_date": ""})
check("a record that has applied cannot blank its date here: the thread's delete un-files it",
      r.status_code == 400 and "delete that event" in r.text and _lead_applied() == 1, r.status_code)

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

# ...and the same as a BROWSER sends it. Every textarea is submitted with CRLF
# breaks (the HTML spec), while stored JDs use LF, so until 24 Sep 2026 saving
# the form with the JD untouched counted as a change: a duplicate extraction,
# a cleared embedding, and CRs written into the stored text.
_edit = {"company": "Edit Test Co", "title": "Senior Backend Engineer",
         "platform": "linkedin", "applied_date": "2026-05-12"}
client.post(f"/applications/{edit_app}/edit",
            data={**_edit, "jd_text": "Build data pipelines.\nGo and Postgres."})
with db.connect() as conn, conn.transaction():
    conn.execute("DELETE FROM job_queue WHERE type = 'extract_jd' "
                 "AND payload->>'posting_id' = %s", (str(jd_posting),))
client.post(f"/applications/{edit_app}/edit",
            data={**_edit, "jd_text": "Build data pipelines.\r\nGo and Postgres.\r\n"})
with db.connect() as conn:
    q = conn.execute(
        "SELECT count(*) AS n FROM job_queue WHERE type = 'extract_jd' "
        "AND payload->>'posting_id' = %s", (str(jd_posting),)).fetchone()["n"]
    check("the same jd with a browser's CRLF breaks is not a change", q == 0, q)
    p = conn.execute("SELECT jd_text FROM postings WHERE id = %s", (jd_posting,)).fetchone()
    check("...and is stored with LF breaks", p["jd_text"] == "Build data pipelines.\nGo and Postgres.",
          p["jd_text"])

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
                               classification, extraction, matched_application_id, triage_state,
                               body_text)
           VALUES (%s, 'gm-refile-test', 'no-reply@ashbyhq.com', 'Thanks for applying', now(),
                   'confirmation', %s, %s, 'auto_matched',
                   'Thanks for applying to WrongCo. We will be in touch.')
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

# The email's own text is readable from the page it is filed under — the same
# disclosure triage offers — so a misfiling can be judged without leaving the
# thread. Hidden entirely, not shown empty, when there is no body to read.
r = client.get(f"/applications/{wrong_app}")
check("the detail page offers to read a linked email",
      "Read the email" in r.text and "We will be in touch." in r.text, r.status_code)

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
        "VALUES (%s, %s, 'applied', 'manual', now() - interval '12 days', '{}')",
        (user_id, stale_app))
    # Since 7 Oct 2026 an unanswered application is a ROW here only with
    # someone to write to (analytics.queue): the recruiter on record.
    conn.execute(
        "INSERT INTO contacts (user_id, job_id, name, url, source) "
        "VALUES (%s, %s, 'Jane Quiet', 'https://www.linkedin.com/in/jane-quiet', 'manual')",
        (user_id, stale_job))

r = client.get("/")
check("the list no longer carries the queue at all",
      r.status_code == 200 and "Needs follow-up" not in r.text
      and 'class="card fu"' not in r.text
      and 'value="follow_up_sent"' not in r.text, r.status_code)
check("it carries a counted link to them instead",
      'href="/follow-ups"' in r.text and "to make" in r.text, r.status_code)

r = client.get("/follow-ups")
check("the queue page lists the stale thread with its wait length, as a nudge naming the recruiter",
      r.status_code == 200 and "Your move" in r.text and "Worth a nudge" in r.text
      and "quietcorp" in r.text and "12d" in r.text
      and 'href="https://www.linkedin.com/in/jane-quiet">Jane Quiet</a> on LinkedIn' in r.text,
      r.status_code)
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

print("follow-ups: an earlier application to a role applied to again")
# 24 Sep 2026: 15 of 150 queue rows were an older record of a role reposted and
# applied to again. The rule suggests (analytics.reapplications); the user
# confirms (mark_reapplied). Shapes from that day's measurement.
REPOST_JD = "We build payments rails. You will own the ledger service in Go and Postgres."


def _seed_app(conn, company, title, days_ago, jd=None):
    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) VALUES (%s, %s, %s) "
        "RETURNING id", (user_id, company, title)).fetchone()["id"]
    if jd is not None:
        conn.execute("INSERT INTO postings (user_id, job_id, platform, captured_via, jd_text) "
                     "VALUES (%s, %s, 'linkedin', 'extension', %s)", (user_id, job, jd))
    app_id = conn.execute("INSERT INTO applications (user_id, job_id) VALUES (%s, %s) "
                          "RETURNING id", (user_id, job)).fetchone()["id"]
    conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
                 "VALUES (%s, %s, 'applied', 'manual', now() - make_interval(days => %s), '{}')",
                 (user_id, app_id, days_ago))
    return app_id


with db.connect() as conn, conn.transaction():
    # One role applied to three times: each older one names the NEXT, a chain.
    rp_1 = _seed_app(conn, "repostco", "Ledger Engineer", 40, REPOST_JD)
    rp_2 = _seed_app(conn, "repostco", "Ledger Engineer", 20, REPOST_JD)
    rp_3 = _seed_app(conn, "repostco", "Ledger Engineer", 5, REPOST_JD)
    # Same company, byte-identical title, a DIFFERENT job description: two
    # roles (the real case was answered separately on each).
    tw_1 = _seed_app(conn, "twinroles", "Staff Software Engineer", 40,
                     "Robotics perception team, C++ and CUDA on embedded boards.")
    _seed_app(conn, "twinroles", "Staff Software Engineer", 5,
              "Billing platform, Kotlin services and a Kafka event pipeline.")
    # Two roles at a studio whose titles share a suffix (similarity ~0.7).
    st_1 = _seed_app(conn, "studioco", "Agentic AI Engineer (Studio Portfolio Company)", 40)
    _seed_app(conn, "studioco", "Senior AI Engineer (Studio Portfolio Company)", 5)
    # Two records that know neither title: the placeholder never matches.
    from pipeline.ingest import UNKNOWN_TITLE
    uk_1 = _seed_app(conn, "blankco", UNKNOWN_TITLE, 40)
    _seed_app(conn, "blankco", UNKNOWN_TITLE, 5)
    applied_2 = conn.execute("SELECT occurred_at FROM events WHERE application_id = %s "
                             "AND type = 'applied'", (rp_2,)).fetchone()["occurred_at"]

with db.connect() as conn:
    again = analytics.reapplications(conn, user_id)
check("the oldest names the NEXT application, not the newest",
      again.get(rp_1, {}).get("id") == rp_2, again.get(rp_1))
check("the middle one names the newest", again.get(rp_2, {}).get("id") == rp_3, again.get(rp_2))
check("same title, different job description: not suggested", tw_1 not in again)
check("titles alike only by a shared suffix: not suggested", st_1 not in again)
check("the unknown-role placeholder never matches", uk_1 not in again)

r = client.get("/follow-ups")
row = r.text.split(f'href="/applications/{rp_1}"')[1].split('class="fu-row"')[0]
check("the queue marks the row, naming the later application with a link",
      "You applied again on" in row and f'href="/applications/{rp_2}"' in row
      and f'value="{rp_2}"' in row, row[:400])
check("the lede counts the suggestions", "look like an earlier application" in r.text)
with db.connect() as conn:
    _tw_q = analytics.queue(conn, user_id)
check("the two-roles row carries no suggestion — so with nobody to write to it is a count here, "
      "not a row (7 Oct 2026)",
      tw_1 not in {x["id"] for x in _tw_q["again"]} and f'href="/applications/{tw_1}"' not in r.text
      and tw_1 in {x["id"] for x in _tw_q["quiet"] + _tw_q["waiting"]})

r = client.post(f"/applications/{rp_1}/reapplied",
                data={"later_id": str(rp_2), "redirect_to": "/follow-ups"})
check("confirming returns to the queue", r.status_code == 303
      and r.headers["location"] == "/follow-ups", r.headers.get("location"))
with db.connect() as conn:
    ev = conn.execute("SELECT id, type, source, occurred_at, payload FROM events "
                      "WHERE application_id = %s AND type = 'withdrawn'", (rp_1,)).fetchone()
    check("filed as a manual withdrawal linked to the later application",
          ev and ev["source"] == "manual" and ev["payload"] == {"superseded_by": str(rp_2)}, ev)
    check("dated when the later application went in — the day this wait ended",
          ev["occurred_at"] == applied_2, (ev["occurred_at"], applied_2))
    check("the record reads withdrawn", conn.execute(
        "SELECT status FROM application_status WHERE application_id = %s",
        (rp_1,)).fetchone()["status"] == "withdrawn")
check("it leaves the queue", f'href="/applications/{rp_1}"' not in client.get("/follow-ups").text)
r = client.get(f"/applications/{rp_1}")
check("its timeline says you applied again, not that you withdrew, with the link",
      "You applied again" in r.text and "You withdrew" not in r.text
      and f'href="/applications/{rp_2}">see the later application' in r.text, r.status_code)

r = client.post(f"/applications/{rp_2}/reapplied", data={"later_id": str(rp_1)})
check("an EARLIER application is refused as the later one",
      r.status_code == 303 and "event_error" in r.headers["location"], r.headers.get("location"))
r = client.post(f"/applications/{rp_1}/reapplied", data={"later_id": str(rp_3)})
check("an application already closed is refused",
      r.status_code == 303 and "event_error" in r.headers["location"], r.headers.get("location"))
check("a later id that isn't an application is a 404",
      client.post(f"/applications/{rp_2}/reapplied", data={"later_id": "nope"}).status_code == 404)
with db.connect() as conn:
    check("...and none of the refusals wrote anything", conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id IN (%s, %s) "
        "AND type = 'withdrawn'", (rp_1, rp_2)).fetchone()["n"] == 1)

# Editing the close keeps its link — the form rebuilds the payload from fields.
client.post(f"/applications/{rp_1}/events/{ev['id']}/edit",
            data={"type": "withdrawn", "note": "reposted in Aug",
                  "occurred_on": applied_2.date().isoformat()})
with db.connect() as conn:
    p = conn.execute("SELECT payload FROM events WHERE id = %s", (ev["id"],)).fetchone()["payload"]
    check("an edited close keeps superseded_by beside the new note",
          p == {"note": "reposted in Aug", "superseded_by": str(rp_2)}, p)
# Undo is deleting the event: the row comes back to the queue.
client.post(f"/applications/{rp_1}/events/{ev['id']}/delete")
check("deleting the close puts the row back in the queue",
      f'href="/applications/{rp_1}"' in client.get("/follow-ups").text)

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

with db.connect() as conn:
    _wa_q = analytics.queue(conn, user_id)
check("before: the untouched thread is in the follow-up queue — since 7 Oct 2026 as a count "
      "(nobody to write to), never a row",
      wa_app in {x["id"] for x in _wa_q["waiting"] + _wa_q["quiet"]}
      and "sponsorless" not in client.get("/follow-ups").text)

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

with db.connect() as conn:
    _wa_q = analytics.queue(conn, user_id)
check("the row leaves the follow-up queue, because it is genuinely answered now",
      wa_app not in {x["id"] for x in _wa_q["waiting"] + _wa_q["quiet"] + _wa_q["nudge"]})

r = client.get(f"/applications/{wa_app}")
check("the timeline shows the reason as the selected why, and the channel it came through",
      "selected>visa / sponsorship" in r.text and "WhatsApp" in r.text, r.status_code)

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
# Refused outright until 24 Sep 2026 ("invariant #9 reserves it for triage").
# A human may file it now, but only as the START of the thread: this record was
# applied 20 days ago and a blank date means today, so it is refused, with a
# banner rather than a 400, and the record does not move.
r = client.post(f"/applications/{wa_app}/events", data={"type": "recruiter_outreach"})
with db.connect() as conn:
    _o = conn.execute("SELECT origin FROM applications WHERE id = %s", (wa_app,)).fetchone()
check("recruiter_outreach is accepted only as the thread's start — dated after the "
      "application, it is refused and the record stays put",
      r.status_code == 303 and "event_error" in r.headers["location"]
      and _o["origin"] == "applied", (r.status_code, r.headers.get("location"), _o))

print("rejection reasons: any rejected event, whatever its source")
# Northwind's rejection came by email (test_integration seeded it through the
# matcher), so the edit route refuses it on principle — and until 9 Sep 2026
# that left no way to say WHY it closed. The reason is the user's annotation,
# not the email's claim, so it has its own narrow door. A third rejected
# application with no reason at all keeps the `unrecorded` assertions honest
# whatever else the seed holds.
with db.connect() as conn, conn.transaction():
    nw_rej = conn.execute(
        "SELECT id, type, source, occurred_at, payload FROM events "
        "WHERE application_id = %s AND type = 'rejected'", (northwind_app,)).fetchone()
    nw_applied = conn.execute(
        "SELECT id FROM events WHERE application_id = %s AND type = 'applied' LIMIT 1",
        (northwind_app,)).fetchone()["id"]
    ut_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'untagged co', 'Data Engineer') RETURNING id", (user_id,)).fetchone()["id"]
    ut_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, ut_job)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now() - interval '30 days', '{}'), "
        "       (%s, %s, 'rejected', 'email', now() - interval '10 days', '{}')",
        (user_id, ut_app, user_id, ut_app))
check("precondition: an email-sourced rejection with no reason",
      nw_rej["source"] == "email" and "reason" not in nw_rej["payload"], nw_rej)
r = client.get(f"/applications/{northwind_app}/events/{nw_rej['id']}/edit")
check("the edit route still refuses an emailed event", r.status_code == 404, r.status_code)
r = client.get(f"/applications/{northwind_app}")
check("the timeline offers the why-select on it, reading not recorded",
      f"/events/{nw_rej['id']}/reason" in r.text and "Why? Not recorded" in r.text,
      r.status_code)

r = client.get("/?status=rejected&reason=unrecorded")
check("before: the untagged rejections are in the unrecorded queue",
      r.status_code == 200 and f'href="/applications/{northwind_app}"' in r.text
      and "untagged co" in r.text, r.status_code)
check("and the tagged one is not — its reason sits on one of two rejected events, "
      "which is enough: an application wears the newest reason it has",
      "sponsorless" not in r.text)

r = client.post(f"/applications/{northwind_app}/events/{nw_rej['id']}/reason",
                data={"reason": "visa"})
check("tagging an emailed rejection redirects to the thread", r.status_code == 303, r.status_code)
with db.connect() as conn:
    after = conn.execute(
        "SELECT type, source, occurred_at, payload FROM events WHERE id = %s",
        (nw_rej["id"],)).fetchone()
check("only the reason changed — type, source and date are still the email's",
      after["payload"] == {**nw_rej["payload"], "reason": "visa"}
      and after["type"] == "rejected" and after["source"] == "email"
      and after["occurred_at"] == nw_rej["occurred_at"], after)
r = client.get(f"/applications/{northwind_app}")
check("the timeline shows it as the selected reason",
      "selected>visa / sponsorship" in r.text, r.status_code)

r = client.get("/")
nw_row = r.text.split(f'href="/applications/{northwind_app}"')[1].split("</a>")[0]
check("the list row wears the reason in grey next to the status",
      ">rejected</span>" in nw_row and ">visa</span>" in nw_row, nw_row[-400:])
ut_row = r.text.split(f'href="/applications/{ut_app}"')[1].split("</a>")[0]
check("an untagged rejection wears nothing extra",
      ">rejected</span>" in ut_row and ">visa</span>" not in ut_row
      and "not recorded" not in ut_row, ut_row[-400:])

r = client.get("/?reason=visa")
check("a reason filter is a rejected filter: tagged rows only, funnel marked filtered",
      r.status_code == 200 and 'class="funnel filtered"' in r.text
      and f'href="/applications/{northwind_app}"' in r.text and "sponsorless" in r.text
      and "untagged co" not in r.text, r.status_code)
chips = r.text.split("why it closed")[1].split("</div>")[0]
check("the why-chips unfold under the legend with this reason active",
      'class="active"' in chips and "visa / sponsorship</a>" in chips, chips)
check("the unrecorded bucket is a chip too, last, linking to its queue",
      "reason=unrecorded" in chips and chips.rstrip().endswith("not recorded</a>")
      and chips.index("visa / sponsorship") < chips.index("not recorded"), chips)
funnel = r.text.split('class="funnel')[1].split("</div>")[0]
check("the funnel's own links drop the reason — it belongs to rejected",
      "reason=" not in funnel, funnel[:300])
r = client.get("/?reason=visa&q=zz")
# `&amp;` — the macro's output is autoescaped like any other expression.
check("the search's Clear link carries it, like every other filter",
      re.search(r'href="/\?status=rejected&(amp;)?reason=visa"', r.text) is not None,
      r.status_code)
check("and the search form re-submits it",
      '<input type="hidden" name="reason" value="visa">' in r.text)

r = client.get("/?status=rejected&reason=unrecorded")
check("after tagging, the queue no longer lists the tagged one",
      f'href="/applications/{northwind_app}"' not in r.text and "untagged co" in r.text,
      r.status_code)
r = client.get("/?reason=carrier-pigeon")
check("an unknown reason is ignored, not an error — the unfiltered list",
      r.status_code == 200 and "untagged co" in r.text
      and 'class="funnel filtered"' not in r.text, r.status_code)

r = client.get("/analytics")
tbl = r.text.split("Why it closed")[1].split("</table>")[0]
check("analytics counts the reasons, each linking to the list filtered by it",
      r.status_code == 200
      and 'href="/?status=rejected&amp;reason=visa">visa / sponsorship</a>' in tbl
      and 'href="/?status=rejected&amp;reason=unrecorded">not recorded</a>' in tbl, tbl)
check("the unrecorded row is last however large it is",
      tbl.index("not recorded") > tbl.rindex("visa / sponsorship"), tbl)

r = client.post(f"/applications/{northwind_app}/events/{nw_rej['id']}/reason",
                data={"reason": "telepathy"})
check("an unknown reason is refused", r.status_code == 400, r.status_code)
r = client.post(f"/applications/{northwind_app}/events/{nw_applied}/reason",
                data={"reason": "visa"})
check("a reason on anything but a rejection is 404, like an ineligible edit",
      r.status_code == 404, r.status_code)
r = client.post(f"/applications/{wa_app}/events/{nw_rej['id']}/reason",
                data={"reason": "visa"})
check("and so is another application's event", r.status_code == 404, r.status_code)
r = client.post(f"/applications/{northwind_app}/events/{nw_rej['id']}/reason",
                data={"reason": ""})
with db.connect() as conn:
    p = conn.execute("SELECT payload FROM events WHERE id = %s",
                     (nw_rej["id"],)).fetchone()["payload"]
check("a blank clears it — the key goes, nothing else in the payload moves",
      r.status_code == 303 and p == nw_rej["payload"], p)
check("the select then reads not recorded again",
      "Why? Not recorded" in client.get(f"/applications/{northwind_app}").text)
client.post(f"/applications/{northwind_app}/events/{nw_rej['id']}/reason",
            data={"reason": "visa"})

print("a reason the email stated: the email's until the user says otherwise")
# matcher._append_event files it when email_classifier.rejection_reason finds
# one (tests/test_integration.py path 3j); written directly here, as that
# payload. Its own record, removed at the end: the sections below count
# rejected applications by bucket.
with db.connect() as conn:
    st_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'stated co', 'Platform Engineer') RETURNING id", (user_id,)).fetchone()["id"]
    st_app = conn.execute("INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
                          (user_id, st_job)).fetchone()["id"]
    conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
                 "VALUES (%s, %s, 'applied', 'manual', now() - interval '20 days', '{}')",
                 (user_id, st_app))
    st_rej = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'rejected', 'email', now() - interval '5 days', %s) RETURNING id",
        (user_id, st_app, Json({"reason": "visa", "reason_source": "email",
                                "reason_quote": "the team can not sponsor your EP"}))).fetchone()["id"]


def st_payload():
    with db.connect() as conn:
        return conn.execute("SELECT payload FROM events WHERE id = %s", (st_rej,)).fetchone()["payload"]


r = client.get(f"/applications/{st_app}")
check("the timeline selects the email's reason and says it is the email's, in its words",
      "selected>visa / sponsorship" in r.text
      and "the email says <q>the team can not sponsor your EP</q>" in r.text, r.status_code)
st_row = client.get("/").text.split(f'href="/applications/{st_app}"')[1].split("</a>")[0]
check("the row's reason tag carries the email's sentence in its title",
      ">visa</span>" in st_row and "The email: the team can not sponsor your EP" in st_row, st_row[-400:])
client.post(f"/applications/{st_app}/events/{st_rej}/reason", data={"reason": "visa"})
check("saving the same reason leaves it the email's, quote and all",
      st_payload() == {"reason": "visa", "reason_source": "email",
                       "reason_quote": "the team can not sponsor your EP"}, st_payload())
client.post(f"/applications/{st_app}/events/{st_rej}/reason", data={"reason": "salary"})
check("picking another makes it the user's: the source and the quote go",
      st_payload() == {"reason": "salary"}, st_payload())
check("...and the page stops quoting the email for it",
      "the email says" not in client.get(f"/applications/{st_app}").text)
client.post(f"/applications/{st_app}/events/{st_rej}/reason", data={"reason": ""})
check("clearing drops all three keys", st_payload() == {}, st_payload())
with db.connect() as conn:
    conn.execute("DELETE FROM events WHERE application_id = %s", (st_app,))
    conn.execute("DELETE FROM applications WHERE id = %s", (st_app,))
    conn.execute("DELETE FROM jobs WHERE id = %s", (st_job,))

print("how it ended: a derived partition beside why it closed")
# The stage is on the timeline already, so this breakdown is complete without
# tagging anything: one bucket per rejected application — visa first (the
# reason wins over the stage), then whether a human round ever happened. A
# fourth rejected application, closed by hand after an interview, gives the
# after-a-round bucket a row; untagged co (applied, then a form letter) is the
# without-a-round one; Northwind, tagged visa above, is the visa one.
with db.connect() as conn, conn.transaction():
    rt_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'roundtrip co', 'Platform Engineer') RETURNING id",
        (user_id,)).fetchone()["id"]
    rt_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, rt_job)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now() - interval '40 days', '{}'), "
        "       (%s, %s, 'interview_invite', 'manual', now() - interval '20 days', '{}'), "
        "       (%s, %s, 'rejected', 'manual', now() - interval '5 days', "
        "        '{\"reason\": \"role_closed\"}')",
        (user_id, rt_app, user_id, rt_app, user_id, rt_app))

print("how it ended: LinkedIn's automatic rejection is a screen, not a visa reason")
# Two applications LinkedIn rejected on its 72-hour must-have timer (25 Sep
# 2026, analytics.screen_sql): one whose form recorded that you need
# sponsorship, one whose form did not ask. The sponsored one is then TAGGED
# visa by hand — it must stay a screen, since the author asked for a form
# filter and a person's "visa" to stay apart. A third letter, a day later than
# the timer, is an ordinary rejection.
def _screened(conn, name, hours, answer=None):
    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, %s, 'Backend Engineer') RETURNING id", (user_id, name)).fetchone()["id"]
    app_ = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, job)).fetchone()["id"]
    sent_at = datetime(2026, 8, 3, 1, 12, tzinfo=timezone.utc)
    mail = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, received_at,
                               classification, extraction, triage_state, matched_application_id)
           VALUES (%s, %s, 'jobs-noreply@linkedin.com', 'Your application to Backend Engineer',
                   %s, 'rejection', %s, 'auto_matched', %s) RETURNING id""",
        (user_id, f"gm-screen-{name}", sent_at + timedelta(hours=hours),
         Json({"platform": "linkedin"}), app_)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', %s, '{\"external\": false}')",
        (user_id, app_, sent_at))
    rej = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, source_email_id) "
        "VALUES (%s, %s, 'rejected', 'email', %s, %s) RETURNING id",
        (user_id, app_, sent_at + timedelta(hours=hours), mail)).fetchone()["id"]
    if answer:
        conn.execute(
            "INSERT INTO application_answers (user_id, application_id, question, question_norm, answer) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, app_, answer[0], answer[0].lower().rstrip("?"), answer[1]))
    return app_, rej


with db.connect() as conn, conn.transaction():
    sp_app, sp_rej = _screened(conn, "screened sponsor co", 72.01,
                               ("Will you now or in the future require sponsorship for employment visa status", "Yes"))
    fs_app, _ = _screened(conn, "screened form co", 72.02,
                          ("How many years of work experience do you have with Python", "3"))
    late_app, _ = _screened(conn, "late letter co", 96,
                            ("Will you now or in the future require sponsorship for employment visa status", "Yes"))
r = client.post(f"/applications/{sp_app}/events/{sp_rej}/reason", data={"reason": "visa"})
check("the sponsored screen is tagged visa by hand", r.status_code == 303, r.status_code)
r = client.get("/?how=sponsorship_screen")
check("a sponsorship screen: LinkedIn's timer on a form that recorded the need — "
      "and it stays one after a visa tag",
      "screened sponsor co" in r.text and "screened form co" not in r.text
      and "late letter co" not in r.text, r.status_code)
r = client.get("/?how=form_screen")
check("a form screen: the same timer, no sponsorship on record",
      "screened form co" in r.text and "screened sponsor co" not in r.text, r.status_code)
r = client.get("/?how=visa")
check("so visa counts only what a person said, never a screen",
      "screened sponsor co" not in r.text, r.status_code)
r = client.get("/?how=no_round")
check("a letter a day off the timer is an ordinary rejection",
      "late letter co" in r.text and "screened form co" not in r.text, r.status_code)
r = client.get("/?status=rejected&q=screened")
check("each screened row wears its screen, beside (not instead of) the reason",
      ">sponsorship screen</span>" in r.text and ">form screen</span>" in r.text
      and ">visa</span>" in r.text, r.status_code)

print("how it ended: the sponsorship rule is one rule in Python and SQL")
import json as _json                                                   # noqa: E402
from pipeline import answers as _answers                               # noqa: E402
_cases = _json.load(open(Path(__file__).parent / "sponsorship_answers.json",
                         encoding="utf-8"))["cases"]
with db.connect() as conn:
    _got = conn.execute(
        "SELECT " + _answers.declares_sponsorship_sql("v.q", "v.a") + " AS needs "
        "FROM unnest(%s::text[], %s::text[]) WITH ORDINALITY AS v(q, a, i) ORDER BY v.i",
        ([c["q"] for c in _cases], [c["a"] for c in _cases])).fetchall()
_bad = [(c["q"][:50], c["a"][:20]) for c, g in zip(_cases, _got) if g["needs"] != c["needs"]]
check(f"the SQL agrees with every case in sponsorship_answers.json ({len(_cases)})",
      len(_got) == len(_cases) and not _bad, _bad)

print("email apply: the rule is one rule in Python and SQL")
from pipeline import email_apply as _email_apply                      # noqa: E402
_ea_cases = _json.load(open(Path(__file__).parent / "email_apply.json",
                            encoding="utf-8"))["cases"]
with db.connect() as conn:
    _ea_got = conn.execute(
        "SELECT " + _email_apply.asks_by_email_sql("v.jd") + " AS owed "
        "FROM unnest(%s::text[]) WITH ORDINALITY AS v(jd, i) ORDER BY v.i",
        ([c["jd"] for c in _ea_cases],)).fetchall()
_ea_bad = [c["jd"][:50] for c, g in zip(_ea_cases, _ea_got) if g["owed"] is not c["owed"]]
check(f"the SQL agrees with every case in email_apply.json ({len(_ea_cases)})",
      len(_ea_got) == len(_ea_cases) and not _ea_bad, _ea_bad)

r = client.get("/")
# The closed group's breakdown (9 Oct 2026: the legend is two groups, and the
# rejected entry's buckets hang under the closed one).
legend = r.text.split('<div class="lg closed">')[1].split('<form class="bar')[0]
check("the legend's rejected entry carries every bucket at rest, each a filter",
      all(f"how={k}" in legend for k in web._HOW_FILTERS)
      and "after a round" in legend and "without a round" in legend, legend)
check("a chip says what closed them — the hand-filed one here",
      "filed by hand" in legend, legend)
check("the two screens are one entry, screened, with its two parts beside it",
      re.search(r'how=screen"[^>]*>(\d+) screened</a> \(<a[^>]*how=sponsorship_screen"[^>]*>\d+ sponsorship</a>'
                r' · <a[^>]*how=form_screen"[^>]*>\d+ form</a>\)', legend) is not None, legend)
_segs = re.findall(r'status=(\w+)"', r.text.split('class="funnel')[1].split("</div>")[0])
check("the closed group holds exactly the bar's closes, so withdrawn sits beside rejected",
      set(re.findall(r'status=(\w+)"', legend.split('class="sub"')[0]))
      == {k for k in _segs if k in web.trace.TERMINAL} and "rejected" in _segs, (_segs, legend[:400]))
import re as _re_how
for _k in [*web._HOW_FILTERS, web._HOW_SCREEN]:
    _m = _re_how.search(rf'how={_k}"[^>]*>(\d+) ', legend)
    _n = int(_m.group(1))
    _rows = client.get(f"/?how={_k}").text.count('<a class="tl" href="/applications/')
    check(f"the {_k} chip's number is the number of rows it shows ({_n})",
          _n == _rows and _n >= 1, (_n, _rows))
r = client.get("/?how=after_round")
check("after a round: the interviewed one — not the form letter, not the visa one",
      'class="funnel filtered"' in r.text and "roundtrip co" in r.text
      and "untagged co" not in r.text
      and f'href="/applications/{northwind_app}"' not in r.text, r.status_code)
r = client.get("/?how=visa")
check("visa: the tagged one, whatever its stage — the reason wins over the round",
      f'href="/applications/{northwind_app}"' in r.text and "roundtrip co" not in r.text,
      r.status_code)
r = client.get("/?how=no_round")
check("without a round: the form letter only",
      "untagged co" in r.text and "roundtrip co" not in r.text
      and f'href="/applications/{northwind_app}"' not in r.text, r.status_code)
r = client.get("/?how=no_round&reason=unrecorded")
check("how and why combine — the untagged form letters are the tagging queue's bulk",
      r.status_code == 200 and "untagged co" in r.text and "roundtrip co" not in r.text,
      r.status_code)
why = r.text.split("why it closed")[1].split("</div>")[0]
check("the why-chips carry the how filter", "how=no_round" in why, why[:400])
sub = r.text.split('<div class="lg closed">')[1].split('class="sub"')[1].split("</div>")[0]
check("and the how chips carry the reason, with this one active",
      "reason=unrecorded" in sub and 'class="active"' in sub, sub)
funnel = r.text.split('class="funnel')[1].split("</div>")[0]
check("the funnel's own links drop it — it belongs to rejected", "how=" not in funnel, funnel[:300])
r = client.get("/?how=visa&q=zz")
check("the search's Clear link carries it, like every other filter",
      re.search(r'href="/\?status=rejected&(amp;)?how=visa"', r.text) is not None,
      r.status_code)
check("and the search form re-submits it", '<input type="hidden" name="how" value="visa">' in r.text)
r = client.get("/?how=teleport")
check("an unknown how is ignored, not an error — the unfiltered list",
      r.status_code == 200 and "roundtrip co" in r.text
      and 'class="funnel filtered"' not in r.text, r.status_code)

print("the status bar: each segment painted with its own rows (9 Oct 2026)")
# Painted by status token, the bar had drawn 45 real records blue where the
# rows' waits drew 7. Now a segment's bands must be its rows, colour for
# colour: the rail's live numeral, each wait's heat, the closes by status.
from collections import Counter as _Counter                             # noqa: E402
_BAND = re.compile(r'class="t-(\w+)" style="--n:(\d+)(?:;--heat:(\d+)%)?"')
_ROW = re.compile(r'<a class="tl" href="/applications/[^"]+" title="[^"]*" style="--heat:(\d+)%">'
                  r'.*?<b class="d( live)?( dash)?">.*?<span class="badge"[^>]*>([^<]+)</span>', re.S)
for _path in ("/", "/inbound"):
    r = client.get(_path)
    if _path != "/" and '<div class="funnel' not in r.text:
        continue                     # no approaches in the suite's data yet
    _drawn, _order_ok = {}, True
    for _label, _seg in re.findall(r'<a class="seg[^"]*"[^>]*title="\d+ ([a-z]+)[^"]*"[^>]*>(.*?)</a>',
                                   r.text.split('<div class="funnel')[1].split("</div>")[0], re.S):
        _seq = [(_t, int(_h or 0), int(_n)) for _t, _n, _h in _BAND.findall(_seg)]
        _order_ok &= _seq == sorted(_seq, key=lambda b: ({"live": 0, "wait": 1}.get(b[0], 2), b[1]))
        _drawn[_label] = _Counter()
        for _t, _h, _n in _seq:
            _drawn[_label][(_t, _h)] += _n
    _rows_by = {}
    for _heat, _live, _dash, _st in _ROW.findall(r.text):
        _tone = _st if _st in ("rejected", "withdrawn", "offer") else "live" if _live else "wait"
        _rows_by.setdefault(_st, _Counter())[(_tone, int(_heat) if _tone == "wait" else 0)] += 1
    check(f"{_path}: every segment is its rows, colour for colour — the live, each wait's heat, "
          f"the closes",
          _drawn and _drawn == _rows_by, (_drawn, _rows_by))
    check(f"{_path}: shortest silence first in every segment — the live, then the waits as "
          f"their heat rises", _order_ok, _drawn)

print("the applied entry: how long it has waited, each span a filter (9 Oct 2026)")
r = client.get("/")
_open_sub = r.text.split('<div class="lg">')[1].split('<div class="lg closed">')[0]
_spans = re.findall(r'href="/\?status=applied&(?:amp;)?wait=(\w+)"[^>]*>(\d+) ', _open_sub)
_applied_n = int(re.search(r'title="(\d+) applied', r.text.split('class="funnel')[1]).group(1))
check("the spans sum to the applied entry, in their fixed order",
      _spans and sum(int(n) for _, n in _spans) == _applied_n
      and [k for k, _ in _spans] == [k for k in analytics.WAIT_SPANS if k in dict(_spans)],
      (_spans, _applied_n))
for _k, _n in _spans:
    _rows = client.get(f"/?wait={_k}").text.count('<a class="tl" href="/applications/')
    check(f"the {_k} span's number is the number of rows it shows ({_n})", int(_n) == _rows,
          (_n, _rows))
_k0 = _spans[0][0]
r = client.get(f"/?wait={_k0}")
check("a wait filter is an applied filter: the funnel marked filtered, applied active",
      'class="funnel filtered"' in r.text
      and re.search(r'class="seg active"\s+href="[^"]*"\s+style="[^"]*"\s+title="\d+ applied', r.text),
      r.status_code)
funnel = r.text.split('class="funnel')[1].split("</div>")[0]
check("the funnel's own links drop it — it belongs to applied", "wait=" not in funnel, funnel[:300])
r = client.get(f"/?wait={_k0}&q=zz")
check("the search's Clear link and its form carry it",
      re.search(rf'href="/\?status=applied&(amp;)?wait={_k0}"', r.text) is not None
      and f'<input type="hidden" name="wait" value="{_k0}">' in r.text, r.status_code)
r = client.get(f"/?wait={_k0}&how=visa")
check("a rejected filter wins over a wait: the two cannot both hold",
      'name="wait"' not in r.text and '<input type="hidden" name="how" value="visa">' in r.text,
      r.status_code)
r = client.get("/?wait=forever")
check("an unknown wait is ignored, not an error — the unfiltered list",
      r.status_code == 200 and 'class="funnel filtered"' not in r.text, r.status_code)

r = client.get("/analytics")
tbl = r.text.split("How it ended")[1].split("</table>")[0]
check("analytics counts the buckets in the same fixed order, each linking to its rows",
      'href="/?status=rejected&amp;how=after_round">after a round</a>' in tbl
      and 'href="/?status=rejected&amp;how=no_round">without a round</a>' in tbl
      and 'href="/?status=rejected&amp;how=sponsorship_screen">sponsorship screen</a>' in tbl
      and tbl.index("after a round") < tbl.index(">visa<") < tbl.index("sponsorship screen")
      < tbl.index("form screen") < tbl.index("without a round"), tbl)

print("analytics: the drawn page agrees with the SQL the list counts by")
# /analytics is insights.report() over one fetch (25 Sep 2026); the list's
# lede and chips are analytics.summary() and rejection_ends(). Two pages must
# not count one thing two ways, so the report is held to the SQL here, on the
# real fixture database rather than on hand-built rows.
from pipeline import insights                                        # noqa: E402
with db.connect() as conn:
    apps_, events_ = analytics.facts(conn, user_id)
    rep = insights.report(apps_, events_, datetime.now(timezone.utc), None, 10,
                          status_word=web._display, how_words=web._HOW_FILTERS)
    summ = analytics.summary(conn, user_id, False)
    ends = {e["how"]: e["n"] for e in analytics.rejection_ends(conn, user_id)}
    funnel = {f["key"]: f["n"] for f in web._funnel(conn, user_id, None)}
check("sent and heard back are the list lede's applications and replies",
      rep["head"]["sent"] == summ["applied"] and rep["head"]["heard"] == summ["responded"],
      (rep["head"], summ))
flow_nodes = {n["id"]: n["value"] for n in rep["flow"]["nodes"]}
check("the flow's statuses are the funnel's counts, both pages together",
      {k: v for k, v in flow_nodes.items() if k in web.FUNNEL_ORDER} == funnel,
      (flow_nodes, funnel))
check("the flow's rejection branches are rejection_ends' buckets",
      {k[4:]: v for k, v in flow_nodes.items() if k.startswith("how_")} == ends,
      (flow_nodes, ends))
r = client.get("/analytics")
check("every section renders on the fixture data",
      r.status_code == 200 and all(f'id="{s}"' in r.text for s in
                                   ("weeks", "stand", "answered", "ended", "seen")),
      r.status_code)
check("every square links its application and wears a tone",
      re.search(r'<a class="u t-(live|wait|rejected|offer|withdrawn)( r)?" style="--heat:\d+%"\s+'
                r'href="/applications/[0-9a-f-]{36}"', r.text) is not None)
check("a band of the flow opens the list filtered to its rows",
      re.search(r'<a href="/(inbound)?\?status=[a-z_]+"><path class="band', r.text) is not None)

print("what the JD says about visas: a grey tag on the row (jd_extract_v2)")
with db.connect() as conn, conn.transaction():
    nw_posting = conn.execute(
        "SELECT p.id FROM postings p JOIN applications a ON a.job_id = p.job_id "
        "WHERE a.id = %s ORDER BY p.captured_at DESC LIMIT 1", (northwind_app,)).fetchone()["id"]
    nw_x = conn.execute(
        "INSERT INTO extractions (user_id, posting_id, visa_signal, visa_notes, model, prompt_version) "
        "VALUES (%s, %s, 'no_sponsorship', 'Employer sponsorship (work pass) is not available.', "
        "'claude-sonnet-5', 'jd_extract_v2') RETURNING id", (user_id, nw_posting)).fetchone()["id"]
r = client.get("/?q=northwind")
check("the newest extraction's signal is a grey tag on the role line, its sentence the title, "
      "naming its speaker",
      re.search(r'<span class="tag"\s+title="The job description: Employer sponsorship \(work pass\) '
                r'is not available\.">JD: no sponsorship</span>', r.text) is not None, r.status_code)
r = client.get(f"/applications/{northwind_app}")
check("the detail page offers v2's vocabulary in its own words",
      '<option value="in_country">in-country only</option>' in r.text
      and '<option value="local_only">' not in r.text
      and "no sponsorship (keep)" in r.text, r.status_code)
r = client.post(f"/extractions/{nw_x}/verify",
                data={"application_id": str(northwind_app), "visa_signal": "in_country"})
with db.connect() as conn:
    now_signal = conn.execute("SELECT visa_signal FROM extractions WHERE id = %s", (nw_x,)).fetchone()
check("a v2 signal is accepted as a correction", r.status_code == 303
      and now_signal["visa_signal"] == "in_country", (r.status_code, now_signal))
r = client.post(f"/extractions/{nw_x}/verify",
                data={"application_id": str(northwind_app), "visa_signal": "telepathy_only"})
check("an unknown signal is refused", r.status_code == 400, r.status_code)

print("visa, at a glance: every cell opens exactly its rows")
# /analytics' matrix (form answer x what the JD says) and the list's
# visa/form filters are formatted from the same SQL; loop EVERY cell, so a
# new bucket is covered by this test the day it is added.
r = client.get("/analytics")
cells = re.findall(r'<a class="vm-n" href="/\?visa=(\w+)&amp;form=(\w+)"[^>]*>(\d+)</a>', r.text)
check("the matrix renders with cells to open", r.status_code == 200 and len(cells) >= 3, cells)
for visa, form, n in cells:
    rows_ = client.get(f"/?visa={visa}&form={form}").text.count('<a class="tl" href="/applications/')
    check(f"cell form={form} visa={visa}: {n} counted, {rows_} shown", int(n) == rows_, (n, rows_))
with db.connect() as conn:
    mine = conn.execute("SELECT count(*) AS n FROM applications WHERE origin <> 'inbound'").fetchone()["n"]
check("the cells account for every record on /", sum(int(n) for *_, n in cells) == mine,
      (sum(int(n) for *_, n in cells), mine))
r = client.get("/?visa=restricts&form=needs")
check("a filtered list names its filter in words, with a way out",
      "the JD restricts who may apply and you told the form you need sponsorship" in r.text
      and "Show every visa case" in r.text, r.status_code)
check("and the page keeps it: the search re-submits it, a status link carries it",
      'name="visa" value="restricts"' in r.text and 'name="form" value="needs"' in r.text
      and re.search(r'href="/\?status=\w+&amp;visa=restricts&amp;form=needs"', r.text) is not None,
      r.status_code)
r = client.get("/?q=screened+sponsor")
check("a form that recorded the need wears it, in your voice, quoting the question and the answer",
      re.search(r'title="The form asked “Will you now or in the future require sponsorship for '
                r'employment visa status” and you answered “Yes”">you: need sponsorship</span>',
                r.text) is not None, r.status_code)
# An answer stored as an option id (Workday's dropdowns, 9 Oct 2026) may be
# the declaration itself: the form's bucket says it cannot tell, and the tag
# says why instead of quoting the id. A readable "Yes" states no need.
_OPAQUE = "8c17e811c1be0168d7a23229d0001c9b"
with db.connect() as conn, conn.transaction():
    _ur, _ = _screened(conn, "unread answer co", 120, ("Are you legally authorized to work in Singapore?", _OPAQUE))
    _nn, _ = _screened(conn, "no need co", 120, ("Are you legally authorized to work in Singapore?", "Yes"))
r = client.get("/?q=unread+answer")
check("an answer stored as an option id is unread: its tag says so, and its title explains rather "
      "than quoting the id",
      ">you: answer unread</span>" in r.text and "option id, which the tracker cannot read" in r.text
      and _OPAQUE not in r.text, r.status_code)
check("a readable answer that states no need says exactly that",
      ">you: no need stated</span>" in client.get("/?q=no+need+co").text)
check("each opens under its own form filter, and not the other's",
      "unread answer co" in client.get("/?form=unread").text
      and "unread answer co" not in client.get("/?form=asked").text
      and "no need co" in client.get("/?form=asked").text
      and "no need co" not in client.get("/?form=unread").text)
for _id in (_ur, _nn):
    client.post(f"/applications/{_id}/delete")
r = client.get("/?visa=everything&form=psychic")
check("unknown visa/form values are ignored, not an error", r.status_code == 200
      and 'class="filter-note"' not in r.text, r.status_code)

print("form answers: detail page + answer bank")
# The real key function, never a copy of it: a hand-rolled key here is a row
# `answers.renorm()` would find stale.
from pipeline.answers import norm_question                           # noqa: E402
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
            (user_id, app_id, extra[0], norm_question(extra[0]), extra[1]))

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

# A worker that cannot reach the API stalled for four days in Sep 2026 with
# nothing on any page saying so. The band under the header is the fix; these
# pin the three things it must get right: silent when healthy, silent for a
# job in ordinary backoff, loud for stale work and for anything dead.
print("pipeline health: header band, settings section, requeue")
r = client.get("/")
check("healthy queue shows no stall band", 'class="stall"' not in r.text)
with db.connect() as conn, conn.transaction():
    uid = db.single_user_id(conn)
    stalled_email = conn.execute(
        "INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text, received_at) "
        "VALUES (%s, 'gm-stalled', 'x@example.com', 'stalled probe', 'body', now()) "
        "RETURNING id", (uid,)).fetchone()["id"]
    fresh = conn.execute(
        "INSERT INTO job_queue (user_id, type, payload, state, attempts, last_error, run_after) "
        "VALUES (%s, 'classify_email', %s, 'pending', 1, %s, now() + interval '30 seconds') "
        "RETURNING id",
        (uid, Json({"email_id": str(stalled_email)}),
         "Traceback (most recent call last):\n  ...\nValueError: transient")).fetchone()["id"]
r = client.get("/")
check("a job in fresh backoff is not a stall", 'class="stall"' not in r.text)
with db.connect() as conn, conn.transaction():
    conn.execute(
        "UPDATE job_queue SET created_at = now() - interval '2 hours', last_error = %s "
        "WHERE id = %s",
        ("Anthropic API 400: Your credit balance is too low to access the Anthropic API.",
         fresh))
r = client.get("/")
with db.connect() as conn:       # the count is whatever is unprocessed right now —
    q = db.queue_health(conn)    # earlier sections leave one deliberately unread
check("stale work raises the band with the count and the reason",
      'class="stall"' in r.text and q["waiting"] >= 1
      and f"{q['waiting']} email{'' if q['waiting'] == 1 else 's'} waiting" in r.text
      and "credit balance is too low" in r.text, (q, r.text[:400]))
check("band links to the pipeline section", 'href="/settings#pipeline"' in r.text)
check("band is on every page, not just the list",
      'class="stall"' in client.get("/answers").text)
with db.connect() as conn, conn.transaction():
    dead = conn.execute(
        "INSERT INTO job_queue (user_id, type, payload, state, attempts, last_error) "
        "VALUES (%s, 'extract_jd', %s, 'dead', 5, %s) RETURNING id",
        (uid, Json({}), "Traceback (most recent call last):\n  ...\nRuntimeError: it died")
    ).fetchone()["id"]
r = client.get("/settings")
check("settings lists the dead job by its last line",
      r.status_code == 200 and "1 job gave up" in r.text and "RuntimeError: it died" in r.text
      and "Run them again" in r.text, r.status_code)
check("settings names the newest failure", "credit balance is too low" in r.text)
r = client.post("/settings/queue/requeue")
with db.connect() as conn:
    row = conn.execute(
        "SELECT state, attempts, run_after <= now() AS runnable FROM job_queue WHERE id = %s",
        (dead,)).fetchone()
check("requeue puts the dead job back with a fresh budget",
      r.status_code == 200 and "1 job queued" in r.text and row["state"] == "pending"
      and row["attempts"] == 0 and row["runnable"], (r.status_code, row))
with db.connect() as conn, conn.transaction():      # leave the queue as we found it
    conn.execute("DELETE FROM job_queue WHERE id IN (%s, %s)", (fresh, dead))
    conn.execute("DELETE FROM emails WHERE id = %s", (stalled_email,))
check("band gone once the queue is clean", 'class="stall"' not in client.get("/").text)

print("inbound: a recruiter's approach filed by hand moves the record (24 Sep 2026)")
# The shape of the real case: the tracker heard of the thread only when the user
# emailed a resume, so it filed the record as theirs. The recruiter's WhatsApp
# message is then stated by hand. Dates are fixed at 12:00 UTC so their local
# date is the same in any timezone the test user might carry.
with db.connect() as conn, conn.transaction():
    _jid = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'approachco', 'AI Engineer') RETURNING id", (user_id,)).fetchone()["id"]
    appr_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, _jid)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'email', '2026-08-10 12:00+00', '{}')", (user_id, appr_app))


def _appr_state():
    with db.connect() as conn:
        origin = conn.execute("SELECT origin FROM applications WHERE id = %s",
                              (appr_app,)).fetchone()["origin"]
        evs = conn.execute(
            "SELECT id, occurred_at, payload FROM events WHERE application_id = %s "
            "AND type = 'recruiter_outreach' ORDER BY occurred_at", (appr_app,)).fetchall()
        applied = conn.execute(
            "SELECT occurred_at FROM events WHERE application_id = %s AND type = 'applied'",
            (appr_app,)).fetchone()["occurred_at"]
    return origin, evs, applied


r = client.post(f"/applications/{appr_app}/events", data={
    "type": "recruiter_outreach", "occurred_on": "2026-08-12", "channel": "whatsapp"})
origin, evs, _ = _appr_state()
check("an approach dated after the application is refused, with the reason",
      r.status_code == 303 and "event_error=" in r.headers["location"]
      and origin == "applied" and not evs, (r.headers.get("location"), origin, evs))
check("...and the refusal names the event for a later message",
      "reached out" in client.get(r.headers["location"]).text)
r = client.post(f"/applications/{appr_app}/events", data={
    "type": "recruiter_outreach", "occurred_on": ""})
origin, evs, _ = _appr_state()
check("a blank date means today, which is after the application, so it is refused too",
      "event_error=" in r.headers["location"] and origin == "applied" and not evs,
      r.headers.get("location"))

r = client.post(f"/applications/{appr_app}/events", data={
    "type": "recruiter_outreach", "occurred_on": "2026-08-10", "channel": "whatsapp",
    "note": "messaged about the role"})
origin, evs, applied_at = _appr_state()
check("filed for the day of the application, it lands", r.status_code == 303
      and r.headers["location"] == f"/applications/{appr_app}", r.headers.get("location"))
check("the record is now inbound", origin == "inbound", origin)
check("the approach carries its channel and what it moved the record from",
      len(evs) == 1 and evs[0]["payload"].get("channel") == "whatsapp"
      and evs[0]["payload"].get("origin_was") == "applied", evs)
check("and on the same day it sits strictly BEFORE the application on the timeline",
      evs[0]["occurred_at"] < applied_at, (evs[0]["occurred_at"], applied_at))
check("it left the record page for /inbound",
      ">approachco<" not in client.get("/").text and ">approachco<" in client.get("/inbound").text)
r = client.get(f"/applications/{appr_app}")
check("its page shows the approach, by hand, on WhatsApp, and lights Inbound",
      "Recruiter reached out" in r.text and "WhatsApp" in r.text
      and 'href="/inbound" class="active"' in r.text, r.status_code)
approach_id = evs[0]["id"]

r = client.post(f"/applications/{appr_app}/events", data={
    "type": "recruiter_outreach", "occurred_on": "2026-08-01"})
_, evs, _ = _appr_state()
check("a second hand-filed approach is refused: a thread has one start",
      "event_error=" in r.headers["location"] and len(evs) == 1, r.headers.get("location"))

r = client.post(f"/applications/{appr_app}/events/{approach_id}/edit", data={
    "type": "recruiter_outreach", "occurred_on": "2026-08-05", "channel": "whatsapp"})
origin, evs, _ = _appr_state()
check("editing its date keeps it an approach and keeps what it moved the record from",
      r.status_code == 303 and origin == "inbound"
      and evs[0]["payload"].get("origin_was") == "applied"
      and evs[0]["occurred_at"].date().isoformat() in ("2026-08-04", "2026-08-05"), evs)
r = client.post(f"/applications/{appr_app}/events/{approach_id}/edit", data={
    "type": "recruiter_outreach", "occurred_on": "2026-08-12"})
check("editing it to a date after the application is refused", r.status_code == 400
      and "comes before the application" in r.text, r.status_code)

r = client.post(f"/applications/{appr_app}/edit", data={
    "company": "approachco", "title": "AI Engineer", "platform": "other",
    "applied_date": "2026-08-01"})
check("the application can't be moved to before the approach either",
      r.status_code == 400 and "approached you first" in r.text, r.status_code)

r = client.post(f"/applications/{appr_app}/events/{approach_id}/edit", data={
    "type": "note", "occurred_on": "2026-08-05", "note": "not an approach after all"})
origin, evs, _ = _appr_state()
check("re-typing the approach as something else puts the record back",
      r.status_code == 303 and origin == "applied" and not evs, (origin, evs))
r = client.post(f"/applications/{appr_app}/events/{approach_id}/edit", data={
    "type": "recruiter_outreach", "occurred_on": "2026-08-05"})
origin, evs, _ = _appr_state()
check("and re-typing it back moves it again, remembering where it came from",
      origin == "inbound" and evs[0]["payload"].get("origin_was") == "applied", (origin, evs))

r = client.post(f"/applications/{appr_app}/events/{approach_id}/delete")
origin, evs, _ = _appr_state()
check("deleting the approach puts the record back where it was",
      r.status_code == 303 and origin == "applied" and not evs, (origin, evs))
check("...on the record page again", ">approachco<" in client.get("/").text)

print("inbound: manual entry, who started it")
r = client.post("/applications/new", data={
    "company": "Approach Manual Co", "title": "Data Engineer", "platform": "other",
    "started_by": "recruiter", "approach_date": "2026-08-03", "approach_channel": "phone",
    "applied_date": "2026-08-04", "after": "view"})
check("recruiter-started with an application redirects to the record", r.status_code == 303,
      r.text[:300])
_man = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    _row = conn.execute("SELECT origin FROM applications WHERE id = %s::uuid", (_man,)).fetchone()
    _evs = conn.execute(
        "SELECT type, source, occurred_at, payload FROM events WHERE application_id = %s::uuid "
        "ORDER BY occurred_at", (_man,)).fetchall()
check("it is inbound, and its thread starts with the approach, then the application",
      _row["origin"] == "inbound" and [e["type"] for e in _evs] == ["recruiter_outreach", "applied"]
      and _evs[0]["source"] == "manual" and _evs[0]["payload"].get("channel") == "phone"
      and _evs[0]["payload"].get("origin_was") == "applied", (_row, _evs))

r = client.post("/applications/new", data={
    "company": "Approach Lead Co", "title": "ML Engineer", "platform": "other",
    "started_by": "recruiter", "approach_date": "2026-08-06", "approach_channel": "whatsapp",
    "applied_date": "", "after": "view"})
check("recruiter-started with NO application is accepted: a lead", r.status_code == 303,
      r.text[:300])
_lead = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    _row = conn.execute(
        "SELECT a.origin, s.status FROM applications a JOIN application_status s "
        "ON s.application_id = a.id WHERE a.id = %s::uuid", (_lead,)).fetchone()
    _n = conn.execute("SELECT count(*) AS n FROM events WHERE application_id = %s::uuid "
                      "AND type = 'applied'", (_lead,)).fetchone()["n"]
check("it is an inbound lead awaiting a decision, with no applied event invented",
      _row["origin"] == "inbound" and _row["status"] == "interested" and _n == 0, (_row, _n))
r = client.get("/inbound")
check("and /inbound pins it with the other leads",
      r.text.index('<div class="tl-sep">Awaiting your call</div>') < r.text.index("Approach Lead Co")
      < r.text.index('<div class="tl-sep">Underway or closed</div>'))

r = client.post("/applications/new", data={
    "company": "Approach Late Co", "title": "Engineer", "platform": "other",
    "started_by": "recruiter", "approach_date": "2026-08-09", "applied_date": "2026-08-04"})
check("an approach after the application is refused on this form too",
      r.status_code == 400 and "dated after your application" in r.text, r.status_code)
r = client.post("/applications/new", data={
    "company": "Approach Undated Co", "title": "Engineer", "platform": "other",
    "started_by": "recruiter", "applied_date": "2026-08-04"})
check("recruiter-started needs the approach's date",
      r.status_code == 400 and "date the recruiter approached you" in r.text, r.status_code)
r = client.post("/applications/new", data={
    "company": "Approach Ignored Co", "title": "Engineer", "platform": "other",
    "started_by": "me", "approach_date": "2026-08-01", "approach_channel": "whatsapp",
    "applied_date": "2026-08-04", "after": "view"})
_ign = r.headers["location"].rsplit("/", 1)[1]
with db.connect() as conn:
    _row = conn.execute("SELECT origin FROM applications WHERE id = %s::uuid", (_ign,)).fetchone()
    _n = conn.execute("SELECT count(*) AS n FROM events WHERE application_id = %s::uuid "
                      "AND type = 'recruiter_outreach'", (_ign,)).fetchone()["n"]
check("under “I applied” stray approach fields are ignored, not filed",
      _row["origin"] == "applied" and _n == 0, (_row, _n))
r = client.post("/applications/new", data={
    "company": "Approach Blank Co", "title": "Engineer", "platform": "other",
    "started_by": "me", "applied_date": ""})
check("under “I applied” the applied date is still required",
      r.status_code == 400 and "valid applied date" in r.text, r.status_code)

print("inbound: adding one by hand from the page")
# /inbound's door onto the same form (28 Sep 2026), opened as an approach. The
# assertion that matters is the BLANK applied date: the form pre-fills it with
# today, and a lead saved as-is from that default would be an application.
r = client.get("/inbound")
check("/inbound offers “Add one by hand”, opened as an approach",
      '<a href="/applications/new?started_by=recruiter">Add one by hand</a>' in r.text,
      r.status_code)
check("...while the record's own link still opens it as an application",
      '<a href="/applications/new">Add one by hand</a>' in client.get("/").text)


def _date_input(html, name):
    """(value, max) of a date input — max is the form's own `today`."""
    m = re.search(rf'name="{name}" value="([^"]*)" max="([^"]*)"', html)
    return m.groups() if m else (None, None)


r = client.get("/applications/new?started_by=recruiter")
_appr_val, _today = _date_input(r.text, "approach_date")
_appl_val, _ = _date_input(r.text, "applied_date")
check("opened from /inbound the form is an approach: selected, dated today, Inbound lit",
      r.status_code == 200 and 'value="recruiter" selected' in r.text
      and _appr_val == _today and "Add a recruiter's approach" in r.text
      and 'href="/inbound" class="active"' in r.text and 'href="/" class="active"' not in r.text,
      (r.status_code, _appr_val, _today))
check("...and its applied date starts BLANK, so a lead saved as-is invents no application",
      _appl_val == "", _appl_val)

r = client.get("/applications/new")
_appr_val, _ = _date_input(r.text, "approach_date")
_appl_val, _today = _date_input(r.text, "applied_date")
check("opened plainly it is still an application: “I applied”, dated today, Applications lit",
      'value="me" selected' in r.text and _appl_val == _today and _appr_val == ""
      and "Add a past application" in r.text and 'href="/" class="active"' in r.text,
      (_appl_val, _appr_val))
r = client.get("/applications/new?started_by=bogus")
check("an unknown started_by opens it as an application",
      'value="me" selected' in r.text and "Add a past application" in r.text)

r = client.post("/applications/new", data={
    "company": "Approach Run Co", "title": "Platform Engineer", "platform": "linkedin",
    "started_by": "recruiter", "approach_date": "2026-08-07", "applied_date": "",
    "after": "another"})
_loc = r.headers.get("location", "")
check("“Save and add another” on an approach keeps the next form an approach, "
      "carrying the approach's date and NOT an applied one",
      r.status_code == 303 and "started_by=recruiter" in _loc
      and "approach_date=2026-08-07" in _loc and not re.search(r"[?&]date=", _loc), _loc)
r = client.get(_loc)
_appr_val, _ = _date_input(r.text, "approach_date")
_appl_val, _ = _date_input(r.text, "applied_date")
check("...which names what was added and opens as an approach again",
      "Approach Run Co" in r.text and 'value="recruiter" selected' in r.text
      and _appr_val == "2026-08-07" and _appl_val == "", (_appr_val, _appl_val))

print("inbound: closing an approach yourself (28 Sep 2026)")


def _new_lead(company, approach_date):
    r = client.post("/applications/new", data={
        "company": company, "title": "Platform Engineer", "platform": "other",
        "started_by": "recruiter", "approach_date": approach_date, "applied_date": "",
        "after": "view"})
    assert r.status_code == 303, r.text[:300]
    return r.headers["location"].rsplit("/", 1)[1]


def _closes(app_id):
    with db.connect() as conn:
        return conn.execute(
            "SELECT id, source, occurred_at, payload FROM events WHERE application_id = %s::uuid "
            "AND type = 'withdrawn' ORDER BY occurred_at", (app_id,)).fetchall()


def _state(app_id):
    with db.connect() as conn:
        s = conn.execute("SELECT s.status, a.user_id FROM application_status s "
                         "JOIN applications a ON a.id = s.application_id "
                         "WHERE s.application_id = %s::uuid", (app_id,)).fetchone()
        return s["status"], analytics.lead_count(conn, s["user_id"])


_dec = _new_lead("Close Decline Co", "2026-08-20")
_st, _leads0 = _state(_dec)
r = client.get(f"/applications/{_dec}")
check("an open approach offers the close: not for me, or they went quiet",
      f'action="/applications/{_dec}/close"' in r.text and 'value="decline"' in r.text
      and 'value="quiet"' in r.text and _st == "interested", r.status_code)
r = client.post(f"/applications/{_dec}/close", data={"action": "decline", "why": "experience"})
_c = _closes(_dec)
_st, _leads1 = _state(_dec)
check("“Not for me” files ONE withdrawal, by hand, saying declined and why",
      r.status_code == 303 and r.headers["location"] == f"/applications/{_dec}"
      and len(_c) == 1 and _c[0]["source"] == "manual"
      and _c[0]["payload"] == {"closed": "declined", "why": "experience"}, _c)
check("...which closes it: withdrawn, off the pin and out of the nav pill",
      _st == "withdrawn" and _leads1 == _leads0 - 1, (_st, _leads0, _leads1))
r = client.get(f"/applications/{_dec}")
check("its timeline says “You declined” and why, and the close form is gone",
      "You declined" in r.text and "not my experience or skills" in r.text
      and f'action="/applications/{_dec}/close"' not in r.text and "You withdrew" not in r.text,
      r.status_code)
r = client.get("/inbound")
_row = r.text.split("Close Decline Co", 1)[1].split("</a>")[0]
check("/inbound wears it as a grey tag beside the status word, with why as its title",
      ">withdrawn</span>" in _row and ">declined</span>" in _row
      and "You declined: not my experience or skills" in _row, _row[-400:])

r = client.post(f"/applications/{_dec}/events/{_c[0]['id']}/edit", data={
    "type": "withdrawn", "occurred_on": "2026-08-22"})
_c2 = _closes(_dec)
check("editing its date keeps what kind of close it was, and why",
      r.status_code == 303 and _c2[0]["payload"] == {"closed": "declined", "why": "experience"}
      and _c2[0]["occurred_at"].date().isoformat() == "2026-08-22", _c2)
r = client.post(f"/applications/{_dec}/close", data={"action": "quiet"})
check("a closed thread refuses a second close",
      "event_error=" in r.headers.get("location", "") and len(_closes(_dec)) == 1,
      r.headers.get("location"))
r = client.post(f"/applications/{_dec}/events/{_c[0]['id']}/delete")
_st, _leads2 = _state(_dec)
check("deleting it is the undo: an open lead again, pinned and counted",
      r.status_code == 303 and not _closes(_dec) and _st == "interested" and _leads2 == _leads0,
      (_st, _leads2))

_qui = _new_lead("Close Quiet Co", "2026-08-21")
r = client.post(f"/applications/{_qui}/close", data={
    "action": "quiet", "why": "experience", "redirect_to": "/inbound"})
_c = _closes(_qui)
check("“They went quiet” files the other kind, and a stray why is not kept",
      r.status_code == 303 and r.headers["location"] == "/inbound"
      and len(_c) == 1 and _c[0]["payload"] == {"closed": "went_quiet"}, _c)
check("...worded so on its page and tagged so on /inbound",
      "They went quiet" in client.get(f"/applications/{_qui}").text
      and ">went quiet</span>" in client.get("/inbound").text.split("Close Quiet Co", 1)[1].split("</a>")[0])

_bad = _new_lead("Close Guard Co", "2026-08-21")
r = client.post(f"/applications/{_bad}/close", data={"action": "decline", "occurred_on": "2026-08-01"})
check("a close dated before the approach is refused",
      "event_error=" in r.headers.get("location", "") and not _closes(_bad), r.headers.get("location"))
r = client.post(f"/applications/{_bad}/close", data={"action": "decline",
                                                     "redirect_to": "https://example.com/"})
check("an unknown redirect_to lands on the record, never off-site",
      r.headers["location"] == f"/applications/{_bad}", r.headers["location"])
r = client.post(f"/applications/{_bad}/close", data={"action": "delete"})
check("an unknown action is refused", r.status_code == 400, r.status_code)
r = client.post(f"/applications/{manual_app_1}/close", data={"action": "decline"})
check("an application you started is not closed this way (“I withdrew” is its words)",
      "event_error=" in r.headers.get("location", "") and not _closes(manual_app_1),
      r.headers.get("location"))
check("...and its page offers no such form",
      f'action="/applications/{manual_app_1}/close"' not in client.get(f"/applications/{manual_app_1}").text)

# Same-day placement: a bare date anchors at local noon, which would sort the
# close above an approach that came in later that day.
with db.connect() as conn:
    _tz = conn.execute("SELECT timezone FROM users ORDER BY created_at LIMIT 1").fetchone()["timezone"]
from zoneinfo import ZoneInfo                                  # noqa: E402
_today = datetime.now(ZoneInfo(_tz or "UTC")).date().isoformat()
_sd = _new_lead("Close Sameday Co", _today)
with db.connect() as conn, conn.transaction():
    # The approach arrived at 23:00 local — after the noon a bare date means.
    _late = datetime.now(ZoneInfo(_tz or "UTC")).replace(hour=23, minute=0, second=0, microsecond=0)
    conn.execute("UPDATE events SET occurred_at = %s WHERE application_id = %s::uuid "
                 "AND type = 'recruiter_outreach'", (_late, _sd))
r = client.post(f"/applications/{_sd}/close", data={"action": "decline", "occurred_on": _today})
_c = _closes(_sd)
check("a close dated the same day as the thread's latest event sorts just after it, not at noon",
      len(_c) == 1 and _c[0]["occurred_at"] == _late + timedelta(seconds=1), _c)

print("inbound: “I replied” moves the wait to them (28 Sep 2026)")


def _queue(user_id):
    with db.connect() as conn:
        rows = {str(x["id"]): x for x in analytics.reminders(conn, user_id)}
        return rows, analytics.queue_count(conn, user_id)


_rep = _new_lead("Reply Lead Co", "2026-09-01")
with db.connect() as conn:
    _uid = conn.execute("SELECT user_id FROM applications WHERE id = %s::uuid", (_rep,)).fetchone()["user_id"]
_st, _l0 = _state(_rep)
r = client.get(f"/applications/{_rep}")
check("an open lead offers “I replied”",
      f'action="/applications/{_rep}/replied"' in r.text and _st == "interested", r.status_code)
r = client.post(f"/applications/{_rep}/replied", data={"channel": "linkedin", "occurred_on": "2026-09-03"})
with db.connect() as conn:
    _n = conn.execute("SELECT id, source, payload FROM events WHERE application_id = %s::uuid "
                      "AND type = 'note'", (_rep,)).fetchall()
_st, _l1 = _state(_rep)
check("it files a note by hand, marked as your reply, with how",
      r.status_code == 303 and len(_n) == 1 and _n[0]["source"] == "manual"
      and _n[0]["payload"] == {"reply": True, "channel": "linkedin"}, _n)
check("...which leaves the status alone and moves the wait to them: off the pin, out of the pill",
      _st == "interested" and _l1 == _l0 - 1, (_st, _l0, _l1))
r = client.get(f"/applications/{_rep}")
check("its timeline says “You replied”, by LinkedIn message",
      "You replied" in r.text and "LinkedIn message" in r.text, r.status_code)
_i = client.get("/inbound").text
_m = re.search(r"<b>(\d+)</b><span>awaiting your call", _i)
_nav = _i.split('href="/inbound"')[1].split("</a>")[0]
check("/inbound lists it below the pin, and its lede and nav pill both count what the pin holds",
      _i.index('<div class="tl-sep">Underway or closed</div>') < _i.index("Reply Lead Co")
      and _m is not None and int(_m.group(1)) == _l1 and f'class="pill">{_l1}<' in _nav,
      (_m and _m.group(1), _l1, _nav))

r = client.post(f"/applications/{_rep}/events/{_n[0]['id']}/edit", data={
    "type": "note", "occurred_on": "2026-09-04", "channel": "whatsapp"})
with db.connect() as conn:
    _p = conn.execute("SELECT payload FROM events WHERE id = %s", (_n[0]["id"],)).fetchone()["payload"]
check("editing the reply keeps it a reply", r.status_code == 303
      and _p == {"reply": True, "channel": "whatsapp"}, _p)

with db.connect() as conn, conn.transaction():
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s::uuid, 'recruiter_outreach', 'manual', %s, '{}')",
        (_uid, _rep, datetime(2026, 9, 5, 4, tzinfo=timezone.utc)))
check("when the recruiter writes again, the next move is yours again", _state(_rep)[1] == _l0)

# A reply you EMAILED: the matcher files it as a plain note from a sent email.
with db.connect() as conn, conn.transaction():
    _em = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, received_at,
                               classification, triage_state, sent_by_user, matched_application_id)
           VALUES (%s, 'gm-reply-lead', 'me@example.com', 'Re: an opportunity', %s,
                   'sent_reply', 'auto_matched', true, %s::uuid)
           ON CONFLICT (user_id, gmail_message_id) DO UPDATE SET sent_by_user = true
           RETURNING id""",
        (_uid, datetime(2026, 9, 6, 4, tzinfo=timezone.utc), _rep)).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload, "
        "source_email_id) VALUES (%s, %s::uuid, 'note', 'email', %s, '{}', %s)",
        (_uid, _rep, datetime(2026, 9, 6, 4, tzinfo=timezone.utc), _em))
check("an emailed reply counts the same: the wait is theirs again", _state(_rep)[1] == _l1)
check("...and reads “You replied” too", client.get(f"/applications/{_rep}").text.count("You replied") == 2)

_rows, _cnt = _queue(_uid)
check("REMINDER_DAYS after your reply, with nothing since, it is in the follow-up queue",
      _rep in _rows and _rows[_rep]["applied_at"] is None and _rows[_rep]["days_waiting"] >= 10
      and _cnt >= 1, (_rows.get(_rep), _cnt, len(_rows)))
_fu = client.get("/follow-ups").text
_frow = _fu.split("Reply Lead Co", 1)[1].split('class="fu-row"')[0]
check("its row says when you replied, beside “They went quiet” and “Followed up”",
      "You replied to their approach on" in _frow and 'value="quiet"' in _frow
      and "Followed up" in _frow and "approach you answered that went quiet" in _fu, _frow[:600])
r = client.post(f"/applications/{_rep}/events", data={"type": "follow_up_sent",
                                                      "redirect_to": "/follow-ups"})
check("“Followed up” is your move too: it clears the row and restarts the clock",
      r.headers["location"] == "/follow-ups" and _rep not in _queue(_uid)[0] and _state(_rep)[1] == _l1)

_old = _new_lead("Reply Quiet Co", "2026-08-10")
client.post(f"/applications/{_old}/replied", data={"occurred_on": "2026-08-12"})
check("an answered lead long silent is queued...", _old in _queue(_uid)[0])
r = client.post(f"/applications/{_old}/close", data={"action": "quiet", "redirect_to": "/follow-ups"})
check("...and “They went quiet” from the queue closes it and clears the row",
      r.headers["location"] == "/follow-ups" and _state(_old)[0] == "withdrawn"
      and _old not in _queue(_uid)[0], r.headers.get("location"))

_fresh = _new_lead("Reply Fresh Co", _today)
client.post(f"/applications/{_fresh}/replied", data={})
check("a reply younger than REMINDER_DAYS is not queued yet", _fresh not in _queue(_uid)[0])
r = client.post(f"/applications/{manual_app_1}/replied", data={})
check("“I replied” is refused on your own application",
      "event_error=" in r.headers.get("location", ""), r.headers.get("location"))
r = client.post(f"/applications/{_qui}/replied", data={})
check("...and on a closed approach", "event_error=" in r.headers.get("location", ""),
      r.headers.get("location"))

print("your own application: an interview thread gone quiet (29 Sep 2026)")
# Asked how to handle interviews "ignored after chasing them for status", or
# that went badly and never got an answer. None could reach /follow-ups (any
# response kept a record out) and none could be closed truthfully. Dates are
# relative to today, so the waits are real.
from urllib.parse import unquote_plus                          # noqa: E402


def _ago(n):
    return (datetime.now(ZoneInfo(_tz or "UTC")).date() - timedelta(days=n)).isoformat()


def _new_app(company, applied_days_ago):
    r = client.post("/applications/new", data={
        "company": company, "title": "Platform Engineer", "platform": "other",
        "applied_date": _ago(applied_days_ago), "after": "view"})
    assert r.status_code == 303, r.text[:300]
    return r.headers["location"].rsplit("/", 1)[1]


def _row_on(page, company, after=""):
    """The row for `company`, from its name to the next row. `after` is a
    section heading to look below: a thread can be in two sections at once
    (a round owed a word and gone quiet, since 8 Oct 2026)."""
    text = client.get(page).text
    if after:
        text = text.split(after, 1)[1]
    return text.split(company, 1)[1].split('class="fu-row"')[0]


_rnd = _new_app("Round Quiet Co", 30)
client.post(f"/applications/{_rnd}/events", data={"type": "interview_invite", "occurred_on": _ago(20)})
_rows, _cnt = _queue(_uid)
check("an interview silent 20 days is queued as a round, and the nav badge counts it",
      _rnd in _rows and _rows[_rnd]["kind"] == "round" and 19 <= _rows[_rnd]["days_waiting"] <= 20
      and _cnt >= 1, (_rows.get(_rnd), _cnt, len(_rows)))
_fu = client.get("/follow-ups").text
_frow = _row_on("/follow-ups", "Round Quiet Co", after="After a round")
check("its row says they invited you and when, beside “They went quiet” and “Followed up”",
      "They invited you to interview on" in _frow and 'value="quiet"' in _frow
      and "Followed up" in _frow and "gone quiet after an interview" in _fu
      and "Same role, close" not in _frow, _frow[:600])
check("its page offers the close: “Heard nothing since?”",
      "Heard nothing since?" in client.get(f"/applications/{_rnd}").text)

client.post(f"/applications/{_rnd}/events", data={"type": "note", "note": "the panel seemed keen",
                                                  "occurred_on": _ago(2)})
_rows = _queue(_uid)[0]
check("a note to self is not a move: the wait still runs from the invitation",
      _rnd in _rows and 19 <= _rows[_rnd]["days_waiting"] <= 20, _rows.get(_rnd))
client.post(f"/applications/{_rnd}/events", data={"type": "follow_up_sent", "occurred_on": _ago(12)})
_rows = _queue(_uid)[0]
check("chased 12 days ago and silent since: still queued, waiting from the follow-up",
      _rnd in _rows and _rows[_rnd]["moved_as"] == "followed_up"
      and 11 <= _rows[_rnd]["days_waiting"] <= 12, _rows.get(_rnd))
check("...and its row says so",
      "You followed up on" in _row_on("/follow-ups", "Round Quiet Co", after="After a round"))
r = client.post(f"/applications/{_rnd}/events", data={"type": "follow_up_sent",
                                                      "redirect_to": "/follow-ups"})
check("“Followed up” today restarts the clock: the row leaves, the thread stays open",
      r.headers["location"] == "/follow-ups" and _rnd not in _queue(_uid)[0]
      and _state(_rnd)[0] == "interview_invite", r.headers.get("location"))

_rq = _new_app("Round Close Co", 25)
client.post(f"/applications/{_rq}/events", data={"type": "interview_invite", "occurred_on": _ago(15)})
check("another round, silent 15 days, is queued", _rq in _queue(_uid)[0])
r = client.post(f"/applications/{_rq}/close", data={
    "action": "quiet", "note": "the system design round went badly", "redirect_to": "/follow-ups"})
_c = _closes(_rq)
check("“They went quiet” closes your own application after a round: one withdrawal, by hand, "
      "with your note",
      r.headers["location"] == "/follow-ups" and len(_c) == 1 and _c[0]["source"] == "manual"
      and _c[0]["payload"] == {"closed": "went_quiet", "note": "the system design round went badly"}, _c)
check("...which ends it: withdrawn, and out of the queue",
      _state(_rq)[0] == "withdrawn" and _rq not in _queue(_uid)[0], _state(_rq))
r = client.get(f"/applications/{_rq}")
check("its timeline says “They went quiet” with your note, never “You withdrew”, and the form is gone",
      "They went quiet" in r.text and "the system design round went badly" in r.text
      and "Heard nothing since?" not in r.text and "You withdrew" not in r.text, r.status_code)
_row = client.get("/").text.split("Round Close Co", 1)[1].split("</a>")[0]
check("the list wears it as a grey “went quiet” tag beside the status word",
      ">withdrawn</span>" in _row and ">went quiet</span>" in _row, _row[-400:])
r = client.post(f"/applications/{_rq}/close", data={"action": "quiet"})
check("a closed thread refuses a second close",
      "event_error=" in r.headers.get("location", "") and len(_closes(_rq)) == 1)

_nr = _new_app("Round Guard Co", 20)
r = client.post(f"/applications/{_nr}/close", data={"action": "quiet"})
check("an application nobody answered is not closed as gone quiet (it waits in the queue)",
      "event_error=" in r.headers.get("location", "") and not _closes(_nr)
      and "Follow-ups" in unquote_plus(r.headers.get("location", "")), r.headers.get("location"))
check("...and its page offers no such form",
      "Heard nothing since?" not in client.get(f"/applications/{_nr}").text)
client.post(f"/applications/{_nr}/events", data={"type": "engaged", "occurred_on": _ago(5)})
check("a person getting in touch is a round too, but 5 days of silence is not queued yet",
      _state(_nr)[0] == "engaged" and _nr not in _queue(_uid)[0]
      and "Heard nothing since?" in client.get(f"/applications/{_nr}").text)
r = client.post(f"/applications/{_nr}/close", data={"action": "decline"})
check("“Not for me” stays an approach's close, even after a round",
      "event_error=" in r.headers.get("location", "") and not _closes(_nr), r.headers.get("location"))
r = client.post(f"/applications/{_nr}/close", data={"action": "quiet", "occurred_on": _ago(25)})
check("a close dated before the thread began is refused, in an application's own words",
      "this thread began" in unquote_plus(r.headers.get("location", "")) and not _closes(_nr),
      r.headers.get("location"))

_ir = _new_lead("Round Inbound Co", _ago(30))
client.post(f"/applications/{_ir}/events", data={"type": "interview_invite", "occurred_on": _ago(14)})
_rows = _queue(_uid)[0]
check("an approach that reached an interview and went quiet is queued as a round, not a lead",
      _ir in _rows and _rows[_ir]["kind"] == "round", _rows.get(_ir))

# The "you applied again" suggestion is for a record the employer never
# answered: an older record they DID answer is not closed as a repost. The
# control, the same pair without the invitation, is offered.
with db.connect() as conn, conn.transaction():
    # (_seed_app, above: manual entry would take the second of a pair for the
    # same job.)
    _ra = _seed_app(conn, "roundagainco", "Platform Engineer", 40)
    conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
                 "VALUES (%s, %s, 'interview_invite', 'manual', now() - interval '30 days', '{}')",
                 (user_id, _ra))
    _seed_app(conn, "roundagainco", "Platform Engineer", 5)
    _rc = _seed_app(conn, "roundagainctrl", "Platform Engineer", 40)
    _seed_app(conn, "roundagainctrl", "Platform Engineer", 5)
with db.connect() as conn:
    _again = analytics.reapplications(conn, user_id)
    _rows = {x["id"]: x for x in analytics.reminders(conn, user_id)}
check("a round gone quiet is never offered as “you applied again”; the unanswered control is",
      _ra in _rows and _rows[_ra]["kind"] == "round" and _ra not in _again and _rc in _again,
      (_ra in _rows, _ra in _again, _rc in _again))

print("an offer is a round, not a close (7 Oct 2026, migration 019)")
with db.connect() as conn:
    _offers0 = analytics.summary(conn, _uid)["offers"]
_of = _new_app("Offer Open Co", 40)
client.post(f"/applications/{_of}/events", data={"type": "interview_invite", "occurred_on": _ago(30)})
client.post(f"/applications/{_of}/events", data={"type": "offer", "channel": "whatsapp",
                                                 "note": "the seat moved to another city",
                                                 "occurred_on": _ago(16)})
_rows = _queue(_uid)[0]
check("an offer in hand is an open thread: status offer, queued as a round after REMINDER_DAYS "
      "of silence, the wait running from the offer",
      _state(_of)[0] == "offer" and _of in _rows and _rows[_of]["kind"] == "round"
      and _rows[_of]["moved_as"] == "offer" and 15 <= _rows[_of]["days_waiting"] <= 16,
      _rows.get(_of))
check("its row says they made an offer and when",
      "They made an offer on" in _row_on("/follow-ups", "Offer Open Co"))
r = client.get(f"/applications/{_of}")
check("its page asks about the offer: a why, “Not for me” and “They went quiet”",
      "Offer in hand:" in r.text and 'value="decline"' in r.text and 'value="quiet"' in r.text
      and "Heard nothing since?" not in r.text, r.status_code)
r = client.post(f"/applications/{_of}/close", data={"action": "decline", "why": "location",
                                                     "note": "would not relocate"})
_c = _closes(_of)
check("declining it files ONE withdrawal, by hand, saying declined, why and your note",
      r.status_code == 303 and len(_c) == 1 and _c[0]["source"] == "manual"
      and _c[0]["payload"] == {"closed": "declined", "why": "location", "note": "would not relocate"},
      _c)
with db.connect() as conn:
    _offers1 = analytics.summary(conn, _uid)["offers"]
check("...which ends it: withdrawn outranks the offer, out of the queue, and the offer still counts",
      _state(_of)[0] == "withdrawn" and _of not in _queue(_uid)[0] and _offers1 == _offers0 + 1,
      (_state(_of)[0], _offers0, _offers1))
r = client.get(f"/applications/{_of}")
check("its timeline says “You declined” with your note, and the panel is gone",
      "You declined" in r.text and "would not relocate" in r.text and "Offer in hand:" not in r.text
      and "You withdrew" not in r.text, r.status_code)
_row = client.get("/").text.split("Offer Open Co", 1)[1].split("</a>")[0]
check("the list wears it as withdrawn with a grey “declined” tag naming the why",
      ">withdrawn</span>" in _row and ">declined</span>" in _row and "location or work mode" in _row,
      _row[-500:])

_rs = _new_app("Offer Pulled Co", 30)
client.post(f"/applications/{_rs}/events", data={"type": "offer", "occurred_on": _ago(10)})
client.post(f"/applications/{_rs}/events", data={"type": "rejected", "reason": "role_closed",
                                                 "occurred_on": _ago(10)})
check("a rescinded offer, filed as “They rejected me” the same day, reads rejected — the close "
      "outranks the offer at the same instant — and ended after a round",
      _state(_rs)[0] == "rejected"
      and "Offer Pulled Co" in client.get("/?status=rejected&how=after_round").text, _state(_rs))

_io = _new_lead("Offer Inbound Co", _ago(30))
client.post(f"/applications/{_io}/events", data={"type": "offer", "occurred_on": _ago(12)})
r = client.get(f"/applications/{_io}")
_rows = _queue(_uid)[0]
check("an approach that reached an offer keeps its own close, and is queued as a round",
      _state(_io)[0] == "offer" and 'value="decline"' in r.text and 'value="quiet"' in r.text
      and _io in _rows and _rows[_io]["kind"] == "round", (_state(_io)[0], _rows.get(_io)))

print("your move: the queue by the move it offers (7 Oct 2026)")
_now = datetime.now(timezone.utc)
with db.connect() as conn:
    _odds = analytics.reply_odds(conn, _uid, _now)
    _fa, _fe = analytics.facts(conn, _uid)
_facts = insights.build_facts(_fa, _fe, _now, 10)
_curve_a = insights.reply_curve(_facts)
check("the queue's reply curve is /analytics' curve: as many replies, the same odds at every "
      "day, the same cut",
      _odds.heard == sum(1 for f in _facts if f["sent"] and f["lag"] is not None)
      and all(abs(insights.still_chance(_odds.curve, d) - insights.still_chance(_curve_a, d)) < .01
              for d in range(0, 120))
      and (not _odds.enough or _odds.quiet_after == insights.quiet_after(_curve_a, 10)),
      (_odds.heard, _odds.quiet_after, insights.quiet_after(_curve_a, 10)))
check("this suite's data draws a cut past day 11 (the checks below build on both sides of it)",
      _odds.enough and _odds.quiet_after is not None and _odds.quiet_after > 11,
      (_odds.heard, _odds.quiet_after))
_T = _odds.quiet_after

_nu = _new_app("Nudge Co", 11)
_wa = _new_app("Unreachable Co", 11)
_qu = _new_app("Quiet Co", _T + 20)
with db.connect() as conn, conn.transaction():
    _nu_job = conn.execute("SELECT job_id FROM applications WHERE id = %s::uuid", (_nu,)).fetchone()["job_id"]
    conn.execute("INSERT INTO contacts (user_id, job_id, name, url, source) VALUES (%s, %s, "
                 "'Jane Nudge', 'https://www.linkedin.com/in/jane-nudge', 'extension')", (_uid, _nu_job))
    # An older application to a role applied to again, past the odds: the
    # bulk close keeps the link, as "Same role, close" would.
    _old = str(_seed_app(conn, "bulkagainco", "Platform Engineer", _T + 30))
    _new = str(_seed_app(conn, "bulkagainco", "Platform Engineer", 3))
with db.connect() as conn:
    _q = analytics.queue(conn, _uid)
    _cnt = analytics.queue_count(conn, _uid)
    _owed_n = analytics.email_owed_count(conn, _uid)
    _rate_n = len(analytics.ratings_owed(conn, _uid))


def _ids(rows):
    return {str(r["id"]) for r in rows}


check("inside the odds with a recruiter on record is a nudge; nobody to write to waits; past the "
      "odds is quiet — and the cut is the one line between them; applied again is its own band",
      _nu in _ids(_q["nudge"]) and _wa in _ids(_q["waiting"])
      and _qu in _ids(_q["quiet"]) and _old in _ids(_q["again"]) and _old not in _ids(_q["quiet"])
      and all(r["days_waiting"] >= _T for r in _q["quiet"])
      and all(r["days_waiting"] < _T for r in _q["nudge"] + _q["waiting"]),
      {k: len(v) for k, v in _q.items() if k != "odds"})
check("nudges run best odds first, each carrying its chance",
      [r["days_waiting"] for r in _q["nudge"]] == sorted(r["days_waiting"] for r in _q["nudge"])
      and all(r["chance"] is not None and r["chance"] >= insights.QUIET_CHANCE for r in _q["nudge"]))
_fu = client.get("/follow-ups").text
_nrow = _row_on("/follow-ups", "Nudge Co")
check("the page: the nudge row names the recruiter, the move and the odds; the unreachable and the "
      "quiet are counts, and the quiet count carries its close",
      "Your move" in _fu and "Worth a nudge" in _fu
      and 'href="https://www.linkedin.com/in/jane-nudge">Jane Nudge</a> on LinkedIn' in _nrow
      and "of applications this old" in _nrow and 'value="follow_up_sent"' in _nrow
      # The company cell, not the bare name: "Round Quiet Co" (an unrated
      # open interview, above) is on the page since 8 Oct 2026.
      and '>Unreachable Co<' not in _fu and '>Quiet Co<' not in _fu
      and f"<b>{len(_q['waiting'])}</b> recent application" in _fu
      and f"Close all {len(_q['quiet'])} as gone quiet" in _fu and 'href="/follow-ups/quiet"' in _fu
      and f"past <b>{_T}</b> days" in _fu, _fu[:300])
_pill = re.search(r'href="/follow-ups" class="[^"]*">Follow-ups<span class="pill warm">(\d+)</span>', _fu)
check("the pill counts the rows that carry a move — the page's rows, and analytics.queue_count "
      "everywhere else",
      _pill is not None
      and int(_pill.group(1)) == _cnt == _rate_n + _owed_n + len(_q["rounds"]) + len(_q["nudge"])
      and _fu.count('class="fu-row"') == _cnt + len(_q["again"]),
      (_pill and _pill.group(1), _cnt, _fu.count('class="fu-row"'), _rate_n, _owed_n,
       len(_q["rounds"]), len(_q["nudge"]), len(_q["again"])))
check("the list's aside says the same number, as moves to make",
      f">{_cnt} moves to make<" in client.get("/").text)

r = client.get("/follow-ups/quiet")
_qrow = r.text.split("Quiet Co", 1)[1].split('class="fu-row"')[0]
_orow = r.text.split("bulkagainco", 1)[1].split('class="fu-row"')[0]
_both = len(_q["quiet"]) + len(_q["again"])
check("the confirmation lists every quiet and applied-again row with its fate and carries their "
      "ids, and nothing else",
      r.status_code == 200 and f"Close {_both} in one go" in r.text
      and f'name="ids" value="{_qu}"' in r.text and "applications this old" in _qrow
      and f'name="ids" value="{_old}"' in r.text and "applied again on" in _orow
      and f'href="/applications/{_new}"' in _orow
      and "Nudge Co" not in r.text and r.text.count('name="ids"') == _both
      and f"Close all {_both}</button>" in r.text, r.status_code)
r = client.post("/follow-ups/quiet", data={"ids": [_qu, _old, _nu]})
with db.connect() as conn:
    _new_at = conn.execute("SELECT min(occurred_at) AS t FROM events WHERE application_id = %s::uuid "
                           "AND type = 'applied'", (_new,)).fetchone()["t"]
check("closing files one withdrawal per quiet row — gone quiet dated now, applied again dated at the "
      "later submission and linked — and leaves a row inside the odds alone",
      r.status_code == 303 and r.headers["location"] == "/follow-ups?closed=2"
      and _state(_qu)[0] == "withdrawn" and _closes(_qu)[0]["payload"] == {"closed": "went_quiet"}
      and _state(_old)[0] == "withdrawn" and _closes(_old)[0]["payload"] == {"superseded_by": _new}
      and _closes(_old)[0]["occurred_at"] == _new_at
      and _state(_nu)[0] == "applied" and not _closes(_nu),
      (r.headers.get("location"), _closes(_qu), _closes(_old), _closes(_nu)))
with db.connect() as conn:
    _q2 = analytics.queue(conn, _uid)
check("...and both leave the queue, the nudge stays, and the page says what it closed",
      _qu not in _ids(_q2["quiet"]) and _old not in _ids(_q2["again"]) and _nu in _ids(_q2["nudge"])
      and "Closed 2</strong> as gone quiet" in client.get("/follow-ups?closed=2").text
      and "They went quiet" in client.get(f"/applications/{_qu}").text
      and "You applied again" in client.get(f"/applications/{_old}").text)

print("the lists: one sentence in one shape, months as landmarks, who approached (7-9 Oct 2026)")
_wk_app = _new_app("Week Fresh Co", 1)
with db.connect() as conn:
    _summ = analytics.summary(conn, user_id, False)
r = client.get("/")
_figs = re.sub(r"\s+", " ", r.text.split('<div class="figures"')[1].split("</div>")[0])
check("the record's head is the whole search in figures — how many since the first, heard back "
      "with its rate, the interviews — and no week (dropped 9 Oct 2026), no sentence",
      f'<b>{_summ["applied"]}</b><span>applications</span> <small>since ' in _figs
      and f'<b>{_summ["responded"]}</b><span>heard back<em>{_summ["response_rate"]}%</em></span>' in _figs
      and (f'<b>{_summ["sat"]}</b><span>interviews</span>' in _figs) == bool(_summ["sat"])
      and "In the last" not in r.text and '<p class="lede">' not in r.text
      and not hasattr(analytics, "week"), _figs)
_since = re.search(r"<small>since ([^<]+)</small>", _figs)
_figs_q = re.sub(r"\s+", " ", client.get("/?q=Week+Fresh").text.split('<div class="figures"')[1].split("</div>")[0])
check("...its “since” is the page's first application, so a search moves neither the date nor "
      "the numbers",
      _since is not None and f"<small>since {_since.group(1)}</small>" in _figs_q
      and f'<b>{_summ["applied"]}</b><span>applications</span>' in _figs_q, (_figs, _figs_q))

# Months: a divider at each month's first row, and the head's index jumping to it.
_rows_n = r.text.count('<a class="tl"')
_seps = re.findall(r'<div class="tl-sep" id="(m-[^"]+)">([^<]+)<span class="n">(\d+)</span>', r.text)
_head = r.text.split('class="tl tl-head"')[1].split('class="axis"')[0]
_idx = re.findall(r'<a href="#(m-[^"]+)" title="(\d+) in [^"]+">', _head)
_chunks = r.text.split('<div class="tl-sep" id="m-')
check("the record divides by month: the head's index names every month newest first, a divider "
      "opens each but the newest (which the index names right above the first row), each month "
      "counts exactly its rows, and they sum to the page",
      len(_idx) >= 2 and [i for i, _ in _idx] == sorted((i for i, _ in _idx), reverse=True)
      and [s[0] for s in _seps] == [i for i, _ in _idx[1:]]
      and [int(s[2]) for s in _seps] == [int(n) for _, n in _idx[1:]]
      and _chunks[0].count('<a class="tl"') == int(_idx[0][1])
      and all(c.count('<a class="tl"') == int(s[2]) for c, s in zip(_chunks[1:], _seps))
      and sum(int(n) for _, n in _idx) == _rows_n
      and all(re.fullmatch(r"[A-Z][a-z]+( \d{4})?", s[1]) for s in _seps),
      (_idx, [(s[0], s[2]) for s in _seps], _rows_n))
check("the head names the column by its contents, and the newest month's jump lands on the "
      "card, its sticky head",
      re.search(rf'class="lab count">{_rows_n} applications<span class="months">', _head) is not None
      and f'<div class="card register" id="{_idx[0][0]}">' in r.text, (_idx[:1], _head[:300]))
check("no month landmarks under a sort that is not by date, nor on /inbound",
      'class="tl-sep" id="m-' not in client.get("/?sort=company").text
      and 'class="tl-sep" id="m-' not in client.get("/inbound").text
      and 'class="months"' not in client.get("/?sort=company").text)
check("one toolbar row: search and sort, then the page's links, before the list",
      r.text.index('class="bar listbar"') < r.text.index("Add one by hand")
      < r.text.index('<div class="card register"') and 'class="rowline"' not in r.text)

# Who approached: the recruiter on record, beside the company, on /inbound only.
_who = _new_lead("Who Lead Co", _ago(5))
_who_q = _new_app("Who Record Co", 12)
with db.connect() as conn, conn.transaction():
    for _a, _name in ((_who, "Jane Recruiter"), (_who_q, "John Sourcer")):
        _j = conn.execute("SELECT job_id FROM applications WHERE id = %s::uuid", (_a,)).fetchone()["job_id"]
        conn.execute("INSERT INTO contacts (user_id, job_id, name, source) VALUES (%s, %s, %s, 'manual')",
                     (user_id, _j, _name))
_i = client.get("/inbound").text
_wrow = _i.split(f'href="/applications/{_who}"')[1].split("</a>")[0]
check("an approach names who made it, beside the company",
      'Who Lead Co<span class="who" title="Who approached you">Jane Recruiter</span>' in _wrow, _wrow[:300])
check("the record does not: there the company is who you wrote to",
      'class="who"' not in client.get("/").text)
check("a recruiter's name finds their threads, on either page",
      "Who Lead Co" in client.get("/inbound?q=jane+recr").text
      and "Who Record Co" in client.get("/?q=john+sourcer").text
      and "Who Lead Co" not in client.get("/?q=jane+recr").text)
# The inbound lede, the same sentence in the same shape (9 Oct 2026): an
# approach interviewed and then rejected gives it a lost interview to say.
_ll = _new_lead("Lost Lead Co", _ago(40))
client.post(f"/applications/{_ll}/events", data={"type": "interview_invite", "occurred_on": _ago(20)})
client.post(f"/applications/{_ll}/events", data={"type": "rejected", "occurred_on": _ago(10)})
with db.connect() as conn:
    _isumm = analytics.summary(conn, user_id, True)
_ifigs = re.sub(r"\s+", " ", client.get("/inbound").text.split('<div class="figures"')[1].split("</div>")[0])
check("the inbound head is the same row in the same order: how many approaches since the first, "
      "awaiting your call, then the interviews and the lost among them from the page's own counts",
      "</b><span>approaches</span> <small>since " in _ifigs
      and "</b><span>awaiting your call</span>" in _ifigs
      and f'href="/inbound?interviews=sat"><b>{_isumm["sat"]}</b><span>interviews</span>' in _ifigs
      and _isumm["lost"] >= 1
      and f'href="/inbound?interviews=lost"><b>{_isumm["lost"]}</b><span>lost</span> <small>'
          f'{_isumm["lost_rejected"]} after a rejection</small>' in _ifigs
      and "In the last" not in _ifigs, _ifigs)
check("...and the lost link opens exactly its rows on /inbound",
      client.get("/inbound?interviews=lost").text.count('<a class="tl" href="/applications/')
      == _isumm["lost"] and f'href="/applications/{_ll}"' in client.get("/inbound?interviews=lost").text)
client.post(f"/applications/{_ll}/delete")

print("a listing that asked for the CV by email (4 Oct 2026)")
_EA_JD = ("We are hiring.\nPlease send your updated resume in Word format to "
          "jane@contoso-search.example, quoting the job title.\nOnly shortlisted candidates "
          "will be notified.")
_EA_SENTENCE = ("Please send your updated resume in Word format to "
                "jane@contoso-search.example, quoting the job title.")


def _owed():
    with db.connect() as conn:
        return ({str(x["id"]) for x in analytics.emails_owed(conn, _uid)},
                analytics.email_owed_count(conn, _uid))


def _new_ea_app(company, jd=_EA_JD):
    r = client.post("/applications/new", data={
        "company": company, "title": "Data Engineer", "platform": "other",
        "applied_date": _ago(3), "jd_text": jd, "after": "view"})
    assert r.status_code == 303, r.text[:300]
    return r.headers["location"].rsplit("/", 1)[1]


def _ea_events(app_id):
    with db.connect() as conn:
        return conn.execute("SELECT id, type, source, payload FROM events WHERE application_id = "
                            "%s::uuid AND type = 'note'", (app_id,)).fetchall()


_ea = _new_ea_app("Email Ask Co")
_ea_online = _new_ea_app("Email Online Co", "Please apply online or email your CV to "
                                            "jane@contoso-search.example")
_set, _n = _owed()
check("a JD asking for the CV by email makes the application owe it; one offering "
      "the apply button too does not; the count is the rows",
      _ea in _set and _ea_online not in _set and _n == len(_set), (_set, _n))
_p = client.get(f"/applications/{_ea}").text
check("its page quotes the listing's sentence with a mailto naming the role, and both answers",
      "The listing asks for your CV by email" in _p and _EA_SENTENCE in _p
      and 'href="mailto:jane@contoso-search.example?subject=Application%3A%20Data%20Engineer"' in _p
      and 'value="sent"' in _p and 'value="not_needed"' in _p, _p[:200])
check("...and the control's page asks nothing",
      "The listing asks for your CV by email" not in client.get(f"/applications/{_ea_online}").text)
_fu = client.get("/follow-ups").text
_m = re.search(r"<b>(\d+)</b> listings?\s+asked for your CV by email", _fu)
_frow = _row_on("/follow-ups", "Email Ask Co")
check("/follow-ups lists it under its own heading, counted, with the sentence and both buttons",
      'id="by-email"' in _fu and _m is not None and int(_m.group(1)) == _n
      and _EA_SENTENCE in _frow and "Write the email" in _frow and "I emailed it" in _frow
      and "Not needed" in _frow and "Email Online Co" not in _fu.split("Waiting on them")[0],
      (_m and _m.group(1), _frow[:600]))
check("the list nudges with the same number, in grey",
      f'<a href="/follow-ups#by-email">{_n} asked for your CV by email</a>' in client.get("/").text)

r = client.post(f"/applications/{_ea}/emailed", data={"outcome": "not_needed",
                                                     "redirect_to": "/follow-ups"})
_e = _ea_events(_ea)
check("“Not needed” files a note by hand and clears the row, back on the queue",
      r.headers["location"] == "/follow-ups" and _ea not in _owed()[0] and len(_e) == 1
      and _e[0]["source"] == "manual" and _e[0]["payload"] == {"emailed": "not_needed"}, _e)
check("...its timeline says so, and the page stops asking",
      "No email needed" in (_p := client.get(f"/applications/{_ea}").text)
      and "The listing asks for your CV by email" not in _p)
r = client.post(f"/applications/{_ea}/emailed", data={"outcome": "sent"})
check("an email no longer owed cannot be answered again",
      "event_error=" in r.headers.get("location", ""), r.headers.get("location"))
r = client.post(f"/applications/{_ea}/events/{_e[0]['id']}/edit", data={
    "type": "note", "occurred_on": _ago(1)})
check("editing the answer keeps it the answer",
      r.status_code == 303 and _ea_events(_ea)[0]["payload"] == {"emailed": "not_needed"}
      and _ea not in _owed()[0], _ea_events(_ea))
check("an unknown answer is refused",
      client.post(f"/applications/{_ea_online}/emailed", data={"outcome": "maybe"}).status_code == 400)

_ea2 = _new_ea_app("Email Sent Co")
r = client.post(f"/applications/{_ea2}/emailed", data={"outcome": "sent"})
check("“I emailed it” clears it too, and reads “You emailed your CV”",
      r.status_code == 303 and _ea2 not in _owed()[0]
      and "You emailed your CV" in client.get(f"/applications/{_ea2}").text)

_ea3 = _new_ea_app("Email Mailed Co")
with db.connect() as conn, conn.transaction():
    _em = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, received_at,
                               classification, triage_state, sent_by_user, matched_application_id)
           VALUES (%s, 'gm-email-ask', 'me@example.com', 'Application: Data Engineer', now(),
                   'sent_application', 'auto_matched', true, %s::uuid) RETURNING id""",
        (_uid, _ea3)).fetchone()["id"]
    conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, "
                 "payload, source_email_id) VALUES (%s, %s::uuid, 'note', 'email', now(), '{}', %s)",
                 (_uid, _ea3, _em))
check("the CV emailed from the mailbox clears it with no click", _ea3 not in _owed()[0])

_ea4 = _new_ea_app("Email Rejected Co")
client.post(f"/applications/{_ea4}/events", data={"type": "rejected"})
_ea5 = _new_ea_app("Email Inbound Co")
with db.connect() as conn, conn.transaction():
    conn.execute("UPDATE applications SET origin = 'inbound' WHERE id = %s::uuid", (_ea5,))
check("a response, and an approach a recruiter started, owe no email",
      _ea4 not in _owed()[0] and _ea5 not in _owed()[0], _owed())

print("triage, redrawn: the matcher's picks, runs, twins, the review strip (7 Oct 2026)")
from pipeline import ingest as _ing, triage as _tri
from pipeline.email_classifier import norm_company

_now = datetime.now(timezone.utc)
with db.connect() as conn:
    _tu = db.single_user_id(conn)


def _rec(company, title, applied, platform="linkedin", pid=None, jd=None,
         answers=0, captured_via="extension", ats_job_id=None, source="extension"):
    """One captured record the way /captures files it: upsert + applied event
    (+ answers). Returns (application id, job id, posting id). No JD by
    default: a JD enqueues extraction, and this suite runs no worker."""
    with db.connect() as conn, conn.transaction():
        r = _ing.upsert_record(conn, _tu, platform=platform, captured_via=captured_via,
                               platform_job_id=pid, company=company, title=title, jd_text=jd,
                               ats_job_id=ats_job_id)
        if applied is not None:
            conn.execute(
                "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
                "VALUES (%s, %s, 'applied', %s, %s, %s)",
                (_tu, r["application_id"], source, applied, Json({"external": True})))
        for i in range(answers):
            conn.execute(
                "INSERT INTO application_answers (user_id, application_id, posting_id, question, "
                "question_norm, answer, occurrence, ordinal) VALUES (%s, %s, %s, %s, %s, 'Yes', 0, %s)",
                (_tu, r["application_id"], r["posting_id"], f"Question {i}?", f"question {i}", i))
    return str(r["application_id"]), str(r["job_id"]), str(r["posting_id"])


def _mail(gm, subject, company, role, received, classification="status_update",
          sender="Careers <careers@quillfeather.example>", state="pending",
          app=None, score=None, processed=None, platform="direct", recruiter=None):
    with db.connect() as conn, conn.transaction():
        return str(conn.execute(
            """INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text,
                                   received_at, classification, extraction, triage_state,
                                   matched_application_id, match_score, processed_at)
               VALUES (%s, %s, %s, %s, 'body', %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now()))
               RETURNING id""",
            (_tu, gm, sender, subject, received, classification,
             Json({"company": company, "role_title": role, "platform": platform, "ats": None,
                   "event_date": None, "status_detail": None, "recruiter": recruiter, "notes": None}),
             state, app, score, processed)).fetchone()["id"])


def _card(html, email_id):
    """One email's card out of the page: from its anchor to the next card."""
    part = html.split(f'id="email-{email_id}"', 1)
    return part[1].split('<div class="tri', 1)[0] if len(part) == 2 else ""


r = client.get("/triage")
check("the lanes are named for what they hold",
      "Your applications" in r.text and "Approaches" in r.text
      and "Actionable" not in r.text, r.text[:2000])

# -- the matcher's own picks, and why the email waited ------------------------
_tie_a, _, _ = _rec("Quillfeather Robotics", "Lattice Platform Engineer", _now - timedelta(days=3),
                    pid="LI-quill-1")
_tie_b, _, _ = _rec("Quillfeather Robotics", "Lattice Platform Engineer", _now - timedelta(days=2),
                    pid="LI-quill-2")
_tie_mail = _mail("gm-tri-tie", "Your application was viewed", "Quillfeather Robotics",
                  "Lattice Platform Engineer", _now - timedelta(hours=5))
r = client.get("/triage")
c = _card(r.text, _tie_mail)
check("two records that tie are both offered, and the card says they tie",
      "2 records fit about equally." in c and c.count('class="pick"') == 2
      and f'name="application_id" value="{_tie_a}"' in c
      and f'name="application_id" value="{_tie_b}"' in c, c[:3000])
check("the best pick is the primary button, the list of every record moves behind a disclosure",
      c.index('class="primary"') < c.index('class="pick"', c.index('class="pick"') + 1)
      and "<summary>Another record</summary>" in c, c[:3000])
check("the card says how long it has waited", "Waiting 5 hours." in c, c[:1500])
r = client.post(f"/triage/{_tie_mail}", data={"action": "link", "application_id": _tie_b,
                                               "lane": "actionable"})
with db.connect() as conn:
    row = conn.execute("SELECT triage_state, matched_application_id FROM emails WHERE id = %s",
                       (_tie_mail,)).fetchone()
check("a pick files through the route the dropdown always used",
      r.status_code == 303 and row["triage_state"] == "resolved"
      and str(row["matched_application_id"]) == _tie_b, row)

_weak_app, _, _ = _rec("Inkwell Analytics", "Data Platform Lead", _now - timedelta(days=45),
                       pid="LI-inkwell-1")
_weak_mail = _mail("gm-tri-weak", "Thanks for applying", "Inkwell Analytics",
                   "Junior Frontend Developer", _now - timedelta(days=1))
_none_mail = _mail("gm-tri-none", "A message from a recruiter", None, None,
                   _now - timedelta(days=2))
_nobody_mail = _mail("gm-tri-nobody", "Your application", "Nobodyhere Holdings", "Engineer",
                     _now - timedelta(days=2))
r = client.get("/triage")
c = _card(r.text, _weak_mail)
check("a weak fit names the record and says which signals held it back",
      "The nearest record is a weak fit: the role reads differently and you applied 44 days "
      "before it." in c and f'value="{_weak_app}"' in c.split("<select")[0], c[:3000])
c = _card(r.text, _none_mail)
check("an email that names no company says so, and its list is not hidden",
      "It names no company, so no record was searched." in c
      and 'class="pick"' not in c and "Another record" not in c and "<select" in c, c[:3000])
c = _card(r.text, _nobody_mail)
check("no record under the company: said in words, no picks",
      "No record is filed under that company." in c and 'class="pick"' not in c, c[:3000])

# -- the fallback list, nearest the email first --------------------------------
_opts = [{"id": "a", "started_at": _now - timedelta(days=30)},
         {"id": "b", "started_at": _now - timedelta(days=1)},
         {"id": "c", "started_at": None},
         {"id": "d", "started_at": _now + timedelta(hours=2)}]
check("nearest orders by distance from the email either side, and skips undated records",
      [o["id"] for o in _tri.nearest(_opts, _now, 3)] == ["d", "b", "a"])
c = _card(r.text, _weak_mail)
_sel = c.split("<select", 1)[1].split("</select>", 1)[0]
check("the fallback list opens with the records that started nearest the email, "
      "and still holds every record after them",
      _sel.index('label="Started nearest this email"') < _sel.index('label="Every record"')
      and _sel.split('label="Every record"')[1].count("<option ") >= 1, _sel[:1500])

# -- a run of one sender's identical mail --------------------------------------
_run_ids = [
    _mail("gm-tri-run-1", "You've been REFERRED to a role!", "Featherline", "Backend Engineer",
          _now - timedelta(days=1, minutes=3), classification="recruiter_outreach",
          sender="Featherline Careers <referrals@featherline.example>"),
    _mail("gm-tri-run-2", "You've been referred  to a role!", "Featherline", "Data Engineer",
          _now - timedelta(days=1, minutes=2), classification="recruiter_outreach",
          sender="Featherline Careers <REFERRALS@featherline.example>"),
    _mail("gm-tri-run-3", "you've been referred to a role!", None, "ML Engineer",
          _now - timedelta(days=1, minutes=1), classification="recruiter_outreach",
          sender='"featherline  careers" <referrals@featherline.example>'),
]
_run_other = _mail("gm-tri-run-4", "A different subject", "Featherline", "SRE",
                   _now - timedelta(days=1), classification="recruiter_outreach",
                   sender="HR Central <referrals@featherline.example>")
cards = _tri.runs([{"sender": s, "subject": j, "received_at": t, "id": i} for i, s, j, t in (
    (1, "Ann Lee <x@y.z>", "Hello", _now), (2, '"ann  LEE" <X@Y.Z>', " hello ", _now - timedelta(1)),
    (3, "Ann Lee <x@y.z>", "Other", _now - timedelta(2)),
    (4, "Bo Tan <x@y.z>", "Hello", _now - timedelta(3)))])
check("runs: one sender, address and name, under one subject, case, spacing and quotes folded, "
      "is one card at its newest",
      [c["run"] for c in cards] == [True, False, False]
      and [e["id"] for e in cards[0]["emails"]] == [1, 2], cards)
# 8 Oct 2026: LinkedIn sends every person's connection request from one
# address under one subject; the name is who.
cards = _tri.runs([{"sender": f"{n} <invitations@linkedin.com>", "subject": "You have an invitation",
                    "received_at": _now - timedelta(days=k), "id": k} for k, n in enumerate(
                        ("Jane Recruiter", "John Sourcer", "Jane Recruiter"))])
check("runs: one relay address, two people, is two cards, each person's mail together",
      sorted([e["id"] for e in c["emails"]] if c["run"] else [c["email"]["id"]] for c in cards)
      == [[0, 2], [1]], cards)
check("who: the person and company an email names, for a row with no role",
      [_tri.who({"recruiter": {"name": "Jane Recruiter", "email": None}, "company": "Contoso Talent"}),
       _tri.who({"recruiter": None, "company": "Contoso Talent"}),
       _tri.who({"recruiter": "Jane Recruiter", "company": " "}), _tri.who(None)]
      == ["Jane Recruiter, Contoso Talent", "Contoso Talent", "Jane Recruiter", None])
_invites = [_mail(f"gm-tri-inv-{k}", "You have an invitation", co, None,
                  _now - timedelta(days=3, minutes=k), classification="recruiter_outreach",
                  sender=f"{n} <invitations@linkedin.com>", recruiter={"name": n, "email": None})
            for k, (n, co) in enumerate((("Jane Recruiter", "Contoso Talent"),
                                         ("Jane Recruiter", "Contoso Talent"),
                                         ("John Sourcer", "Fabrikam Search")))]
r = client.get("/triage?lane=inbound")
check("one person's two requests are a run headed by their name, its rows naming them "
      "where no role is known; another person's request is its own card",
      "2 emails from Jane Recruiter &lt;invitations@linkedin.com&gt;" in r.text
      and r.text.count(">Jane Recruiter, Contoso Talent</a>") == 2
      and 'id="email-' + _invites[2] + '"' in r.text
      and "emails from John Sourcer" not in r.text, r.text[:3000])
with db.connect() as conn, conn.transaction():
    conn.execute("DELETE FROM emails WHERE id = ANY(%s::uuid[])", (_invites,))
r = client.get("/triage?lane=inbound")
check("the three identical notices are one card, the fourth its own",
      "3 emails from" in r.text and 'id="email-' + _run_other + '"' in r.text
      and all(f'href="/triage?email={i}"' in r.text for i in _run_ids), r.text[:3000])
r = client.get(f"/triage?email={_run_ids[0]}")
check("a run's row opens its email as a full card, in its own lane",
      r.status_code == 200 and _card(r.text, _run_ids[0]) and "One email from a run" in r.text
      and 'name="lane" value="inbound"' in r.text, r.text[:2000])
q = "&".join(f"ids={i}" for i in _run_ids)
r = client.get(f"/triage/batch?action=lead&lane=inbound&{q}")
check("the bulk action is a page that lists what it will do, and what it cannot",
      r.status_code == 200 and "Track 2 in one go" in r.text
      and r.text.count("a new lead under Featherline") == 2
      and "left as it is: no company" in r.text and "1 of these names no company" in r.text,
      r.text[:3000])
r = client.post("/triage/batch", data={"action": "lead", "lane": "inbound",
                                        "ids": _run_ids + [_run_other, "not-a-uuid"]})
with db.connect() as conn:
    states = {str(e["id"]): (e["triage_state"], e["origin"]) for e in conn.execute(
        "SELECT e.id, e.triage_state, a.origin FROM emails e "
        "LEFT JOIN applications a ON a.id = e.matched_application_id WHERE e.id = ANY(%s::uuid[])",
        (_run_ids + [_run_other],)).fetchall()}
check("the POST files exactly the ones it can, each as one lead, and says how many",
      r.status_code == 303 and "done=3&did=lead" in r.headers["location"]
      and states[_run_ids[0]] == ("resolved", "inbound")
      and states[_run_ids[1]] == ("resolved", "inbound")
      and states[_run_ids[2]] == ("pending", None)
      and states[_run_other] == ("resolved", "inbound"), (r.headers.get("location"), states))
check("the receipt says it in words", "3 tracked as leads." in client.get(r.headers["location"]).text)
r = client.post("/triage/batch", data={"action": "ignore", "lane": "inbound", "ids": _run_ids})
with db.connect() as conn:
    st = conn.execute("SELECT triage_state FROM emails WHERE id = %s", (_run_ids[2],)).fetchone()
check("an email filed since the page was drawn is left alone; the rest are ignored",
      "done=1&did=ignore" in r.headers["location"] and st["triage_state"] == "ignored",
      (r.headers.get("location"), st))
check("an unknown bulk action is refused",
      client.post("/triage/batch", data={"action": "delete", "ids": _run_ids}).status_code == 400)
_run2 = [_mail(f"gm-tri-run2-{n}", "Roles you may like at Quillfeather", "Quillfeather Robotics",
               "Lattice Platform Engineer", _now - timedelta(hours=n),
               classification="recruiter_outreach", sender="Talent <talent@quillfeather.example>")
         for n in (1, 2)]
r = client.get("/triage?lane=inbound")
_run2_card = r.text.split("2 emails from Talent", 1)[1].split('<form method="get"', 1)[0]
check("a run's row offers its best record only, and links to the others on its own card",
      _run2_card.count('class="pick"') == 2 and _run2_card.count("or 1 more") == 2, _run2_card[:2000])
check("and each pick's date opens its record, for a look before filing",
      f'href="/applications/{_tie_a}"' in _run2_card or f'href="/applications/{_tie_b}"' in _run2_card,
      _run2_card[:2000])
_weak_approach = _mail("gm-tri-weak-approach", "A role you may like", "Inkwell Analytics",
                       "Junior Frontend Developer", _now - timedelta(hours=3),
                       classification="recruiter_outreach",
                       sender="Inkwell Talent <talent@inkwell.example>")
c = _card(client.get("/triage?lane=inbound").text, _weak_approach)
check("an approach is offered a record only at the matcher's own bar, since its lane says no "
      "“weak fit”", c and 'class="pick"' not in c, c[:2000])

# -- an application filed twice ------------------------------------------------
_t0 = _now - timedelta(minutes=1)
_form_app, _form_job, _ = _rec(None, "Quantum Ledger Archivist", _t0, platform="other",
                               pid="career9.successfactors.com/tenantq/4242", jd=None, answers=3,
                               ats_job_id="career9.successfactors.com/tenantq/4242")
_board_app, _board_job, _ = _rec("Brightwater Holdings", "Quantum Ledger Archivist",
                                 _t0 + timedelta(seconds=40), pid="LI-twin-4242")
def _pair():
    with db.connect() as conn:
        return [(str(t["form_app"]), str(t["board_app"])) for t in _tri.twins(conn, _tu)]
check("a nameless form and the board's record of its title, minutes apart, are a twin",
      (_form_app, _board_app) in _pair(), _pair())
_stranger_form, _, _ = _rec("Cobalt Shipping", "Harbour Systems Analyst", _t0, platform="other",
                            pid="cobalt.wd3.myworkdayjobs.com/r123")
_stranger_board, _, _ = _rec("Juniper Bakeries", "Harbour Systems Analyst", _t0, pid="LI-juniper-1")
check("two named employers that do not agree are not, whatever the title",
      (_stranger_form, _stranger_board) not in _pair(), _pair())
r = client.get("/triage")
check("the band shows both records with what each holds",
      "Filed twice?" in r.text and f'value="{_form_app}"' in r.text
      and "Brightwater Holdings, Quantum Ledger Archivist" in r.text
      and "3 answers" in r.text, r.text[:4000])
with db.connect() as conn:
    expected = conn.execute(
        "SELECT (SELECT count(*) FROM emails WHERE triage_state = 'pending' "
        "        AND classification IS DISTINCT FROM 'recruiter_outreach') + "
        "       (SELECT count(*) FROM duplicate_candidates WHERE state = 'pending') AS n"
    ).fetchone()["n"] + len(_tri.twins(conn, _tu))
check("the nav pill counts the twin, by the same rule the band draws",
      f'class="pill">{expected}<' in r.text.split('href="/triage"')[1].split("</a>")[0], expected)
r = client.post("/triage/twins", data={"action": "merge", "form_app": _form_app,
                                        "board_app": _board_app})
with db.connect() as conn:
    gone = conn.execute("SELECT 1 FROM applications WHERE id = %s", (_form_app,)).fetchone()
    evs = conn.execute("SELECT type, occurred_at FROM events WHERE application_id = %s",
                       (_board_app,)).fetchall()
    n_ans = conn.execute("SELECT count(*) AS n FROM application_answers WHERE application_id = %s",
                         (_board_app,)).fetchone()["n"]
    job = conn.execute("SELECT ats_job_id, company_norm FROM jobs WHERE id = %s",
                       (_board_job,)).fetchone()
    n_post = conn.execute("SELECT count(*) AS n FROM postings WHERE job_id = %s",
                          (_board_job,)).fetchone()["n"]
check("merging opens the board's record",
      r.status_code == 303 and r.headers["location"] == f"/applications/{_board_app}",
      r.headers.get("location"))
check("one application: the board's, with the form's answers, posting and job id",
      gone is None and n_ans == 3 and n_post == 2
      and job["ats_job_id"] == "career9.successfactors.com/tenantq/4242"
      and job["company_norm"] == norm_company("Brightwater Holdings"), (gone, n_ans, n_post, job))
check("and one applied event, the form's submit time",
      [e["type"] for e in evs] == ["applied"] and evs[0]["occurred_at"] == _t0, evs)
check("a stale merge does nothing",
      client.post("/triage/twins", data={"action": "merge", "form_app": _form_app,
                                         "board_app": _board_app}).headers["location"] == "/triage")
_f2, _, _ = _rec("Saltmarsh Logistics Pte Ltd", "Routing Engineer", _t0, platform="other",
                 pid="saltmarsh.wd1.myworkdayjobs.com/r9")
_b2, _, _ = _rec("Saltmarsh Logistics", "Routing Engineer II", _t0, pid="LI-saltmarsh-9")
check("names that agree by the gate pair, whatever the titles", (_f2, _b2) in _pair(), _pair())
client.post("/triage/twins", data={"action": "apart", "form_app": _f2, "board_app": _b2})
with db.connect() as conn:
    both = conn.execute("SELECT count(*) AS n FROM applications WHERE id = ANY(%s::uuid[])",
                        ([_f2, _b2],)).fetchone()["n"]
check("“Two different applications” keeps both and the pair never returns",
      both == 2 and (_f2, _b2) not in _pair(), _pair())

# -- the matcher's own less certain filings ------------------------------------
_rv_app, _, _ = _rec("Halcyon Freight", "Customs Data Engineer", _now - timedelta(days=4),
                     pid="LI-halcyon-1")
_rv_new = _mail("gm-tri-rv-new", "Thank you for applying", "Halcyon Freight", "Customs Data Engineer",
                _now - timedelta(hours=3), classification="confirmation", state="auto_matched",
                app=_rv_app, score=None)
_rv_weak = _mail("gm-tri-rv-weak", "Application update", "Halcyon Freight", "Customs Data Engineer",
                 _now - timedelta(hours=2), state="auto_matched", app=_rv_app, score=0.78)
_rv_name = _mail("gm-tri-rv-name", "Interview", "Tidewater Partners", "Customs Data Engineer",
                 _now - timedelta(hours=1), classification="interview_invite",
                 state="auto_matched", app=_rv_app, score=1.0)
_rv_fine = _mail("gm-tri-rv-fine", "Viewed", "Halcyon Freight", "Customs Data Engineer",
                 _now - timedelta(hours=1), state="auto_matched", app=_rv_app, score=0.95)
_rv_old = _mail("gm-tri-rv-old", "Old", "Tidewater Partners", "Customs Data Engineer",
                _now - timedelta(days=9), state="auto_matched", app=_rv_app, score=0.76,
                processed=_now - timedelta(days=9))
_rv_th1 = _mail("gm-tri-rv-th1", "Opportunity at Halcyon", "Halcyon Freight", "Customs Data Engineer",
                _now - timedelta(hours=6), state="auto_matched", app=_rv_app, score=0.76,
                processed=_now - timedelta(hours=6))
_rv_th2 = _mail("gm-tri-rv-th2", "RE:  re: Opportunity at Halcyon", "Halcyon Freight",
                "Customs Data Engineer", _now - timedelta(hours=4), state="auto_matched",
                app=_rv_app, score=0.77, processed=_now - timedelta(hours=4))
_rv_short = _mail("gm-tri-rv-short", "Short name", "Halcyon", "Customs Data Engineer",
                  _now - timedelta(hours=1), state="auto_matched", app=_rv_app, score=1.0)
with db.connect() as conn:
    _rows = _tri.review(conn, _tu, _now)
    rv = {str(x["id"]): x["why"] for x in _rows}
check("a reply thread on one record is one row, carrying every email in it, newest first",
      [x["ids"] for x in _rows if _rv_th2 in x["ids"]] == [[_rv_th2, _rv_th1]], _rows)
check("a short name inside the record's longer one is the gate working, not a difference",
      _rv_short not in rv, rv)
check("the strip holds a record the mail started, a weak fit and names that differ, each said",
      rv.get(_rv_new) == "It started this record" and rv.get(_rv_weak) == "A weak fit"
      and rv.get(_rv_name) == "The email reads “Tidewater Partners”", rv)
check("and not a sure filing, nor one older than the window",
      _rv_fine not in rv and _rv_old not in rv, rv)
r = client.get("/triage")
check("the page shows the strip, each row linking to its email on the record",
      "Filed on their own, less sure" in r.text
      and f'href="/applications/{_rv_app}#email-{_rv_name}"' in r.text, r.text[-4000:])
check("the record's page has that anchor",
      f'id="email-{_rv_name}"' in client.get(f"/applications/{_rv_app}").text)
client.post("/triage/review", data={"ids": [_rv_new]})
with db.connect() as conn:
    rv = {str(x["id"]) for x in _tri.review(conn, _tu, _now)}
check("“Looks right” takes one off the strip", _rv_new not in rv and _rv_weak in rv, rv)
client.post("/triage/review", data={"ids": [_rv_weak, _rv_name, "junk"]})
with db.connect() as conn:
    rv = {str(x["id"]) for x in _tri.review(conn, _tu, _now)}
    left = conn.execute("SELECT triage_state FROM emails WHERE id = %s", (_rv_name,)).fetchone()
check("“All look right” takes the rest, and leaves who filed it unchanged",
      not ({_rv_weak, _rv_name} & rv) and left["triage_state"] == "auto_matched", (rv, left))
client.post("/triage/review", data={"ids": [_rv_th1, _rv_th2]})
# This section's leftover mail, filed through the same bulk route so the
# suites after this one see no triage they did not make.
r = client.post("/triage/batch", data={"action": "ignore", "lane": "actionable",
                                        "ids": [_weak_mail, _none_mail, _nobody_mail,
                                                _weak_approach] + _run2})
check("the leftovers are ignored in one go", "done=6&did=ignore" in r.headers["location"],
      r.headers.get("location"))

print("how a round went, in your judgement (8 Oct 2026)")
# Two real records read the same — "rejected after a round", "they went
# quiet" — while the author knew one interview had gone badly and the other
# well. The rating is yours, on the round (an invitation the email filed, or
# a call filed by hand), never a reason on the close; its time goes with it,
# so a rating made in hindsight is counted apart on /analytics.
_wt = _new_app("Rated Round Co", 30)
with db.connect() as conn:
    _wt_inv = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'interview_invite', 'email', now() - interval '20 days', %s) RETURNING id",
        (user_id, _wt, Json({"stated_date": _ago(19)}))).fetchone()["id"]
    _wt_rej = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'rejected', 'email', now() - interval '10 days', '{}') RETURNING id",
        (user_id, _wt)).fetchone()["id"]


def _wt_event(eid):
    with db.connect() as conn:
        return conn.execute("SELECT type, source, occurred_at, payload FROM events WHERE id = %s",
                            (eid,)).fetchone()


r = client.get(f"/applications/{_wt}")
check("the invitation's line asks how it went; the rejection's keeps its why-select",
      f'action="/applications/{_wt}/events/{_wt_inv}/went"' in r.text and "How did it go?" in r.text
      and f"/events/{_wt_rej}/reason" in r.text and f"/events/{_wt_rej}/went" not in r.text,
      r.status_code)
check("...and nothing else on the thread asks", r.text.count('/went"') == 1, r.text.count('/went"'))
_before = _wt_event(_wt_inv)
r = client.post(f"/applications/{_wt}/events/{_wt_inv}/went", data={"went": "badly"})
_after = _wt_event(_wt_inv)
check("rating an emailed invitation redirects to the thread and writes the word with its time",
      r.status_code == 303 and _after["payload"].get("went") == "badly"
      and _after["payload"].get("went_at"), _after["payload"])
check("only the rating moved — type, source, date and the email's own keys are untouched",
      _after["type"] == "interview_invite" and _after["source"] == "email"
      and _after["occurred_at"] == _before["occurred_at"]
      and {k: v for k, v in _after["payload"].items() if k not in ("went", "went_at")}
      == _before["payload"], _after)
_page = client.get(f"/applications/{_wt}").text
check("the timeline shows it selected, and offers to clear it",
      "selected>went badly" in _page and "Clear the rating" in _page)
_t1 = _after["payload"]["went_at"]
client.post(f"/applications/{_wt}/events/{_wt_inv}/went", data={"went": "badly"})
check("re-saving the same word keeps the first filing's time",
      _wt_event(_wt_inv)["payload"]["went_at"] == _t1, _wt_event(_wt_inv)["payload"])
client.post(f"/applications/{_wt}/events/{_wt_inv}/went", data={"went": "mixed"})
_p = _wt_event(_wt_inv)["payload"]
check("another word is a new filing: the time moves with it",
      _p["went"] == "mixed" and _p["went_at"] > _t1, _p)
r = client.post(f"/applications/{_wt}/events/{_wt_inv}/went", data={"went": "brilliantly"})
check("a word outside the vocabulary is refused", r.status_code == 400, r.status_code)
r = client.post(f"/applications/{_wt}/events/{_wt_rej}/went", data={"went": "well"})
check("a rating on anything but a round you sat is 404, like a reason off a rejection",
      r.status_code == 404, r.status_code)
r = client.post(f"/applications/{northwind_app}/events/{_wt_inv}/went", data={"went": "well"})
check("and so is another application's round", r.status_code == 404, r.status_code)

_wt2 = _new_app("Rated Screen Co", 12)


def _rating_owed():
    with db.connect() as conn:
        return {str(r["id"]): r for r in analytics.ratings_owed(conn, user_id)}


# A person getting in touch is not a round you sat (9 Oct 2026: the list's
# "11 interviews" counted an agency recruiter's pitch, filed by hand as "They
# reached out", where trace.rounds said no round). Owed no word, takes none.
client.post(f"/applications/{_wt2}/events", data={"type": "engaged", "occurred_on": _ago(6)})
with db.connect() as conn:
    _wt2_touch = conn.execute("SELECT id FROM events WHERE application_id = %s AND type = 'engaged'",
                              (_wt2,)).fetchone()["id"]
check("a person getting in touch is owed no word and takes none — 404, like any non-round",
      _wt2 not in _rating_owed()
      and client.post(f"/applications/{_wt2}/events/{_wt2_touch}/went",
                      data={"went": "well"}).status_code == 404
      and f"/events/{_wt2_touch}/went" not in client.get(f"/applications/{_wt2}").text)
client.post(f"/applications/{_wt2}/events", data={"type": "interview_invite", "occurred_on": _ago(4)})
with db.connect() as conn:
    _wt2_call = conn.execute("SELECT id FROM events WHERE application_id = %s "
                             "AND type = 'interview_invite'", (_wt2,)).fetchone()["id"]

# /follow-ups asks for the word (analytics.rating_owed_sql): an open thread
# whose newest round you sat has passed unrated, no offer since.
_o = _rating_owed()
check("a round you sat on an open thread, its day passed and no word yet, is owed one — the "
      "interview filed by hand; the rejected thread's interview is not",
      _wt2 in _o and _o[_wt2]["round_type"] == "interview_invite"
      and str(_o[_wt2]["event_id"]) == str(_wt2_call) and _wt not in _o, sorted(_o))
_fu = client.get("/follow-ups").text
_rrow = _row_on("/follow-ups", "Rated Screen Co")
check("the page asks first of all: the row names the round and its day, and its select posts to "
      "that event and comes back here",
      'id="rate"' in _fu
      and all(_fu.index('id="rate"') < _fu.index(s) for s in ('id="by-email"', 'id="rounds"', 'id="nudge"')
              if s in _fu)
      and f'action="/applications/{_wt2}/events/{_wt2_call}/went"' in _rrow
      and 'value="/follow-ups"' in _rrow and "They invited you to interview on" in _rrow
      and '<option value="well">went well</option>' in _rrow and ">4d<" in _rrow, _rrow[:700])
with db.connect() as conn:
    _q = analytics.queue(conn, user_id)
    _n = (analytics.queue_count(conn, user_id), len(_o), analytics.email_owed_count(conn, user_id),
          len(_q["rounds"]), len(_q["nudge"]))
check("the pill counts these rows with the rest of the page's moves", _n[0] == sum(_n[1:]), _n)
with db.connect() as conn:
    _fut = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'interview_invite', 'email', now(), %s) RETURNING id",
        (user_id, _wt2, Json({"stated_date": _ago(-1)}))).fetchone()["id"]
check("a newer invitation for a day still to come takes the thread off the list until then",
      _wt2 not in _rating_owed())
with db.connect() as conn:
    conn.execute("UPDATE events SET payload = %s WHERE id = %s", (Json({"stated_date": _ago(2)}), _fut))
_o = _rating_owed()
_rrow = _row_on("/follow-ups", "Rated Screen Co")
check("...and once that day has passed, it is the round asked about, by the interview's day, not "
      "the email's",
      _wt2 in _o and str(_o[_wt2]["event_id"]) == str(_fut)
      and "Interview on " in _rrow and ">2d<" in _rrow, (_o.get(_wt2), _rrow[:400]))
with db.connect() as conn:
    _off = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'offer', 'manual', now(), '{}') RETURNING id", (user_id, _wt2)).fetchone()["id"]
check("an offer since puts the outcome on record: no longer asked", _wt2 not in _rating_owed())
with db.connect() as conn:
    conn.execute("DELETE FROM events WHERE id IN (%s, %s)", (_off, _fut))
r = client.post(f"/applications/{_wt2}/events/{_wt2_call}/went",
                data={"went": "well", "redirect_to": "/follow-ups"})
check("an interview filed by hand takes a rating too — from the page's own row, which then leaves",
      r.status_code == 303 and r.headers["location"] == "/follow-ups"
      and _wt_event(_wt2_call)["payload"]["went"] == "well"
      and _wt2 not in _rating_owed() and "Rated Screen Co" not in client.get("/follow-ups").text,
      r.headers.get("location"))
_t2 = _wt_event(_wt2_call)["payload"]["went_at"]
r = client.post(f"/applications/{_wt2}/events/{_wt2_call}/edit",
                data={"type": "interview_invite", "occurred_on": _ago(5),
                      "note": "the hiring manager rang"})
_p = _wt_event(_wt2_call)["payload"]
check("re-saving the event through the edit form carries the rating and its time across",
      r.status_code == 303 and _p.get("went") == "well" and _p.get("went_at") == _t2
      and _p.get("note") == "the hiring manager rang", _p)
r = client.post(f"/applications/{_wt2}/events/{_wt2_call}/edit",
                data={"type": "engaged", "occurred_on": _ago(5), "note": "the hiring manager rang"})
check("re-typed as something that is not a round you sat — a person getting in touch, since "
      "9 Oct 2026 — it drops the rating",
      r.status_code == 303 and "went" not in _wt_event(_wt2_call)["payload"],
      _wt_event(_wt2_call)["payload"])
client.post(f"/applications/{_wt2}/events/{_wt2_call}/edit",
            data={"type": "interview_invite", "occurred_on": _ago(5)})
client.post(f"/applications/{_wt2}/events/{_wt2_call}/went", data={"went": "well"})

with db.connect() as conn:
    _apps, _evs = analytics.facts(conn, user_id)
_fx = {str(f["id"]): f for f in insights.build_facts(_apps, _evs, datetime.now(timezone.utc), 10)}
check("facts: the rejected one reads its rating beside the rejection, filed in hindsight",
      _fx[_wt]["went"] == "mixed" and _fx[_wt]["went_next"] == "rejected"
      and _fx[_wt]["went_hindsight"],
      {k: _fx[_wt][k] for k in ("went", "went_next", "went_hindsight")})
check("the interview filed by hand is rated well and still waiting, which is not hindsight",
      _fx[_wt2]["went"] == "well" and _fx[_wt2]["went_next"] == "waiting"
      and not _fx[_wt2]["went_hindsight"],
      {k: _fx[_wt2][k] for k in ("went", "went_next", "went_hindsight")})
r = client.get("/analytics")
_pnl = r.text.split("Your interviews, as you rated them")[1].split("</h2>", 1)[1].split("<h2")[0]
check("the page has the panel: a row per word plus the unrated, a column per outcome, each "
      "record a square linking to it",
      r.status_code == 200
      and all(w in _pnl for w in ("went well", "mixed", "went badly", "not rated"))
      and all(w in _pnl for w in insights.NEXT_LABELS.values())
      and f'href="/applications/{_wt}"' in _pnl and f'href="/applications/{_wt2}"' in _pnl,
      _pnl[:800])
check("...and says how many were rated after the fact",
      "rated after the outcome was known" in _pnl and 'href="#interviews"' in r.text)

r = client.post(f"/applications/{_wt}/events/{_wt_inv}/went", data={"went": ""})
check("blank clears the rating and its time; nothing else moves",
      r.status_code == 303 and _wt_event(_wt_inv)["payload"] == _before["payload"],
      _wt_event(_wt_inv)["payload"])
client.post(f"/applications/{_wt2}/events", data={"type": "interview_invite"})
check("a round sat today is not asked about until its day has passed", _wt2 not in _rating_owed())
# This section's records go, so the suites after count what they counted.
for _id in (_wt, _wt2):
    client.post(f"/applications/{_id}/delete")
check("this section's records are gone", client.get(f"/applications/{_wt}").status_code == 404)

print("interviews lost: the list's count opens exactly its rows (8 Oct 2026)")
# "I failed 2 interviews in total: one they rejected me after, one they went
# quiet on after a mixed or bad one." analytics.round_fate_sql buckets every
# application with a round you sat; the lede counts them and each count
# opens its rows; insights._went is its Python twin, held equal here over
# every application in this database.
_lf = {}
for _co, _went, _end in (("Lost Rejected Co", "well", "rejected"), ("Lost Quiet Co", "mixed", "quiet"),
                         ("Unexplained Co", "well", "quiet"), ("Quiet Unrated Co", None, "quiet"),
                         ("Visa Stop Co", "well", "visa"), ("Role Closed Co", "well", "role_closed")):
    _id = _new_app(_co, 40)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
            "VALUES (%s, %s, 'interview_invite', 'email', now() - interval '30 days', %s)",
            (user_id, _id, Json({"went": _went, "went_at": "2026-09-09T00:00:00+00:00"} if _went else {})))
    if _end == "rejected":
        client.post(f"/applications/{_id}/events", data={"type": "rejected", "occurred_on": _ago(20)})
    elif _end in ("visa", "role_closed"):
        # A screen, then "we do not sponsor" / "the role is closed": a
        # rejection with a reason that is not the interview.
        client.post(f"/applications/{_id}/events", data={"type": "rejected", "reason": _end,
                                                         "occurred_on": _ago(20)})
    else:
        client.post(f"/applications/{_id}/close", data={"action": "quiet", "occurred_on": _ago(20)})
    _lf[_co] = _id
with db.connect() as conn:
    _rows = {str(r["id"]): (r["origin"], r["fate"]) for r in conn.execute(
        f"SELECT a.id, a.origin, {analytics.round_fate_sql('a')} AS fate FROM applications a "
        f"WHERE a.user_id = %s", (user_id,)).fetchall()}
    _fa, _fe = analytics.facts(conn, user_id)
    _summ = analytics.summary(conn, user_id, False)
_bf = insights.build_facts(_fa, _fe, datetime.now(timezone.utc), 10)
_py = {str(f["id"]): f["round_fate"] for f in _bf}
check("the SQL bucket is insights._went's, on every application in this database",
      {k: v[1] for k, v in _rows.items()} == _py and len(_rows) > 20,
      [(k, _rows[k][1], _py.get(k)) for k in _rows if _rows[k][1] != _py.get(k)][:5])
# The interviews figure and the rounds tag are one notion (9 Oct 2026): until
# then the figure took a person getting in touch as a round you sat, which
# trace.rounds never has, and the list counted an agency's pitch email as one
# of "11 interviews" on a row whose tag said no round.
_sat_ids = {k for k, v in _rows.items() if v[1] is not None}
_round_ids = {str(f["id"]) for f in _bf if f["n_rounds"]}
check("an application counts as an interview exactly when trace.rounds finds a round on it",
      _sat_ids == _round_ids and _sat_ids, (_sat_ids ^ _round_ids))
check("the six fixtures land in their buckets — a visa stop or a closed role after a round is "
      "'stopped', not lost",
      _rows[_lf["Lost Rejected Co"]][1] == "rejected" and _rows[_lf["Lost Quiet Co"]][1] == "lost_quiet"
      and _rows[_lf["Unexplained Co"]][1] == "unexplained"
      and _rows[_lf["Quiet Unrated Co"]][1] == "quiet"
      and _rows[_lf["Visa Stop Co"]][1] == "stopped" and _rows[_lf["Role Closed Co"]][1] == "stopped",
      {k: _rows[v][1] for k, v in _lf.items()})
check("every bucket the SQL returned has a label", {v[1] for v in _rows.values()} - {None}
      <= set(analytics.ROUND_FATES), {v[1] for v in _rows.values()})
_figs = re.sub(r"\s+", " ", client.get("/").text.split('<div class="figures"')[1].split("</div>")[0])
check("the figures count the interviews and the lost, with its two parts under the figure, the "
      "visa stop and the unexplained, each a link to its rows",
      f'href="/?interviews=sat"><b>{_summ["sat"]}</b><span>interviews</span></a>' in _figs
      and f'href="/?interviews=lost"><b>{_summ["lost"]}</b><span>lost</span> <small>'
          f'{_summ["lost_rejected"]} after a rejection · {_summ["lost_quiet"]} quiet after a mixed or bad '
          f'one</small></a>' in _figs
      and f'href="/?interviews=stopped"><b>{_summ["stopped"]}</b><span>stopped, not the interview</span> '
          f'<small>{_summ["stopped_visa"]} visa · {_summ["stopped_role_closed"]} role closed</small></a>' in _figs
      and f'href="/?interviews=unexplained"><b>{_summ["unexplained"]}</b><span>quiet after a good one</span>'
      in _figs
      and _summ["lost"] == _summ["lost_rejected"] + _summ["lost_quiet"] >= 2 and _summ["unexplained"] >= 1
      and _summ["stopped"] == _summ["stopped_visa"] + _summ["stopped_role_closed"] >= 2
      and _summ["stopped_by"] == [("visa", _summ["stopped_visa"]), ("role closed", _summ["stopped_role_closed"])],
      _figs)


def _fate_count(key, inbound):
    return sum(1 for o, v in _rows.values() if (o == "inbound") == inbound
               and (v == key or (key == "lost" and v in analytics.LOST_FATES)
                    or (key == "sat" and v is not None)))


check("the figures' numbers are the record page's rows",
      _summ["sat"] == sum(_fate_count(k, False) for k in analytics.ROUND_FATES) == _fate_count("sat", False)
      and _summ["lost"] == _fate_count("lost", False), (_summ["sat"], _summ["lost"]))
# The registry, looped, on both pages: a bucket added later is covered the
# day it is added.
for _k in ["sat", "lost", *analytics.ROUND_FATES]:
    for _pg, _inb in (("/", False), ("/inbound", True)):
        _shown = client.get(f"{_pg}?interviews={_k}").text.count('<a class="tl" href="/applications/')
        check(f"{_pg}?interviews={_k}: {_fate_count(_k, _inb)} counted, {_shown} shown",
              _fate_count(_k, _inb) == _shown, (_fate_count(_k, _inb), _shown))
r = client.get("/?interviews=lost")
check("a filtered list names its filter in the lede's words, with a way out, and keeps it: the "
      "search re-submits it, a status link carries it",
      "Only interviews you lost: followed by a rejection or you rated mixed or badly, then silence."
      in r.text and "Show every interview" in r.text
      and 'name="interviews" value="lost"' in r.text
      and re.search(r'href="/\?status=\w+&amp;interviews=lost"', r.text) is not None, r.status_code)
r = client.get("/?interviews=telepathy")
check("an unknown value is ignored, not an error",
      r.status_code == 200 and 'class="filter-note"' not in r.text, r.status_code)
r = client.get("/?interviews=lost&q=nothing-is-called-this")
check("an empty filtered page says what it was looking for",
      r.status_code == 200 and "Nothing matches" in r.text, r.status_code)
for _id in _lf.values():
    client.post(f"/applications/{_id}/delete")
check("this section's records are gone",
      all(client.get(f"/applications/{_id}").status_code == 404 for _id in _lf.values()))

print("what an invitation counts as: the status reads the rounds (9 Oct 2026)")
# An automated screening questionnaire and its reminder made a real thread
# "interviewing" while trace.rounds and the interviews figure said no round:
# the status view ranked events by type alone. Migration 021 ranks each as
# what it counts as (analytics.effective_type_sql, insights.effective_type):
# a non-round kind as the confirmation, a line excluded by itself as engaged.
from collections import defaultdict as _defaultdict                      # noqa: E402


def _invite(app_id, days_ago, payload, source="email"):
    with db.connect() as conn:
        return conn.execute(
            "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
            "VALUES (%s, %s, 'interview_invite', %s, now() - make_interval(days => %s), %s) RETURNING id",
            (user_id, app_id, source, days_ago, Json(payload))).fetchone()["id"]


_ec = {"questionnaire": _new_app("Questionnaire Only Co", 12),
       "scheduling": _new_app("Scheduling Only Co", 12),
       "not_a_round": _new_app("Not A Round Co", 12),
       "screened_out": _new_app("Questionnaire Then No Co", 30),
       "real": _new_app("Real Round Co", 12)}
# The kind on the invitation only: its reminder, a week on, names the same
# deadline, so it is the same round and a questionnaire too.
_invite(_ec["questionnaire"], 11, {"round_kind": "questionnaire", "invite_role": "invitation",
                                   "stated_date": _ago(-3)})
_invite(_ec["questionnaire"], 4, {"invite_role": "reminder", "stated_date": _ago(-3)})
_invite(_ec["scheduling"], 2, {"invite_role": "scheduling"})
_nar = _invite(_ec["not_a_round"], 3, {"invite_role": "invitation", "stated_date": _ago(2)})
client.post(f"/applications/{_ec['not_a_round']}/events/{_nar}/round", data={"round_is": "none"})
_invite(_ec["screened_out"], 29, {"round_kind": "questionnaire"})
client.post(f"/applications/{_ec['screened_out']}/events", data={"type": "rejected", "occurred_on": _ago(20)})
_invite(_ec["real"], 3, {"round_kind": "screen", "invite_role": "invitation", "stated_date": _ago(2)})
_ecs = {k: _state(v)[0] for k, v in _ec.items()}
check("a questionnaire, kind on its line or on its round's other line, is part of applying: the "
      "status is the confirmation's, and the row reads applied",
      _ecs["questionnaire"] == "confirmation"
      and re.search(r"Questionnaire Only Co.*?<span class=\"badge\"[^>]*>applied</span>",
                    client.get("/").text, re.S) is not None, _ecs)
check("a mail arranging an interview, or a line you said is not a round, is a person writing: "
      "engaged; an interview is still one",
      _ecs["scheduling"] == "engaged" and _ecs["not_a_round"] == "engaged"
      and _ecs["real"] == "interview_invite", _ecs)
check("a questionnaire then a rejection ended without a round, not after one",
      "Questionnaire Then No Co" in client.get("/?how=no_round").text
      and "Questionnaire Then No Co" not in client.get("/?how=after_round").text)
r = client.post(f"/applications/{_ec['questionnaire']}/close", data={"action": "quiet"})
check("and a questionnaire opens no “They went quiet”: nobody has answered",
      "event_error=" in r.headers.get("location", "") and not _closes(_ec["questionnaire"]),
      r.headers.get("location"))
with db.connect() as conn:
    _sql_eff = {str(x["id"]): x["t"] for x in conn.execute(
        f"SELECT e.id, {analytics.effective_type_sql('e')} AS t FROM events e "
        f"JOIN applications a ON a.id = e.application_id WHERE a.user_id = %s", (user_id,)).fetchall()}
    _view = {str(x["application_id"]): x["status"] for x in conn.execute(
        "SELECT application_id, status FROM application_status WHERE user_id = %s", (user_id,)).fetchall()}
    _fa, _fe = analytics.facts(conn, user_id)
    _vdef = conn.execute("SELECT pg_get_viewdef('application_status'::regclass) AS d").fetchone()["d"]
_bfx = {str(f["id"]): f for f in insights.build_facts(_fa, _fe, datetime.now(timezone.utc), 10)}
_qf = _bfx[_ec["questionnaire"]]
check("a questionnaire is still heard back, as LinkedIn's “viewed” notice is, but neither an answer "
      "nor a round",
      _qf["signal_at"] is not None and _qf["answer_at"] is None and _qf["round_at"] is None,
      {k: _qf[k] for k in ("signal_at", "answer_at", "round_at")})
_evs_by = _defaultdict(list)
for _e in _fe:
    _evs_by[str(_e["application_id"])].append(_e)
_py_eff = {str(_e["id"]): insights.effective_type(_e, _evs_by[str(_e["application_id"])]) for _e in _fe}
check("what each event counts as, in SQL and in Python, on every event in this database",
      _sql_eff == _py_eff and len(_sql_eff) > 100,
      [(k, _sql_eff[k], _py_eff.get(k)) for k in _sql_eff if _sql_eff[k] != _py_eff.get(k)][:5])
# The view's own copy of the rule (migration 021's static SQL), by its rank:
# two events of one rank at one instant may be named either way.
_RANK = {"withdrawn": 60, "rejected": 60, "offer": 55, "interview_invite": 50, "engaged": 45,
         "viewed": 40, "confirmation": 30, "applied": 30, "interested": 10}


def _py_status(evs):
    ranked = [(_RANK[insights.effective_type(e, evs)], e["occurred_at"]) for e in evs
              if e["type"] in _RANK]
    top = max(ranked, default=None)
    return top[0] if top else 10


check("the status view ranks every application as the Python twin does",
      all(_RANK.get(_view[k], 10) == _py_status(_evs_by[k]) for k in _view) and len(_view) > 20,
      [(k, _view[k], _py_status(_evs_by[k])) for k in _view
       if _RANK.get(_view[k], 10) != _py_status(_evs_by[k])][:5])
_iv_view = {k for k, v in _view.items() if v == "interview_invite"}
_iv_rounds = {k for k, f in _bfx.items() if f["n_rounds"] and not f["offer"] and f["ended_at"] is None}
check("“interviewing” is exactly an open thread trace.rounds finds a round on",
      _iv_view == _iv_rounds and _iv_view, _iv_view ^ _iv_rounds)
check("the view states trace's vocabularies and span — a change there needs a migration restating it",
      all(f"'{v}'" in _vdef for v in (*web.trace.NON_ROUND_KINDS, *web.trace.NON_ROUND_ROLES,
                                       web.trace.NOT_A_ROUND))
      and re.search(rf"days\s*=>\s*{web.trace.ROUND_SPAN_DAYS}\b", _vdef) is not None, _vdef[:600])
for _id in _ec.values():
    client.post(f"/applications/{_id}/delete")
check("this section's records are gone",
      all(client.get(f"/applications/{_id}").status_code == 404 for _id in _ec.values()))

print("interview rounds: one interview is several events (8 Oct 2026)")
# An invitation and its calendar notification name one day: one round, one
# select, and a rating on the invitation keeps /follow-ups from asking again
# on the notification. A second invitation two weeks on is round 2.
_rr = _new_app("Two Rounds Co", 40)
with db.connect() as conn:
    _rr_inv = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'interview_invite', 'email', now() - interval '21 days', %s) RETURNING id",
        (user_id, _rr, Json({"stated_date": _ago(20)}))).fetchone()["id"]
    _rr_note = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'interview_invite', 'email', now() - interval '20 days', %s) RETURNING id",
        (user_id, _rr, Json({"stated_date": _ago(20)}))).fetchone()["id"]
r = client.get(f"/applications/{_rr}")
check("the page says one round; both lines say round 1; one select, on the newest line",
      "1 interview round<" in r.text and r.text.count("<small>round 1</small>") == 2
      and r.text.count('/went"') == 1 and f"/events/{_rr_note}/went" in r.text, r.status_code)
check("/follow-ups asks about the round through that same event",
      str(_rating_owed()[_rr]["event_id"]) == str(_rr_note), _rating_owed().get(_rr))
client.post(f"/applications/{_rr}/events/{_rr_inv}/went", data={"went": "well"})
r = client.get(f"/applications/{_rr}")
check("a rating on the invitation is the round's: the select moves to it, and /follow-ups "
      "stops asking though the notification carries none",
      "selected>went well" in r.text and r.text.count('/went"') == 1
      and f"/events/{_rr_inv}/went" in r.text and _rr not in _rating_owed(), r.status_code)
_row = client.get("/?q=Two+Rounds").text.split(f'href="/applications/{_rr}"')[1].split("</a>")[0]
check("the list row wears the count in grey, the day in its title",
      ">1 round</span>" in _row and "Interview round on " in _row
      and "the day each invitation named" in _row, _row[-500:])
with db.connect() as conn:
    _rr_two = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'interview_invite', 'email', now() - interval '6 days', %s) RETURNING id",
        (user_id, _rr, Json({"stated_date": _ago(5)}))).fetchone()["id"]
r = client.get(f"/applications/{_rr}")
check("a second invitation two weeks on is round 2 of 2, with its own select; the first keeps its rating",
      "2 interview rounds<" in r.text and "<small>round 2 of 2</small>" in r.text
      and r.text.count("<small>round 1 of 2</small>") == 2 and r.text.count('/went"') == 2
      and f"/events/{_rr_two}/went" in r.text, r.status_code)
check("...and it is owed a word, through its own event; the row says 2 rounds",
      str(_rating_owed()[_rr]["event_id"]) == str(_rr_two)
      and ">2 rounds</span>" in client.get("/?q=Two+Rounds").text, _rating_owed().get(_rr))
r = client.get("/analytics")
_dp = r.text.split("How far you got")[1].split("<h2")[0] if "How far you got" in r.text else ""
check("/analytics draws how far you got, rows by rounds reached, this thread in the 2-rounds row",
      r.status_code == 200 and '2 rounds <b>' in _dp and f'href="/applications/{_rr}"' in _dp, _dp[:600])

# Your correction of the grouping (set_round_is): the first real thread
# derived 4 rounds and had sat 2.
r = client.post(f"/applications/{_rr}/events/{_rr_two}/round", data={"round_is": "none"})
_pg = client.get(f"/applications/{_rr}").text
check("“not a round” on the second invitation: one round again, the line says so, offers to count "
      "it again, and takes no rating",
      r.status_code == 303 and "1 interview round<" in _pg and "<small>not a round</small>" in _pg
      and ">counts as a round</button>" in _pg and f"/events/{_rr_two}/went" not in _pg
      and _pg.count("<small>round 1</small>") == 2,
      (r.status_code, "1 interview round<" in _pg, "<small>not a round</small>" in _pg,
       ">counts as a round</button>" in _pg, f"/events/{_rr_two}/went" not in _pg))
with db.connect() as conn:
    _sql_fate = conn.execute(f"SELECT {analytics.round_fate_sql('a')} AS f FROM applications a "
                             f"WHERE a.id = %s::uuid", (_rr,)).fetchone()["f"]
    _fa, _fe = analytics.facts(conn, user_id)
_py_fate = next(f for f in insights.build_facts(_fa, _fe, datetime.now(timezone.utc), 10)
                if str(f["id"]) == _rr)
check("every count leaves it out: the list tag, /follow-ups, and both twins of the fate anchor",
      ">1 round</span>" in client.get("/?q=Two+Rounds").text and _rr not in _rating_owed()
      and _py_fate["n_rounds"] == 1 and _sql_fate == _py_fate["round_fate"] == "waiting",
      (_sql_fate, _py_fate["round_fate"], _py_fate["n_rounds"]))
r = client.post(f"/applications/{_rr}/events/{_rr_inv}/round", data={"round_is": "sometimes"})
check("a value outside the two is refused", r.status_code == 400, r.status_code)
with db.connect() as conn:
    _rr_applied = conn.execute("SELECT id FROM events WHERE application_id = %s AND type = 'applied'",
                               (_rr,)).fetchone()["id"]
r = client.post(f"/applications/{_rr}/events/{_rr_applied}/round", data={"round_is": "none"})
check("and anything but an invitation is 404", r.status_code == 404, r.status_code)
r = client.post(f"/applications/{_rr}/events/{_rr_two}/round", data={"round_is": ""})
check("blank counts it again: round 2 of 2 is back, owed its word",
      r.status_code == 303 and "<small>round 2 of 2</small>" in client.get(f"/applications/{_rr}").text
      and str(_rating_owed()[_rr]["event_id"]) == str(_rr_two), _rating_owed().get(_rr))
# A round filed by hand on the day the first invitation named (9 Oct 2026):
# its own round, not the invitation's — a recruiter screen beside a test.
client.post(f"/applications/{_rr}/events", data={"type": "interview_invite", "occurred_on": _ago(20),
                                                 "note": "recruiter screen"})
_pg = client.get(f"/applications/{_rr}").text
check("a hand-filed round on an invitation's day is a round of its own: three rounds, the hand-filed "
      "one second by the clock",
      "3 interview rounds<" in _pg and _pg.count("<small>round 1 of 3</small>") == 2
      and _pg.count("<small>round 2 of 3</small>") == 1 and "<small>round 3 of 3</small>" in _pg
      and ">3 rounds</span>" in client.get("/?q=Two+Rounds").text,
      sorted(set(re.findall(r"<small>round (\d of \d)</small>", _pg))))
with db.connect() as conn:
    _rr_hand = conn.execute("SELECT id FROM events WHERE application_id = %s AND type = 'interview_invite' "
                            "AND source = 'manual'", (_rr,)).fetchone()["id"]
# The notification carries the suite's own time of day, later than the
# hand-filed round's local noon, so it goes too: the hand-filed round is
# then the newest sat event, on the rated invitation's own day.
for _e in (_rr_two, _rr_note):
    client.post(f"/applications/{_rr}/events/{_e}/round", data={"round_is": "none"})
check("the hand-filed round, newest, is owed its own word — the rating on the invitation of the same "
      "day is not its",
      str(_rating_owed()[_rr]["event_id"]) == str(_rr_hand), _rating_owed().get(_rr))

# What kind of round (9 Oct 2026): said once per round, worn on every line
# of it, in the list tag's title, and taken by the timeline form.
r = client.post(f"/applications/{_rr}/events/{_rr_hand}/kind", data={"kind": "screen"})
_pg = client.get(f"/applications/{_rr}").text
check("a round's kind, said on its line, follows the label and is the select's choice",
      r.status_code == 303 and "<small>round 2 of 2, recruiter screen</small>" in _pg
      and "selected>recruiter screen" in _pg, re.findall(r"<small>round [^<]+</small>", _pg))
client.post(f"/applications/{_rr}/events/{_rr_inv}/kind", data={"kind": "test"})
_pg = client.get(f"/applications/{_rr}").text
_row = client.get("/?q=Two+Rounds").text.split(f'href="/applications/{_rr}"')[1].split("</a>")[0]
check("...and the list tag's title names each round's day and kind",
      "<small>round 1 of 2, coding test or take-home</small>" in _pg
      and "(coding test or take-home), " in _row and "(recruiter screen)" in _row, _row[-400:])
check("a kind outside the vocabulary is refused; anything but an invitation is 404",
      client.post(f"/applications/{_rr}/events/{_rr_hand}/kind", data={"kind": "vibes"}).status_code == 400
      and client.post(f"/applications/{_rr}/events/{_rr_applied}/kind",
                      data={"kind": "screen"}).status_code == 404)
client.post(f"/applications/{_rr}/events", data={"type": "interview_invite", "occurred_on": _ago(2),
                                                 "round_kind": "technical", "note": "panel"})
_pg = client.get(f"/applications/{_rr}").text
check("the timeline form files a hand-filed round with its kind",
      "<small>round 3 of 3, technical interview</small>" in _pg,
      re.findall(r"<small>round [^<]+</small>", _pg))
with db.connect() as conn:
    _rr_tech = conn.execute("SELECT id FROM events WHERE application_id = %s "
                            "AND payload->>'round_kind' = 'technical'", (_rr,)).fetchone()["id"]
client.post(f"/applications/{_rr}/events/{_rr_tech}/round", data={"round_is": "none"})
client.post(f"/applications/{_rr}/events/{_rr_tech}/edit",
            data={"type": "interview_invite", "occurred_on": _ago(3), "note": "panel, moved"})
_p = _wt_event(_rr_tech)["payload"]
check("re-saving a hand-filed round through the edit form keeps its kind and your 'not a round'",
      _p.get("round_kind") == "technical" and _p.get("round_is") == "none"
      and _p.get("note") == "panel, moved", _p)
client.post(f"/applications/{_rr}/events/{_rr_hand}/kind", data={"kind": ""})
check("blank clears the kind", "<small>round 2 of 2</small>" in client.get(f"/applications/{_rr}").text)
# An automated questionnaire (9 Oct 2026): a kind that is not a round
# reached. Said once, on the round; its lines keep the label and leave
# every count, and the SQL twins agree.
r = client.post(f"/applications/{_rr}/events/{_rr_inv}/kind", data={"kind": "questionnaire"})
_pg = client.get(f"/applications/{_rr}").text
with db.connect() as conn:
    _sql_fate = conn.execute(f"SELECT {analytics.round_fate_sql('a')} AS f FROM applications a "
                             f"WHERE a.id = %s::uuid", (_rr,)).fetchone()["f"]
    _fa, _fe = analytics.facts(conn, user_id)
_py = next(f for f in insights.build_facts(_fa, _fe, datetime.now(timezone.utc), 10) if str(f["id"]) == _rr)
check("a questionnaire's line says so and takes no rating; the thread has one round, the hand-filed "
      "one, and both twins anchor on it",
      r.status_code == 303 and "<small>automated questionnaire</small>" in _pg
      and "1 interview round<" in _pg and "<small>round 1</small>" in _pg
      and f"/events/{_rr_inv}/went" not in _pg and f"/events/{_rr_inv}/kind" in _pg
      and _py["n_rounds"] == 1 and _sql_fate == _py["round_fate"] == "waiting"
      and ">1 round</span>" in client.get("/?q=Two+Rounds").text,
      (_sql_fate, _py["round_fate"], _py["n_rounds"], re.findall(r"<small>[^<]+</small>", _pg)))
client.post(f"/applications/{_rr}/events/{_rr_inv}/kind", data={"kind": "test"})
# What the mail did (stage 4, 9 Oct 2026): a scheduling reply filed as an
# invitation is no round, in both twins, and the line says why.
with db.connect() as conn:
    _rr_sched = conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'interview_invite', 'email', now() - interval '1 day', %s) RETURNING id",
        (user_id, _rr, Json({"invite_role": "scheduling"}))).fetchone()["id"]
    _sql_fate = conn.execute(f"SELECT {analytics.round_fate_sql('a')} AS f FROM applications a "
                             f"WHERE a.id = %s::uuid", (_rr,)).fetchone()["f"]
    _fa, _fe = analytics.facts(conn, user_id)
_py = next(f for f in insights.build_facts(_fa, _fe, datetime.now(timezone.utc), 10) if str(f["id"]) == _rr)
_pg = client.get(f"/applications/{_rr}").text
check("a mail the stage read as scheduling chatter is no round: the page says so, the count stays at "
      "the two real rounds, both twins skip it, and /follow-ups does not ask about it",
      "<small>arranging it, not a round</small>" in _pg and "2 interview rounds<" in _pg
      and _py["n_rounds"] == 2 and _sql_fate == _py["round_fate"]
      and ">2 rounds</span>" in client.get("/?q=Two+Rounds").text
      and str(_rating_owed().get(_rr, {}).get("event_id")) != str(_rr_sched),
      (_sql_fate, _py["round_fate"], _py["n_rounds"]))
client.post(f"/applications/{_rr}/events/{_rr_inv}/kind", data={"kind": "screen"})
check("your word on the kind replaces the email's reading (kind_source goes), the same word keeps it",
      "kind_source" not in _wt_event(_rr_inv)["payload"]
      and _wt_event(_rr_inv)["payload"]["round_kind"] == "screen")
client.post(f"/applications/{_rr}/delete")
check("this section's record is gone", client.get(f"/applications/{_rr}").status_code == 404)

print("\nALL WEB PATHS PASS")
