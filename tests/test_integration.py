"""End-to-end integration test against a live database (no external APIs).

Stubs the two LLM stage functions and drives the real queue, worker, matcher,
and event writes. Covers the paths that matter:

  1. auto-match  — rejection email matched to a seeded application, event appended
  2. create      — confirmation from an unseen company creates job/application/posting/events
  3. pending     — non-confirmation with no candidate lands in triage, writes nothing
  3c. recruiter_outreach — never auto-matches (even same-company) or auto-creates
  3h. a date the email states rides in the payload; the event stays on arrival
  4. backoff    — a failing job retries with attempts+1 and a future run_after

Run:  TRACKER_DATABASE_URL=postgresql:///tracker python3 tests/test_integration.py
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psycopg.types.json import Json

from pipeline import analytics, db, email_classifier, matcher, worker
from pipeline.email_classifier import Classification, Extraction

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- LLM stubs

FAKE_CLASSIFY = {
    "northwind-rejection": Classification(True, "rejection", 0.95, "stub"),
    "acme-confirmation": Classification(True, "confirmation", 0.9, "stub"),
    "mystery-rejection": Classification(True, "rejection", 0.9, "stub"),
    "newsletter": Classification(False, None, 0.98, "stub"),
    "recruiter-pitch": Classification(True, "recruiter_outreach", 0.9, "stub"),
    "recruiter-unknown": Classification(True, "recruiter_outreach", 0.85, "stub"),
    "rebrand-confirmation": Classification(True, "confirmation", 0.93, "stub"),
    "tagline-confirmation": Classification(True, "confirmation", 0.93, "stub"),
    "stranger-invite": Classification(True, "interview_invite", 0.93, "stub"),
    "alpine-invite": Classification(True, "interview_invite", 0.93, "stub"),
    "lamna-dp-rejection": Classification(True, "rejection", 0.93, "stub"),
    "lamna-ai-invite": Classification(True, "interview_invite", 0.93, "stub"),
    # Mail the user SENT (emails.sent_by_user) — path 3g.
    "sent-reply": Classification(True, "sent_reply", 0.9, "stub"),
    "sent-follow-up": Classification(True, "sent_follow_up", 0.9, "stub"),
    "sent-resume-again": Classification(True, "sent_application", 0.9, "stub"),
    "sent-resume-new": Classification(True, "sent_application", 0.9, "stub"),
    "sent-reply-stranger": Classification(True, "sent_reply", 0.9, "stub"),
    "sent-withdrawal": Classification(True, "sent_withdrawal", 0.9, "stub"),
}
def _fake_extraction(**kw):
    """Mirror the real extract_email(): raw always carries the full payload."""
    d = {"company": None, "role_title": None, "platform": "unknown", "ats": None,
         "event_date": None, "status_detail": None, "recruiter": None, "notes": None, **kw}
    return Extraction(**d, raw=d)


FAKE_EXTRACT = {
    # The rejection quotes the day the application went in — the seeded
    # applied event's day. A real one did (7 Sep 2026: "...on 04/08/2026"),
    # and while a stated date moved the event, it filed a month back on the
    # apply day and sorted BEFORE the applied event it answers.
    "northwind-rejection": _fake_extraction(company="Northwind Labs Inc",
                                       role_title="Senior AI Engineer",
                                       platform="linkedin", event_date="2026-07-10"),
    "acme-confirmation": _fake_extraction(company="Acme Pte. Ltd.",
                                          role_title="AI Platform Engineer",
                                          platform="ats", ats="greenhouse",
                                          event_date="2026-07-15",
                                          recruiter={"name": "Jo Tan", "email": "jo@acme.example"}),
    "mystery-rejection": _fake_extraction(company="Totally Unknown Corp",
                                          role_title="Data Engineer"),
    # Same company as the seeded Northwind application, but a DIFFERENT role — the
    # regression case for "recruiter_outreach must never auto-match onto an
    # existing application's timeline", since by definition it's a role the
    # user did not apply to.
    "recruiter-pitch": _fake_extraction(company="Northwind Labs Inc",
                                        role_title="Staff ML Engineer",
                                        platform="linkedin",
                                        recruiter={"name": "Recruiter Ren", "email": None}),
    "recruiter-unknown": _fake_extraction(company="Totally New Agency",
                                          role_title="Backend Engineer"),
    # The seeded Northwind application again, but the sender brands itself with
    # a longer name that falls under COMPANY_TRGM_MIN against 'northwind labs'
    # — the shape of three real duplicates on 4 Aug 2026 (contoso/"Contoso
    # Markets", fabrikam/"Fabrikam", litware/"Litware International"), where
    # an employer's ATS (or LinkedIn's own mail) named the company differently
    # enough to miss the gate while still SHARING A WORD with it. The title is
    # identical, which is what the fallback keys on; the case differs from the
    # stored title_canonical on purpose. Until 23 Sep 2026 this fixture read
    # "Vestbridge Holdings" — a name sharing nothing — which modelled a rule
    # broader than any real case and is exactly the shape that mis-filed a
    # live interview thread onto a same-titled agency (path 3f).
    "rebrand-confirmation": _fake_extraction(company="Northwind International Pte Ltd",
                                             role_title="Senior AI Engineer",
                                             platform="linkedin"),
    # 23 Sep 2026: a recruiter at "Woodgrove" writes the role as "Senior AI
    # Engineer" — the seeded Northwind title, byte for byte — while the real
    # Woodgrove application (seeded in path 3f under LinkedIn's longer employer
    # name, with a differently worded title) misses the gate. Rule 1 must not
    # hand this to Northwind: the two company names share no word.
    "stranger-invite": _fake_extraction(company="Woodgrove",
                                        role_title="Senior AI Engineer",
                                        platform="direct"),
    # The shape of the Wingtip Talent Group duplicate (4 Aug 2026), which walked
    # past BOTH the company gate and the exact-title fallback: the mail carries
    # the bare employer name while the job board carried the same name plus a
    # marketing tagline (company_sim 0.453, under COMPANY_TRGM_MIN), and the
    # board's title carries a suffix the mail does not, so the byte-identical
    # test misses too. Only the company-word-containment rule reaches it.
    "tagline-confirmation": _fake_extraction(company="Harbourline Consulting Group",
                                             role_title="Staff Platform Engineer",
                                             platform="linkedin"),
    # An invitation naming the interview day, two weeks after it arrives —
    # path 3h. The shape of ten real invites that filed on the interview day
    # (24 Sep 2026), two of them in the future.
    "alpine-invite": _fake_extraction(company="Alpine Ski House",
                                      role_title="Platform Engineer",
                                      platform="linkedin", event_date="2026-08-04"),
    # Path 3i: mail under the SHORT name of an employer whose records use two
    # (24 Sep 2026: an employer's short name beside the same name + "Group", an
    # agency's name beside its "<agency> x <client>" record). "lamna" is 0.50 and 0.26
    # similar to the longer forms, under COMPANY_TRGM_MIN, as the real ones were.
    "lamna-dp-rejection": _fake_extraction(company="Lamna", role_title="Data Platform Engineer",
                                           platform="ats"),
    "lamna-ai-invite": _fake_extraction(company="Lamna", role_title="AI Engineer",
                                        platform="direct"),
}
# The user's own messages in a thread about the seeded Proseware application.
# The extractor reads the counterpart correctly off the quoted thread (verified
# on all 26 real sent emails, 24 Sep 2026) — and still finds a date in it: the
# interview day being arranged, which is the other side's event, not the
# user's. event_date is set on every one so path 3g can prove it is ignored.
_proseware = dict(company="Proseware Pte Ltd", role_title="Backend Engineer",
                  platform="direct", event_date="2026-08-14",
                  recruiter={"name": "Jane Recruiter", "email": None})
for _k in ("sent-reply", "sent-follow-up", "sent-resume-again", "sent-withdrawal"):
    FAKE_EXTRACT[_k] = _fake_extraction(**_proseware)
FAKE_EXTRACT["sent-resume-new"] = _fake_extraction(company="Lucerne Publishing",
                                                   role_title="Platform Engineer",
                                                   platform="direct", event_date="2026-07-01")
FAKE_EXTRACT["sent-reply-stranger"] = _fake_extraction(company="Trey Research",
                                                       role_title="Data Engineer",
                                                       platform="direct")

CLASSIFY_CALLS: list[tuple[str, bool]] = []   # (subject, sent) — what the worker asked


def _fake_classify(client, sender, subject, received, body, sent=False):
    CLASSIFY_CALLS.append((subject, sent))
    return FAKE_CLASSIFY[subject]


email_classifier.classify_email = _fake_classify
email_classifier.extract_email = lambda client, sender, subject, received, body, ctype: \
    FAKE_EXTRACT[subject]

# Stage 3, the reason a rejection states (path 3j). Stubbed like the other two:
# the worker calls it for every rejection, and an unstubbed call would reach
# the real API with the dummy key and read as an outage. One rejection states
# its reason, the shape of a recruiter's one-line reply (25 Sep 2026); the rest
# are form letters.
FAKE_REASON = {
    "lamna-dp-rejection": {"reason": "visa", "quote": "the team can not sponsor your EP",
                           "model": "stub", "prompt_version": "rejection_reason_v1"},
}
REASON_CALLS: list[str] = []


def _fake_reason(client, sender, subject, received, body):
    REASON_CALLS.append(subject)
    return FAKE_REASON.get(subject) or {"reason": None, "quote": None, "model": "stub",
                                        "prompt_version": "rejection_reason_v1"}


email_classifier.rejection_reason = _fake_reason


# ---------------------------------------------------------------- helpers

def seed_email(conn, user_id, key, sender="noreply@linkedin.com", sent=False):
    row = conn.execute(
        """INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text,
                               received_at, sent_by_user)
           VALUES (%s, %s, %s, %s, 'body', %s, %s) RETURNING id""",
        (user_id, f"gm-{key}", sender, key, NOW, sent),
    ).fetchone()
    db.enqueue(conn, user_id, "classify_email", {"email_id": str(row["id"])})
    return row["id"]


def drain(conn):
    while worker.process_one(conn):
        pass


def email_state(conn, email_id):
    return conn.execute(
        "SELECT classification, triage_state, matched_application_id, match_score "
        "FROM emails WHERE id = %s", (email_id,)).fetchone()


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


# ---------------------------------------------------------------- test

with db.connect() as conn:
    user_id = db.single_user_id(conn)

    # Seed a known application: Northwind, Senior AI Engineer, applied 11 days ago via LinkedIn.
    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'northwind labs', 'senior ai engineer') RETURNING id", (user_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, platform_job_id, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'LI-northwind-1', 'extension')", (user_id, job["id"]))
    app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, job["id"])).fetchone()
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', %s, %s)",
        (user_id, app["id"], NOW.replace(day=10), Json({})))
    conn.commit()

    print("path 1: auto-match")
    e1 = seed_email(conn, user_id, "northwind-rejection")
    conn.commit()
    drain(conn)
    s1 = email_state(conn, e1)
    check("classified as rejection", s1["classification"] == "rejection", s1)
    check("auto_matched", s1["triage_state"] == "auto_matched", s1)
    check("matched to seeded application", str(s1["matched_application_id"]) == str(app["id"]), s1)
    check("score above threshold", s1["match_score"] and s1["match_score"] >= 0.75, s1)
    ev = conn.execute(
        "SELECT type, source_email_id, occurred_at, payload FROM events WHERE application_id = %s "
        "AND type = 'rejected'", (app["id"],)).fetchone()
    check("rejected event appended with provenance", ev and str(ev["source_email_id"]) == str(e1), ev)
    check("...dated when it arrived, not the apply day it quotes", ev["occurred_at"] == NOW, ev)
    check("...which it keeps as the stated date", ev["payload"].get("stated_date") == "2026-07-10", ev)
    applied_at = conn.execute(
        "SELECT occurred_at FROM events WHERE application_id = %s AND type = 'applied'",
        (app["id"],)).fetchone()["occurred_at"]
    check("...so it sorts AFTER the applied event it answers", ev["occurred_at"] > applied_at,
          (ev["occurred_at"], applied_at))
    st = conn.execute("SELECT status FROM application_status WHERE application_id = %s",
                      (app["id"],)).fetchone()
    check("derived status is rejected", st["status"] == "rejected", st)

    print("path 2: create (backfill)")
    e2 = seed_email(conn, user_id, "acme-confirmation", sender="no-reply@greenhouse.io")
    conn.commit()
    drain(conn)
    s2 = email_state(conn, e2)
    check("auto_matched via create", s2["triage_state"] == "auto_matched", s2)
    new_app = conn.execute(
        """SELECT a.id, a.applied_via_posting_id, j.company_norm, p.captured_via, p.jd_text,
                  p.company_norm AS posting_norm
           FROM applications a JOIN jobs j ON j.id = a.job_id
           LEFT JOIN postings p ON p.id = a.applied_via_posting_id
           WHERE a.id = %s""", (s2["matched_application_id"],)).fetchone()
    check("job created with normalized company", new_app["company_norm"] == "acme", new_app)
    check("...and its posting carries it too, as a captured one does",
          new_app["posting_norm"] == "acme", new_app)
    check("posting is email_only needing enrichment",
          new_app["captured_via"] == "email_only" and new_app["jd_text"] is None, new_app)
    evs = {r["type"] for r in conn.execute(
        "SELECT type FROM events WHERE application_id = %s", (new_app["id"],)).fetchall()}
    check("applied + confirmation events written", {"applied", "confirmation"} <= evs, evs)
    # Until 24 Sep 2026 this asserted the opposite: the stated date (15 Jul)
    # won over arrival, for the fabricated start and the email's own event.
    created = {r["type"]: r for r in conn.execute(
        "SELECT type, occurred_at, payload FROM events WHERE application_id = %s",
        (new_app["id"],)).fetchall()}
    check("created record's events are dated when the email arrived, not the date it states",
          created["applied"]["occurred_at"] == NOW and created["confirmation"]["occurred_at"] == NOW,
          created)
    check("the email's own event keeps the stated date",
          created["confirmation"]["payload"].get("stated_date") == "2026-07-15", created)
    check("...and the fabricated start does not claim it",
          "stated_date" not in created["applied"]["payload"], created)
    contact = conn.execute(
        "SELECT name FROM contacts WHERE user_id = %s AND name = 'Jo Tan'", (user_id,)).fetchone()
    check("recruiter contact captured", contact is not None)

    print("path 3: pending triage")
    e3 = seed_email(conn, user_id, "mystery-rejection", sender="hr@unknowncorp.example")
    conn.commit()
    drain(conn)
    s3 = email_state(conn, e3)
    check("lands in pending triage", s3["triage_state"] == "pending", s3)
    check("no application guessed", s3["matched_application_id"] is None, s3)

    print("path 3b: non-job mail ignored")
    e4 = seed_email(conn, user_id, "newsletter")
    conn.commit()
    drain(conn)
    s4 = email_state(conn, e4)
    check("marked not_job_related and ignored",
          s4["classification"] == "not_job_related" and s4["triage_state"] == "ignored", s4)

    print("path 3c: recruiter outreach never auto-matches or auto-creates")
    northwind_events_before = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s", (app["id"],)).fetchone()["n"]
    e5 = seed_email(conn, user_id, "recruiter-pitch")
    conn.commit()
    drain(conn)
    s5 = email_state(conn, e5)
    check("recruiter pitch lands pending, not auto-matched",
          s5["triage_state"] == "pending" and s5["matched_application_id"] is None, s5)
    check("no score recorded (never a silent guess)", s5["match_score"] is None, s5)
    northwind_events_after = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s", (app["id"],)).fetchone()["n"]
    check("Northwind application's timeline untouched despite company match",
          northwind_events_after == northwind_events_before, (northwind_events_before, northwind_events_after))

    e6 = seed_email(conn, user_id, "recruiter-unknown")
    conn.commit()
    drain(conn)
    s6 = email_state(conn, e6)
    check("recruiter pitch at unknown company also lands pending, no create",
          s6["triage_state"] == "pending" and s6["matched_application_id"] is None, s6)
    ghost = conn.execute(
        "SELECT 1 FROM jobs WHERE user_id = %s AND company_norm = 'totally new agency'",
        (user_id,)).fetchone()
    check("no job fabricated for the unknown recruiter pitch", ghost is None)

    print("path 3d: a rebranded sender still reaches its own application")
    # Without the exact-title fallback in matcher.find_match, the company gate
    # admits nobody here and _create_application quietly mints a SECOND
    # application for a job already tracked — silently, with match_score NULL.
    # That is what produced three real duplicates (Contoso, Fabrikam, Litware).
    e7 = seed_email(conn, user_id, "rebrand-confirmation")
    conn.commit()
    drain(conn)
    s7 = email_state(conn, e7)
    check("auto-matched despite the company name not matching at all",
          s7["triage_state"] == "auto_matched"
          and str(s7["matched_application_id"]) == str(app["id"]), s7)
    check("scored on title/date/platform, above the bar",
          s7["match_score"] and s7["match_score"] >= 0.75, s7)
    dupe = conn.execute(
        "SELECT 1 FROM jobs WHERE user_id = %s AND company_norm = 'northwind international'",
        (user_id,)).fetchone()
    check("no duplicate job created for the rebranded name", dupe is None)
    ev7 = conn.execute(
        "SELECT type FROM events WHERE source_email_id = %s AND application_id = %s",
        (e7, app["id"])).fetchone()
    check("confirmation landed on the existing timeline", ev7 is not None, ev7)

    print("path 3e: a tagline-padded company name still reaches its own application")
    # Rule 1 (exact title) cannot save this one — the board's title carries a
    # suffix the mail does not — so this fails unless rule 2 (company word
    # containment) is in _CANDIDATES_RESCUE_SQL. The real one minted a third Wingtip
    # Consulting Group record on 2 Aug 2026, again with match_score NULL.
    tag_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, %s, %s) RETURNING id",
        (user_id, "harbourline consulting group global niche technology recruitment",
         "staff platform engineer- hybrid - singapore")).fetchone()
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, platform_job_id, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'LI-harbourline-1', 'extension')", (user_id, tag_job["id"]))
    tag_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, tag_job["id"])).fetchone()
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', %s, %s)",
        (user_id, tag_app["id"], NOW, Json({})))
    conn.commit()
    e8 = seed_email(conn, user_id, "tagline-confirmation")
    conn.commit()
    drain(conn)
    s8 = email_state(conn, e8)
    check("auto-matched though company_sim is under the gate AND the title is not identical",
          s8["triage_state"] == "auto_matched"
          and str(s8["matched_application_id"]) == str(tag_app["id"]), s8)
    check("scored on title/date/platform, above the bar",
          s8["match_score"] and s8["match_score"] >= 0.75, s8)
    dupe8 = conn.execute(
        "SELECT 1 FROM jobs WHERE user_id = %s AND company_norm = 'harbourline consulting group'",
        (user_id,)).fetchone()
    check("no duplicate job created for the bare company name", dupe8 is None)

    print("path 3f: an exact title at a company sharing no word is a stranger, not a rescue")
    # The 23 Sep 2026 mis-file: the real application is under the board's
    # longer employer name with a differently worded title (rule 2 reaches it,
    # rule 1 does not), and an unrelated application elsewhere carries the
    # mail's title byte for byte. Before the shared-word condition, rule 1
    # admitted the stranger, the title term outscored the true record 1.0 to
    # ~0.5, and four emails of a live interview thread filed onto it.
    wg_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'woodgrove southeast asia', 'Full Stack Engineer – Generative AI') "
        "RETURNING id", (user_id,)).fetchone()
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, platform_job_id, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'LI-woodgrove-1', 'extension')", (user_id, wg_job["id"]))
    wg_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, wg_job["id"])).fetchone()
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', %s, %s)",
        (user_id, wg_app["id"], NOW, Json({})))
    conn.commit()
    northwind_events_before = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s", (app["id"],)).fetchone()["n"]
    e9 = seed_email(conn, user_id, "stranger-invite", sender="recruiter@woodgrove.example")
    conn.commit()
    drain(conn)
    s9 = email_state(conn, e9)
    check("the same-titled stranger did NOT auto-match",
          str(s9["matched_application_id"]) != str(app["id"]), s9)
    check("it lands in triage (the true record scores under the bar on its worded-differently title)",
          s9["triage_state"] == "pending" and s9["matched_application_id"] is None, s9)
    check("a score was recorded — the true record WAS seen as a candidate (rule 2), "
          "not a NULL 'nobody found'",
          s9["match_score"] is not None, s9)
    northwind_events_after = conn.execute(
        "SELECT count(*) AS n FROM events WHERE application_id = %s", (app["id"],)).fetchone()["n"]
    check("Northwind's timeline untouched by the stranger's interview invite",
          northwind_events_after == northwind_events_before,
          (northwind_events_before, northwind_events_after))

    print("path 3g: mail the user SENT is what they did, never the employer's outcome")
    # 24 Sep 2026: ingest reads All Mail, which holds the user's own replies,
    # and 26 of them had been classified and filed as if the employer sent
    # them — 11 interview_invite events for the user confirming a slot, one of
    # them dated three weeks in the future off the interview day quoted below
    # it, and a follow-up chasing a silent employer filed as a note.
    pw_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'proseware', 'Backend Engineer') RETURNING id", (user_id,)).fetchone()
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, platform_job_id, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'LI-proseware-1', 'extension')", (user_id, pw_job["id"]))
    pw_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, pw_job["id"])).fetchone()
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', %s, %s)",
        (user_id, pw_app["id"], NOW.replace(day=16), Json({})))
    conn.commit()

    def pw_events():
        return conn.execute(
            "SELECT type, occurred_at FROM events WHERE application_id = %s ORDER BY created_at",
            (pw_app["id"],)).fetchall()

    def pw_status():
        return conn.execute("SELECT status FROM application_status WHERE application_id = %s",
                            (pw_app["id"],)).fetchone()["status"]

    me = "Jane Applicant <jane.applicant@example.com>"
    e10 = seed_email(conn, user_id, "sent-reply", sender=me, sent=True)
    conn.commit()
    drain(conn)
    s10 = email_state(conn, e10)
    check("the worker told the classifier this message was sent",
          ("sent-reply", True) in CLASSIFY_CALLS, CLASSIFY_CALLS)
    check("received mail is still classified as received",
          ("northwind-rejection", False) in CLASSIFY_CALLS, CLASSIFY_CALLS)
    check("the user's reply auto-matches its own thread's application",
          s10["triage_state"] == "auto_matched"
          and str(s10["matched_application_id"]) == str(pw_app["id"]), s10)
    ev10 = pw_events()[-1]
    check("...and files as a note, never as the employer inviting them",
          ev10["type"] == "note", ev10)
    check("...dated when it was SENT, not the interview day quoted in the thread",
          ev10["occurred_at"] == NOW, ev10)
    check("...which it keeps as the stated date", conn.execute(
        "SELECT payload FROM events WHERE source_email_id = %s", (e10,)
    ).fetchone()["payload"].get("stated_date") == "2026-08-14")
    check("...and moves no status", pw_status() == "applied", pw_status())

    check("before following up, the application waits in the reminders queue",
          any(str(r["id"]) == str(pw_app["id"]) for r in analytics.reminders(conn, user_id)))
    e11 = seed_email(conn, user_id, "sent-follow-up", sender=me, sent=True)
    conn.commit()
    drain(conn)
    check("a follow-up the user sent files as follow_up_sent",
          pw_events()[-1]["type"] == "follow_up_sent", pw_events())
    check("...which takes the application off the reminders queue",
          not any(str(r["id"]) == str(pw_app["id"]) for r in analytics.reminders(conn, user_id)))

    e12 = seed_email(conn, user_id, "sent-resume-again", sender=me, sent=True)
    conn.commit()
    drain(conn)
    applied_n = sum(1 for e in pw_events() if e["type"] == "applied")
    check("a resume emailed for a role already on record adds a note, not a second start",
          pw_events()[-1]["type"] == "note" and applied_n == 1, pw_events())

    e13 = seed_email(conn, user_id, "sent-resume-new", sender=me, sent=True)
    conn.commit()
    drain(conn)
    s13 = email_state(conn, e13)
    check("a resume emailed to an employer with no record creates one",
          s13["triage_state"] == "auto_matched" and s13["matched_application_id"], s13)
    ev13 = conn.execute(
        "SELECT type, occurred_at FROM events WHERE application_id = %s",
        (s13["matched_application_id"],)).fetchall()
    check("...with exactly ONE applied event (its own, not a fabricated twin)",
          [e["type"] for e in ev13] == ["applied"], ev13)
    check("...dated when the resume was sent", ev13[0]["occurred_at"] == NOW, ev13)

    e14 = seed_email(conn, user_id, "sent-reply-stranger", sender=me, sent=True)
    conn.commit()
    drain(conn)
    s14 = email_state(conn, e14)
    check("a reply with no record waits in triage; replies never mint records",
          s14["triage_state"] == "pending" and s14["matched_application_id"] is None, s14)

    e15 = seed_email(conn, user_id, "sent-withdrawal", sender=me, sent=True)
    conn.commit()
    drain(conn)
    check("a withdrawal the user sent closes the application as withdrawn",
          pw_status() == "withdrawn", pw_status())

    # Loop the registry, not a hand-picked sample (CLAUDE.md gotcha): every
    # stored sent type has an event, and migration 016's CHECK accepts it.
    import psycopg
    for stored in email_classifier.SENT_TYPES.values():
        check(f"{stored!r} maps to an event", stored in matcher.EVENT_TYPE, matcher.EVENT_TYPE)
        conn.execute("SAVEPOINT sent_type")
        try:
            conn.execute("UPDATE emails SET classification = %s WHERE id = %s", (stored, e14))
            accepted = True
        except psycopg.errors.CheckViolation:
            accepted = False
        conn.execute("ROLLBACK TO SAVEPOINT sent_type")
        check(f"{stored!r} is accepted by emails_classification_check", accepted)
    conn.commit()

    print("path 3h: a date the email states is kept, never the event's time")
    # 24 Sep 2026: ten received invites filed on the interview day they named,
    # 1-21 days after they arrived — two in the future, where they stretched
    # the trace axis past "today" and read as the thread's latest news.
    as_job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) "
        "VALUES (%s, 'alpine ski house', 'Platform Engineer') RETURNING id", (user_id,)).fetchone()
    conn.execute(
        "INSERT INTO postings (user_id, job_id, platform, platform_job_id, captured_via) "
        "VALUES (%s, %s, 'linkedin', 'LI-alpine-1', 'extension')", (user_id, as_job["id"]))
    as_app = conn.execute(
        "INSERT INTO applications (user_id, job_id) VALUES (%s, %s) RETURNING id",
        (user_id, as_job["id"])).fetchone()
    conn.execute(
        "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
        "VALUES (%s, %s, 'applied', 'extension', %s, %s)",
        (user_id, as_app["id"], NOW.replace(day=14), Json({})))
    conn.commit()
    e16 = seed_email(conn, user_id, "alpine-invite", sender="talent@alpineskihouse.example")
    conn.commit()
    drain(conn)
    s16 = email_state(conn, e16)
    check("the invitation auto-matches its application",
          s16["triage_state"] == "auto_matched"
          and str(s16["matched_application_id"]) == str(as_app["id"]), s16)
    ev16 = conn.execute(
        "SELECT occurred_at, payload FROM events WHERE source_email_id = %s", (e16,)).fetchone()
    check("...dated when it arrived, not the interview day it names", ev16["occurred_at"] == NOW, ev16)
    check("...which it keeps as the stated date",
          ev16["payload"].get("stated_date") == "2026-08-04", ev16)
    check("...so nothing on the timeline sits after the latest mail", conn.execute(
        "SELECT max(occurred_at) AS m FROM events WHERE application_id = %s",
        (as_app["id"],)).fetchone()["m"] == NOW)

    print("path 3i: a sibling name no longer hides an employer's record")
    # Word containment was only a RESCUE, run when the company gate admitted
    # nobody; one record under the short name then hid every record under the
    # long one. Measured 24 Sep 2026 over 432 real filed emails: an employer's
    # short-name mail could not reach its "<name> Group" applications, and an
    # agency's mail about a live interview auto-matched a REJECTED lead of the
    # same title.
    def _lamna(company, title, events, origin="applied"):
        j = conn.execute("INSERT INTO jobs (user_id, company_norm, title_canonical) "
                         "VALUES (%s, %s, %s) RETURNING id", (user_id, company, title)).fetchone()
        conn.execute("INSERT INTO postings (user_id, job_id, platform, captured_via) "
                     "VALUES (%s, %s, 'linkedin', 'extension')", (user_id, j["id"]))
        a_ = conn.execute("INSERT INTO applications (user_id, job_id, origin) VALUES (%s, %s, %s) "
                          "RETURNING id", (user_id, j["id"], origin)).fetchone()
        for etype, at in events:
            conn.execute("INSERT INTO events (user_id, application_id, type, source, occurred_at, "
                         "payload) VALUES (%s, %s, %s, 'manual', %s, '{}')",
                         (user_id, a_["id"], etype, at))
        return str(a_["id"])
    from datetime import timedelta as _td
    _lamna("lamna", "Frontend Engineer", [("applied", NOW - _td(days=30))])  # a sibling, another role
    # An old lead of the same title, never applied to and since closed — the
    # shape of the real one (a rejected inbound lead with no applied event).
    lead = _lamna("lamna", "AI Engineer", [("recruiter_outreach", NOW - _td(days=20)),
                                           ("rejected", NOW - _td(days=15))], origin="inbound")
    dp = _lamna("lamna group", "Data Platform Engineer", [("applied", NOW - _td(days=10))])
    live = _lamna("lamna x woodgrove bank", "AI Engineer", [("applied", NOW)])  # the live thread
    conn.commit()
    e17 = seed_email(conn, user_id, "lamna-dp-rejection", sender="noreply@lamna.example")
    e18 = seed_email(conn, user_id, "lamna-ai-invite", sender="recruiter@lamna.example")
    conn.commit()
    drain(conn)
    s17, s18 = email_state(conn, e17), email_state(conn, e18)
    check("short-name mail reaches the record under the long name (was: triage, never a candidate)",
          s17["triage_state"] == "auto_matched" and str(s17["matched_application_id"]) == dp, s17)
    check("...and a same-titled sibling lead no longer captures the live thread's mail (was: auto onto the lead)",
          str(s18["matched_application_id"]) == live and str(s18["matched_application_id"]) != lead, s18)

    print("path 3j: the reason a rejection states")
    ev17 = conn.execute("SELECT payload FROM events WHERE source_email_id = %s AND type = 'rejected'",
                        (e17,)).fetchone()["payload"]
    check("a stated reason files ON the rejected event, marked as the email's, with its sentence",
          ev17.get("reason") == "visa" and ev17.get("reason_source") == "email"
          and ev17.get("reason_quote") == "the team can not sponsor your EP", ev17)
    check("a form letter files no reason (the tagging queue keeps it)",
          "reason" not in ev["payload"] and "reason_source" not in ev["payload"], ev["payload"])
    check("the stage ran on rejections and nothing else",
          set(REASON_CALLS) == {"northwind-rejection", "mystery-rejection", "lamna-dp-rejection"},
          REASON_CALLS)
    ex3 = conn.execute("SELECT extraction FROM emails WHERE id = %s", (e3,)).fetchone()["extraction"]
    check("a rejection waiting in triage keeps the stage's answer in its extraction, so a human "
          "resolving it later files the same thing (matcher.extraction_from_raw)",
          ex3.get("rejection_reason", {}).get("prompt_version") == "rejection_reason_v1"
          and matcher.extraction_from_raw(ex3).rejection_reason == ex3["rejection_reason"], ex3)
    rr17 = matcher.extraction_from_raw(
        conn.execute("SELECT extraction FROM emails WHERE id = %s", (e17,)).fetchone()["extraction"])
    check("...and the rebuilt extraction carries a stated reason too",
          rr17.rejection_reason and rr17.rejection_reason["reason"] == "visa", rr17.rejection_reason)

    print("path 4: failure backoff")
    db.enqueue(conn, user_id, "classify_email",
               {"email_id": "00000000-0000-0000-0000-000000000000"})
    conn.commit()
    worker.process_one(conn)
    jq = conn.execute(
        "SELECT state, attempts, run_after > now() AS deferred, last_error FROM job_queue "
        "ORDER BY id DESC LIMIT 1").fetchone()
    check("failed job requeued with backoff",
          jq["state"] == "pending" and jq["attempts"] == 1 and jq["deferred"], jq)
    check("error recorded", "not found" in (jq["last_error"] or ""), jq["last_error"])

    # The shape of 3-7 Sep 2026: the account ran out of credit and every job
    # burned attempts against a failure that was nobody's job's fault. An
    # outage must leave the job exactly as claimed — same attempts, same
    # run_after, still oldest-first — while a job's OWN failure is charged.
    print("path 5: an outage releases the job; a job's own failure charges it")
    import anthropic
    import httpx2
    real_classify = email_classifier.classify_email
    _req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def api_error(cls, status, message, err_type="invalid_request_error"):
        body = {"type": "error", "error": {"type": err_type, "message": message}}
        return cls(f"Error code: {status} - {body}",
                   response=httpx2.Response(status, request=_req), body=body)

    def raising(exc):
        def stub(*_a, **_k):
            raise exc
        return stub

    def job_row():
        return conn.execute(
            "SELECT id, state, attempts, run_after <= now() AS runnable, last_error "
            "FROM job_queue WHERE payload->>'email_id' = %s", (str(e5),)).fetchone()

    FAKE_CLASSIFY["outage-probe"] = Classification(False, None, 0.98, "stub")
    e5 = seed_email(conn, user_id, "outage-probe")
    conn.commit()
    outages = [
        ("credit exhausted (a 400, told apart by its message)",
         api_error(anthropic.BadRequestError, 400,
                   "Your credit balance is too low to access the Anthropic API. "
                   "Please go to Plans & Billing to upgrade or purchase credits."),
         "credit balance"),
        ("rate limited", api_error(anthropic.RateLimitError, 429,
                                   "This request would exceed your rate limit",
                                   "rate_limit_error"), "429"),
        ("network down", anthropic.APIConnectionError(request=_req), "cannot reach"),
    ]
    for label, exc, marker in outages:
        email_classifier.classify_email = raising(exc)
        try:
            worker.process_one(conn)
            raised = False
        except worker.Outage as out:
            raised = marker in str(out)
        j = job_row()
        check(f"{label}: raises Outage naming the cause", raised)
        check(f"{label}: job released uncharged and still runnable",
              j["state"] == "pending" and j["attempts"] == 0 and j["runnable"], j)
        check(f"{label}: reason recorded for the UI", marker in (j["last_error"] or ""),
              j["last_error"])
    # `work --once` must not exit 0 on an outage, or a cron run fails quietly.
    # run() opens its OWN connection, and the job_row() reads above left this
    # one inside an implicit transaction holding the row lock from the last
    # UPDATE — the worker's FOR UPDATE SKIP LOCKED would skip the job, see an
    # idle queue and exit 0 for the wrong reason. Commit first.
    conn.commit()
    email_classifier.classify_email = raising(outages[0][1])
    try:
        worker.run(once=True)
        code = 0
    except SystemExit as ex:
        code = ex.code
    check("work --once exits non-zero on an outage", code == 1, code)
    check("...and still charged nothing", job_row()["attempts"] == 0, job_row())

    email_classifier.classify_email = raising(api_error(
        anthropic.BadRequestError, 400,
        'messages: roles must alternate between "user" and "assistant"'))
    worker.process_one(conn)
    j = job_row()
    check("a malformed request IS the job's fault: charged and deferred",
          j["state"] == "pending" and j["attempts"] == 1 and not j["runnable"]
          and "roles must alternate" in j["last_error"], j)

    email_classifier.classify_email = real_classify
    conn.execute("UPDATE job_queue SET run_after = now() WHERE id = %s", (j["id"],))
    conn.commit()
    drain(conn)
    j = job_row()
    check("once the API is back the same job completes on its next try",
          j["state"] == "done" and j["attempts"] == 2, j)
    check("...and the email is processed",
          email_state(conn, e5)["triage_state"] == "ignored", email_state(conn, e5))

print("\nALL PATHS PASS")
