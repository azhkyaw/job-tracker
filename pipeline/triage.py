"""What /triage offers beside a box to file mail by hand (7 Oct 2026).

Until that day the page held one kind of thing: an email the matcher would
not file, with a dropdown of every record and nothing else. Measured over the
231 emails filed by hand by then: 190 went onto a record that already
existed, and in 113 of those the record chosen was the matcher's own best
candidate, which the page had reduced to "closest match scored 0.62". Six
applications had meanwhile been filed twice (an employer's form beside the
job board's record), each found by eye and merged by a hand-written script,
and the filings the matcher made ON ITS OWN were never shown anywhere unless
someone opened the record they landed on.

Each rule the page shows is defined once, here, so the page and the nav pill
read the same one (`.claude/rules/web-ui.md` rule 9's lesson):

- suggest(): the records the matcher weighed for a pending email, and why it
  waited for a person;
- runs(): pending mail that came as a run, one sender and one subject, as one
  card;
- twins(): an application filed twice, the form's submit beside the board's
  popover record, minutes apart;
- review(): mail the matcher filed itself with less to go on.

nearest() orders the fallback list of records. Everything that writes a
decision lives in web.py's routes, except the two writes that are the whole
of a decision (merge_twin, keep_apart).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import config, dedup, matcher
from .email_classifier import norm_company
from .ingest import UNKNOWN_COMPANY, UNKNOWN_TITLE

# How the page names the platform a posting was captured on. "other" is
# what the extension files for any hiring system's own page.
PLATFORM_WORDS = {"linkedin": "LinkedIn", "jobstreet": "JobStreet", "indeed": "Indeed",
                  "other": "the employer's form"}
_BOARDS = ["linkedin", "jobstreet", "indeed"]

# At most this many records offered as one-click filings on a card.
MAX_PICKS = 3


# --------------------------------------------------------------------- suggest

def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _weak(cand: dict, occurred: datetime, platform_known: bool) -> str:
    """Which of matcher._score's three signals held the best record back, in
    words. Each threshold is where that signal falls to half its weight: a
    title similarity under 0.5, a date DATE_DECAY_DAYS / 2 away or more, a
    board the record was not captured on."""
    words = []
    if cand["title_sim"] is None:
        words.append("the email names no role")
    elif cand["title_sim"] < 0.5:
        words.append("the role reads differently")
    if cand["applied_at"] is None:
        words.append("the record has no applied date")
    else:
        days = abs((occurred - cand["applied_at"]).days)
        if days >= config.DATE_DECAY_DAYS / 2:
            words.append(f"you applied {days} days before it" if occurred >= cand["applied_at"]
                         else f"it came {days} days before you applied")
    if platform_known and not cand["platform_match"]:
        words.append("the record is from another platform")
    return _and(words) if words else "the role, the date and the platform each fit only in part"


def suggest(conn, user_id, email: dict) -> dict:
    """{"picks": [application id, ...], "why": str | None} for one pending email.

    `picks`, best first: the record the email names by its job's id on a
    hiring system (matcher.match_by_ats_id); else the matcher's best and every
    record within AUTO_MATCH_MARGIN of it, which are the ones that tied it, at
    most MAX_PICKS. They come from matcher.scored_candidates, the list
    find_match decides on, so a suggestion is the matcher's own ranking and
    never a second opinion.

    `why` says why the email waited for a person, or None for an approach:
    those are never filed without one (invariant #9), and their lane says so."""
    x = matcher.extraction_from_raw(email["extraction"])
    approach = email["classification"] == "recruiter_outreach"
    by_id = matcher.match_by_ats_id(conn, user_id, email, x)
    if by_id:
        return {"picks": [by_id], "why": None if approach else
                "A record now holds the job's id on its hiring system, and the email names it."}
    occurred = matcher._event_time(email)
    scored = matcher.scored_candidates(conn, user_id, x, occurred)
    if not scored:
        if approach:
            why = None
        elif not norm_company(x.company or ""):
            why = "It names no company, so no record was searched."
        else:
            why = "No record is filed under that company."
        return {"picks": [], "why": why}
    best, top = scored[0]
    tied = [c for s, c in scored if best - s < config.AUTO_MATCH_MARGIN][:MAX_PICKS]
    if approach:
        # An approach is offered a record only when the matcher would have
        # filed onto it by its own bar. Its lane has no sentence to say "a
        # weak fit", and over the search an approach was filed onto the
        # matcher's best record 3 times in 46; seen on 7 Oct 2026, every row
        # of a referral run offered a record, a data scientist's role among
        # them offered a software engineer's.
        if best < config.AUTO_MATCH_SCORE:
            return {"picks": [], "why": None}
        why = None
    elif best < config.AUTO_MATCH_SCORE:
        why = ("The nearest record is a weak fit: "
               + _weak(top, occurred, matcher.platform_identifiable(x)) + ".")
    elif len(tied) > 1:
        why = f"{len(tied)} records fit about equally."
    else:
        # Not "it did not fit when the email arrived": an email sent back here
        # from a record's page fits that record by the rule just as well.
        why = "This record fits by the matcher's own rule now."
    return {"picks": [str(c["application_id"]) for c in tied], "why": why}


# ------------------------------------------------------------------------ runs

_ADDRESS = re.compile(r"<([^>]+)>")


def _sender_key(sender: str | None) -> str:
    """Who a mail is from, for a run: its address AND the name shown with
    it, case, spacing and quotes folded. An address alone was the key until
    8 Oct 2026, and a relay address speaks for many people: LinkedIn sends
    every person's connection request from invitations@linkedin.com under
    one subject, so five recruiters' requests were one card headed by the
    newest one's name. Measured over all stored mail that day: 4 of 169
    address runs split by name, each correctly (people on LinkedIn's relay,
    two employers on one vendor's), and the 14 identical referral notices
    runs were made for stay one."""
    s = sender or ""
    m = _ADDRESS.search(s)
    address = (m.group(1) if m else s).strip().lower()
    name = " ".join(_ADDRESS.sub(" ", s).replace('"', " ").lower().split()) if m else ""
    return f"{name} <{address}>"


def who(extraction: dict | None) -> str | None:
    """The person and company an email names, "Jane Recruiter, Contoso",
    for a row whose role is unknown: a recruiter's connection request names
    no role, so a run of them read "role unknown" on every row."""
    x = extraction or {}
    rec = x.get("recruiter")
    name = rec.get("name") if isinstance(rec, dict) else rec if isinstance(rec, str) else None
    parts = [p.strip() for p in (name, x.get("company")) if p and p.strip()]
    return ", ".join(parts) or None


def _subject_key(subject: str | None) -> str:
    return " ".join((subject or "").lower().split())


def runs(emails: list[dict]) -> list[dict]:
    """The pending emails as cards, in the order given (newest first).

    Two or more from one sender (address and name, _sender_key) under one
    subject, case and spacing folded, are one card at its newest member's
    place: {"run": True,
    "emails", "first", "last"}. Every other email is {"run": False,
    "email"}. A run keeps each member's own actions; the card adds the ones
    for all of them. The case was a referral tool that sent one notice per
    role, 15 the same minute (28 Aug 2026), which made 15 cards of 163px,
    each with its own list of every record."""
    groups: dict[tuple, list] = {}
    for e in emails:
        groups.setdefault((_sender_key(e["sender"]), _subject_key(e["subject"])), []).append(e)
    cards = []
    for members in groups.values():
        if len(members) >= 2:
            when = [m["received_at"] for m in members]
            cards.append({"run": True, "emails": members, "first": min(when), "last": max(when),
                          "at": max(when)})
        else:
            cards.append({"run": False, "email": members[0], "at": members[0]["received_at"]})
    return sorted(cards, key=lambda c: c["at"], reverse=True)


# --------------------------------------------------------------------- nearest

def nearest(options: list[dict], at: datetime, n: int = config.NEAREST_RECORDS) -> list[dict]:
    """The n records whose start (`started_at`, web._application_options:
    the submission, else the first event) is nearest `at`, nearest first.
    An ORDER for the fallback list, never a filter: 9 of the 52 records
    people chose by hand there were created after their email arrived."""
    dated = [o for o in options if o.get("started_at")]
    return sorted(dated, key=lambda o: abs((o["started_at"] - at).total_seconds()))[:n]


# ----------------------------------------------------------------------- twins

# A form capture that named no employer agrees with a board capture only on
# its title, so the title must carry it: trigram similarity this high.
TWIN_TITLE_MIN = 0.6

_TWINS_SQL = """
WITH caps AS (
  SELECT a.id AS app_id, a.user_id, a.job_id, a.created_at,
         j.company_norm, j.title_canonical,
         p.id AS posting_id, p.platform,
         COALESCE(p.company_raw, j.company_norm) AS company_display,
         (p.jd_text IS NOT NULL) AS has_jd,
         (SELECT count(*) FROM application_answers x WHERE x.application_id = a.id) AS answers,
         (SELECT min(e.occurred_at) FROM events e
           WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at
  FROM applications a
  JOIN jobs j ON j.id = a.job_id
  JOIN postings p ON p.id = a.applied_via_posting_id
  WHERE p.captured_via = 'extension'
    AND (%(user_id)s::uuid IS NULL OR a.user_id = %(user_id)s::uuid)
)
SELECT f.app_id AS form_app, f.job_id AS form_job, f.posting_id AS form_posting,
       f.company_display AS form_company, f.title_canonical AS form_title,
       f.created_at AS form_created, f.applied_at AS form_applied,
       f.answers AS form_answers, f.has_jd AS form_jd,
       b.app_id AS board_app, b.job_id AS board_job, b.posting_id AS board_posting,
       b.company_display AS board_company, b.title_canonical AS board_title,
       b.platform AS board_platform,
       b.created_at AS board_created, b.applied_at AS board_applied,
       b.answers AS board_answers, b.has_jd AS board_jd,
       abs(extract(epoch FROM b.created_at - f.created_at))::int AS gap_seconds
FROM caps f
JOIN caps b ON b.user_id = f.user_id
           AND f.platform = 'other' AND b.platform = ANY(%(boards)s)
           AND abs(extract(epoch FROM b.created_at - f.created_at)) <= %(window)s
WHERE ((f.company_norm <> %(unknown)s
        AND (f.company_norm = b.company_norm
             OR similarity(f.company_norm, b.company_norm) >= %(cmin)s
             OR string_to_array(f.company_norm, ' ') @> string_to_array(b.company_norm, ' ')
             OR string_to_array(f.company_norm, ' ') <@ string_to_array(b.company_norm, ' ')))
       OR (f.company_norm = %(unknown)s
           AND (f.title_canonical = %(untitled)s
                OR similarity(f.title_canonical, b.title_canonical) >= %(tmin)s)))
  AND NOT EXISTS (SELECT 1 FROM duplicate_candidates d
                  WHERE d.posting_a = LEAST(f.posting_id, b.posting_id)
                    AND d.posting_b = GREATEST(f.posting_id, b.posting_id)
                    AND d.state = 'rejected')
ORDER BY f.created_at DESC
"""


def twins(conn, user_id=None) -> list[dict]:
    """Applications that look filed twice: the extension's capture of an
    employer's form (platform `other`) and its capture of a job board's
    record (LinkedIn, JobStreet, Indeed), made within TWIN_WINDOW_MINUTES of
    each other, whose employers agree by the matcher's own company gate — or,
    when the form named no employer, whose titles agree, or the form named
    no role either. A pair a person kept apart (keep_apart) never returns.

    All five twins of 28 Sep - 7 Oct 2026 inside the window pass it: names
    equal (SmartRecruiters), contained ("coho" in "coho career site",
    Oracle), alike by trigram (two forms of one employer's name, 0.68,
    Workday), and the two forms that named nobody (Phenom, with no title
    either; SuccessFactors, with the board's exact title).

    `user_id` None leaves the scoping to RLS, as the nav pill's count does
    (web._pending_count); a test on an admin connection passes it."""
    return conn.execute(_TWINS_SQL, {
        "user_id": user_id, "boards": _BOARDS,
        "window": config.TWIN_WINDOW_MINUTES * 60,
        "unknown": UNKNOWN_COMPANY, "untitled": UNKNOWN_TITLE,
        "cmin": config.COMPANY_TRGM_MIN, "tmin": TWIN_TITLE_MIN}).fetchall()


def _pair(conn, user_id, form_app: str, board_app: str) -> dict | None:
    return next((t for t in twins(conn, user_id)
                 if str(t["form_app"]) == form_app and str(t["board_app"]) == board_app), None)


def merge_twin(conn, user_id, form_app: str, board_app: str) -> bool:
    """One application again, onto the BOARD's record: it has the employer's
    name, the job description and the board's id, and its job wins the merge
    (dedup.merge_jobs moves the form's answers, its posting, its events and
    the job's ATS id across). The board record's own `applied` is dropped
    when the form has one, since the form's is the moment the application
    was sent and the board's is when the popover was answered. That is the
    repair each of the six hand merges made (snapshots in
    job-tracker-snapshots/, 28 Sep - 7 Oct 2026). dedup's own band merges
    onto the OLDER job, which is the form here, so it could not be reused.

    Re-checks the pair against twins() first and returns False when it no
    longer is one (merged, kept apart, or deleted since the page was drawn)."""
    t = _pair(conn, user_id, form_app, board_app)
    if t is None:
        return False
    if t["form_applied"] is not None:
        conn.execute("DELETE FROM events WHERE application_id = %s AND type = 'applied' "
                     "AND source = 'extension'", (t["board_app"],))
    dedup.merge_jobs(conn, user_id, t["board_job"], t["form_job"])
    return True


def keep_apart(conn, user_id, form_app: str, board_app: str) -> bool:
    """A person said the pair is two applications. Recorded where dedup
    records the same answer about two postings, a `rejected` pair, which
    twins() then skips and dedup's own detector honours too."""
    t = _pair(conn, user_id, form_app, board_app)
    if t is None:
        return False
    conn.execute(
        """
        INSERT INTO duplicate_candidates (user_id, posting_a, posting_b, state)
        VALUES (%s, LEAST(%s::uuid, %s::uuid), GREATEST(%s::uuid, %s::uuid), 'rejected')
        ON CONFLICT (posting_a, posting_b) DO UPDATE SET state = 'rejected'
        """, (user_id, t["form_posting"], t["board_posting"],
              t["form_posting"], t["board_posting"]))
    return True


# ---------------------------------------------------------------------- review

_REVIEW_SQL = """
SELECT e.id, e.subject, e.sender, e.received_at, e.processed_at, e.match_score,
       e.extraction->>'company' AS email_company,
       a.id AS app_id, j.company_norm, j.title_canonical,
       COALESCE((SELECT p.company_raw FROM postings p
                  WHERE p.job_id = j.id AND p.company_raw IS NOT NULL
                  ORDER BY p.captured_at DESC LIMIT 1), j.company_norm) AS company_display
FROM emails e
JOIN applications a ON a.id = e.matched_application_id
JOIN jobs j ON j.id = a.job_id
WHERE e.user_id = %(user_id)s
  AND e.triage_state = 'auto_matched' AND e.reviewed_at IS NULL
  AND e.processed_at >= %(since)s
ORDER BY e.processed_at DESC
"""


_REPLY_PREFIX = re.compile(r"^\s*((re|fwd?|fw)\s*:\s*)+", re.IGNORECASE)


def _thread_key(subject: str | None) -> str:
    """A subject with its reply and forward prefixes gone, folded like runs()."""
    return _subject_key(_REPLY_PREFIX.sub("", subject or ""))


def _names_differ(norm: str, record: str, sim: float) -> bool:
    """The email's company reaches the record by none of the company gate's
    terms (matcher._COMPANY_GATE): not equal, trigram under COMPANY_TRGM_MIN,
    and neither name's words inside the other's. So the record came in
    through the rescue, on its title. A short name inside a long one
    ("Contoso" in "Contoso Markets") is a term of the gate, kept there on
    purpose since 24 Sep 2026 (`.claude/rules/matching.md`); counting it as
    differing made 19 of the 28 rows this test flagged over all 388
    auto-filings on 7 Oct 2026."""
    if not norm or record == UNKNOWN_COMPANY or norm == record:
        return False
    a, b = set(norm.split()), set(record.split())
    return sim < config.COMPANY_TRGM_MIN and not (a <= b or b <= a)


def review(conn, user_id, now: datetime) -> list[dict]:
    """Mail the matcher filed on its own in the last REVIEW_DAYS, where it had
    less to go on, one row per thread on a record, newest first. Each row has
    `ids` (every email in it), `why` (the words for what put it here) and the
    newest email's fields.

    - it STARTED the record: a confirmation with no candidate is created,
      not triaged (matcher.dispatch), and every such record a confirmation
      should have joined was silent until found (4 Aug, 7 Sep, 30 Sep,
      2 Oct 2026);
    - it scored under REVIEW_SCORE_BELOW: the 24 Sep agency lead was filed
      at exactly the 0.75 bar;
    - its company reaches the record by no term of the gate (_names_differ):
      the 23 Sep stranger scored 1.0 on its title and only its names
      differed. A record with no name yet is exempt, since an id joined it
      and there is nothing to differ.

    One thread is one row: a recruiter's back-and-forth filed on one record
    put six rows on the strip on 7 Oct 2026, and if the first was filed
    right the replies were too. The names are compared through norm_company
    (invariant #4) and pg_trgm, in one query for the lot."""
    rows = conn.execute(_REVIEW_SQL, {
        "user_id": user_id, "since": now - timedelta(days=config.REVIEW_DAYS)}).fetchall()
    norms = [norm_company(r["email_company"] or "") for r in rows]
    sims = [s["s"] for s in conn.execute(
        "SELECT similarity(n.a, n.b) AS s FROM unnest(%s::text[], %s::text[]) "
        "WITH ORDINALITY AS n(a, b, i) ORDER BY n.i",
        ([r["company_norm"] for r in rows], norms)).fetchall()] if rows else []
    threads: dict[tuple, dict] = {}
    for r, norm, sim in zip(rows, norms, sims):
        why = []
        if r["match_score"] is None:
            why.append("it started this record")
        elif r["match_score"] < config.REVIEW_SCORE_BELOW:
            why.append("a weak fit")
        if (_names_differ(norm, r["company_norm"], sim)
                and r["company_norm"] not in matcher.company_aliases(r["email_company"])):
            why.append(f"the email reads “{r['email_company']}”")
        if not why:
            continue
        key = (str(r["app_id"]), _thread_key(r["subject"]))
        t = threads.get(key)
        if t is None:
            threads[key] = {**r, "ids": [str(r["id"])], "reasons": why}
        else:
            t["ids"].append(str(r["id"]))
            t["reasons"] += [w for w in why if w not in t["reasons"]]
    out = []
    for t in threads.values():
        # Upper-cased here, not by Jinja's `capitalize`, which lowers every
        # later letter and would print a company's name wrong.
        text = "; ".join(t.pop("reasons"))
        out.append({**t, "why": text[0].upper() + text[1:]})
    return out
