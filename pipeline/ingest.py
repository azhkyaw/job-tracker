"""The single write path for capture-originated records: upsert_record()
creates/enriches jobs + postings + applications from any live capture
(extension /captures today, manual entry from Phase 5 on). Mirrors dedup.py's
role as "the ONLY place records combine" — this is the only place they're
*created* from a capture.

Deliberately NOT used by matcher._create_application (the email-backfill
path): that path has different semantics (captured_at = the email's event
time, source_email_id provenance, and no enrich lookup since find_match()
already returned "no candidate") and folding it in here would risk the
tested email pipeline for no user-facing gain.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from . import config, db, salary
from .email_classifier import norm_company

# Prefer attaching to an email_only record for the same company/role (the
# Gmail backfill created it; this capture enriches it) over creating a
# duplicate job. Matches only when EVERY posting on the job is email_only —
# once a real posting (extension/manual) exists, the job is no longer a bare
# backfill placeholder and shouldn't silently absorb an unrelated capture.
#
# The title bar is config.ENRICH_TITLE_MIN and is deliberately strict: one
# company_norm routinely covers several unrelated roles (every recruitment
# agency, and any employer with more than one opening), so company agreement
# alone carries almost no information and the title is doing all the work.
# See that constant for the measurement behind the number — it was 0.5 here,
# inline, and 0.5 silently fused two different Northwind Recruiting roles into
# one application. Note this heuristic is unusual in having no inverse: dedup
# can merge two jobs that should have been one, but nothing in the codebase can
# split one job that should have been two.
ENRICH_JOB_SQL = """
SELECT j.id
FROM jobs j
WHERE j.user_id = %(user_id)s
  AND j.company_norm = %(company)s
  AND similarity(coalesce(j.title_canonical, ''), coalesce(%(title)s::text, ''))
      >= %(title_min)s
  AND NOT EXISTS (SELECT 1 FROM postings p
                  WHERE p.job_id = j.id AND p.captured_via <> 'email_only')
ORDER BY similarity(coalesce(j.title_canonical, ''), coalesce(%(title)s::text, '')) DESC
LIMIT 1
"""


# Where a date-only entry lands when the user didn't give a time. Local noon,
# for two reasons: it's far enough from either date boundary that the entry
# renders on the date the user typed in EVERY timezone, and a flat 12:00 reads
# as "date known, time not recorded" instead of fabricating a precise-looking
# instant. See local_date_to_utc for why this isn't midnight.
DEFAULT_TIME_OF_DAY = time(12, 0)


def local_date_to_utc(d: date, tz: ZoneInfo, t: time | None = None) -> datetime:
    """A bare form date carries no instant on its own.

    An explicit time `t` anchors the date to exactly that wall-clock time in
    the user's zone. Without one, the date anchors to DEFAULT_TIME_OF_DAY
    (local noon) rather than to the moment of submission: borrowing "now"'s
    time-of-day made a backfilled April application claim it was submitted at,
    say, 14:23 — precision the user never supplied and can't correct.

    NOT midnight, in either zone. Midnight UTC renders as the PREVIOUS day for
    anyone west of UTC; local midnight is correct today but sits on the date
    boundary, so it silently shifts if the user later changes their timezone.
    Noon is immune to both.

    The cost is that several same-day entries share one instant instead of
    landing in entry order. That's tolerable because nothing derives meaning
    from their relative order: application_status breaks same-instant ties by
    event-type precedence, and the one ordering that must hold — an outcome
    after its application — is enforced explicitly by the caller.

    `tz` must be a real ZoneInfo, not a fixed-offset timezone: combining it
    with the historical date `d` and re-resolving via .astimezone(utc) is what
    makes this correct across DST boundaries (a January date entered in July
    gets January's offset, not July's).
    """
    return datetime.combine(d, t or DEFAULT_TIME_OF_DAY,
                            tzinfo=tz).astimezone(timezone.utc)


def upsert_record(conn, user_id, *, platform, captured_via, platform_job_id=None,
                  url=None, company=None, title=None, jd_text=None, location=None,
                  posted_label=None, reposted=None, ats=None, captured_at=None,
                  origin="applied", salary_raw=None, work_type=None,
                  salary_match=None) -> dict:
    """Create or enrich job + posting + application for one captured ad.

    Returns {job_id, posting_id, application_id, created, enriched,
    application_existed}. `created` is True only when a brand-new jobs row
    was inserted. `enriched` is True when either an existing posting gained
    jd_text for the first time, or a new posting attached to a reused
    email_only job — two different meanings kept as-is (from the original
    /captures logic) rather than split, to keep this a pure move.

    `origin` only applies when a NEW application row is inserted — an
    existing application's provenance is never overwritten by a later capture
    (e.g. the extension re-capturing a lead you're now actually applying to).

    Caller owns the connection and its transaction, and is responsible for
    everything after this: events, notes, contacts — their
    semantics differ too much between callers (extension vs. manual entry)
    to live here.
    """
    company_norm = norm_company(company or "") or None
    # Parsed here, once, from the string the platform displayed — never in the
    # extension and never in SQL (invariant #4's rule, applied to pay).
    sal = salary.parse(salary_raw, url)
    created, enriched = False, False

    existing = None
    if platform_job_id:
        existing = conn.execute(
            "SELECT id, job_id FROM postings WHERE user_id = %s AND platform = %s "
            "AND platform_job_id = %s",
            (user_id, platform, platform_job_id)).fetchone()

    if existing:
        # Re-capture of a known ad: fill gaps, never blank existing data.
        enriched = bool(jd_text)
        had_jd = conn.execute("SELECT jd_text IS NOT NULL AS h FROM postings "
                              "WHERE id = %s", (existing["id"],)).fetchone()["h"]
        conn.execute(
            """
            UPDATE postings SET
                jd_text      = COALESCE(%s, jd_text),
                url          = COALESCE(%s, url),
                title        = COALESCE(%s, title),
                company_raw  = COALESCE(%s, company_raw),
                company_norm = COALESCE(%s, company_norm),
                location     = COALESCE(%s, location),
                posted_label = COALESCE(%s, posted_label),
                reposted     = COALESCE(%s, reposted),
                ats          = COALESCE(%s, ats),
                salary_raw      = COALESCE(%s, salary_raw),
                salary_min      = COALESCE(%s, salary_min),
                salary_max      = COALESCE(%s, salary_max),
                salary_currency = COALESCE(%s, salary_currency),
                salary_period   = COALESCE(%s, salary_period),
                work_type       = COALESCE(%s, work_type),
                salary_match    = COALESCE(%s, salary_match)
            WHERE id = %s
            """,
            (jd_text, url, title, company, company_norm, location, posted_label,
             reposted, ats, sal["salary_raw"], sal["salary_min"], sal["salary_max"],
             sal["salary_currency"], sal["salary_period"], work_type, salary_match,
             existing["id"]))
        posting_id, job_id = existing["id"], existing["job_id"]
        if jd_text and not had_jd:
            db.enqueue(conn, user_id, "extract_jd", {"posting_id": str(posting_id)})
    else:
        # New ad. Prefer attaching to an email_only record for the same
        # company/role (the backfill created it; this capture enriches it)
        # over creating a duplicate. Full cross-posting dedup is Phase 3.
        job = None
        if company_norm:
            job = conn.execute(ENRICH_JOB_SQL, {
                "user_id": user_id, "company": company_norm,
                "title": title,
                "title_min": config.ENRICH_TITLE_MIN}).fetchone()
        if job is None:
            job = conn.execute(
                "INSERT INTO jobs (user_id, company_norm, title_canonical) "
                "VALUES (%s, %s, %s) RETURNING id",
                (user_id, company_norm or "unknown company",
                 title or "unknown role")).fetchone()
            created = True
        else:
            enriched = True
        job_id = job["id"]
        posting_id = conn.execute(
            """
            INSERT INTO postings (user_id, job_id, platform, platform_job_id,
                                  url, company_raw, company_norm, title,
                                  jd_text, location, posted_label, reposted,
                                  ats, salary_raw, salary_min, salary_max,
                                  salary_currency, salary_period, work_type,
                                  salary_match, captured_via, captured_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
            RETURNING id
            """,
            (user_id, job_id, platform, platform_job_id, url, company,
             company_norm, title, jd_text, location, posted_label, reposted,
             ats, sal["salary_raw"], sal["salary_min"], sal["salary_max"],
             sal["salary_currency"], sal["salary_period"], work_type, salary_match,
             captured_via, captured_at)).fetchone()["id"]
        if jd_text:
            db.enqueue(conn, user_id, "extract_jd", {"posting_id": str(posting_id)})

    app_row = conn.execute(
        "SELECT id FROM applications WHERE user_id = %s AND job_id = %s",
        (user_id, job_id)).fetchone()
    application_existed = app_row is not None
    if app_row is None:
        app_row = conn.execute(
            "INSERT INTO applications (user_id, job_id, applied_via_posting_id, origin) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, job_id, posting_id, origin)).fetchone()

    return {
        "job_id": job_id, "posting_id": posting_id,
        "application_id": app_row["id"], "created": created, "enriched": enriched,
        "application_existed": application_existed,
    }
