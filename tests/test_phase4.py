"""Phase 4 tests — accounts, sessions, per-user tokens, RLS tenant isolation.

The isolation checks are the point of this suite: they verify cross-tenant
access fails at BOTH layers — through the web app (404s), and at raw SQL under
the RLS role even with no WHERE clause at all.

Run LAST (it creates a second user, which disables the legacy token fallback
that test_captures relies on):
  TRACKER_API_TOKEN=testtok TRACKER_DATABASE_URL=postgresql:///tracker_test \
      python3 tests/test_phase4.py
"""

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
os.environ.setdefault("TRACKER_API_TOKEN", "testtok")
os.environ.setdefault("TRACKER_SECRET_KEY", "phase4-test-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from pipeline import auth, covers, db, worker
from pipeline.web import app


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


def new_client():
    return TestClient(app, follow_redirects=False)


print("auth gate")
anon = new_client()
r = anon.get("/")
check("unauthenticated page redirects to login",
      r.status_code == 303 and r.headers["location"] == "/login", r.status_code)
r = anon.get("/login")
check("login page renders", r.status_code == 200)
check("unauthenticated page defaults to data-theme=\"auto\" (no AttributeError "
      "on the unset request.state.theme)", 'data-theme="auto"' in r.text, r.text[:200])

print("signup")
alice = new_client()
r = alice.post("/signup", data={"email": "Alice@Example.com", "password": "alicepass1"})
check("signup renders settings with one-time token", r.status_code == 200 and "newtok" in r.text, r.status_code)
alice_token = re.search(r'id="newtok"[^>]*>([^<]+)<', r.text).group(1).strip()
check("token has expected shape", alice_token.startswith("trk_"), alice_token)
check("session cookie set", "session" in alice.cookies)
check("email normalized to lowercase", "alice@example.com" in r.text)

bob = new_client()
r = bob.post("/signup", data={"email": "bob@example.com", "password": "bobpass12"})
bob_token = re.search(r'id="newtok"[^>]*>([^<]+)<', r.text).group(1).strip()
check("second account created", r.status_code == 200)
check("duplicate email rejected", new_client().post(
    "/signup", data={"email": "alice@example.com", "password": "whatever1"}).status_code == 400)
check("short password rejected", new_client().post(
    "/signup", data={"email": "c@example.com", "password": "short"}).status_code == 400)

print("login")
check("wrong password rejected", new_client().post(
    "/login", data={"email": "alice@example.com", "password": "wrong"}).status_code == 401)
carol = new_client()
r = carol.post("/login", data={"email": "alice@example.com", "password": "alicepass1"})
check("correct password creates session", r.status_code == 303 and "session" in carol.cookies)

print("tenant isolation — web layer")
r = alice.post("/captures", headers={"Authorization": f"Bearer {alice_token}"}, json={
    "platform": "linkedin", "platform_job_id": "P4-A1",
    "company": "Alice Corp Pte Ltd", "title": "Senior AI Engineer",
    "jd_text": "JD for alice", "trigger": "apply"})
check("capture with alice's token lands", r.status_code == 200, r.text)
alice_app = r.json()["application_id"]
# display uses the captured company_raw verbatim; company_norm (-> "alice")
# is only the internal dedup/matching key, stripped of "Corp"/"Pte Ltd".
check("alice sees her application",
      ">Alice Corp Pte Ltd</a>" in alice.get("/").text)
check("bob does not see it on his pages",
      ">Alice Corp Pte Ltd</a>" not in bob.get("/").text)
check("bob's direct fetch of alice's application 404s",
      bob.get(f"/applications/{alice_app}").status_code == 404)
check("bob cannot log events on it", bob.post(
    f"/applications/{alice_app}/events", data={"type": "note", "note": "x"}).status_code == 404)
check("legacy env token dead with multiple users", new_client().post(
    "/captures", headers={"Authorization": "Bearer testtok"},
    json={"platform": "linkedin", "trigger": "apply", "title": "x", "jd_text": "y"}
).status_code == 401)
check("random token rejected", new_client().post(
    "/captures", headers={"Authorization": "Bearer trk_nope"},
    json={"platform": "linkedin", "trigger": "apply", "title": "x", "jd_text": "y"}
).status_code == 401)

print("tenant isolation — raw SQL under the RLS role")
with db.connect() as admin:
    alice_id = admin.execute("SELECT id FROM users WHERE email = 'alice@example.com'").fetchone()["id"]
    bob_id = admin.execute("SELECT id FROM users WHERE email = 'bob@example.com'").fetchone()["id"]
with db.connect_scoped(bob_id) as conn:
    n = conn.execute("SELECT count(*) AS n FROM applications").fetchone()["n"]
    check("bob's scoped SELECT with no WHERE sees zero rows", n == 0, n)
    updated = conn.execute("UPDATE applications SET focused = true RETURNING id").fetchall()
    check("bob's blanket UPDATE touches nothing", updated == [], updated)
    try:
        conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
                     "VALUES (%s, %s::uuid, 'note', 'manual', now(), '{}')",
                     (alice_id, alice_app))
        conn.commit()
        raise SystemExit("FAIL: bob wrote an event as alice")
    except Exception as e:
        conn.rollback()
        check("bob's forged-user INSERT rejected by policy", "policy" in str(e).lower() or "row-level" in str(e).lower(), e)
with db.connect_scoped(alice_id) as conn:
    n = conn.execute("SELECT count(*) AS n FROM applications").fetchone()["n"]
    check("alice's scoped SELECT sees exactly her row", n == 1, n)
    n = conn.execute("SELECT count(*) AS n FROM application_status").fetchone()["n"]
    check("view honors RLS (security_invoker)", n == 1, n)

print("token lifecycle")
r = alice.post("/settings/token")
new_token = re.search(r'id="newtok"[^>]*>([^<]+)<', r.text).group(1).strip()
check("rotation mints a fresh token", new_token != alice_token)
check("old token now 401", alice.post("/captures",
      headers={"Authorization": f"Bearer {alice_token}"},
      json={"platform": "linkedin", "trigger": "apply", "title": "x", "jd_text": "y"}
).status_code == 401)
check("new token works", alice.post("/captures",
      headers={"Authorization": f"Bearer {new_token}"},
      json={"platform": "linkedin", "platform_job_id": "P4-A1",
            "trigger": "apply", "title": "Senior AI Engineer", "jd_text": "JD for alice"}
).status_code == 200)

print("settings: per-user resume profile grounds alice's letters")
covers.generate = lambda c, profile, company, title, jd: f"LETTER[{profile}]"
worker.covers.generate = covers.generate
r = alice.post("/settings/profile", data={"resume_profile": "ALICE-PROFILE-MARKER"})
check("profile saved", r.status_code == 303)
alice.post(f"/applications/{alice_app}/cover-letter")
with db.connect() as conn:
    while worker.process_one(conn):
        pass
    ar = conn.execute("SELECT content FROM artifacts WHERE application_id = %s::uuid "
                      "AND kind = 'cover_letter' ORDER BY created_at DESC LIMIT 1",
                      (alice_app,)).fetchone()
check("letter grounded in HER stored profile",
      ar and "ALICE-PROFILE-MARKER" in ar["content"], ar)

print("settings: theme preference")
r = alice.get("/settings")
check("default theme is follow-my-system", '<option value="" selected>' in r.text, r.text)
r = alice.get("/")
check("default page carries data-theme=\"auto\"", 'data-theme="auto"' in r.text, r.text[:200])

r = alice.post("/settings/theme", data={"theme": "dark"})
check("theme=dark accepted", r.status_code == 303, r.status_code)
check("home page now carries data-theme=\"dark\"",
      'data-theme="dark"' in alice.get("/").text)
check("settings page reflects the saved pin",
      '<option value="dark" selected>' in alice.get("/settings").text)

r = alice.post("/settings/theme", data={"theme": "light"})
check("theme=light accepted", r.status_code == 303, r.status_code)
check("home page now carries data-theme=\"light\"",
      'data-theme="light"' in alice.get("/").text)

r = alice.post("/settings/theme", data={"theme": "neon"})
check("unknown theme value rejected", r.status_code == 400, r.status_code)
check("rejected value did not overwrite the saved pin",
      'data-theme="light"' in alice.get("/").text)

r = alice.post("/settings/theme", data={"theme": ""})
check("blank theme clears the pin", r.status_code == 303, r.status_code)
check("page returns to data-theme=\"auto\"",
      'data-theme="auto"' in alice.get("/").text)

print("password change + logout")
check("wrong current password blocked", alice.post(
    "/settings/password", data={"current": "nope", "new": "newpass123"}).status_code == 400)
check("password change ok", alice.post(
    "/settings/password", data={"current": "alicepass1", "new": "newpass123"}).status_code == 200)
check("old password dead", new_client().post(
    "/login", data={"email": "alice@example.com", "password": "alicepass1"}).status_code == 401)
check("new password works", new_client().post(
    "/login", data={"email": "alice@example.com", "password": "newpass123"}).status_code == 303)
r = alice.post("/logout")
check("logout redirects", r.status_code == 303)
check("session revoked server-side", alice.get("/").status_code == 303)

print("credential encryption roundtrip")
secret = '{"refresh_token": "rt-123"}'
enc = auth.encrypt(secret)
check("ciphertext differs and decrypts", enc != secret and auth.decrypt(enc) == secret)
check("tampered ciphertext yields None", auth.decrypt(enc[:-2] + "xx") is None)

print("\nALL PHASE 4 PATHS PASS")
