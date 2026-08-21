"""Tests for POST /captures (Phase 2 server side).

Run on a database with migrations + one user (independent of the other suites):
  TRACKER_API_TOKEN=testtok TRACKER_DATABASE_URL=postgresql:///tracker_test \
      python3 tests/test_captures.py
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
os.environ.setdefault("TRACKER_API_TOKEN", "testtok")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from pipeline import db
from pipeline.web import app

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {os.environ['TRACKER_API_TOKEN']}"}


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


def post(payload, headers=AUTH):
    return client.post("/captures", json=payload, headers=headers)


BASE = {
    "platform": "linkedin", "platform_job_id": "LI-cap-1",
    "url": "https://www.linkedin.com/jobs/view/999/",
    "company": "Sea Labs Pte. Ltd.", "title": "Senior AI Engineer",
    "jd_text": "Build LLM systems. Python, PyTorch.", "trigger": "apply",
    "note": "referred by K",
}

print("auth")
check("missing token rejected", post(BASE, headers={}).status_code == 401)
check("wrong token rejected",
      post(BASE, headers={"Authorization": "Bearer nope"}).status_code == 401)
check("unknown platform rejected",
      post({**BASE, "platform": "monster"}, headers=AUTH).status_code == 422)

print("create on apply")
r = post(BASE)
check("created", r.status_code == 200 and r.json()["created"] is True, r.text)
app_id = r.json()["application_id"]
with db.connect() as conn:
    a = conn.execute(
        """SELECT j.company_norm, s.status
           FROM applications a JOIN jobs j ON j.id = a.job_id
           JOIN application_status s ON s.application_id = a.id
           WHERE a.id = %s::uuid""", (app_id,)).fetchone()
    check("company normalized", a["company_norm"] == "sea labs", a)
    check("status applied", a["status"] == "applied", a)
    evs = [r_["type"] for r_ in conn.execute(
        "SELECT type FROM events WHERE application_id = %s::uuid ORDER BY type",
        (app_id,)).fetchall()]
    check("applied + note events", evs == ["applied", "note"], evs)

print("re-capture upserts, never duplicates")
r2 = post({**BASE, "jd_text": "Build LLM systems. Python, PyTorch. (updated)",
           "note": None})
check("same application returned", r2.json()["application_id"] == app_id, r2.text)
check("flagged enriched, not created",
      r2.json()["enriched"] is True and r2.json()["created"] is False, r2.text)
with db.connect() as conn:
    n_applied = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s::uuid "
        "AND type = 'applied'", (app_id,)).fetchone()["n"]
    check("applied event not duplicated", n_applied == 1, n_applied)
    jd = conn.execute(
        "SELECT jd_text FROM postings WHERE platform_job_id = 'LI-cap-1'"
    ).fetchone()["jd_text"]
    check("jd updated in place", jd.endswith("(updated)"), jd)

print("email_only record gets enriched instead of duplicated")
with db.connect() as conn:
    user_id = db.single_user_id(conn)
    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'harborview', 'Machine Learning Engineer') RETURNING id",
        (user_id,)).fetchone()
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, captured_via) "
        "VALUES (%s, %s, 'other', 'email_only')", (user_id, job["id"]))
    seeded_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, job["id"])).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'email', now() - interval '3 days', '{}')",
        (user_id, seeded_app))
    conn.commit()
r3 = post({"platform": "jobstreet", "platform_job_id": "JS-77",
           "company": "Harborview Pte. Ltd.", "title": "Machine Learning Engineer (LLM)",
           "jd_text": "the JD", "trigger": "apply"})
check("attached to the email_only application",
      r3.json()["application_id"] == str(seeded_app), r3.text)
check("marked enriched", r3.json()["enriched"] is True, r3.text)
with db.connect() as conn:
    n_post = conn.execute(
        "SELECT count(*) AS n FROM postings WHERE job_id = %s", (job["id"],)).fetchone()["n"]
    check("posting added to the same job (now 2)", n_post == 2, n_post)
    n_applied = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s AND type = 'applied'",
        (seeded_app,)).fetchone()["n"]
    check("email's applied event kept, no duplicate", n_applied == 1, n_applied)

print("platform-stated salary: parsing (migration 011)")
from pipeline import salary as _salary                              # noqa: E402

SG = "https://sg.jobstreet.com/job/1"
def _sal(raw, url=SG):
    r = _salary.parse(raw, url)
    return (r["salary_min"], r["salary_max"], r["salary_currency"], r["salary_period"])

check("a range on the SG site", _sal("$10,000 – $11,000 per month")
      == (10000, 11000, "SGD", "monthly"))
check("a single figure fills both ends", _sal("$5,500 per month")
      == (5500, 5500, "SGD", "monthly"))
check("'up to' is a ceiling, not a floor", _sal("Up to $10,000 per month")
      == (None, 10000, "SGD", "monthly"))
check("'from' is a floor, not a ceiling", _sal("From $8,000 per month")
      == (8000, None, "SGD", "monthly"))
check("annual is not silently treated as monthly",
      _sal("$120,000 – $150,000 per year") == (120000, 150000, "SGD", "annual"))
# The separator trap: Indonesia writes 15.000.000 where Singapore writes
# 15,000,000. Reading that dot as a decimal point turns 15 million into 15.
check("Indonesian dot grouping is not a decimal point",
      _sal("Rp15.000.000 – Rp20.000.000 per month",
           "https://id.jobstreet.co.id/job/1") == (15000000, 20000000, "IDR", "monthly"))
check("Malaysian RM", _sal("RM8,000 – RM12,000 per month",
                           "https://my.jobstreet.com/job/1")
      == (8000, 12000, "MYR", "monthly"))
# A bare "$" means different currencies on different SEEK sites, so the host
# decides — the symbol alone cannot.
check("a bare $ resolves by site, not by symbol",
      _salary.parse("$150,000 per annum",
                    "https://www.seek.com.au/job/1")["salary_currency"] == "AUD")
check("hourly rates survive the small-number floor",
      _sal("$45 per hour") == (45, 45, "SGD", "hourly"))
check("an unpriced ad keeps its words and stores no numbers",
      _salary.parse("Competitive salary", SG)["salary_raw"] == "Competitive salary"
      and _sal("Competitive salary")[:2] == (None, None))
check("nothing in, nothing out", _salary.parse(None)["salary_raw"] is None)

print("platform-stated salary: capture round-trip")
r_sal = post({"platform": "jobstreet", "platform_job_id": "JS-pay",
              "url": "https://sg.jobstreet.com/job/77001",
              "company": "Lumen Systems", "title": "Platform Engineer",
              "trigger": "apply", "salary_raw": "$10,000 – $11,000 per month",
              "work_type": "Full time", "salary_match": True,
              "posted_label": "1d ago", "location": "Central Region"})
with db.connect() as conn:
    p = conn.execute(
        "SELECT salary_raw, salary_min, salary_max, salary_currency, salary_period, "
        "       work_type, salary_match, location, posted_label "
        "FROM postings WHERE platform_job_id = 'JS-pay'").fetchone()
    check("the displayed string is kept verbatim",
          p["salary_raw"] == "$10,000 – $11,000 per month", p["salary_raw"])
    check("parsed into comparable numbers server-side",
          (p["salary_min"], p["salary_max"], p["salary_currency"], p["salary_period"])
          == (10000, 11000, "SGD", "monthly"), dict(p))
    check("work type, salary match, location and posted label all land",
          (p["work_type"], p["salary_match"], p["location"], p["posted_label"])
          == ("Full time", True, "Central Region", "1d ago"), dict(p))

# Re-capture fills gaps and never blanks what's already there — same contract
# as every other posting column.
post({"platform": "jobstreet", "platform_job_id": "JS-pay",
      "company": "Lumen Systems", "title": "Platform Engineer", "trigger": "apply"})
with db.connect() as conn:
    p2 = conn.execute("SELECT salary_min, work_type FROM postings "
                      "WHERE platform_job_id = 'JS-pay'").fetchone()
    check("a later capture without salary doesn't erase it",
          (p2["salary_min"], p2["work_type"]) == (10000, "Full time"), dict(p2))

print("a submit signal corrects the applied time the opening click recorded")
# JobStreet's apply button navigates, so the capture happens when the flow
# OPENS and the record is minutes early — a real one came out at 01:20 for an
# application sent at 01:25. The record is still written on that first click
# (losing it would be far worse than an early timestamp); `completed` is the
# later, genuine submit correcting when it happened.
r_open = post({"platform": "jobstreet", "platform_job_id": "JS-late",
               "url": "https://sg.jobstreet.com/job/8899",
               "company": "Lantern Group", "title": "Platform Engineer",
               "jd_text": "the JD", "trigger": "apply"})
late_app = r_open.json()["application_id"]
with db.connect() as conn, conn.transaction():
    # Backdate it so "moved forward to now" is measurable at all.
    conn.execute(
        "UPDATE events SET occurred_at = now() - interval '5 minutes' "
        "WHERE application_id = %s::uuid AND type = 'applied'", (late_app,))
    opened_at = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s::uuid "
        "AND type = 'applied'", (late_app,)).fetchone()["occurred_at"]

r_done = post({"platform": "jobstreet", "platform_job_id": "JS-late",
               "company": "Lantern Group", "title": "Platform Engineer",
               "trigger": "apply", "completed": True})
check("the submit lands on the same application, not a second one",
      r_done.json()["application_id"] == late_app, r_done.text)
with db.connect() as conn:
    rows = conn.execute(
        "SELECT occurred_at, source FROM events WHERE application_id = %s::uuid "
        "AND type = 'applied'", (late_app,)).fetchall()
    check("still exactly one applied event", len(rows) == 1, rows)
    check("its time moved forward to the submit", rows[0]["occurred_at"] > opened_at,
          (opened_at, rows[0]["occurred_at"]))

# Without the flag nothing moves — an ordinary re-capture is not a submit.
with db.connect() as conn, conn.transaction():
    conn.execute(
        "UPDATE events SET occurred_at = now() - interval '5 minutes' "
        "WHERE application_id = %s::uuid AND type = 'applied'", (late_app,))
post({"platform": "jobstreet", "platform_job_id": "JS-late",
      "company": "Lantern Group", "title": "Platform Engineer", "trigger": "apply"})
with db.connect() as conn:
    still = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s::uuid "
        "AND type = 'applied'", (late_app,)).fetchone()["occurred_at"]
    check("a plain re-capture leaves the time alone",
          (datetime.now(timezone.utc) - still).total_seconds() > 60, still)

# The email path knows a truer time than a click ever does, and a stray match
# on some other page must never drag it backwards or forwards.
with db.connect() as conn:
    email_time = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s AND type = 'applied'",
        (seeded_app,)).fetchone()["occurred_at"]
post({"platform": "jobstreet", "platform_job_id": "JS-77",
      "company": "Harborview Pte. Ltd.", "title": "Machine Learning Engineer (LLM)",
      "trigger": "apply", "completed": True})
with db.connect() as conn:
    after = conn.execute(
        "SELECT occurred_at, source FROM events WHERE application_id = %s "
        "AND type = 'applied'", (seeded_app,)).fetchone()
    check("an email-sourced applied event is never rewritten by a submit signal",
          after["occurred_at"] == email_time and after["source"] == "email", after)

print("Easy Apply form answers")
from pipeline.answers import MAX_ANSWER, clean, norm_question   # noqa: E402

check("norm strips case, punctuation, and the required marker",
      norm_question("Years of experience with Python? *")
      == norm_question("years of experience with python"))
check("norm keeps distinct questions distinct",
      norm_question("Notice period") != norm_question("Notice period in weeks"))
check("clean drops answerless and unlabelled rows",
      [r["question"] for r in clean([
          {"question": "Notice period", "answer": "1 month"},
          {"question": "Salary", "answer": "   "},
          {"question": "", "answer": "orphan"}])] == ["Notice period"])
# Migration 010: a repeated label is a REPEATER (one "City" per employer in a
# work history), not a correction. Before it, everything after the first was
# silently overwritten and a real multi-employer history came out as one row.
check("clean keeps every answer to a repeated question, numbered in form order",
      clean([{"question": "A", "answer": "1"}, {"question": "B", "answer": "2"},
             {"question": "A?", "answer": "3"}])
      == [{"question": "A", "question_norm": "a", "answer": "1",
           "field_type": None, "ordinal": 0, "occurrence": 0},
          {"question": "B", "question_norm": "b", "answer": "2",
           "field_type": None, "ordinal": 1, "occurrence": 0},
          {"question": "A?", "question_norm": "a", "answer": "3",
           "field_type": None, "ordinal": 2, "occurrence": 1}])
check("occurrence counts per question, not across the form",
      [r["occurrence"] for r in clean([
          {"question": "City", "answer": "SG"}, {"question": "Industry", "answer": "SaaS"},
          {"question": "City", "answer": "JKT"}, {"question": "Industry", "answer": "Fin"},
          {"question": "City", "answer": "KL"}])] == [0, 0, 1, 1, 2])

QA = [
    {"question": "How many years of experience do you have with Python?",
     "answer": "8", "type": "number"},
    {"question": "What is your notice period? *", "answer": "1 month", "type": "text"},
    {"question": "Are you legally authorised to work in Singapore?",
     "answer": "Yes", "type": "radio"},
    {"question": "Expected salary", "answer": "   ", "type": "text"},   # dropped
]
r5 = post({"platform": "linkedin", "platform_job_id": "LI-qa-1",
           "url": "https://www.linkedin.com/jobs/view/4242/",
           "company": "Meridian Systems", "title": "Staff AI Engineer",
           "jd_text": "the JD", "trigger": "apply", "answers": QA})
qa_app = r5.json()["application_id"]
check("blank answer not counted", r5.json()["answers"] == 3, r5.text)
with db.connect() as conn:
    rows = conn.execute(
        "SELECT question, question_norm, answer, field_type, ordinal, posting_id "
        "FROM application_answers WHERE application_id = %s::uuid ORDER BY ordinal",
        (qa_app,)).fetchall()
    check("three answers stored in form order", len(rows) == 3 and
          [r_["answer"] for r_ in rows] == ["8", "1 month", "Yes"], rows)
    check("question stored as shown, normalised for grouping",
          rows[1]["question"] == "What is your notice period? *" and
          rows[1]["question_norm"] == "what is your notice period", rows[1])
    check("field type kept", rows[2]["field_type"] == "radio", rows[2])
    check("posting provenance recorded",
          str(rows[0]["posting_id"]) == r5.json()["posting_id"], rows[0])

print("re-applying overwrites an answer, never stacks it")
r6 = post({"platform": "linkedin", "platform_job_id": "LI-qa-1",
           "company": "Meridian Systems", "title": "Staff AI Engineer",
           "trigger": "apply",
           "answers": [{"question": "What is your notice period?", "answer": "2 months"},
                       {"question": "Do you have a work pass?", "answer": "EP"}]})
check("same application", r6.json()["application_id"] == qa_app, r6.text)
with db.connect() as conn:
    rows = conn.execute(
        "SELECT question_norm, answer FROM application_answers "
        "WHERE application_id = %s::uuid ORDER BY ordinal", (qa_app,)).fetchall()
    by_q = {r_["question_norm"]: r_["answer"] for r_ in rows}
    check("no duplicate row for the re-asked question", len(rows) == 4, rows)
    check("notice period updated in place",
          by_q["what is your notice period"] == "2 months", by_q)
    check("earlier answers untouched", by_q["how many years of experience do you"
                                            " have with python"] == "8", by_q)
    check("new question appended", by_q["do you have a work pass"] == "EP", by_q)

print("a repeater section keeps every entry (migration 010)")
# Shaped like the real LinkedIn work-history repeater that lost data on
# 27 Jul 2026: the same three labels, once per employer.
REPEAT = [
    {"question": "Company", "answer": "Humongous AI", "type": "text"},
    {"question": "Industry", "answer": "ERP SaaS", "type": "text"},
    {"question": "City", "answer": "Singapore", "type": "text"},
    {"question": "Company", "answer": "Alpine Finance", "type": "text"},
    {"question": "Industry", "answer": "Fintech", "type": "text"},
    {"question": "City", "answer": "Jakarta", "type": "text"},
    {"question": "Company", "answer": "Nod Media", "type": "text"},
    {"question": "Industry", "answer": "Media", "type": "text"},
    {"question": "City", "answer": "Kuala Lumpur", "type": "text"},
]
r8 = post({"platform": "linkedin", "platform_job_id": "LI-repeat-1",
           "url": "https://www.linkedin.com/jobs/view/9911/",
           "company": "Vanarsdel", "title": "AI Engineer", "trigger": "apply",
           "answers": REPEAT})
repeat_app = r8.json()["application_id"]
check("all nine repeated fields counted, not three", r8.json()["answers"] == 9, r8.text)
with db.connect() as conn:
    rows = conn.execute(
        "SELECT question_norm, answer, occurrence FROM application_answers "
        "WHERE application_id = %s::uuid ORDER BY ordinal", (repeat_app,)).fetchall()
    check("every entry survived", len(rows) == 9, len(rows))
    check("cities kept in form order, each with its own occurrence",
          [(r_["answer"], r_["occurrence"]) for r_ in rows if r_["question_norm"] == "city"]
          == [("Singapore", 0), ("Jakarta", 1), ("Kuala Lumpur", 2)], rows)

print("re-capturing a shorter work history prunes the entries it dropped")
r9 = post({"platform": "linkedin", "platform_job_id": "LI-repeat-1",
           "company": "Vanarsdel", "title": "AI Engineer", "trigger": "apply",
           "answers": [{"question": "Company", "answer": "Humongous AI"},
                       {"question": "Industry", "answer": "Enterprise SaaS"},
                       {"question": "City", "answer": "Singapore"},
                       {"question": "Company", "answer": "Alpine Finance"},
                       {"question": "Industry", "answer": "Fintech"},
                       {"question": "City", "answer": "Jakarta"}]})
check("same application", r9.json()["application_id"] == repeat_app, r9.text)
with db.connect() as conn:
    rows = conn.execute(
        "SELECT question_norm, answer, occurrence FROM application_answers "
        "WHERE application_id = %s::uuid ORDER BY ordinal", (repeat_app,)).fetchall()
    check("the third employer's three rows are gone, not orphaned", len(rows) == 6, rows)
    check("occurrence 0 overwritten in place, not stacked",
          [(r_["answer"], r_["occurrence"]) for r_ in rows
           if r_["question_norm"] == "industry"]
          == [("Enterprise SaaS", 0), ("Fintech", 1)], rows)
    check("no trace of the dropped entry",
          not any(r_["answer"] == "Kuala Lumpur" for r_ in rows), rows)

print("a capture with no form answers is unchanged")
r7 = post({"platform": "indeed", "platform_job_id": "IN-qa-none",
           "company": "Quiet Co", "title": "Data Engineer", "trigger": "apply"})
check("answers key absent is fine", r7.status_code == 200 and r7.json()["answers"] == 0,
      r7.text)

print("over-long answers are truncated, not rejected")
r8 = post({"platform": "linkedin", "platform_job_id": "LI-qa-long",
           "company": "Verbose Ltd", "title": "Writer", "trigger": "apply",
           "answers": [{"question": "Why this role?", "answer": "x" * (MAX_ANSWER + 500)}]})
check("capture still succeeded", r8.status_code == 200, r8.text)
with db.connect() as conn:
    n = conn.execute(
        "SELECT length(answer) AS n FROM application_answers "
        "WHERE application_id = %s::uuid", (r8.json()["application_id"],)).fetchone()["n"]
    check("stored at the cap", n == MAX_ANSWER, n)

print("deleting an application takes its answers with it")
# The FK would refuse the delete outright if _delete_application had missed
# this table, so this asserts the order as much as the cleanup.
from pipeline import web as _web                                   # noqa: E402
long_app = r8.json()["application_id"]
with db.connect() as conn, conn.transaction():
    a = conn.execute("SELECT id, job_id FROM applications WHERE id = %s::uuid",
                     (long_app,)).fetchone()
    _web._delete_application(conn, a)
with db.connect() as conn:
    left = conn.execute(
        "SELECT count(*) AS n FROM application_answers WHERE application_id = %s::uuid",
        (long_app,)).fetchone()["n"]
    check("answers deleted with the application", left == 0, left)

print("save-first: capture writes before any tag, tag follows separately")
# What the extension now sends on an Easy Apply submit: no note.
r9 = post({"platform": "linkedin", "platform_job_id": "LI-tag-1",
           "company": "Lattice Robotics", "title": "Perception Engineer",
           "jd_text": "the JD", "trigger": "apply",
           "note": None})
tag_app = r9.json()["application_id"]
check("record exists with no tag at all", r9.status_code == 200, r9.text)
check("response carries a label for the receipt",
      r9.json()["label"] == "Lattice Robotics · Perception Engineer", r9.json())
with db.connect() as conn:
    st = conn.execute(
        "SELECT status FROM application_status WHERE application_id = %s::uuid",
        (tag_app,)).fetchone()
    check("already counts as applied without the tag", st["status"] == "applied", st)

t = client.post(f"/captures/{tag_app}/tag", json={"note": "  referred by K  "},
                headers=AUTH)
check("note accepted", t.status_code == 200 and t.json()["ok"] is True, t.text)
with db.connect() as conn:
    notes = conn.execute(
        "SELECT payload FROM events WHERE application_id = %s::uuid AND type = 'note'",
        (tag_app,)).fetchall()
    check("one note event, trimmed", len(notes) == 1
          and notes[0]["payload"]["note"] == "referred by K", notes)

t = client.post(f"/captures/{tag_app}/tag", json={"note": "   "}, headers=AUTH)
with db.connect() as conn:
    n = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s::uuid "
        "AND type = 'note'", (tag_app,)).fetchone()["n"]
    check("blank note logs nothing", t.status_code == 200 and n == 1, n)

check("tag needs the bearer token",
      client.post(f"/captures/{tag_app}/tag", json={"note": "x"}).status_code == 401)
check("tag 404s on an unknown application",
      client.post("/captures/00000000-0000-0000-0000-000000000000/tag",
                  json={"note": "x"}, headers=AUTH).status_code == 404)
check("tag 404s on a malformed id, never 500s",
      client.post("/captures/not-a-uuid/tag", json={"note": "x"},
                  headers=AUTH).status_code == 404)

print("manual capture -> interested")
r4 = post({"platform": "indeed", "platform_job_id": "IN-42",
           "company": "Solstice Mobility", "title": "AI Platform Engineer",
           "jd_text": "the JD", "trigger": "manual"})
with db.connect() as conn:
    st = conn.execute(
        "SELECT status FROM application_status WHERE application_id = %s::uuid",
        (r4.json()["application_id"],)).fetchone()
    check("status interested", st["status"] == "interested", st)

print("\nALL CAPTURE PATHS PASS")
