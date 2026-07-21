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
from datetime import datetime, timezone

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

_CANDIDATES_SQL = """
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
  AND (j.company_norm = %(company)s
       OR similarity(j.company_norm, %(company)s) >= %(cmin)s)
"""


@dataclass
class MatchResult:
    action: str                     # auto | create | pending
    application_id: str | None = None
    score: float | None = None


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
    cands = conn.execute(_CANDIDATES_SQL, {
        "user_id": user_id,
        "company": company,
        "title": extraction.role_title,
        "platform": extraction.platform,
        "cmin": config.COMPANY_TRGM_MIN,
    }).fetchall()
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
    return MatchResult("pending", None, round(best_score, 3))


# --------------------------------------------------------------------------- writes

def _event_time(extraction: Extraction, received_at: datetime) -> datetime:
    """§8: the email's stated event date wins; received time is the fallback."""
    if extraction.event_date:
        return datetime.strptime(extraction.event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
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


def _create_application(conn, user_id, email_row, extraction: Extraction,
                        classification: str = "confirmation") -> str:
    """The backfill path: a confirmation for a job we have no record of."""
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
                              captured_via, captured_at)
        VALUES (%s, %s, %s, %s, %s, 'email_only', %s)
        RETURNING id
        """,
        (user_id, job["id"], platform, extraction.company, extraction.role_title, occurred_at),
    ).fetchone()
    app = conn.execute(
        """
        INSERT INTO applications (user_id, job_id, applied_via_posting_id)
        VALUES (%s, %s, %s) RETURNING id
        """,
        (user_id, job["id"], posting["id"]),
    ).fetchone()
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
    result = find_match(conn, user_id, extraction, occurred_at)

    if result.action == "pending" and classification == "confirmation" \
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
