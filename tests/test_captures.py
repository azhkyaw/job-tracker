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
# 3-8 Oct 2026: a blank SuccessFactors combobox answered with its dropdown
# arrow, an icon font's Private Use Area glyph.
check("an icon glyph is no answer, and is dropped from one that has words",
      [(r["question"], r["answer"]) for r in clean([
          {"question": "Industry", "answer": "\ue1ef"},
          {"question": "Functional Area", "answer": " \ue1ef \U000f0001 "},
          {"question": "Country \ue1ef", "answer": "Singapore \ue1ef"}])]
      == [("Country", "Singapore")])
check("occurrence counts per question, not across the form",
      [r["occurrence"] for r in clean([
          {"question": "City", "answer": "SG"}, {"question": "Industry", "answer": "SaaS"},
          {"question": "City", "answer": "JKT"}, {"question": "Industry", "answer": "Fin"},
          {"question": "City", "answer": "KL"}])] == [0, 0, 1, 1, 2])

# 24 Sep 2026: the key kept [a-z0-9] only, so C# and C++ keyed alike and a
# question in Chinese keyed as its one Latin letter. The same list is checked
# against the extension's normKey in tests/test_extension.js.
import json as _json                                                  # noqa: E402
_NORMS = _json.loads((Path(__file__).resolve().parent / "question_norms.json")
                     .read_text(encoding="utf-8"))["cases"]
for q, want in _NORMS:
    # ascii(), not repr(): the list holds CJK and Devanagari, and a Windows
    # console's cp1252 cannot print them (CLAUDE.md gotcha).
    check(f"norm {ascii(q)}", norm_question(q) == want, ascii(norm_question(q)))
check("C#, C++ and C are three questions",
      len({norm_question(f"Years with {x}?") for x in ("C#", "C++", "C")}) == 3)

# Migration 014's resume promotion, on both Easy Apply layouts. The classic
# modal labelled the picker's radios "Deselect resume <file>"; the rebuilt one
# (Aug 2026, measured live 2 Sep) names each card by its bare filename under a
# "Resume*" heading, so the filename arrives as the ANSWER and nothing in the
# question says "picker" — for two weeks that pair reached the bank as a
# question nobody asked, and resume_file stayed NULL.
from pipeline.answers import resume_file   # noqa: E402
check("resume: classic 'Deselect resume <file>' label",
      resume_file([{"question": "Deselect resume Contoso-resume.pdf",
                    "answer": "Deselect resume Contoso-resume.pdf", "type": "radio"}])
      == "Contoso-resume.pdf")
check("resume: rebuilt layout - heading as question, filename as answer",
      resume_file([{"question": "Resume*", "answer": "Contoso-resume-AI.pdf",
                    "type": "radio"}]) == "Contoso-resume-AI.pdf")
check("resume: the heading block with its description line still counts",
      resume_file([{"question": "Resume*Select or upload a resume in DOC, DOCX, or PDF "
                                "format that is less than 2MB",
                    "answer": "cv.docx", "type": "radio"}]) == "cv.docx")
check("resume: last pick wins",
      resume_file([{"question": "Resume*", "answer": "a.pdf", "type": "radio"},
                   {"question": "Resume*", "answer": "b.pdf", "type": "radio"}]) == "b.pdf")
check("resume: a 'Resume link' question answered with a URL is a question",
      resume_file([{"question": "Resume link", "answer": "https://x.example/cv.pdf",
                    "type": "text"}]) is None)
# A resume UPLOAD (Greenhouse's job boards, read live 29 Sep 2026): a file
# field in a role="group" named "Resume/CV*", which the extension now sends as
# the chosen file's name. Any extension there (the field accepts txt and rtf),
# never a path, and only under a resume heading.
check("resume: an upload under 'Resume/CV*' is the resume, whatever its extension",
      [resume_file([{"question": "Resume/CV*", "answer": f, "type": "file"}])
       for f in ("Jane-Doe_resume.pdf", "Jane-Doe_resume.txt")]
      == ["Jane-Doe_resume.pdf", "Jane-Doe_resume.txt"])
# Eightfold's form (read live 2 Oct 2026) asks "Upload your resume" and shows
# the chosen file in a combobox: a TEXT input whose value is the bare name.
check("resume: a combobox under 'Upload your resume' showing the file is the resume",
      resume_file([{"question": "Upload your resume", "answer": "Jane-Doe_resume.pdf",
                    "type": "text"}]) == "Jane-Doe_resume.pdf")
check("resume: …but typed text that is not a bare document name stays a question",
      [resume_file([{"question": "Upload your resume", "answer": a, "type": "text"}])
       for a in ("see LinkedIn", "https://x.example/cv.pdf", "cv.txt")]
      == [None, None, None])
check("resume: three words before 'resume' is a question about it, not the picker",
      resume_file([{"question": "Is your current resume up to date?", "answer": "cv.pdf",
                    "type": "text"}]) is None)
# Upload widgets label their input with their own chrome (4 Oct 2026): iCIMS
# names it after the resume mid-sentence, and four others only by the widget,
# whose files were named "…_Resume.pdf". None of the five was promoted.
check("resume: an upload whose label says resume/CV anywhere, or whose file is named so",
      [resume_file([{"question": q, "answer": f, "type": "file"}]) for q, f in (
          ("My Computer (Opens new window) Upload your resume/CV (max size: 5 MB)", "JD.pdf"),
          ("Upload options", "Jane-Doe-resume.pdf"),
          ("Choose a file or drop it here", "Jane-Doe_AI-engineer_Resume.pdf"),
          ("Attach", "jane-cv.docx"))]
      == ["JD.pdf", "Jane-Doe-resume.pdf", "Jane-Doe_AI-engineer_Resume.pdf", "jane-cv.docx"])
check("resume: …but an upload saying neither (a transcript, a portfolio) stays an answer",
      [resume_file([{"question": q, "answer": f, "type": "file"}]) for q, f in (
          ("Upload options", "transcript.pdf"), ("Portfolio", "work-samples.pdf"),
          ("Cover letter", "Jane-Doe_Cover_Letter.pdf"))] == [None, None, None])
check("resume: another upload (a cover letter), or a path, is not",
      [resume_file([{"question": "Cover Letter", "answer": "cover.pdf", "type": "file"}]),
       resume_file([{"question": "Resume/CV*", "answer": "C:\\fakepath\\cv.pdf", "type": "file"}])]
      == [None, None])
check("clean drops the picker on both layouts, LinkedIn's chrome and its search filter, "
      "and keeps the real question",
      [r["question"] for r in clean([
          {"question": "Deselect resume Contoso-resume.pdf",
           "answer": "Deselect resume Contoso-resume.pdf", "type": "radio"},
          {"question": "Resume*", "answer": "Contoso-resume-AI.pdf", "type": "radio"},
          {"question": "Mark job as a top choice", "answer": "No", "type": "checkbox"},
          {"question": "Filter results by: Date posted", "answer": "Any time Filter by Any time",
           "type": "radio"},
          {"question": "Resume link", "answer": "https://x.example/cv.pdf",
           "type": "text"},
          {"question": "Resume/CV*", "answer": "Jane-Doe_resume.pdf", "type": "file"}])]
      == ["Resume link"])
# A captcha's hidden response field inside an ATS's application form (Lever's
# hCaptcha, measured 24 Sep 2026) — the extension skips it as machinery, and
# this is the second line, should one ever arrive.
check("clean drops a captcha's response field, keeps the question beside it",
      [r["question"] for r in clean([
          {"question": "g-recaptcha-response", "answer": "03AFcWeA.token", "type": "textarea"},
          {"question": "h-captcha-response", "answer": "P1_eyJ0eXAi.token", "type": "textarea"},
          {"question": "cf-turnstile-response", "answer": "0.token", "type": "text"},
          {"question": "Notice period", "answer": "1 month", "type": "text"}])] == ["Notice period"])
r_res = post({"platform": "linkedin", "platform_job_id": "LI-qa-resume",
              "url": "https://www.linkedin.com/jobs/view/4243/",
              "company": "Meridian Systems", "title": "Applied AI Engineer",
              "jd_text": "the JD", "trigger": "apply",
              "answers": [{"question": "Resume*", "answer": "Contoso-resume-AI.pdf",
                           "type": "radio"},
                          {"question": "Will you now or in the future require "
                                       "sponsorship for employment visa status?",
                           "answer": "No", "type": "radio"}]})
check("rebuilt-layout picker lands in resume_file, not the answer bank",
      r_res.status_code == 200 and r_res.json()["answers"] == 1, r_res.text)
with db.connect() as conn:
    check("resume_file promoted from the filename answer",
          conn.execute("SELECT resume_file FROM applications WHERE id = %s::uuid",
                       (r_res.json()["application_id"],)).fetchone()["resume_file"]
          == "Contoso-resume-AI.pdf")

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

print("C# and C++ on one form are two questions, and renorm re-keys old rows")
# The real shape (a 3 Aug 2026 form): C++ asked just before C#, stored under one
# key as occurrence 0 and 1 — a repeater that never was — beside a genuine
# two-entry repeater the re-key must leave alone.
LANGS = [{"question": "How many years of work experience do you have with C++?",
          "answer": "1", "type": "number"},
         {"question": "How many years of work experience do you have with C#?",
          "answer": "10", "type": "number"},
         {"question": "City", "answer": "Singapore", "type": "text"},
         {"question": "City", "answer": "Jakarta", "type": "text"}]
r_cs = post({"platform": "linkedin", "platform_job_id": "LI-langs-1",
             "company": "Fourth Coffee", "title": "Software Engineer",
             "trigger": "apply", "answers": LANGS})
cs_app = r_cs.json()["application_id"]
_by_lang = lambda rs: {r_["answer"]: (r_["question_norm"], r_["occurrence"]) for r_ in rs}  # noqa: E731
with db.connect() as conn:
    rows = conn.execute("SELECT question_norm, occurrence, answer FROM application_answers "
                        "WHERE application_id = %s::uuid", (cs_app,)).fetchall()
    got = _by_lang(rows)
    check("each language keeps its own question, both at occurrence 0",
          got["1"] == ("how many years of work experience do you have with c plus plus", 0)
          and got["10"] == ("how many years of work experience do you have with c sharp", 0), got)
    # Put the two back as the old rule stored them, then let renorm undo it.
    conn.execute("UPDATE application_answers SET question_norm = "
                 "'how many years of work experience do you have with c', "
                 "occurrence = CASE answer WHEN '1' THEN 0 ELSE 1 END "
                 "WHERE application_id = %s::uuid AND answer IN ('1', '10')", (cs_app,))
from pipeline import answers as _answers                              # noqa: E402
with db.connect() as conn:
    plan = _answers.renorm(conn)
    _stem = "how many years of work experience do you have with "
    check("dry run plans exactly the two stale rows",
          sorted((p["old_occurrence"], p["new_norm"], p["new_occurrence"]) for p in plan)
          == [(0, _stem + "c plus plus", 0), (1, _stem + "c sharp", 0)], plan)
    check("...and writes nothing", conn.execute(
        "SELECT count(*) AS n FROM application_answers WHERE application_id = %s::uuid "
        "AND question_norm LIKE '%%with c'", (cs_app,)).fetchone()["n"] == 2)
with db.connect() as conn:
    _answers.renorm(conn, apply=True)
with db.connect() as conn:
    rows = conn.execute("SELECT question_norm, occurrence, answer FROM application_answers "
                        "WHERE application_id = %s::uuid", (cs_app,)).fetchall()
    check("applied: C++ and C# re-keyed apart, C# renumbered to 0",
          _by_lang(rows)["1"][1] == 0 and _by_lang(rows)["10"] ==
          ("how many years of work experience do you have with c sharp", 0), rows)
    check("the untouched repeater keeps its numbering",
          sorted((r_["answer"], r_["occurrence"]) for r_ in rows
                 if r_["question_norm"] == "city") == [("Jakarta", 1), ("Singapore", 0)], rows)
    check("a second run finds nothing to do", _answers.renorm(conn) == [])

print("sensitive answers are withheld, their questions kept")
# 24 Sep 2026: 5 stored answers were equal-opportunity questions (gender twice,
# race/ethnicity, veteran status, disability), and Singapore employer forms ask
# for NRIC and date of birth. The same list is checked against the extension's
# isSensitive in tests/test_extension.js.
_SENS = _json.loads((Path(__file__).resolve().parent / "sensitive_questions.json")
                    .read_text(encoding="utf-8"))["cases"]
for q, want in _SENS:
    check(f"sensitive {ascii(q)} -> {want}", _answers.is_sensitive(q) is want)
check("clean withholds the value and keeps the question",
      [(r["question"], r["answer"]) for r in clean([
          {"question": "Gender*", "answer": "Female", "type": "select"},
          {"question": "NRIC/FIN No.", "answer": "S0000000X", "type": "text"},
          {"question": "Nationality", "answer": "Examplestan", "type": "select"},
          {"question": "Date of Birth", "answer": "   ", "type": "text"}])]
      == [("Gender*", _answers.REDACTED), ("NRIC/FIN No.", _answers.REDACTED),
          ("Nationality", "Examplestan")])
r_sens = post({"platform": "linkedin", "platform_job_id": "LI-sens-1",
               "company": "Proseware", "title": "Platform Engineer", "trigger": "apply",
               "answers": [{"question": "Race/Ethnicity", "answer": "Asian", "type": "select"},
                           {"question": "Notice period", "answer": "1 month", "type": "text"}]})
sens_app = r_sens.json()["application_id"]
with db.connect() as conn:
    got = {r_["question"]: r_["answer"] for r_ in conn.execute(
        "SELECT question, answer FROM application_answers WHERE application_id = %s::uuid",
        (sens_app,)).fetchall()}
    check("a capture that sends the raw value still stores it withheld",
          got == {"Race/Ethnicity": _answers.REDACTED, "Notice period": "1 month"}, got)
    # A row stored before the rule existed, written past clean() the way the
    # old code wrote it — what redact_stored() is for.
    conn.execute(
        "INSERT INTO application_answers (user_id, application_id, question, "
        "question_norm, answer, field_type, ordinal, occurrence) "
        "SELECT user_id, id, 'Veteran status', 'veteran status', 'I am not a veteran', "
        "'select', 9, 0 FROM applications WHERE id = %s::uuid", (sens_app,))
with db.connect() as conn:
    plan = _answers.redact_stored(conn)
    check("dry run finds exactly the pre-rule row, and reports no value",
          [(p["question"], set(p)) for p in plan]
          == [("Veteran status", {"id", "application_id", "question"})], plan)
    check("...and writes nothing", conn.execute(
        "SELECT answer FROM application_answers WHERE application_id = %s::uuid "
        "AND question = 'Veteran status'", (sens_app,)).fetchone()["answer"]
        == "I am not a veteran")
with db.connect() as conn:
    _answers.redact_stored(conn, apply=True)
with db.connect() as conn:
    check("applied: the value is gone", conn.execute(
        "SELECT answer FROM application_answers WHERE application_id = %s::uuid "
        "AND question = 'Veteran status'", (sens_app,)).fetchone()["answer"]
        == _answers.REDACTED)
    check("a second run finds nothing to do", _answers.redact_stored(conn) == [])

# prune_stored(): rows a _control_kind() rule now calls form chrome, stored
# before the rule existed (written past clean(), as the old code wrote them).
# The real case: an iCIMS filter box's typed text, 8 rows on the first iCIMS
# apply (3 Oct 2026), stored before 0.27.2 skipped the box.
_PRUNE_ROWS = [("— Type to Search —", "type to search", "singa", "text", 20, 0),
               ("— Type to Search —", "type to search", "sin", "text", 21, 1),
               ("Resume", "resume", "Jane-Doe-CV.pdf", "radio", 30, 0),
               ("Resume", "resume", "Attached above", "text", 31, 1)]
with db.connect() as conn:
    for q_, norm_, a_, ft_, ord_, occ_ in _PRUNE_ROWS:
        conn.execute(
            "INSERT INTO application_answers (user_id, application_id, question, "
            "question_norm, answer, field_type, ordinal, occurrence) "
            "SELECT user_id, id, %s, %s, %s, %s, %s, %s FROM applications WHERE id = %s::uuid",
            (q_, norm_, a_, ft_, ord_, occ_, sens_app))
    conn.execute("UPDATE applications SET resume_file = NULL WHERE id = %s::uuid", (sens_app,))
_n_before = None
with db.connect() as conn:
    _n_before = conn.execute("SELECT count(*) AS n FROM application_answers").fetchone()["n"]
    plan = _answers.prune_stored(conn)
    check("prune dry run: the filter box's rows and the resume pick, and no answer reported",
          sorted((p["kind"], p["question"], p["promote"]) for p in plan)
          == [("drop", "— Type to Search —", None), ("drop", "— Type to Search —", None),
              ("resume", "Resume", "Jane-Doe-CV.pdf")]
          and all(set(p) == {"id", "application_id", "question", "kind", "promote"} for p in plan), plan)
    check("...and writes nothing", conn.execute(
        "SELECT count(*) AS n FROM application_answers").fetchone()["n"] == _n_before)
with db.connect() as conn:
    _answers.prune_stored(conn, apply=True)
with db.connect() as conn:
    left = [(r["question_norm"], r["occurrence"], r["answer"]) for r in conn.execute(
        "SELECT question_norm, occurrence, answer FROM application_answers "
        "WHERE application_id = %s::uuid AND question_norm IN ('type to search', 'resume') "
        "ORDER BY 1, 2", (sens_app,)).fetchall()]
    check("applied: the chrome is gone, and the resume group left renumbered from 0",
          left == [("resume", 0, "Attached above")], left)
    check("applied: the pick became the application's resume",
          conn.execute("SELECT resume_file FROM applications WHERE id = %s::uuid",
                       (sens_app,)).fetchone()["resume_file"] == "Jane-Doe-CV.pdf")
    check("a second run finds nothing to do", _answers.prune_stored(conn) == [])
    # A pick stored where the application already names a resume: removed,
    # and the resume a capture recorded is never replaced.
    conn.execute(
        "INSERT INTO application_answers (user_id, application_id, question, "
        "question_norm, answer, field_type, ordinal, occurrence) "
        "SELECT user_id, id, 'Resume', 'resume', 'Old-CV.pdf', 'radio', 29, 1 "
        "FROM applications WHERE id = %s::uuid", (sens_app,))
with db.connect() as conn:
    plan = _answers.prune_stored(conn, apply=True)
    check("a pick on an application that names its resume: removed, nothing to promote",
          [(p["kind"], p["promote"]) for p in plan] == [("resume", None)], plan)
    check("...and its resume is the one already recorded",
          conn.execute("SELECT resume_file FROM applications WHERE id = %s::uuid",
                       (sens_app,)).fetchone()["resume_file"] == "Jane-Doe-CV.pdf")

print("employer career sites: one id rule with the extension (docs/career-sites.md)")
from pipeline import joburl                                            # noqa: E402
_URLS = _json.loads((Path(__file__).resolve().parent / "job_urls.json")
                    .read_text(encoding="utf-8"))["cases"]
for url, want in _URLS:
    got = joburl.generic_id(url)
    check(f"generic_id {url}", got == want, got)
check("parse: an employer's page is platform 'other', its url kept minus the fragment",
      joburl.parse("https://careers.contoso.com/job/Engineer/1234567890/?locale=en_GB#apply")
      == ("other", "careers.contoso.com/1234567890",
          "https://careers.contoso.com/job/Engineer/1234567890/?locale=en_GB"))
check("parse: a pasted link without its scheme still reads",
      joburl.parse("careers.contoso.com/job/Engineer/42/")[:2]
      == ("other", "careers.contoso.com/42"))
check("parse: a bare site names no job",
      joburl.parse("https://careers.contoso.com/") == (None, None, "https://careers.contoso.com/"))
check("parse: the three platforms are unchanged",
      joburl.parse("https://www.linkedin.com/jobs/view/123/")
      == ("linkedin", "123", "https://www.linkedin.com/jobs/view/123/"))
# The LinkedIn job id, one list with the adapter (linkedin.js:viewId and
# jobFromUrl, tests/test_extension.js): the slug form /jobs/view/<slug>-<id>/
# stored a capture with no job id on 8 Oct 2026.
for url, want in _json.loads((Path(__file__).resolve().parent / "linkedin_urls.json")
                             .read_text(encoding="utf-8"))["cases"]:
    plat, got, canon = joburl.parse(url)
    check(f"parse linkedin {url}", (plat, got) == ("linkedin", want)
          and canon == (f"https://www.linkedin.com/jobs/view/{want}/" if want else url), (plat, got, canon))

SITE = {"platform": "other", "platform_job_id": "careers.contoso.com/9876543210",
        "url": "https://careers.contoso.com/job/Engineer/9876543210/",
        "company": "Contoso", "title": "AVP, Software Engineer", "jd_text": "the JD",
        "trigger": "apply", "external": True, "ats": "successfactors",
        "posted_label": "24 Sep 2026",
        # the line jobposting.js renders from a JobPosting's baseSalary
        "salary_raw": "SGD 134,400 – 176,400 per year"}
r_site = post(SITE)
check("a career-site capture is created", r_site.status_code == 200 and r_site.json()["created"],
      r_site.text)
site_app = r_site.json()["application_id"]
with db.connect() as conn:
    p = conn.execute(
        "SELECT p.platform, p.platform_job_id, p.ats, p.posted_label, p.salary_min, "
        "p.salary_max, p.salary_currency, p.salary_period FROM applications a "
        "JOIN postings p ON p.id = a.applied_via_posting_id WHERE a.id = %s::uuid",
        (site_app,)).fetchone()
    check("posting keeps platform, namespaced id, vendor and the posted date",
          (p["platform"], p["platform_job_id"], p["ats"], p["posted_label"])
          == ("other", "careers.contoso.com/9876543210", "successfactors", "24 Sep 2026"), p)
    check("the rendered baseSalary line parses into numbers, currency and period",
          (int(p["salary_min"]), int(p["salary_max"]), p["salary_currency"], p["salary_period"])
          == (134400, 176400, "SGD", "annual"), p)
    ev = conn.execute("SELECT payload FROM events WHERE application_id = %s::uuid "
                      "AND type = 'applied'", (site_app,)).fetchone()["payload"]
    check("applied on the employer's site: external true", ev == {"external": True}, ev)
check("the same page captured again converges on one record",
      post(SITE).json()["application_id"] == site_app)
r_twin = post({**SITE, "platform_job_id": "careers.fabrikam.example/9876543210",
               "url": "https://careers.fabrikam.example/jobs/9876543210",
               "company": "Fabrikam", "title": "Data Engineer"})
check("another employer's same bare number is a different record, never a collision",
      r_twin.json()["application_id"] != site_app, r_twin.text)

# The popup's "Capture this job as applied" on a PLATFORM page cannot tell Easy
# Apply from an external apply, and sends external: null — the event must then
# say nothing, as manual entry's blank does (web-ui rule 9b: three states).
r_unknown = post({"platform": "linkedin", "platform_job_id": "LI-popup-applied",
                  "company": "Tailspin", "title": "ML Engineer", "trigger": "apply",
                  "external": None})
with db.connect() as conn:
    ev = conn.execute("SELECT payload FROM events WHERE application_id = %s::uuid "
                      "AND type = 'applied'", (r_unknown.json()["application_id"],)).fetchone()
    check("external unknown: the applied event carries no external key",
          ev["payload"] == {}, ev)

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

print("a capture that names no employer: the receipt asks, the tag route fills it")
# 24 Sep 2026: a real SuccessFactors submit was captured with 60 answers and
# no company — the form never names the employer — so the record read
# "unknown company", which no confirmation email can match.
r_anon = post({"platform": "other", "platform_job_id": "career10.successfactors.com/1234",
               "company": None, "title": "AVP, Software Engineer", "trigger": "apply",
               "completed": True, "external": True, "ats": "successfactors"})
anon = r_anon.json()
check("a nameless capture says so, for the receipt to ask",
      r_anon.status_code == 200 and anon["company_known"] is False, r_anon.text)
check("a named capture says it is known", post(SITE).json()["company_known"] is True)
t = client.post(f"/captures/{anon['application_id']}/tag", json={"company": "Contoso Pte Ltd"},
                headers=AUTH)
check("the receipt's company fills the placeholder",
      t.status_code == 200 and t.json()["company"] is True
      and t.json()["label"] == "Contoso Pte Ltd · AVP, Software Engineer", t.text)
with db.connect() as conn:
    row = conn.execute(
        "SELECT j.company_norm, p.company_raw, p.company_norm AS pnorm FROM applications a "
        "JOIN jobs j ON j.id = a.job_id JOIN postings p ON p.job_id = j.id "
        "WHERE a.id = %s::uuid", (anon["application_id"],)).fetchone()
    check("…normalised on the job and the posting alike (norm_company, invariant #4)",
          (row["company_norm"], row["company_raw"], row["pnorm"]) == ("contoso", "Contoso Pte Ltd", "contoso"),
          row)
t2 = client.post(f"/captures/{anon['application_id']}/tag", json={"company": "Fabrikam"},
                 headers=AUTH)
check("a record that names its employer is never overwritten from a receipt",
      t2.status_code == 409, t2.text)
r_anon2 = post({"platform": "other", "platform_job_id": "career10.successfactors.com/1235",
                "title": "Data Engineer", "trigger": "apply"})
t3 = client.post(f"/captures/{r_anon2.json()['application_id']}/tag", json={"company": "Pte Ltd"},
                 headers=AUTH)
check("a 'company' that normalises to nothing is refused, not stored", t3.status_code == 422, t3.text)
check("a note alone still works, and says no company was set",
      client.post(f"/captures/{r_anon2.json()['application_id']}/tag", json={"note": "via referral"},
                  headers=AUTH).json() == {"ok": True, "note": True, "company": False,
                                           "label": "unknown company · Data Engineer"})

print("the job's own id on its hiring system (migration 018; docs/career-sites.md §16)")
from pipeline import dedup


def job_of(app_id):
    with db.connect() as conn:
        return conn.execute(
            "SELECT j.id, j.ats_job_id FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.id = %s::uuid", (app_id,)).fetchone()


# Ids of this suite's own: the suites share one database, and
# test_integration's path 3k already holds ".../51234" on a job, which a
# capture naming it would (correctly) join.
SF_ID = "career2.successfactors.eu/52234"
# An ATS submit that linked to nothing (28 Sep 2026's shape): its own id is
# the ATS id.
r_sf = post({"platform": "other", "platform_job_id": SF_ID, "company": "Litware Bank",
             "title": "Principal AI Engineer", "trigger": "apply", "completed": True,
             "external": True, "ats": "successfactors", "ats_job_id": SF_ID})
sf_app = r_sf.json()["application_id"]
check("an ATS submit keeps the job's ATS id, on its JOB",
      r_sf.json()["created"] is True and job_of(sf_app)["ats_job_id"] == SF_ID, r_sf.text)
# The listing, captured AFTER the submit from the employer's own site, naming
# the same requisition: order does not matter.
r_list = post({"platform": "other", "platform_job_id": "jobs.litwarebank.com/52234",
               "company": "Litware Bank", "title": "Principal AI Engineer",
               "jd_text": "The listing's own JD.", "trigger": "apply", "external": True,
               "ats": "successfactors", "ats_job_id": SF_ID})
check("a later capture naming the same ATS id joins that application, not a new one",
      r_list.json()["application_id"] == sf_app and r_list.json()["created"] is False, r_list.text)
with db.connect() as conn:
    n_post = conn.execute("SELECT count(*) AS n FROM postings WHERE job_id = %s",
                          (job_of(sf_app)["id"],)).fetchone()["n"]
    n_applied = conn.execute("SELECT count(*) AS n FROM events WHERE application_id = %s::uuid "
                             "AND type = 'applied'", (sf_app,)).fetchone()["n"]
check("...as a second posting of the same job (postings != jobs)", n_post == 2, n_post)
check("...with still one applied event", n_applied == 1, n_applied)
# The form named no employer; the listing that joins it does, and replaces
# only the placeholder.
NL_ID = "career2.successfactors.eu/52300"
r_nl = post({"platform": "other", "platform_job_id": NL_ID, "title": "Data Scientist",
             "trigger": "apply", "completed": True, "external": True, "ats_job_id": NL_ID})
post({"platform": "other", "platform_job_id": "jobs.litwarebank.com/52300", "company": "Litware Bank",
      "title": "Data Scientist", "trigger": "apply", "external": True, "ats_job_id": NL_ID})
with db.connect() as conn:
    nl = conn.execute("SELECT j.company_norm FROM applications a JOIN jobs j ON j.id = a.job_id "
                      "WHERE a.id = %s::uuid", (r_nl.json()["application_id"],)).fetchone()
check("a listing joining a nameless ATS record names it", nl["company_norm"] == "litware bank", nl)

# A job board's record, then the ATS submit that completed it (phase B's link):
# the payload carries the BOARD's identity, and the form's ATS id beside it.
WD_ID = "contoso.wd3.myworkdayjobs.com/r200001"
r_board = post({"platform": "linkedin", "platform_job_id": "LI-ats-1", "company": "Contoso Markets",
                "title": "Backend Engineer", "trigger": "apply", "external": True})
post({"platform": "linkedin", "platform_job_id": "LI-ats-1", "company": "Contoso Markets",
      "title": "Backend Engineer", "trigger": "apply", "external": True, "completed": True,
      "ats": "workday", "ats_job_id": WD_ID})
check("a submit that completed a job board's record puts the ATS id on that record's job",
      job_of(r_board.json()["application_id"])["ats_job_id"] == WD_ID)

# An id another job already holds is not copied: two jobs cannot share one
# requisition, and nothing is merged behind anyone's back (invariant #3).
r_fab = post({"platform": "other", "platform_job_id": "careers.fabrikam.com/9",
              "company": "Fabrikam", "title": "Data Engineer", "trigger": "apply"})
r_clash = post({"platform": "other", "platform_job_id": "careers.fabrikam.com/9",
                "company": "Fabrikam", "title": "Data Engineer", "trigger": "apply",
                "ats_job_id": SF_ID})
check("an ATS id held by another job: accepted, and left where it was",
      r_clash.status_code == 200 and job_of(r_fab.json()["application_id"])["ats_job_id"] is None
      and job_of(sf_app)["ats_job_id"] == SF_ID, r_clash.text)
r_blank = post({"platform": "other", "platform_job_id": "careers.fabrikam.com/10",
                "company": "Fabrikam", "title": "ML Engineer", "trigger": "apply", "ats_job_id": "  "})
check("a blank ATS id stores nothing", job_of(r_blank.json()["application_id"])["ats_job_id"] is None)

# dedup.merge_jobs carries it: the loser's id moves to a winner that has none,
# and a winner's own id is kept.
with db.connect() as conn, conn.transaction():
    uid = db.single_user_id(conn)
    mk = lambda ats_id: conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical, ats_job_id) "
        "VALUES (%s, 'coho', 'Engineer', %s) RETURNING id", (uid, ats_id)).fetchone()["id"]
    keep, drop = mk(None), mk("coho.wd3.myworkdayjobs.com/r1")
    dedup.merge_jobs(conn, uid, keep, drop)
    got = conn.execute("SELECT ats_job_id FROM jobs WHERE id = %s", (keep,)).fetchone()["ats_job_id"]
    keep2, drop2 = mk("coho.wd3.myworkdayjobs.com/r2"), mk("coho.wd3.myworkdayjobs.com/r3")
    dedup.merge_jobs(conn, uid, keep2, drop2)
    got2 = conn.execute("SELECT ats_job_id FROM jobs WHERE id = %s", (keep2,)).fetchone()["ats_job_id"]
check("merge_jobs moves the loser's ATS id to a winner without one",
      got == "coho.wd3.myworkdayjobs.com/r1", got)
check("...and a winner keeps its own", got2 == "coho.wd3.myworkdayjobs.com/r2", got2)

print("P4: a thin record completed from its listing, and a tenant's name (docs/career-sites.md §16)")
check("ats_tenant: a SuccessFactors id's tenant",
      joburl.ats_tenant("career2.successfactors.eu/litwarebk/51234") == "litwarebk")
check("ats_tenant: none without one, nor on another vendor",
      [joburl.ats_tenant(x) for x in ("career2.successfactors.eu/51234", "contoso.wd3.myworkdayjobs.com/r1",
                                      "jobs.lever.co/contoso/1")] == [None, None, None])


def app_row(app_id):
    with db.connect() as conn:
        return conn.execute(
            "SELECT j.id AS job_id, j.company_norm, j.title_canonical, j.ats_job_id, "
            "(SELECT count(*) FROM postings p WHERE p.job_id = j.id) AS postings, "
            "EXISTS (SELECT 1 FROM postings p WHERE p.job_id = j.id AND p.jd_text IS NOT NULL) AS has_jd "
            "FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.id = %s::uuid", (app_id,)).fetchone()


# A tenant's name: a hiring-system tenant is ONE employer's instance, so the
# name another record gave it is offered to the next nameless capture there.
TEN = "career5.successfactors.eu/fabrikambk/"
named_t = post({"platform": "other", "platform_job_id": TEN + "61001", "company": "Fabrikam Bank",
                "title": "Risk Analyst", "trigger": "apply", "external": True,
                "ats_job_id": TEN + "61001"}).json()
nameless_t = post({"platform": "other", "platform_job_id": TEN + "61002", "title": "Quant Developer",
                   "trigger": "apply", "completed": True, "external": True,
                   "ats_job_id": TEN + "61002"}).json()
check("a nameless capture on a tenant another record named is offered that name",
      (nameless_t["company_known"], nameless_t["company_suggestion"]) == (False, "Fabrikam Bank"), nameless_t)
check("…offered, not stored: it stays unnamed until the user confirms it",
      app_row(nameless_t["application_id"])["company_norm"] == "unknown company")
other_t = post({"platform": "other", "platform_job_id": "career5.successfactors.eu/relecloud/9",
                "title": "Analyst", "trigger": "apply",
                "ats_job_id": "career5.successfactors.eu/relecloud/9"}).json()
check("…a tenant nobody named offers nothing", other_t["company_suggestion"] is None, other_t)
check("…nor does a capture that names its employer", named_t["company_suggestion"] is None, named_t)

# Litware's shape: the listing's own number IS the requisition, so its
# capture can propose the ATS id as a candidate and join the thin record.
SF4 = "career2.successfactors.eu/litwarebk/53001"
thin = post({"platform": "other", "platform_job_id": SF4, "title": "Staff Data Engineer",
             "trigger": "apply", "completed": True, "external": True, "ats": "successfactors",
             "ats_job_id": SF4}).json()
joined = post({"platform": "other", "platform_job_id": "jobs.litwarebank.com/53001", "company": "Litware Bank",
               "title": "Staff Data Engineer", "jd_text": "The listing's JD.", "trigger": "apply",
               "external": True, "ats": "successfactors", "ats_job_candidates": [SF4]}).json()
row = app_row(thin["application_id"])
check("a listing proposing the requisition as a candidate joins the thin record",
      joined["application_id"] == thin["application_id"] and joined["created"] is False, joined)
check("…names it, gives it the JD, and keeps the form's own ATS id",
      (row["company_norm"], row["has_jd"], row["postings"], row["ats_job_id"]) == ("litware bank", True, 2, SF4), row)
# The other site's shape: the listing's number is NOT the requisition. The
# candidate matches nothing, and records nothing.
miss = post({"platform": "other", "platform_job_id": "careers.contoso.com/1367863566", "company": "Contoso",
             "title": "AVP, Software Engineer", "trigger": "apply", "external": True,
             "ats_job_candidates": ["career10.successfactors.com/contoso/1367863566"]}).json()
check("a candidate that matches nothing: its own record, holding no ATS id",
      miss["created"] is True and app_row(miss["application_id"])["ats_job_id"] is None, miss)

# …so a human attaches that listing to the thin record the form made.
SF5 = "career10.successfactors.com/contoso/1234"
thin5 = post({"platform": "other", "platform_job_id": SF5, "title": "VP, Deployed AI Engineer",
              "trigger": "apply", "completed": True, "external": True, "ats_job_id": SF5}).json()
LISTING = {"platform": "other", "platform_job_id": "careers.contoso.com/1366006266",
           "url": "https://careers.contoso.com/job/Singapore-VP/1366006266/", "company": "Contoso",
           "title": "VP, Deployed AI Engineer", "jd_text": "Deploy AI.", "location": "Singapore"}
att = client.post(f"/captures/{thin5['application_id']}/listing", json=LISTING, headers=AUTH)
row5 = app_row(thin5["application_id"])
check("attach: the listing becomes a posting of the chosen record, with its JD and its employer",
      att.status_code == 200 and att.json()["jd"] is True
      and (row5["postings"], row5["has_jd"], row5["company_norm"]) == (2, True, "contoso"), (att.text, row5))
again = client.post(f"/captures/{thin5['application_id']}/listing", json=LISTING, headers=AUTH)
check("…the same page again changes nothing but gaps", again.status_code == 200
      and app_row(thin5["application_id"])["postings"] == 2, again.text)
taken = client.post(f"/captures/{thin5['application_id']}/listing",
                    json={"platform": "other", "platform_job_id": "careers.fabrikam.com/9", "title": "Data Engineer"},
                    headers=AUTH)
check("…a page that is ANOTHER record's posting is refused: that is a merge", taken.status_code == 409, taken.text)
check("…a page naming no job is refused",
      client.post(f"/captures/{thin5['application_id']}/listing", json={"platform": "other"},
                  headers=AUTH).status_code == 422)
check("…an unknown record is a 404, and the route needs the token",
      [client.post("/captures/00000000-0000-0000-0000-000000000000/listing", json=LISTING, headers=AUTH).status_code,
       client.post(f"/captures/{thin5['application_id']}/listing", json=LISTING, headers={}).status_code] == [404, 401])

# The popup's list: the records a listing might complete, thin ones first.
thin6 = post({"platform": "other", "platform_job_id": "career2.successfactors.eu/litwarebk/53002",
              "title": "ML Engineer", "trigger": "apply", "completed": True, "external": True,
              "ats_job_id": "career2.successfactors.eu/litwarebk/53002"}).json()
rec = client.get("/captures/recent", headers=AUTH)
first = rec.json()["records"][0] if rec.status_code == 200 else None
check("recent: the newest thin record leads the list",
      first and first["application_id"] == thin6["application_id"] and first["has_jd"] is False
      and first["company_known"] is False, rec.text[:300])
check("recent: needs the token", client.get("/captures/recent").status_code == 401)

print("manual capture -> interested")
r4 = post({"platform": "indeed", "platform_job_id": "IN-42",
           "company": "Solstice Mobility", "title": "AI Platform Engineer",
           "jd_text": "the JD", "trigger": "manual"})
with db.connect() as conn:
    st = conn.execute(
        "SELECT status FROM application_status WHERE application_id = %s::uuid",
        (r4.json()["application_id"],)).fetchone()
    check("status interested", st["status"] == "interested", st)

print("the receipt says when the listing asked for the CV by email (4 Oct 2026)")
_EA = {"platform": "linkedin", "url": "https://www.linkedin.com/jobs/view/77/",
       "company": "Contoso Search", "title": "Data Engineer", "trigger": "apply",
       "jd_text": "Great team.\nPlease send your updated resume to jane@contoso-search.example."}
ea = post({**_EA, "platform_job_id": "LI-ea-1"}).json()
check("an apply whose JD asks for the CV by email carries the sentence and a mailto",
      ea["email_ask"] is not None
      and ea["email_ask"]["sentence"] == "Please send your updated resume to jane@contoso-search.example."
      and ea["email_ask"]["to"] == ["jane@contoso-search.example"]
      and ea["email_ask"]["mailto"].startswith("mailto:jane@contoso-search.example?subject="), ea)
on = post({**_EA, "platform_job_id": "LI-ea-2",
           "jd_text": "Please apply online or email your CV to jane@contoso-search.example"}).json()
check("one that also offers the apply button carries none", on["email_ask"] is None, on)
sv = post({**_EA, "platform_job_id": "LI-ea-3", "trigger": "manual"}).json()
check("nor does a job only saved: nothing was applied for yet", sv["email_ask"] is None, sv)
with db.connect() as conn, conn.transaction():
    conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
                 "SELECT user_id, id, 'note', 'manual', now(), '{\"emailed\": \"sent\"}' "
                 "FROM applications WHERE id = %s::uuid", (ea["application_id"],))
again = post({**_EA, "platform_job_id": "LI-ea-1"}).json()
check("once it is sent, a re-capture says nothing",
      again["application_id"] == ea["application_id"] and again["email_ask"] is None, again)

print("a long ATS form keeps every answer, its last step included (4 Oct 2026)")
# The cap was 60 and kept the FIRST 60, so a long form lost its last step,
# where the screening questions are: 14 real captures, 24 Sep - 3 Oct.
_long = [{"question": f"Employer {n}", "answer": f"Co {n}", "type": "text"} for n in range(110)]
_long.append({"question": "Will you now or in the future require sponsorship?",
              "answer": "Yes", "type": "select"})
lg = post({"platform": "other", "platform_job_id": "longform.example/9", "company": "Longform Co",
           "title": "Engineer", "trigger": "apply", "answers": _long}).json()
with db.connect() as conn:
    _qs = [r["question"] for r in conn.execute(
        "SELECT question FROM application_answers WHERE application_id = %s::uuid ORDER BY ordinal",
        (lg["application_id"],)).fetchall()]
check("all 111 answers are stored, and the response says so",
      lg["answers"] == 111 and len(_qs) == 111, (lg["answers"], len(_qs)))
check("...the form's last question among them",
      _qs[-1] == "Will you now or in the future require sponsorship?", _qs[-1:])

print("\nALL CAPTURE PATHS PASS")
