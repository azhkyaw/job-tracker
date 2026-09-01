"""Email -> application matching (design doc §8) and record creation.

dispatch() is the single entry point the worker calls after stage-2
extraction. Outcomes:

  auto    — confident match: event appended, email auto_matched
  create  — confirmation with no candidate: new job + application + email_only
            posting created (the backfill path; posting has no JD text, which
            is the derived "needs_enrichment" state)
  pending — everything uncertain: email lands in the triage queue, no writes
            to applications/events (never a silent guess)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import config
from .email_classifier import Extraction, norm_company

# Classification type -> event type. status_update refines via status_detail.
EVENT_TYPE = {
    "confirmation": "confirmation",
    "rejection": "rejected",
    "interview_invite": "interview_invite",
    "recruiter_outreach": "recruiter_outreach",
    "other": "note",
}

_CANDIDATES_BASE = """
SELECT a.id  AS application_id,
       j.id  AS job_id,
       j.company_norm,
       similarity(j.company_norm, %(company)s)                    AS company_sim,
       CASE WHEN %(title)s::text IS NULL THEN NULL
            ELSE similarity(coalesce(j.title_canonical, ''), %(title)s)
       END                                                        AS title_sim,
       (SELECT min(e.occurred_at) FROM events e
         WHERE e.application_id = a.id AND e.type = 'applied')    AS applied_at,
       EXISTS (SELECT 1 FROM postings p
                WHERE p.job_id = j.id AND p.platform = %(platform)s) AS platform_match
FROM applications a
JOIN jobs j ON j.id = a.job_id
WHERE a.user_id = %(user_id)s
  AND """

_CANDIDATES_SQL = _CANDIDATES_BASE + """(j.company_norm = %(company)s
       OR similarity(j.company_norm, %(company)s) >= %(cmin)s)
"""

# Last resort when the company gate above admits NOBODY. Two independent rules,
# OR'd into ONE query rather than tried in sequence, so the scorer sees every
# rescued candidate at once and AUTO_MATCH_MARGIN arbitrates between them
# instead of an arbitrary precedence between the rules:
#
#   1. the same title, exactly, ignoring the company entirely
#   2. one company name's words are a strict subset of the other's
#
# The gate is the only thing standing between an email and the application it
# belongs to, and an employer that brands itself differently in mail than on
# the job board walks straight past it. Three real duplicate applications came
# from this, all confirmed 4 Aug 2026 (company_sim vs COMPANY_TRGM_MIN = 0.6;
# the employer names are placeholders, the numbers are the real measurements):
#
#   contoso        <- "Contoso Markets"     0.467   employer's own ATS mail
#   fabrikam group <- "Fabrikam"            0.538   SuccessFactors mail
#   litware singapore
#                  <- "Litware International (Singapore) Pte Ltd"
#                                           0.314   LinkedIn's OWN mail, four
#                                                   seconds after the extension
#                                                   captured the same job
#
# In every one the title was byte-identical and the timestamps within a minute,
# so each would have scored ~0.8 and auto-matched correctly — find_match never
# got to see the candidate. match_score came out NULL, which is how these are
# told apart from a real low-confidence miss.
#
# Rule 2 exists because a FOURTH case (Wingtip Talent Group, 4 Aug 2026) walked
# past rule 1 as well: LinkedIn's own confirmation said "Wingtip Talent Group"
# while the extension had captured "Wingtip Talent Group | Specialist
# Technology Recruitment" (company_sim 0.375), and the titles differed by a
# platform-added suffix — "Senior Full Stack .NET Engineer" against "Senior Full
# Stack .NET Engineer- Hybrid - Singapore" — so the byte-identical test missed
# it too. A tagline appended to an employer's name is a strict SUPERSET of the
# name in word terms, which trigram similarity does not reward: the extra words
# dilute it toward zero however exact the shared prefix is. Word containment is
# the far more precise signal, and unlike a lower COMPANY_TRGM_MIN it does not
# loosen anything for names that merely LOOK alike.
#
# Measured before it was written, against all 81 distinct company_norm values in
# the author's real DB: rule 2 admits ONE new pair — the Wingtip one — so on
# real data it has no false positives at all. Against the three cases above it
# would independently have caught Contoso ('contoso' ⊂ 'contoso markets') and
# Fabrikam ('fabrikam' ⊂ 'fabrikam group'); Litware stays rule 1's, since
# norm_company drops its parenthetical and leaves 'litware singapore' against
# 'litware international', neither a subset of the other. The two rules cover
# all four real failures together and neither covers them alone.
#
# Widening COMPANY_TRGM_MIN itself was the other option and is worse: it loosens
# the net for EVERY email, and one title routinely spans several employers here
# ("Senior AI Engineer" covers five), so a same-day confirmation could auto-match
# the wrong company outright. This can only ADD candidates where there were
# none, and everything downstream still applies — AUTO_MATCH_SCORE and
# AUTO_MATCH_MARGIN both have to be satisfied, and several same-titled
# applications will fail the margin and land in triage. That is the failure this
# trades for: a visible triage item instead of a silent duplicate, which is the
# same preference invariant #3 states for merges (a wrong split is recoverable,
# a wrong merge is not). Nothing here can create a match the scorer wouldn't
# have made on its own.
_CANDIDATES_RESCUE_SQL = _CANDIDATES_BASE + """(
          -- rule 1: the same title, exactly, whatever the company is called
          (%(title)s::text IS NOT NULL
           AND lower(btrim(coalesce(j.title_canonical, '')))
             = lower(btrim(%(title)s)))
          -- rule 2: one company name's words contain the other's, either way
          -- round (the email may carry the longer or the shorter form)
       OR string_to_array(j.company_norm, ' ') @> string_to_array(%(company)s, ' ')
       OR string_to_array(j.company_norm, ' ') <@ string_to_array(%(company)s, ' ')
      )
"""


@dataclass
class MatchResult:
    action: str                     # auto | create | pending
    application_id: str | None = None
    score: float | None = None
    had_candidates: bool = False    # a same-company application existed but
                                     # scored below the auto-match bar — dispatch()
                                     # must not treat this as "no record exists"


def extraction_from_raw(raw: dict | None) -> Extraction:
    """Rebuild an Extraction from the emails.extraction jsonb — used by the
    triage UI when a human resolves a pending email."""
    raw = raw or {}
    return Extraction(
        company=raw.get("company"), role_title=raw.get("role_title"),
        platform=raw.get("platform", "unknown"), ats=raw.get("ats"),
        event_date=raw.get("event_date"), status_detail=raw.get("status_detail"),
        recruiter=raw.get("recruiter"), notes=raw.get("notes"), raw=raw,
    )


def _score(cand: dict, occurred_at: datetime, platform_identifiable: bool) -> float:
    """§8 step 3. Missing signals score neutral (0.5) rather than punishing."""
    title = cand["title_sim"] if cand["title_sim"] is not None else 0.5
    if cand["applied_at"] is not None:
        days = abs((occurred_at - cand["applied_at"]).days)
        date = max(0.0, 1.0 - days / config.DATE_DECAY_DAYS)
    else:
        date = 0.5
    if platform_identifiable:
        platform = 1.0 if cand["platform_match"] else 0.0
    else:
        platform = 0.5
    return (config.W_TITLE * title + config.W_DATE * date + config.W_PLATFORM * platform)


def find_match(conn, user_id, extraction: Extraction, occurred_at: datetime) -> MatchResult:
    company = norm_company(extraction.company or "")
    if not company:
        return MatchResult("pending")
    platform_identifiable = extraction.platform in ("linkedin", "jobstreet", "indeed")
    params = {
        "user_id": user_id,
        "company": company,
        "title": extraction.role_title,
        "platform": extraction.platform,
        "cmin": config.COMPANY_TRGM_MIN,
    }
    cands = conn.execute(_CANDIDATES_SQL, params).fetchall()
    if not cands:
        # The company gate admitted nobody — fall back to an exact title or a
        # containing company name, so a rebranded sender can still reach its own
        # application rather than silently minting a second one. This can only
        # ADD candidates where there were none; AUTO_MATCH_SCORE and
        # AUTO_MATCH_MARGIN still decide the outcome. See _CANDIDATES_RESCUE_SQL.
        cands = conn.execute(_CANDIDATES_RESCUE_SQL, params).fetchall()
    if not cands:
        return MatchResult("pending")
    scored = sorted(
        ((_score(c, occurred_at, platform_identifiable), c) for c in cands),
        key=lambda pair: pair[0], reverse=True,
    )
    best_score, best = scored[0]
    margin_ok = len(scored) == 1 or (best_score - scored[1][0]) >= config.AUTO_MATCH_MARGIN
    if best_score >= config.AUTO_MATCH_SCORE and margin_ok:
        return MatchResult("auto", str(best["application_id"]), round(best_score, 3))
    return MatchResult("pending", None, round(best_score, 3), had_candidates=True)


# --------------------------------------------------------------------------- writes

def _event_time(extraction: Extraction, received_at: datetime) -> datetime:
    """§8: the email's stated event date wins over the received date — but
    stated dates never carry a time, so borrow received_at's time-of-day
    rather than defaulting to midnight, which otherwise collapses same-day
    events to identical timestamps and loses their ordering."""
    if extraction.event_date:
        stated = datetime.strptime(extraction.event_date, "%Y-%m-%d").date()
        return datetime.combine(stated, received_at.timetz())
    return received_at


def _event_type(classification: str, extraction: Extraction) -> tuple[str, dict]:
    if classification == "status_update":
        if extraction.status_detail == "viewed":
            return "viewed", {}
        return "note", {"status_detail": extraction.status_detail}
    return EVENT_TYPE[classification], {}


def _append_event(conn, user_id, application_id, email_row, classification,
                  extraction: Extraction) -> None:
    etype, payload = _event_type(classification, extraction)
    from psycopg.types.json import Json
    conn.execute(
        """
        INSERT INTO events (user_id, application_id, type, source, occurred_at,
                            source_email_id, payload)
        VALUES (%s, %s, %s, 'email', %s, %s, %s)
        """,
        (user_id, application_id, etype,
         _event_time(extraction, email_row["received_at"]), email_row["id"], Json(payload)),
    )
    if extraction.recruiter and extraction.recruiter.get("name"):
        conn.execute(
            """
            INSERT INTO contacts (user_id, job_id, name, url, source, notes)
            SELECT %s, a.job_id, %s, %s, 'email', %s
            FROM applications a WHERE a.id = %s
              AND NOT EXISTS (SELECT 1 FROM contacts c
                              WHERE c.job_id = a.job_id AND c.name = %s)
            """,
            (user_id, extraction.recruiter["name"], extraction.recruiter.get("email"),
             extraction.notes, application_id, extraction.recruiter["name"]),
        )
    if extraction.ats:
        # applied_via_posting_id is the specific ad this application's ATS
        # confirmation is about — COALESCE so a later, less-certain email
        # never overwrites an ATS already learned from an earlier one.
        conn.execute(
            """
            UPDATE postings SET ats = COALESCE(ats, %s)
            WHERE id = (SELECT applied_via_posting_id FROM applications WHERE id = %s)
            """,
            (extraction.ats, application_id),
        )


def _create_application(conn, user_id, email_row, extraction: Extraction,
                        classification: str = "confirmation",
                        origin: str = "applied") -> str:
    """Create a job + posting + application for one email with no existing
    record. The 'applied' event is fabricated for every classification EXCEPT
    recruiter_outreach: a rejection/interview_invite/status_update with no
    prior record still implies the user applied (you can't be rejected from a
    role you never applied to) — only recruiter_outreach is "a role the user
    did NOT apply to" by its own classify-prompt definition, so origin='inbound'
    leads get no applied event and derive status 'interested' instead of lying
    about having applied."""
    company_norm = norm_company(extraction.company or "")
    title = extraction.role_title or "unknown role"
    platform = extraction.platform if extraction.platform in ("linkedin", "jobstreet", "indeed") else "other"
    occurred_at = _event_time(extraction, email_row["received_at"])

    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) VALUES (%s, %s, %s) RETURNING id",
        (user_id, company_norm, title),
    ).fetchone()
    posting = conn.execute(
        """
        INSERT INTO postings (user_id, job_id, platform, company_raw, title,
                              ats, captured_via, captured_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'email_only', %s)
        RETURNING id
        """,
        (user_id, job["id"], platform, extraction.company, extraction.role_title,
         extraction.ats, occurred_at),
    ).fetchone()
    app = conn.execute(
        """
        INSERT INTO applications (user_id, job_id, applied_via_posting_id, origin)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (user_id, job["id"], posting["id"], origin),
    ).fetchone()
    if classification != "recruiter_outreach":
        from psycopg.types.json import Json
        conn.execute(
            """
            INSERT INTO events (user_id, application_id, type, source, occurred_at,
                                source_email_id, payload)
            VALUES (%s, %s, 'applied', 'email', %s, %s, %s)
            """,
            (user_id, app["id"], occurred_at, email_row["id"], Json({})),
        )
    _append_event(conn, user_id, app["id"], email_row, classification, extraction)
    return str(app["id"])


def dispatch(conn, user_id, email_row, classification: str, extraction: Extraction) -> MatchResult:
    """Route one extracted email; updates the emails row with the outcome."""
    occurred_at = _event_time(extraction, email_row["received_at"])

    if classification == "recruiter_outreach":
        # By definition "a role the user did NOT apply to" (see the classify
        # prompt) — must never auto-match or auto-append onto an existing
        # application's timeline, and never auto-create either (no silent
        # guessing about which cold pitches are worth tracking). Always
        # triage; the human decides via the inbound lane's "track as lead".
        result = MatchResult("pending")
    else:
        result = find_match(conn, user_id, extraction, occurred_at)

        if result.action == "pending" and not result.had_candidates \
                and classification == "confirmation" \
                and norm_company(extraction.company or ""):
            result = MatchResult("create",
                                 _create_application(conn, user_id, email_row, extraction))
        elif result.action == "auto":
            _append_event(conn, user_id, result.application_id, email_row,
                          classification, extraction)

    triage = "auto_matched" if result.action in ("auto", "create") else "pending"
    conn.execute(
        """
        UPDATE emails SET matched_application_id = %s, match_score = %s,
                          triage_state = %s, processed_at = now()
        WHERE id = %s
        """,
        (result.application_id, result.score, triage, email_row["id"]),
    )
    return result
