"""Tests for POST /captures (Phase 2 server side).

Run on a database with migrations + one user (independent of the other suites):
  TRACKER_API_TOKEN=testtok TRACKER_DATABASE_URL=postgresql:///tracker_test \
      python3 tests/test_captures.py
"""

import os
import sys
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
    "focused": True, "note": "referred by K",
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
        """SELECT a.focused, j.company_norm, s.status
           FROM applications a JOIN jobs j ON j.id = a.job_id
           JOIN application_status s ON s.application_id = a.id
           WHERE a.id = %s::uuid""", (app_id,)).fetchone()
    check("company normalized", a["company_norm"] == "sea labs", a)
    check("status applied", a["status"] == "applied", a)
    check("focused tag stored", a["focused"] is True)
    evs = [r_["type"] for r_ in conn.execute(
        "SELECT type FROM events WHERE application_id = %s::uuid ORDER BY type",
        (app_id,)).fetchall()]
    check("applied + note events", evs == ["applied", "note"], evs)

print("re-capture upserts, never duplicates")
r2 = post({**BASE, "jd_text": "Build LLM systems. Python, PyTorch. (updated)",
           "focused": None, "note": None})
check("same application returned", r2.json()["application_id"] == app_id, r2.text)
check("flagged enriched, not created",
      r2.json()["enriched"] is True and r2.json()["created"] is False, r2.text)
with db.connect() as conn:
    n_applied = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s::uuid "
        "AND type = 'applied'", (app_id,)).fetchone()["n"]
    check("applied event not duplicated", n_applied == 1, n_applied)
    check("focused survives null re-capture", conn.execute(
        "SELECT focused FROM applications WHERE id = %s::uuid",
        (app_id,)).fetchone()["focused"] is True)
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
