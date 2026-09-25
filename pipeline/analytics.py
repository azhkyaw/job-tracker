"""The shared counts behind the list pages and /analytics (design doc §6.6).
Pure SQL over the event log — this is the payoff of status-as-events: response
rates and time-to-response fall out of timestamps.

Since 25 Sep 2026 `/analytics` itself is drawn from `facts()` — one fetch of
every application and every event — by the pure functions in `insights.py`,
because its questions (a reply curve that knows yesterday's application is
"not yet", not "never"; a comparison that says how sure it is) are not
GROUP BY-shaped. The counts the LIST also shows (summary, the rejection
reasons and endings, the reminder queue) stay here in SQL: two pages must not
count one thing two ways, and the page's own numbers are tested against them.

What that replaced, and what it inherited. The page's per-dimension tables
were SQL here — `by_platform`, `by_resume`, `by_technology` — rating a
response over EVERY application, so the last weeks' sends (with no time yet
to be answered) dragged every row down, and counting LinkedIn's "viewed"
notice as a response scored LinkedIn up for a signal no other channel sends.
They are insights.DIMENSIONS now, rated over settled applications on an
answer. A `by_focus` sat beside them until 21 Aug 2026, reading
`applications.focused`; the column never split (175 `false`, 22 null, zero
`true`), so its panel never rendered once. The resume comparison did NOT
replace it and does not answer its question: `focused` asked about EFFORT (was
this application customised for this role), `resume_file` records POSITIONING
(which of two standing resumes went out, neither written per employer). "Does
tailoring pay off" is currently unmeasured, not answered.
"""

from __future__ import annotations

import re

from . import answers, config
from .ingest import UNKNOWN_COMPANY, UNKNOWN_TITLE


def _sql_list(types) -> str:
    return "(" + ",".join(f"'{t}'" for t in types) + ")"


# An employer's response of any kind — what the list calls a reply and
# `/analytics` calls hearing back. The tuple is the one list; the SQL string is
# formatted from it, so insights.py (Python) and every query here (SQL) read
# the same set.
RESPONSE_TYPES = ("viewed", "engaged", "interview_invite", "rejected", "offer")
_RESPONSE_TYPES = _sql_list(RESPONSE_TYPES)

_APPS_CTE = f"""
WITH apps AS (
    SELECT a.id,
           a.job_id,
           a.origin,
           COALESCE(p.platform, 'unknown') AS platform,
           a.resume_file,
           (SELECT min(occurred_at) FROM events e
             WHERE e.application_id = a.id AND e.type = 'applied')      AS applied_at,
           (SELECT min(occurred_at) FROM events e
             WHERE e.application_id = a.id
               AND e.type IN {_RESPONSE_TYPES})                          AS first_resp,
           EXISTS (SELECT 1 FROM events e
                   WHERE e.application_id = a.id
                     AND e.type IN ('interview_invite','offer'))         AS interviewed,
           EXISTS (SELECT 1 FROM events e
                   WHERE e.application_id = a.id AND e.type = 'offer')   AS offered
    FROM applications a
    LEFT JOIN postings p ON p.id = a.applied_via_posting_id
    WHERE a.user_id = %(user_id)s
)
"""

# Below this many applications a per-dimension response rate is noise wearing a
# percent sign — 0% on n=3 says nothing about the technology, only about the
# sample. The raw counts stay visible; only the derived rate is withheld.
MIN_RATE_N = 5


def summary(conn, user_id, inbound: bool | None = None) -> dict:
    """The search in numbers. `inbound` scopes it to one list page the way
    `web._funnel` is scoped (None = the whole search, for `/analytics`): the
    record's lede must agree with the count beside it, and search-wide it did
    not — 2 real inbound leads the user later applied to carry an `applied`
    event, so they counted as applications on a page they are not on (258
    against 256 rows, 24 Sep 2026)."""
    row = conn.execute(_APPS_CTE + """
        SELECT count(*) FILTER (WHERE applied_at IS NOT NULL) AS applied,
               count(*) FILTER (WHERE applied_at IS NULL)     AS interested,
               count(*) FILTER (WHERE first_resp IS NOT NULL) AS responded,
               count(*) FILTER (WHERE interviewed)            AS interviews,
               count(*) FILTER (WHERE offered)                AS offers,
               round(avg(EXTRACT(epoch FROM first_resp - applied_at) / 86400.0)::numeric, 1)
                                                              AS avg_days_to_resp
        FROM apps
        WHERE %(inbound)s::bool IS NULL OR (origin = 'inbound') = %(inbound)s
    """, {"user_id": user_id, "inbound": inbound}).fetchone()
    row["response_rate"] = (round(100.0 * row["responded"] / row["applied"])
                            if row["applied"] else None)
    return row


def rejection_reasons(conn, user_id, inbound: bool | None = None):
    """Why applications closed, counted per APPLICATION from the rejected
    event's `payload.reason` — the closed vocabulary web.py's timeline form
    writes (`_EVENT_REASONS`). Rows: `reason` (a vocabulary key, or NULL when
    nobody has recorded one), `n` applications, `inbound` of which were leads
    the user never applied to.

    The NULL row is the point, not noise. On the author's data 40 of 51
    rejections arrived by email, which until 9 Sep 2026 could not carry a
    reason at all, so "not recorded" is the backlog and the page shows it as
    its own row rather than folding it into `unstated` — the employer gave no
    reason and someone wrote that down, a real answer, distinct from an
    unrecorded one.

    One rejected event per application: the newest one that HAS a reason,
    else the newest at all. An application is rejected once in the world; a
    second rejected event is a recording artefact (the email and a hand-filed
    copy, or a re-sent letter), and a reason on either is the application's
    reason. The list's `reason` filter reads the same event (web.py, the `rr`
    LATERAL), so a chip's count is the number of rows clicking it shows —
    change one and change the other.

    `inbound` is split out because it is a different fact about the market:
    5 of the author's 6 visa rejections were recruiters who approached first
    and then dropped the thread, not applications the user sent.

    `inbound` (the argument) scopes it the way `web._funnel` is scoped — to
    one of the two list pages, `/` (everything the user started) or `/inbound`
    (everything a recruiter started) — so the chips count the page they sit
    on. None is both, which is what `/analytics` wants."""
    return conn.execute("""
        WITH closed AS (
            SELECT DISTINCT ON (e.application_id)
                   e.application_id, e.payload->>'reason' AS reason
            FROM events e
            JOIN applications a ON a.id = e.application_id
            WHERE a.user_id = %(user_id)s AND e.type = 'rejected'
              AND (%(inbound)s::bool IS NULL OR (a.origin = 'inbound') = %(inbound)s)
            ORDER BY e.application_id, (e.payload->>'reason') IS NOT NULL DESC,
                     e.occurred_at DESC, e.created_at DESC
        )
        SELECT c.reason, count(*) AS n,
               count(*) FILTER (WHERE a.origin = 'inbound') AS inbound
        FROM closed c JOIN applications a ON a.id = c.application_id
        GROUP BY c.reason
        ORDER BY n DESC, c.reason
    """, {"user_id": user_id, "inbound": inbound}).fetchall()


# How a rejection ENDED, as one partition of every rejected application (23 Sep
# 2026). The stage an application reached is already on its timeline, so
# unlike the reason chips this breakdown is complete on day one, with nothing
# to tag. Five buckets, in precedence order:
#
#   sponsorship_screen  LinkedIn rejected it automatically (screen_sql, below)
#                       and the form had recorded that you need sponsorship
#   form_screen         the same automatic rejection, on a form that did not
#                       ask, or did not record, that you need sponsorship
#   visa                the closing event carries the visa reason — a person
#                       said so; a market fact worth its own slice whatever
#                       the stage (5 of the author's first 6 were recruiters
#                       who approached and then dropped the thread)
#   after_round         a human round happened: an interview invite, a call
#                       or a message (`engaged`), or an offer
#   no_round            the rest — a form letter, or a hand-filed close with no
#                       round (43 of the author's 55 on 23 Sep, 29 of them
#                       LinkedIn's letter and 11 an ATS's)
#
# The screens come first (25 Sep 2026) because they are the MECHANISM, read
# off the record, and the recorded reason is the user's reading of it: a
# screened application later tagged `visa` stays a screen, so "a form filter
# closed it" and "a person told you it was visa" are never one number. That
# is the point of the split — the author asked for them apart.
#
# Precedence matters because the facts overlap (a visa close can follow a
# round); one bucket per application is what lets the chips sum to the funnel's
# rejected count. ONE definition, formatted into both the list's WHERE (web.py)
# and the count below — two copies would let a chip promise a different number
# of rows than it shows, the same trap rejection_reasons documents.
ROUND_EVENTS = ("interview_invite", "engaged", "offer")
ROUND_TYPES = _sql_list(ROUND_EVENTS)

# What each bucket is called on a page — the list's `how` chips, the
# /analytics table and the flow's branches. Fixed order, not by count (web.py
# has the reasoning). Here, beside the bucket rule, so every page reads one
# vocabulary; web.py's `_HOW_FILTERS` is this dict.
HOW_LABELS = {
    "after_round":        "after a round",
    "visa":               "visa",
    "sponsorship_screen": "sponsorship screen",
    "form_screen":        "form screen",
    "no_round":           "without a round",
}

# LinkedIn's automatic rejection, recognised by its timer. An employer can
# mark a screening question a must-have and have LinkedIn reject whoever
# fails it: "Auto-archived applicants will receive the rejection message
# three days after they apply to your job posting" (LinkedIn Recruiter Help,
# answer a412523). Measured 25 Sep 2026 on the 27 LinkedIn letters answering
# the author's own applications: 21 arrived 72.01-72.02 hours after the
# submission, the nearest other at 69.7 hours, then 114 and later. A window of
# 71-73 hours admits the timer and nothing else. It cannot see other hiring
# systems' knockouts (no fixed timer), nor an employer who turned on "notify
# promptly", and it rests on LinkedIn keeping the three days.
SCREEN_HOURS = (71, 73)


def screen_sql(app: str) -> str:
    """'sponsorship' | 'form' | NULL: whether LinkedIn auto-rejected the
    application whose id is the SQL expression `app`, and whether its form
    had recorded that you need sponsorship (answers.declares_sponsorship).
    Read from ANY rejected event, not the closing one the reason comes from:
    a hand-filed copy of the same rejection must not hide the letter's
    timing. Which must-have failed is not on record — a form can ask several
    — so `sponsorship` states what the form learned, not a proven cause."""
    lo, hi = SCREEN_HOURS
    declared = answers.declares_sponsorship_sql("sq.question_norm", "sq.answer")
    return f"""(CASE WHEN EXISTS (
        SELECT 1 FROM events sr JOIN emails se ON se.id = sr.source_email_id
         WHERE sr.application_id = {app} AND sr.type = 'rejected'
           AND sr.source = 'email' AND se.extraction->>'platform' = 'linkedin'
           AND sr.occurred_at - (SELECT min(sa.occurred_at) FROM events sa
                                  WHERE sa.application_id = {app} AND sa.type = 'applied')
               BETWEEN interval '{lo} hours' AND interval '{hi} hours')
      THEN CASE WHEN EXISTS (SELECT 1 FROM application_answers sq
                              WHERE sq.application_id = {app} AND {declared})
                THEN 'sponsorship' ELSE 'form' END END)"""


def rejected_how_sql(reason: str, had_round: str, screen: str) -> str:
    """The bucket, as a SQL expression over a reason column, a boolean
    had-a-round expression and a screen_sql() expression — the caller supplies
    all three so the list query and the count query name their own sources."""
    return (f"CASE WHEN {screen} = 'sponsorship' THEN 'sponsorship_screen' "
            f"WHEN {screen} = 'form' THEN 'form_screen' "
            f"WHEN {reason} = 'visa' THEN 'visa' "
            f"WHEN {had_round} THEN 'after_round' ELSE 'no_round' END")


def rejected_how(reason: str | None, had_round: bool, screen: str | None = None) -> str:
    """The same bucket in Python, for insights.py's flow — written beside the
    SQL so the two are read (and changed) together; tests/test_web.py holds
    the page's counts equal to rejection_ends(). `screen` is screen_sql()'s
    value, which facts() fetches."""
    if screen in ("sponsorship", "form"):
        return f"{screen}_screen"
    if reason == "visa":
        return "visa"
    return "after_round" if had_round else "no_round"


def rejection_ends(conn, user_id, inbound: bool | None = None):
    """Rejected applications per bucket (`how`), over the same closing event
    rejection_reasons picks (newest with a reason, else newest) and scoped to
    one list page by `inbound` the way the funnel is (None = both). The
    `inbound` COLUMN splits out leads as the reason table does; `linkedin`,
    `other_email` and `by_hand` split a bucket by what closed it — the closing
    email's own extracted platform, or no email at all — for the chip's hover
    text."""
    had_round = (f"EXISTS (SELECT 1 FROM events x WHERE x.application_id = c.application_id "
                 f"AND x.type IN {ROUND_TYPES})")
    return conn.execute(f"""
        WITH closed AS (
            SELECT DISTINCT ON (e.application_id)
                   e.application_id, e.payload->>'reason' AS reason,
                   e.source, e.source_email_id
            FROM events e
            JOIN applications a ON a.id = e.application_id
            WHERE a.user_id = %(user_id)s AND e.type = 'rejected'
              AND (%(inbound)s::bool IS NULL OR (a.origin = 'inbound') = %(inbound)s)
            ORDER BY e.application_id, (e.payload->>'reason') IS NOT NULL DESC,
                     e.occurred_at DESC, e.created_at DESC
        ), ended AS (
            SELECT {rejected_how_sql('c.reason', had_round, screen_sql('c.application_id'))} AS how,
                   a.origin, c.source, em.extraction->>'platform' AS platform
            FROM closed c
            JOIN applications a ON a.id = c.application_id
            LEFT JOIN emails em ON em.id = c.source_email_id
        )
        SELECT how, count(*) AS n,
               count(*) FILTER (WHERE origin = 'inbound')                         AS inbound,
               count(*) FILTER (WHERE source = 'email' AND platform = 'linkedin') AS linkedin,
               count(*) FILTER (WHERE source = 'email'
                                  AND platform IS DISTINCT FROM 'linkedin')       AS other_email,
               count(*) FILTER (WHERE source <> 'email')                          AS by_hand
        FROM ended
        GROUP BY how
    """, {"user_id": user_id, "inbound": inbound}).fetchall()


# "Applied > REMINDER_DAYS ago, no response, no follow-up, not withdrawn" —
# written ONCE because two callers need it: the /follow-ups page wants the rows
# and every other page wants only the count for its nav badge. Two hand-written
# copies of this predicate is the same trap `_RESPONSE_TYPES` documents above,
# and here it would be worse than a stale list: the badge would promise a
# different number of rows than the page it links to.
_REMINDER_WHERE = """
        WHERE a.user_id = %(user_id)s
          AND EXISTS (SELECT 1 FROM events e
                      WHERE e.application_id = a.id AND e.type = 'applied'
                        AND e.occurred_at < now() - make_interval(days => %(days)s))
          AND NOT EXISTS (SELECT 1 FROM events e
                          WHERE e.application_id = a.id
                            AND e.type IN ('viewed','engaged','interview_invite','rejected',
                                           'offer','withdrawn','follow_up_sent'))
"""


def reminders(conn, user_id):
    """Applied > REMINDER_DAYS ago, no response, no follow-up, not withdrawn."""
    return conn.execute(f"""
        SELECT a.id, j.title_canonical,
               COALESCE(
                 (SELECT p.company_raw FROM postings p
                   WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                   ORDER BY p.captured_at DESC LIMIT 1),
                 j.company_norm) AS company_display,
               (SELECT min(occurred_at) FROM events e
                 WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at,
               -- How long they've been sitting on it. The list can't reuse its
               -- own silent_days here: this runs as its own query, and the
               -- number is the whole reason a row is in this block.
               (SELECT date_part('day', now() - min(occurred_at))::int FROM events e
                 WHERE e.application_id = a.id AND e.type = 'applied') AS days_waiting
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        {_REMINDER_WHERE}
        ORDER BY applied_at
    """, {"user_id": user_id, "days": config.REMINDER_DAYS}).fetchall()


_WORDS = re.compile(r"\w+")


def _jd_overlap(a: str | None, b: str | None) -> float | None:
    """Word-set Jaccard of two job descriptions; None when either is missing."""
    wa, wb = set(_WORDS.findall((a or "").lower())), set(_WORDS.findall((b or "").lower()))
    return len(wa & wb) / len(wa | wb) if wa and wb else None


def reapplications(conn, user_id) -> dict:
    """For each application in the follow-up queue, the LATER application that
    looks like the same role applied to again: {old_id: {id, applied_at,
    title_canonical}}. A suggestion only — /follow-ups shows it beside the row
    and nothing is stored until the user confirms it (web.mark_reapplied),
    because the rule can be wrong and a wrong close would hide a real wait.

    Why it exists (24 Sep 2026 audit): 15 of 150 queue rows were an older
    record of a role reposted under a new job id and applied to again. The
    employer answers the newer one, so the older waits forever — in the queue,
    in the amber heat, in every "no reply" count. Merging the two is wrong
    (two submissions, each with its own answers and resume; invariant #3 has no
    inverse for a wrong merge), so the older one is CLOSED instead.

    The rule: same company, title alike to config.REAPPLIED_TITLE_MIN, applied
    later, and — when both have one — job descriptions overlapping at least
    config.REAPPLIED_JD_MIN, which is what tells a repost from a different role
    under the same title (see the constants for the measurement). The EARLIEST
    such later application is named, so a role applied to three times chains
    1 -> 2 -> 3 rather than pointing both older ones at the newest. The
    unknown-company and unknown-role placeholders never match: two records that
    share one know nothing about each other."""
    rows = conn.execute(f"""
        WITH q AS (
            SELECT a.id, a.job_id, j.company_norm, j.title_canonical,
                   (SELECT min(occurred_at) FROM events e
                     WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            {_REMINDER_WHERE}
              AND j.company_norm <> %(unknown_company)s
              AND j.title_canonical <> %(unknown_title)s
        )
        SELECT q.id AS old_id, n.id, n.applied_at, n.title_canonical,
               (SELECT string_agg(p.jd_text, ' ') FROM postings p
                 WHERE p.job_id = q.job_id) AS old_jd,
               (SELECT string_agg(p.jd_text, ' ') FROM postings p
                 WHERE p.job_id = n.job_id) AS new_jd
        FROM q
        JOIN LATERAL (
            SELECT a2.id, a2.job_id, j2.title_canonical,
                   (SELECT min(occurred_at) FROM events e
                     WHERE e.application_id = a2.id AND e.type = 'applied') AS applied_at
            FROM applications a2
            JOIN jobs j2 ON j2.id = a2.job_id
            WHERE a2.user_id = %(user_id)s AND a2.id <> q.id
              AND j2.company_norm = q.company_norm
              AND similarity(lower(j2.title_canonical), lower(q.title_canonical))
                  >= %(title_min)s
        ) n ON n.applied_at > q.applied_at
        ORDER BY q.id, n.applied_at
    """, {"user_id": user_id, "days": config.REMINDER_DAYS,
          "unknown_company": UNKNOWN_COMPANY, "unknown_title": UNKNOWN_TITLE,
          "title_min": config.REAPPLIED_TITLE_MIN}).fetchall()
    out: dict = {}
    for r in rows:
        if r["old_id"] in out:
            continue
        overlap = _jd_overlap(r["old_jd"], r["new_jd"])
        if overlap is not None and overlap < config.REAPPLIED_JD_MIN:
            continue          # a different role under the same title
        out[r["old_id"]] = {"id": r["id"], "applied_at": r["applied_at"],
                            "title_canonical": r["title_canonical"]}
    return out


def reminder_count(conn, user_id) -> int:
    """Just the number, for the nav badge — same predicate as reminders()."""
    return conn.execute(f"""
        SELECT count(*) AS n FROM applications a
        JOIN jobs j ON j.id = a.job_id
        {_REMINDER_WHERE}
    """, {"user_id": user_id, "days": config.REMINDER_DAYS}).fetchone()["n"]


def lead_count(conn, user_id) -> int:
    """Inbound approaches the user has not acted on yet, for the nav badge on
    `/inbound` — the same predicate as `web._LEADS_FIRST` pins by (24 Sep
    2026). Gated on status, not origin alone, for the same reason the pin is:
    origin is immutable, so a lead the user pursued would otherwise count as
    "awaiting your call" forever. A plain pill, not an amber one: the wait
    here is on the user, and amber is reserved for time passing unanswered
    on the other side (UI rule 1)."""
    return conn.execute("""
        SELECT count(*) AS n
        FROM applications a
        JOIN application_status s ON s.application_id = a.id
        WHERE a.user_id = %s AND a.origin = 'inbound' AND s.status = 'interested'
    """, (user_id,)).fetchone()["n"]


# An application's JD extraction: the newest of the posting it was applied
# through, else the newest of any posting of its job. ONE lateral join, used by
# facts() below and the list query (web._list), so the row's visa tag and the
# /analytics comparison read the same extraction. Joins as `x` over `a`.
LATEST_EXTRACTION = """LEFT JOIN LATERAL (
            SELECT x.posting_id, x.visa_signal, x.visa_notes, x.work_mode,
                   x.languages || x.technologies AS tech
            FROM extractions x JOIN postings p3 ON p3.id = x.posting_id
            WHERE p3.job_id = a.job_id
            ORDER BY (p3.id = a.applied_via_posting_id) DESC NULLS LAST,
                     x.extracted_at DESC
            LIMIT 1
        ) x ON true"""


def facts(conn, user_id) -> tuple[list[dict], list[dict]]:
    """Everything /analytics needs, in two queries: one row per application,
    and every event of every application oldest first. insights.py turns them
    into the page; nothing is aggregated here, so a statistic is one pure
    function over a list and testable without a database.

    Application columns are the properties a comparison can split on — how
    the posting looked (platform, hiring system, listing age, repost), what
    its job description said (visa, work mode, technologies: the newest
    extraction of the posting applied through, else of any posting of the
    job), what the user sent (resume, screening fields answered) — plus the
    derived status, from the same view the list reads, and `screen`
    (screen_sql) for a rejected one.

    Event columns are the facts the timing needs and the two a rejection's
    breakdown reads: `reason` (the closing event is chosen in insights.py by
    the rule rejection_reasons() documents) and `mail_platform`, the closing
    email's own extracted platform, which is how "LinkedIn's letter" is told
    from an ATS's. `external` is only ever set on an `applied` event."""
    apps = conn.execute("""
        SELECT a.id, a.origin, a.resume_file, s.status,
               j.company_norm, j.title_canonical,
               COALESCE(pc.company_raw, j.company_norm) AS company_display,
               p.platform, p.ats, p.posted_label, p.reposted,
               EXISTS (SELECT 1 FROM postings pj WHERE pj.job_id = a.job_id
                        AND COALESCE(pj.jd_text, '') <> '')                  AS has_jd,
               EXISTS (SELECT 1 FROM postings pj WHERE pj.job_id = a.job_id
                        AND (pj.salary_min IS NOT NULL OR pj.salary_raw IS NOT NULL)) AS has_salary,
               x.visa_signal, x.work_mode, COALESCE(x.tech, '{}')           AS tech,
               CASE WHEN s.status = 'rejected' THEN @SCREEN@ END            AS screen,
               (x.posting_id IS NOT NULL)                                    AS extracted,
               (SELECT count(*) FROM application_answers aa
                 WHERE aa.application_id = a.id)                             AS n_answers
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        JOIN application_status s ON s.application_id = a.id
        LEFT JOIN postings p ON p.id = a.applied_via_posting_id
        LEFT JOIN LATERAL (
            SELECT p2.company_raw FROM postings p2
             WHERE p2.job_id = a.job_id AND p2.company_raw IS NOT NULL
             ORDER BY p2.captured_at DESC LIMIT 1
        ) pc ON true
        @LATEST_EXTRACTION@
        WHERE a.user_id = %(user_id)s
    """.replace("@SCREEN@", screen_sql("a.id")).replace("@LATEST_EXTRACTION@", LATEST_EXTRACTION),
        {"user_id": user_id}).fetchall()
    events = conn.execute("""
        SELECT e.application_id, e.type, e.source, e.occurred_at, e.created_at,
               CASE WHEN e.type = 'applied'
                    THEN (e.payload->>'external')::bool END  AS external,
               e.payload->>'reason'                          AS reason,
               (e.payload ? 'superseded_by')                 AS superseded,
               em.extraction->>'platform'                    AS mail_platform
        FROM events e
        JOIN applications a ON a.id = e.application_id
        LEFT JOIN emails em ON em.id = e.source_email_id
        WHERE a.user_id = %(user_id)s
        ORDER BY e.occurred_at, e.created_at
    """, {"user_id": user_id}).fetchall()
    return apps, events
