"""Phase 3 tests — extraction chain, dedup, cover letters, analytics, reminders.

Self-contained (seeds via /captures), LLM + embedding seams stubbed. Run on a
fresh or existing test DB after the other suites:
  TRACKER_API_TOKEN=testtok TRACKER_DATABASE_URL=postgresql:///tracker_test \
      python3 tests/test_phase3.py
"""

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
os.environ.setdefault("TRACKER_API_TOKEN", "testtok")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from pipeline import covers, db, dedup, embeddings, jd_extraction, worker
from pipeline.jd_extraction import JdExtraction
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
        # The resume profile lives on the user row and nowhere else — the file
        # fallback was removed 21 Aug 2026 (covers.py:load_profile). Seeding it
        # here is what the cover-letter test needs; the missing-profile case is
        # asserted separately below.
        conn.execute("UPDATE users SET resume_profile = %s WHERE id = %s",
                     ("# AZ\n12y software engineer; AI pivot.", u["id"]))
        sid = _auth.create_session(conn, u["id"])
    client.cookies.set("session", sid)


_bootstrap_session()

AUTH = {"Authorization": f"Bearer {os.environ['TRACKER_API_TOKEN']}"}
DIM = 1024


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


# ---------------------------------------------------------------- stubs

def _fake_jd_extract(client_, jd_text, title):
    d = {"languages": ["Python", "SQL"], "technologies": ["PyTorch", "AWS"],
         "seniority": "Senior", "salary_min": 10000, "salary_max": 14000,
         "currency": "SGD", "work_mode": "hybrid",
         "visa_signal": "sponsors", "visa_notes": "EP sponsorship available"}
    return JdExtraction(**d, raw=d)


def _vec(*head):
    v = [0.0] * DIM
    for i, x in enumerate(head):
        v[i] = x
    return v


VECTORS = {
    "JD-A": _vec(1.0),            # anchor
    "JD-B": _vec(1.0, 0.03),      # cos ~0.9996 vs A -> auto
    "JD-C": _vec(1.0, 0.4843),    # cos ~0.900  vs A -> pending band
    "JD-D": _vec(0.0, 0.0, 1.0),  # orthogonal -> nothing
}


def _fake_embed(texts):
    out = []
    for t in texts:
        key = next((k for k in VECTORS if k in t), None)
        if key is None:
            raise AssertionError(f"no stub vector for text: {t[:40]}")
        out.append(VECTORS[key])
    return out


def _cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    return num / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)))


jd_extraction.extract = _fake_jd_extract
worker.jd_extraction.extract = _fake_jd_extract
embeddings.available = lambda: True
embeddings.embed = _fake_embed
worker.embeddings.available = embeddings.available
worker.embeddings.embed = embeddings.embed
covers.generate = lambda c, profile, company, title, jd: (
    f"Dear Hiring Team,\nRe {title} at {company}. Grounded in: {profile[:20]}")
worker.covers.generate = covers.generate

check("stub geometry: B is auto band", _cos(VECTORS["JD-A"], VECTORS["JD-B"]) >= 0.95)
check("stub geometry: C is pending band",
      0.88 <= _cos(VECTORS["JD-A"], VECTORS["JD-C"]) < 0.95)


def cap(pid, company, title, jd_key, platform="linkedin", answers=None):
    r = client.post("/captures", headers=AUTH, json={
        "platform": platform, "platform_job_id": pid,
        "company": company, "title": title,
        "jd_text": f"{jd_key} Build LLM systems for {company}.",
        "trigger": "apply", "answers": answers})
    assert r.status_code == 200, r.text
    return r.json()


def drain():
    with db.connect() as conn:
        while worker.process_one(conn):
            pass


print("extraction chain: capture -> extract_jd -> embed_jd -> dedup_scan")
ra = cap("P3-A", "Vantage Tech Co., Ltd.", "Senior AI Engineer", "JD-A",
         answers=[{"question": "Notice period?", "answer": "1 month"},
                  {"question": "Years with Python", "answer": "8"}])
drain()
with db.connect() as conn:
    x = conn.execute(
        "SELECT x.* FROM extractions x JOIN postings p ON p.id = x.posting_id "
        "WHERE p.platform_job_id = 'P3-A'").fetchone()
    check("extraction row written", x is not None)
    check("unverified by default", x["verified"] is False)
    check("visa signal extracted", x["visa_signal"] == "sponsors", x)
    emb = conn.execute(
        "SELECT jd_embedding IS NOT NULL AS has FROM postings "
        "WHERE platform_job_id = 'P3-A'").fetchone()
    check("embedding stored", emb["has"] is True)

print("dedup: auto merge (cross-platform, same role)")
rb = cap("P3-B", "Vantage Tech", "Senior AI Engineer (LLM)", "JD-B", platform="jobstreet",
         answers=[{"question": "Notice period", "answer": "2 months"},
                  {"question": "Willing to relocate?", "answer": "No"}])
drain()
with db.connect() as conn:
    check("applications merged into one",
          rb.get("application_id") is not None and conn.execute(
              "SELECT count(DISTINCT job_id) AS n FROM postings "
              "WHERE platform_job_id IN ('P3-A','P3-B')").fetchone()["n"] == 1)
    n_apps = conn.execute(
        """SELECT count(*) AS n FROM applications a
           WHERE a.job_id = (SELECT job_id FROM postings WHERE platform_job_id='P3-A')"""
    ).fetchone()["n"]
    check("single application on the merged job", n_apps == 1, n_apps)
    st = conn.execute(
        "SELECT state FROM duplicate_candidates ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    check("pair recorded as auto", st["state"] == "auto", st)
    # Both postings' forms asked about notice period, in different words. The
    # UNIQUE (application_id, question_norm) would reject a blind move, so
    # merge_jobs drops the loser's copy of a question the winner already has.
    qa = {r_["question_norm"]: r_["answer"] for r_ in conn.execute(
        "SELECT question_norm, answer FROM application_answers aa "
        "JOIN applications a ON a.id = aa.application_id "
        "WHERE a.job_id = (SELECT job_id FROM postings WHERE platform_job_id='P3-A')"
    ).fetchall()}
    check("merged answers: one row per question, loser's duplicate dropped",
          sorted(qa) == ["notice period", "willing to relocate", "years with python"], qa)
    check("surviving application kept its own answer to the shared question",
          qa["notice period"] == "1 month", qa)
    check("the other posting's unique answer came across",
          qa["willing to relocate"] == "No", qa)

print("dedup: pending band goes to triage")
rc = cap("P3-C", "Talent Bridge Agency", "Senior AI Engineer", "JD-C", platform="indeed")
drain()
with db.connect() as conn:
    pend = conn.execute(
        "SELECT id, cosine_sim FROM duplicate_candidates WHERE state = 'pending'"
    ).fetchall()
    check("pending candidate created", len(pend) == 1, pend)
    check("cosine in pending band", 0.88 <= pend[0]["cosine_sim"] < 0.95, pend)
    check("agency job NOT auto-merged", conn.execute(
        "SELECT count(DISTINCT job_id) AS n FROM postings "
        "WHERE platform_job_id IN ('P3-A','P3-C')").fetchone()["n"] == 2)
r = client.get("/triage")
check("duplicate shown in triage", "Same job — merge" in r.text)

print("dedup: human confirm merges")
r = client.post(f"/duplicates/{pend[0]['id']}", data={"action": "confirm"})
check("confirm redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    check("confirmed pair merged jobs", conn.execute(
        "SELECT count(DISTINCT job_id) AS n FROM postings "
        "WHERE platform_job_id IN ('P3-A','P3-C')").fetchone()["n"] == 1)

print("orthogonal JD stays separate")
cap("P3-D", "Lumen Bank", "Data Platform Engineer", "JD-D")
drain()
with db.connect() as conn:
    check("no new candidates for orthogonal JD", conn.execute(
        "SELECT count(*) AS n FROM duplicate_candidates WHERE state = 'pending'"
    ).fetchone()["n"] == 0)

print("verification workflow")
app_id = ra["application_id"]
r = client.post(f"/extractions/{x['id']}/verify", data={
    "application_id": app_id, "languages": "Python, Go",
    "technologies": "", "visa_signal": ""})
check("verify redirects", r.status_code == 303, r.status_code)
with db.connect() as conn:
    row = conn.execute("SELECT verified, languages, technologies, visa_signal "
                       "FROM extractions WHERE id = %s", (x["id"],)).fetchone()
    check("verified set", row["verified"] is True)
    check("corrections applied", row["languages"] == ["Python", "Go"], row)
    check("blank fields kept extracted values",
          row["technologies"] == ["PyTorch", "AWS"] and row["visa_signal"] == "sponsors", row)

print("cover letter")
r = client.post(f"/applications/{app_id}/cover-letter")
check("request queued", r.status_code == 303, r.status_code)
check("detail shows queued state",
      "Draft queued" in client.get(f"/applications/{app_id}").text)
drain()
with db.connect() as conn:
    ar = conn.execute(
        "SELECT content FROM artifacts WHERE application_id = %s::uuid "
        "AND kind = 'cover_letter'", (app_id,)).fetchone()
    check("artifact written", ar is not None and "Dear Hiring Team" in ar["content"], ar)
check("detail shows the letter", "Show text" in client.get(f"/applications/{app_id}").text)

# The release-blocker case: a clean install with nothing saved yet. The old
# behaviour pointed at profile.md, a path nothing else in the product ever
# wrote to, so the error named the one route that did not work. It must name
# the page that does.
try:
    covers.load_profile(None)
    raise SystemExit("FAIL: an empty profile generated a letter anyway")
except covers.ProfileMissing as e:
    check("missing profile names the Settings page, not a file path",
          "Settings" in str(e) and "profile.md" not in str(e), e)
try:
    covers.load_profile("   ")
    raise SystemExit("FAIL: a whitespace-only profile counted as set")
except covers.ProfileMissing:
    check("a whitespace-only profile is treated as missing", True)
check("a real profile is returned verbatim",
      covers.load_profile("# AZ\n12y") == "# AZ\n12y")

print("analytics")
with db.connect() as conn:   # give the merged app a response for the math
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "SELECT user_id, id, 'interview_invite', 'email', now(), '{}' "
        "FROM applications WHERE id = %s::uuid", (app_id,))
    # by_resume renders only once the dimension actually splits — one resume is
    # a row of totals, not a comparison — so give it two to compare. This
    # replaced a by_focus assertion when applications.focused was cut; the
    # panel it covers is the one that dimension was always trying to be.
    ids = [r_["id"] for r_ in conn.execute(
        "SELECT id FROM applications ORDER BY created_at").fetchall()]
    for i, aid in enumerate(ids):
        conn.execute("UPDATE applications SET resume_file = %s WHERE id = %s",
                     (f"resume-variant-{i % 2}.pdf", aid))
    conn.commit()
r = client.get("/analytics")
check("analytics renders", r.status_code == 200, r.status_code)
check("platform table populated", "linkedin" in r.text)
check("resume split renders once the dimension splits",
      "By resume sent" in r.text and "resume-variant-0.pdf" in r.text, r.status_code)
check("technology table populated", "Pytorch" in r.text or "PyTorch" in r.text)

print("reminders")
with db.connect() as conn:
    user_id = db.single_user_id(conn)
    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'stale co', 'Backend Engineer') RETURNING id", (user_id,)).fetchone()
    stale = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, job["id"])).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now() - interval '20 days', '{}')",
        (user_id, stale))
    conn.commit()
r = client.get("/follow-ups")
check("stale application flagged for follow-up",
      "Needs follow-up" in r.text and "stale co" in r.text)
check("responded application not flagged", "vantage tech" not in r.text)
# "stale co" is an application, so it is legitimately in the table on "/" —
# what must be gone is the QUEUE (its heading, its card, its action).
list_html = client.get("/").text
check("the list links to the queue with a count rather than carrying it",
      "Needs follow-up" not in list_html
      and 'value="follow_up_sent"' not in list_html
      and 'href="/follow-ups"' in list_html)

print("logging an engaged event clears a stale application from the follow-up queue")
with db.connect() as conn:
    job2 = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'stale eng co', 'Backend Engineer') RETURNING id", (user_id,)).fetchone()
    stale2 = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, job2["id"])).fetchone()["id"]
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'manual', now() - interval '20 days', '{}')",
        (user_id, stale2))
    conn.commit()
r = client.get("/follow-ups")
check("newly-stale application flagged for follow-up before any response",
      "stale eng co" in r.text)
r = client.post(f"/applications/{stale2}/events",
                data={"type": "engaged", "channel": "whatsapp", "note": "recruiter followed up"})
check("engaged event redirects", r.status_code == 303, r.status_code)
r = client.get("/follow-ups")
check("logging an engaged event clears the application from Needs follow-up",
      "stale eng co" not in r.text)

print("\nALL PHASE 3 PATHS PASS")
