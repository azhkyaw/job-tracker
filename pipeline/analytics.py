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

from . import answers, config, email_apply, trace
from .jd_extraction import visa_group_sql as jd_visa_group_sql
from .ingest import UNKNOWN_COMPANY, UNKNOWN_TITLE


def _sql_list(types) -> str:
    return "(" + ",".join(f"'{t}'" for t in types) + ")"


# An employer's response of any kind — what the list calls a reply and
# `/analytics` calls hearing back. The tuple is the one list; the SQL string is
# formatted from it, so insights.py (Python) and every query here (SQL) read
# the same set.
RESPONSE_TYPES = ("viewed", "engaged", "interview_invite", "rejected", "offer")
_RESPONSE_TYPES = _sql_list(RESPONSE_TYPES)
# The events that end a thread, as SQL: trace.TERMINAL, the one list.
_CLOSES = _sql_list(sorted(trace.TERMINAL))

# A function, not a constant: `interviewed` reads effective_type_sql, defined
# further down beside the round rules it belongs with.
def _apps_cte() -> str:
    return f"""
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
                     AND {effective_type_sql('e')} IN ('interview_invite','offer'))
                                                                          AS interviewed,
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
    row = conn.execute(_apps_cte() + f"""
        SELECT count(*) FILTER (WHERE applied_at IS NOT NULL) AS applied,
               count(*) FILTER (WHERE applied_at IS NULL)     AS interested,
               count(*) FILTER (WHERE first_resp IS NOT NULL) AS responded,
               count(*) FILTER (WHERE interviewed)            AS interviews,
               count(*) FILTER (WHERE offered)                AS offers,
               round(avg(EXTRACT(epoch FROM first_resp - applied_at) / 86400.0)::numeric, 1)
                                                              AS avg_days_to_resp,
               -- The lede's "since": from the same rows as its counts. It
               -- read the trace axis until 7 Oct 2026, which is the VISIBLE
               -- rows', so a search moved the date and not the numbers.
               min(applied_at)                                AS first_applied,
               -- Interviews, by what came of them (round_fate_sql, 8 Oct
               -- 2026): the lede's "N interviews, M lost (…)", each count
               -- the rows the list's `interviews` filter shows.
               count(*) FILTER (WHERE fate IS NOT NULL)       AS sat,
               count(*) FILTER (WHERE fate IN {_LOST_FATES})  AS lost,
               count(*) FILTER (WHERE fate = 'rejected')      AS lost_rejected,
               count(*) FILTER (WHERE fate = 'lost_quiet')    AS lost_quiet,
               count(*) FILTER (WHERE fate = 'stopped')       AS stopped,
               {", ".join(f"count(*) FILTER (WHERE fate = 'stopped' AND reason = '{k}') AS stopped_{k}"
                          for k in STOP_REASONS)},
               count(*) FILTER (WHERE fate = 'unexplained')   AS unexplained
        FROM (SELECT apps.*, {round_fate_sql('apps')} AS fate,
                     {closing_reason_sql('apps')} AS reason FROM apps) apps
        WHERE %(inbound)s::bool IS NULL OR (origin = 'inbound') = %(inbound)s
    """, {"user_id": user_id, "inbound": inbound}).fetchone()
    row["response_rate"] = (round(100.0 * row["responded"] / row["applied"])
                            if row["applied"] else None)
    # The stopped figure's sub-line: each reason's word and count, in
    # STOP_REASONS' order, the empty ones left out.
    row["stopped_by"] = [(STOP_WORDS[k], row[f"stopped_{k}"]) for k in STOP_REASONS
                         if row[f"stopped_{k}"]]
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

# The rounds you SAT — an interview — and so the ones that take your own
# reading of how it went: `payload.went`, a key of WENT_LABELS, written by
# web.set_round_went on the round event whatever its source, with `went_at`,
# when you said so (8 Oct 2026). An offer is a round the thread reached, not
# one you performed in; it takes none. Two facts kept apart on purpose: the
# rating is yours, about the round, and comes first; a close's reason is the
# employer's, about the close, and comes later (rule 12). "I'm sure I didn't
# hear because the interview went badly" and "I know it went well and heard
# nothing" were one record each until then, and read the same — "rejected
# after a round", "they went quiet". insights.interviews reads the rating
# beside what came of the round.
# A person getting in touch (`engaged`) was one too until 9 Oct 2026, while
# trace.rounds had said since the day before that a call is not a round: the
# list's "11 interviews" then counted an agency recruiter's pitch email, filed
# by hand as "They reached out", as an interview, on a row whose rounds tag
# said none. So it is DEFINED as the event trace.rounds groups, and the two
# cannot drift apart again. `engaged` stays a ROUND_EVENT: a person engaging
# is still an answer, and still makes a rejection "after a round".
RATED_EVENTS = (trace.ROUND_EVENT,)
_RATED_TYPES = _sql_list(RATED_EVENTS)
WENT_LABELS = {"well": "went well", "mixed": "mixed", "badly": "went badly"}

# What kind of round it was (`payload.round_kind`, web.set_round_kind and the
# timeline form, 9 Oct 2026): a closed vocabulary, so "which round do I lose
# at" can be counted later. Shown after the round's label ("round 3 of 3,
# technical interview") and in the list tag's title; trace.rounds carries it
# per round. The author's own words for one thread were "coding test,
# recruiter screen, technical".
ROUND_KINDS = {
    "test":          "coding test or take-home",
    "screen":        "recruiter screen",
    "technical":     "technical interview",
    "manager":       "hiring manager",
    "panel":         "panel or onsite",
    "final":         "final round",
    "questionnaire": "automated questionnaire",   # not a round reached: trace.NON_ROUND_KINDS
    "other":         "other",
}
_NON_ROUND_KINDS = _sql_list(trace.NON_ROUND_KINDS)
_NON_ROUND_ROLES = _sql_list(trace.NON_ROUND_ROLES)


def same_round_sql(x: str, r: str) -> str:
    """Event `x` is the same interview as event `r`: the approximation of
    trace.rounds' grouping the SQL can share (insights._same_round is the
    Python twin) — both invitations, neither filed by hand (trace.own_round),
    naming one day or arriving within trace.ROUND_SPAN_DAYS of each other."""
    span = f"make_interval(days => {trace.ROUND_SPAN_DAYS})"
    return f"""({x}.type = '{trace.ROUND_EVENT}' AND {r}.type = '{trace.ROUND_EVENT}'
                AND {x}.source <> 'manual' AND {r}.source <> 'manual'
                AND (COALESCE({x}.payload->>'stated_date', '') = COALESCE({r}.payload->>'stated_date', '?')
                     OR {x}.occurred_at BETWEEN {r}.occurred_at - {span} AND {r}.occurred_at + {span}))"""


def _excluded_line_sql(r: str) -> str:
    """Invitation line `r` is no round by its own line: you said so
    (trace.NOT_A_ROUND) or the mail did (trace.NON_ROUND_ROLES). trace.excluded."""
    return f"""(COALESCE({r}.payload->>'round_is', '') = '{trace.NOT_A_ROUND}'
                OR COALESCE({r}.payload->>'invite_role', '') IN {_NON_ROUND_ROLES})"""


def _non_round_kind_sql(r: str) -> str:
    """Invitation `r` belongs to a round of a kind that is not one
    (trace.NON_ROUND_KINDS: the automated questionnaire), the kind set on it
    or on any event of the same round."""
    return f"""EXISTS (SELECT 1 FROM events k
                        WHERE k.application_id = {r}.application_id
                          AND k.payload->>'round_kind' IN {_NON_ROUND_KINDS}
                          AND (k.id = {r}.id OR {same_round_sql('k', r)}))"""


# What an event COUNTS as (9 Oct 2026): its type, except an invitation that
# is not a round. One of a kind that is not one — the automated
# questionnaire every applicant gets — is part of applying, and reads as the
# confirmation does; one excluded by its line — a mail arranging or
# cancelling an interview, or a line you said is not a round — is a person
# writing about an interview, and reads as `engaged`. Found on a thread whose
# only invitations were a 3-question screening questionnaire and its
# reminder: no round, no interview in the figure, yet the status view, which
# ranked by type alone, said "interviewing". Migration 021 states this
# expression in the view (static SQL cannot import it) and tests/test_web.py
# holds the two, and insights.effective_type, equal on every event.
# Hearing back reads the TYPE, not this: a questionnaire is a response, as
# LinkedIn's "viewed" notice is, but not an answer (UI rule 18).
def effective_type_sql(e: str) -> str:
    """Event `e`'s type as the status and every round count read it."""
    return f"""(CASE WHEN {e}.type <> '{trace.ROUND_EVENT}' THEN {e}.type
                     WHEN {_non_round_kind_sql(e)} THEN 'confirmation'
                     WHEN {_excluded_line_sql(e)} THEN 'engaged'
                     ELSE {e}.type END)"""


def round_sql(e: str) -> str:
    """Event `e` is a ROUND_EVENT once it is read for what it counts as: an
    interview, a person getting in touch (or writing about an interview), an
    offer — never a questionnaire. What "after a round" and "answered" test."""
    return f"({effective_type_sql(e)} IN {ROUND_TYPES})"


def sat_sql(r: str) -> str:
    """Event `r` (an `events` alias) is a round you sat: a rated type that
    counts as itself — not a line you or the mail said is not a round, and
    not of a round whose kind is not one — or nothing here anchors on it."""
    return f"""({r}.type IN {_RATED_TYPES}
        AND NOT {_excluded_line_sql(r)}
        AND NOT {_non_round_kind_sql(r)})"""

# What came of the round you rated: one bucket per application with a round
# you sat, the SQL twin of insights._went (8 Oct 2026). The anchor is the
# newest round carrying a rating, else the newest round; the outcome is the
# first round or close after it, else the thread's close when that sits
# before the round's own mail. Asked for as a number on the list — "I failed
# 2 interviews in total: one they rejected me after, one they went quiet on
# after a mixed or bad one" — so `lost` there is `rejected` + `lost_quiet`
# (LOST_FATES). Silence after a GOOD one is a different fact (`unexplained`:
# the author's "I know I did well and heard nothing"), and after an unrated
# one no fact at all (`quiet`). The keys are the list's `?interviews=`
# filter and the words follow "interviews" — the lede's and the filter
# note's, one vocabulary. Held equal to the Python over every application in
# the suite's database (tests/test_web.py).
ROUND_FATES = {
    "rejected":    "followed by a rejection",
    "lost_quiet":  "you rated mixed or badly, then silence",
    # A rejection whose closing reason is not about the interview — a visa,
    # the role closing, pay, location (STOP_REASONS; rejected_how's rule 13
    # for visa: a fact about the employer or the market wins whatever the
    # stage) — is not lost: the author's call, 9 Oct 2026, on a screen after
    # which the recruiter said they do not sponsor, then on a test after
    # which the role closed. A reason about you in the room (skills,
    # seniority, "other") or none at all stays lost: after a round, the
    # honest default is that the round decided.
    "stopped":     "stopped for a reason that was not the interview",
    "unexplained": "you rated well, then silence",
    "quiet":       "unrated, then silence",
    "ended":       "you closed yourself",
    "on":          "that went on to a further round",
    "waiting":     "still waiting for an answer",
}
LOST_FATES = ("rejected", "lost_quiet")
LOST_WORDS = ("mixed", "badly")
# The stated reasons (web._EVENT_REASONS keys; web asserts the subset) that
# stop a thread without judging the interview, and the figure's word for
# each ("1 visa · 1 role closed" under "stopped, not the interview").
STOP_REASONS = ("visa", "role_closed", "salary", "location")
STOP_WORDS = {"visa": "visa", "role_closed": "role closed", "salary": "pay", "location": "location"}
_LOST_FATES = _sql_list(LOST_FATES)
_LOST_WORDS = _sql_list(LOST_WORDS)
_STOP_REASONS = _sql_list(STOP_REASONS)
# What ends a round's wait: a further round, or a close.
_OUTCOME_TYPES = _sql_list(ROUND_EVENTS + tuple(sorted(trace.TERMINAL)))


def round_fate(went: str | None, went_next: str | None, reason: str | None = None) -> str | None:
    """The bucket in Python, over insights._went's two facts and the thread's
    closing reason (rejection_reasons' rule: the newest rejected event
    carrying one); None with no round sat. Written beside the SQL so the two
    are changed together."""
    if went_next is None:
        return None
    if went_next == "rejected":
        return "stopped" if reason in STOP_REASONS else "rejected"
    if went_next == "quiet":
        return ("lost_quiet" if went in LOST_WORDS else
                "unexplained" if went == "well" else "quiet")
    return {"progressed": "on", "ended": "ended", "waiting": "waiting"}[went_next]


def closing_reason_sql(a: str) -> str:
    """Application `a`'s closing reason: rejection_reasons' rule, the newest
    rejected event carrying one, else the newest (NULL)."""
    return f"""(SELECT e.payload->>'reason' FROM events e
                 WHERE e.application_id = {a}.id AND e.type = 'rejected'
                 ORDER BY (e.payload->>'reason') IS NOT NULL DESC, e.occurred_at DESC, e.created_at DESC
                 LIMIT 1)"""


def round_fate_sql(a: str) -> str:
    """The same bucket as a SQL expression over application `a`: NULL with
    no round sat. `r` is the anchor round, `n` the first round or close
    after it — not a line you said is not a round, and not the anchor's own
    notification or reminder (an invitation naming its day or within
    trace.ROUND_SPAN_DAYS, insights._same_round) — `c` the thread's latest
    close, `o` whichever of the two the outcome is (`n`, else `c` — a close
    filed by hand at noon before the round's own evening mail, the 10 Sep
    2026 record)."""
    return f"""(SELECT CASE
            WHEN o.type IS NULL THEN 'waiting'
            WHEN o.type = 'rejected' THEN
                 CASE WHEN rr.reason IN {_STOP_REASONS} THEN 'stopped' ELSE 'rejected' END
            WHEN o.type = 'withdrawn' AND o.closed = 'went_quiet' THEN
                 CASE WHEN r.payload->>'went' IN {_LOST_WORDS} THEN 'lost_quiet'
                      WHEN r.payload->>'went' = 'well' THEN 'unexplained' ELSE 'quiet' END
            WHEN o.type = 'withdrawn' THEN 'ended'
            ELSE 'on' END
          FROM (SELECT r.id, r.application_id, r.type, r.source, r.occurred_at, r.created_at,
                       r.payload FROM events r
                 WHERE r.application_id = {a}.id AND {sat_sql('r')}
                 ORDER BY (r.payload ? 'went') DESC, r.occurred_at DESC, r.created_at DESC
                 LIMIT 1) r
          LEFT JOIN LATERAL (
            SELECT n.type, n.payload->>'closed' AS closed FROM events n
             WHERE n.application_id = {a}.id AND n.type IN {_OUTCOME_TYPES}
               -- a further round is one you sat (not a line you said is not a
               -- round, nor a questionnaire), and not the anchor's own round
               AND (n.type <> '{trace.ROUND_EVENT}' OR {sat_sql('n')})
               AND NOT {same_round_sql('n', 'r')}
               AND (n.occurred_at, n.created_at) > (r.occurred_at, r.created_at)
             ORDER BY n.occurred_at, n.created_at LIMIT 1) n ON true
          LEFT JOIN LATERAL (
            SELECT c.type, c.payload->>'closed' AS closed FROM events c
             WHERE c.application_id = {a}.id AND c.type IN {_CLOSES}
             ORDER BY c.occurred_at DESC, c.created_at DESC LIMIT 1) c ON true
          LEFT JOIN LATERAL (SELECT {closing_reason_sql(a)} AS reason) rr ON true
          CROSS JOIN LATERAL (
            SELECT COALESCE(n.type, c.type) AS type,
                   CASE WHEN n.type IS NOT NULL THEN n.closed ELSE c.closed END AS closed) o)"""

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
# LinkedIn's two automatic rejections are one mechanism (screen_sql), so the
# list's legend names them as one entry, "screened", with the two as its
# parts; `how=screen` is that entry's filter, a sum of buckets and not a
# bucket, as `lost` is for interviews (9 Oct 2026).
SCREEN_HOWS = ("sponsorship_screen", "form_screen")


# How long an application has waited with nothing back, in the three spans
# the list's `applied` entry breaks into (9 Oct 2026): fresh, under
# REMINDER_DAYS quiet (the rows' grey); inside the odds; and past them, from
# `quiet_after` (insights.quiet_after, /follow-ups' cut) on. Measured on the
# row's own days quiet, the list's "Days quiet" column and the heat the
# status bar paints with (trace.wait: since the last event), where
# /follow-ups counts from the submission and leaves out a row you followed
# up or applied to again; hence "past the odds" here, never its "gone
# quiet". On the day 292 applied split 88 / 57 / 147.
WAIT_SPANS = ("fresh", "inside", "past")


def wait_span(silent_days: int | None, quiet_after: int | None) -> str | None:
    """The span in Python, over trace.wait's silent_days, for the legend's
    counts; written beside the SQL so the two are changed together."""
    if silent_days is None:
        return None
    if silent_days < config.REMINDER_DAYS:
        return "fresh"
    return "inside" if quiet_after is None or silent_days < quiet_after else "past"


def wait_span_sql(a: str) -> str:
    """The same span over application `a`, for the list's `wait` filter.
    `%(reminder)s` and `%(quiet)s` are the caller's parameters (quiet_after,
    NULL with too few replies to draw the odds). Whole days quiet below N is
    the last event later than N days ago, which is trace.wait's floor."""
    last = f"(SELECT max(we.occurred_at) FROM events we WHERE we.application_id = {a}.id)"
    return (f"(CASE WHEN {last} > now() - make_interval(days => %(reminder)s) THEN 'fresh' "
            f"WHEN %(quiet)s::int IS NULL OR {last} > now() - make_interval(days => %(quiet)s::int) "
            f"THEN 'inside' ELSE 'past' END)")

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
                 f"AND {round_sql('x')})")
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
def reply_sql(e: str) -> str:
    """Event `e` is YOUR move on a thread someone else started (28 Sep 2026):
    the fact that turns a lead's wait from yours into theirs. It reaches the
    record three ways, and this is the one definition of all three:
      - a reply you filed by hand (web.mark_replied: a `note` with
        `payload.reply`), for replies no ingest path sees — a LinkedIn
        message, WhatsApp, a call;
      - mail you SENT, filed on the thread (matcher.EVENT_TYPE: a reply files
        a `note`, a follow-up `follow_up_sent`), marked on the email itself
        (`emails.sent_by_user`, migration 016) rather than on the event;
      - a follow-up filed by hand ("I followed up").
    A note to self is none of these. On 28 Sep 2026 all 9 open leads had no
    reply recorded at all, from 6 to 66 days after the approach."""
    return (f"({e}.type = 'follow_up_sent' OR ({e}.type = 'note' AND ({e}.payload ? 'reply' "
            f"OR EXISTS (SELECT 1 FROM emails rm WHERE rm.id = {e}.source_email_id "
            f"AND rm.sent_by_user))))")


def theirs_sql(e: str) -> str:
    """Event `e` is the RECRUITER writing on a lead: an approach, or a message
    of theirs filed as a note (a received email the classifier read as a
    status update). Their other moves — viewed, engaged, an invitation, a
    rejection — move the status past `interested`, so a lead never needs
    them here."""
    return (f"({e}.type = 'recruiter_outreach' OR ({e}.type = 'note' AND EXISTS ("
            f"SELECT 1 FROM emails tm WHERE tm.id = {e}.source_email_id "
            f"AND NOT tm.sent_by_user)))")


def awaiting_you_sql(a: str, status: str) -> str:
    """An inbound lead whose next move is YOURS: still `interested` (nothing
    has moved or closed it), and no reply of yours since the recruiter last
    wrote. ONE expression for everything that says "awaiting your call" — the
    pin (web._LEADS_FIRST), the nav pill (lead_count), the /inbound lede and
    /analytics' squares — so a count and the rows it names cannot disagree.
    Until 28 Sep 2026 it was status alone, so a lead you had answered still
    claimed the next move was yours while you waited on them."""
    return (f"({a}.origin = 'inbound' AND {status} = 'interested' AND NOT EXISTS ("
            f"SELECT 1 FROM events yr WHERE yr.application_id = {a}.id AND {reply_sql('yr')} "
            f"AND yr.occurred_at >= COALESCE((SELECT max(ty.occurred_at) FROM events ty "
            f"WHERE ty.application_id = {a}.id AND {theirs_sql('ty')}), '-infinity')))")


# The latest move of yours on a thread, for the follow-up queue's lead rows.
_LAST_REPLY = f"""(SELECT max(r.occurred_at) FROM events r
                    WHERE r.application_id = a.id AND {reply_sql('r')})"""


def move_sql(e: str) -> str:
    """Event `e` is a MOVE on the thread, by either side: anything but a note
    to self. A note counts only as your reply (reply_sql) or their message
    (theirs_sql). A note you filed for yourself ("the panel seemed keen")
    must not reset the wait it is a note about."""
    return f"({e}.type <> 'note' OR {reply_sql(e)} OR {theirs_sql(e)})"


# The thread's latest move, whoever made it: what a round's wait runs from.
_LAST_MOVE = f"""(SELECT max(m.occurred_at) FROM events m
                   WHERE m.application_id = a.id AND {move_sql('m')})"""

# The statuses of a thread a round has opened and nothing has closed — the
# round events themselves, since an offer is one too (7 Oct 2026, migration
# 019: it ranks below the closes, so accepting, declining or losing it is
# what ends the thread, never the offer itself). A round is a response, which
# is exactly why the first kind of queue row below leaves such a thread alone.
OPEN_ROUND = ROUND_EVENTS
_OPEN_ROUND = _sql_list(OPEN_ROUND)

# Three kinds of row, one queue. An application: applied over REMINDER_DAYS
# ago with no response and nothing sent. It leaves for good once followed up.
# A lead you REPLIED to (28 Sep 2026): your latest move over REMINDER_DAYS old
# and nothing from them since. A ROUND gone quiet (29 Sep 2026): a thread an
# interview or a person opened, whose latest move by either side is over
# REMINDER_DAYS old. Asked about it, the author put it as interviews that are
# "ignored after chasing them for status", or went badly and never got an
# answer; none of them could reach this queue, since any response kept a
# record out, and none could be closed truthfully ("I withdrew" and "They
# rejected me" both say something that did not happen). On the day, 8 such
# threads were open, 2 silent 19 and 20 days. A lead and a round leave by
# a response, or by you closing them ("They went quiet", web.close_approach),
# and a follow-up only resets their clock: a thread you can close is asked
# about again, where a never-answered application, which has no such close,
# is left to run. Never closed for you: answers after a round have come 18
# and 22 days later (6 measured).
_REMINDER_WHERE = f"""
        WHERE a.user_id = %(user_id)s
          AND ((EXISTS (SELECT 1 FROM events e
                        WHERE e.application_id = a.id AND e.type = 'applied'
                          AND e.occurred_at < now() - make_interval(days => %(days)s))
                AND NOT EXISTS (SELECT 1 FROM events e
                                WHERE e.application_id = a.id
                                  AND e.type IN ('viewed','engaged','interview_invite','rejected',
                                                 'offer','withdrawn','follow_up_sent')))
            OR (a.origin = 'inbound'
                AND EXISTS (SELECT 1 FROM application_status ls
                            WHERE ls.application_id = a.id AND ls.status = 'interested')
                AND {_LAST_REPLY} < now() - make_interval(days => %(days)s)
                AND NOT EXISTS (SELECT 1 FROM events t
                                WHERE t.application_id = a.id AND {theirs_sql('t')}
                                  AND t.occurred_at > {_LAST_REPLY}))
            OR (EXISTS (SELECT 1 FROM application_status rs
                        WHERE rs.application_id = a.id AND rs.status IN {_OPEN_ROUND})
                AND {_LAST_MOVE} < now() - make_interval(days => %(days)s)))
"""


# The queue's rows with their kind and wait: ONE statement that reminders()
# reads rows from and queue_count() counts over, so the nav pill cannot
# promise rows the page does not show (the trap _REMINDER_WHERE's comment
# names). The reach columns (7 Oct 2026) say whether there is someone to
# write to: the recruiter recorded on the job with a profile to message (the
# extension's card, or one you added) or a thread you wrote in (mail you
# sent, filed on the application). On the day 126 of 192 unanswered rows had
# neither — a LinkedIn Easy Apply with no card — and the page had offered a
# "Followed up" button on every one. A round or a lead has its own
# conversation and never reads them.
_QUEUE_SQL = f"""
    SELECT q.*,
           CASE WHEN q.status IN {_OPEN_ROUND} THEN 'round'
                WHEN q.applied_at IS NULL THEN 'lead'
                ELSE 'unanswered' END AS kind,
           -- How long they've been sitting on it: since you applied, for
           -- a lead since your latest reply, for a round since the
           -- thread's latest move. The list can't reuse its own
           -- silent_days here: this runs as its own query, and the number
           -- is the whole reason a row is in this block.
           date_part('day', now() - CASE WHEN q.status IN {_OPEN_ROUND} THEN q.moved_at
                                         ELSE COALESCE(q.applied_at, q.replied_at) END)::int
             AS days_waiting,
           (q.contact_url IS NOT NULL OR q.wrote) AS reachable
    FROM (
      SELECT a.id, a.origin, j.title_canonical, s.status,
             COALESCE(
               (SELECT p.company_raw FROM postings p
                 WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                 ORDER BY p.captured_at DESC LIMIT 1),
               j.company_norm) AS company_display,
             (SELECT min(occurred_at) FROM events e
               WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at,
             {_LAST_REPLY} AS replied_at,
             {_LAST_MOVE} AS moved_at,
             -- What that latest move was, for the row's own words: a
             -- follow-up or a reply of yours, a message of theirs, or the
             -- event itself (the invitation, a person getting in touch).
             (SELECT CASE WHEN m.type = 'follow_up_sent' THEN 'followed_up'
                          WHEN m.type = 'note' AND {reply_sql('m')} THEN 'replied'
                          WHEN m.type = 'note' THEN 'wrote'
                          ELSE m.type END
                FROM events m
               WHERE m.application_id = a.id AND {move_sql('m')}
               ORDER BY m.occurred_at DESC, m.created_at DESC LIMIT 1) AS moved_as,
             ct.name AS contact_name, ct.url AS contact_url,
             EXISTS (SELECT 1 FROM emails w WHERE w.matched_application_id = a.id
                        AND w.sent_by_user) AS wrote
      FROM applications a
      JOIN jobs j ON j.id = a.job_id
      JOIN application_status s ON s.application_id = a.id
      LEFT JOIN LATERAL (
        SELECT c.name, c.url FROM contacts c
         WHERE c.job_id = a.job_id AND c.url IS NOT NULL
         ORDER BY c.approached_at DESC NULLS LAST, c.id LIMIT 1
      ) ct ON true
      {_REMINDER_WHERE}
    ) q
"""


def reminders(conn, user_id):
    """The follow-up queue's rows, longest wait first, each with its `kind`
    (_REMINDER_WHERE has all three): `unanswered`, applied > REMINDER_DAYS
    ago with no response and nothing sent; `lead`, an approach you replied to
    that has heard nothing since (`replied_at` set, `applied_at` NULL); and
    `round`, a thread an interview or a person opened whose latest move
    (`moved_at`, and `moved_as`: what it was) is > REMINDER_DAYS old. Each
    carries `reachable` and who that is (`contact_name`/`contact_url`,
    `wrote`); queue() sections them by the move they offer."""
    return conn.execute(f"{_QUEUE_SQL} ORDER BY days_waiting DESC, company_display, id",
                        {"user_id": user_id, "days": config.REMINDER_DAYS}).fetchall()


# ------------------------------------------------------------------- odds
#
# The reply curve, for the queue (7 Oct 2026): the SAME estimator /analytics
# draws (insights.reply_curve over facts) — Kaplan-Meier over every
# application the user sent, heard back at its lag or censored at its age or
# the day it closed. Built here from one query rather than from facts()
# because the nav pill reads it on every page, and facts() fetches every
# event the user has (120 ms against 23 on the dev DB). tests/test_web.py
# holds the two curves to each other.
_REPLY_PAIRS_SQL = f"""
    SELECT GREATEST(0, EXTRACT(epoch FROM COALESCE(
             (SELECT min(e.occurred_at) FROM events e
               WHERE e.application_id = a.id AND e.type IN {_RESPONSE_TYPES}),
             (SELECT min(e.occurred_at) FROM events e
               WHERE e.application_id = a.id AND e.type IN {_CLOSES}),
             %(now)s::timestamptz) - ap.applied_at) / 86400.0) AS dur,
           EXISTS (SELECT 1 FROM events e
                    WHERE e.application_id = a.id AND e.type IN {_RESPONSE_TYPES}) AS heard
    FROM applications a
    JOIN LATERAL (SELECT min(e.occurred_at) AS applied_at FROM events e
                   WHERE e.application_id = a.id AND e.type = 'applied') ap
      ON ap.applied_at IS NOT NULL
    WHERE a.user_id = %(user_id)s AND a.origin <> 'inbound'
"""


class ReplyOdds:
    """What the queue reads off the user's reply curve: `quiet_after`, the
    day from which an unanswered application has gone quiet
    (insights.quiet_after), and `chance(days)`, the share still heard from at
    that age. Both None below insights.MIN_TIMING_N replies — a curve drawn
    by a handful of answers is an anecdote, and must not close anything."""

    def __init__(self, curve, heard: int, reminder_days: int):
        from . import insights        # insights imports this module
        self.curve, self.heard = curve, heard
        self.enough = heard >= insights.MIN_TIMING_N
        self.quiet_after = insights.quiet_after(curve, reminder_days) if self.enough else None

    def chance(self, days_waiting) -> float | None:
        from . import insights
        return insights.still_chance(self.curve, days_waiting) if self.enough else None


def reply_odds(conn, user_id, now=None) -> ReplyOdds:
    from datetime import datetime, timezone
    from . import insights
    rows = conn.execute(_REPLY_PAIRS_SQL, {"user_id": user_id,
                                           "now": now or datetime.now(timezone.utc)}).fetchall()
    pairs = [(float(r["dur"]), r["heard"]) for r in rows]
    return ReplyOdds(insights.kaplan_meier(pairs), sum(1 for _, h in pairs if h),
                     config.REMINDER_DAYS)


def queue(conn, user_id, now=None) -> dict:
    """/follow-ups' sections, every row placed by the MOVE it offers
    (7 Oct 2026). Until then the page was every unanswered application over
    REMINDER_DAYS, oldest first: 192 rows on the day, 150 of them past the
    point where any application of the author's had ever heard back and 126
    with nobody to write to, five follow-ups filed in the whole search. A wait
    is the list's to draw; this page is for work (web-ui rule 9).
      rounds   a thread after a round, or a lead you replied to, gone quiet
               (reminders' `round` and `lead`): chase it or close it
      nudge    unanswered, inside the odds (younger than `quiet_after`) and
               reachable: a person to message or a thread to reply in, best
               odds first — 7 of the 192
      again    unanswered, and reapplications() names a later application to
               the same role, whatever its age: the suggestion IS the move,
               judged row by row ("Same role, close") or taken for all of
               them at once (web.quiet_close). Not in the pill: its rule
               compares job descriptions in Python (89 ms on the dev DB)
               and the pill is one statement.
      quiet    unanswered and past the odds: a count and one confirmed close
               for all of them (web.quiet_close), never rows
      waiting  unanswered, inside the odds, nobody to write to: a count
    `odds` is the ReplyOdds behind the cut and each nudge row's `chance`.
    With too few replies to draw a curve nothing is quiet, and every
    reachable row is a nudge."""
    odds = reply_odds(conn, user_id, now)
    later = reapplications(conn, user_id)
    rounds, nudge, again, quiet, waiting = [], [], [], [], []
    for r in reminders(conn, user_id):
        if r["kind"] != "unanswered":
            rounds.append(r)
            continue
        r["chance"] = odds.chance(r["days_waiting"])
        r["again"] = later.get(r["id"])
        if r["again"]:
            again.append(r)
        elif odds.quiet_after is not None and r["days_waiting"] >= odds.quiet_after:
            quiet.append(r)
        elif r["reachable"]:
            nudge.append(r)
        else:
            waiting.append(r)
    nudge.sort(key=lambda r: (r["days_waiting"], r["company_display"]))
    return {"rounds": rounds, "nudge": nudge, "again": again, "quiet": quiet,
            "waiting": waiting, "odds": odds}


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
              -- Not a thread they answered (a round gone quiet, 29 Sep 2026):
              -- the reason to close an older record, that the employer
              -- answers the newer one, is false there by construction.
              AND NOT EXISTS (SELECT 1 FROM events x WHERE x.application_id = a.id
                              AND {round_sql('x')})
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


# An application the listing asked for BY EMAIL, still owed (4 Oct 2026;
# pipeline/email_apply.py has the rule and its measurement). Your own
# application, applied, and nothing since that makes the email moot:
#   - no response (RESPONSE_TYPES: a "viewed" says the online one reached
#     them) and not withdrawn;
#   - no mail of yours filed on it (emails.sent_by_user): an emailed CV for a
#     role already applied to files as a note (matcher._append_event), and
#     that is how sending it clears the row with no click;
#   - nothing you filed by hand ("I emailed it" / "Not needed",
#     web.mark_emailed: a note with `payload.emailed`).
# An approach a recruiter started is left out: there you answer them.
_EMAIL_CLEARS = _sql_list(RESPONSE_TYPES + ("withdrawn",))


def email_owed_sql(a: str) -> str:
    """Application `a` still owes the email its listing asked for."""
    return f"""({a}.origin <> 'inbound'
        AND EXISTS (SELECT 1 FROM events eo WHERE eo.application_id = {a}.id
                    AND eo.type = 'applied')
        AND NOT EXISTS (SELECT 1 FROM events eo WHERE eo.application_id = {a}.id
                        AND (eo.type IN {_EMAIL_CLEARS} OR eo.payload ? 'emailed'
                             OR EXISTS (SELECT 1 FROM emails em WHERE em.id = eo.source_email_id
                                        AND em.sent_by_user)))
        AND EXISTS (SELECT 1 FROM postings po WHERE po.job_id = {a}.job_id
                    AND {email_apply.asks_by_email_sql('po.jd_text')}))"""


def emails_owed(conn, user_id) -> list[dict]:
    """The applications still owing their email, oldest application first,
    each with `jds`: its postings' JD texts, for email_apply.instruction() to
    quote the sentence from (the SQL can say whether, not what)."""
    return conn.execute(f"""
        SELECT a.id, j.title_canonical,
               COALESCE((SELECT p.company_raw FROM postings p
                          WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                          ORDER BY p.captured_at DESC LIMIT 1),
                        j.company_norm) AS company_display,
               (SELECT min(occurred_at) FROM events e
                 WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at,
               (SELECT array_agg(p.jd_text ORDER BY p.captured_at) FROM postings p
                 WHERE p.job_id = a.job_id AND p.jd_text IS NOT NULL) AS jds
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.user_id = %s AND {email_owed_sql('a')}
        ORDER BY applied_at
    """, (user_id,)).fetchall()


def email_owed_count(conn, user_id) -> int:
    """Just the number, for the list's nudge: the same predicate as
    emails_owed(), so the nudge promises the rows /follow-ups shows."""
    return conn.execute(f"SELECT count(*) AS n FROM applications a "
                        f"WHERE a.user_id = %s AND {email_owed_sql('a')}",
                        (user_id,)).fetchone()["n"]


# A round you sat, waiting for your word (8 Oct 2026): the thread is open,
# its newest round you performed in (RATED_EVENTS) carries no `went`, that
# round's day has passed — the interview day the invitation named
# (`stated_date`), else the event's own — and no offer has come since, which
# would put the outcome on record and make any rating hindsight. Asked on
# /follow-ups first, the day after, before anything comes back: the only
# rating the interviews panel (insights.interviews) can learn from. A day is
# compared as the text the extractor validated (YYYY-MM-DD, by regex) against
# today's, never cast: a date-shaped string can still fail a cast, and this
# predicate runs in the pill on every page. The day is the DATABASE clock's
# (UTC on the dev DB), so a round is asked about from the next UTC day —
# 08:00 the morning after, in Singapore. Measured on the day: 8 open threads
# ended in an unrated invitation, all with a stated day, none in a bare
# `engaged`. ONE rule, formatted into the rows and the pill.
_ISO_DAY = r"^\d{4}-\d{2}-\d{2}$"


def last_sat_sql(a: str) -> str:
    """The id of application `a`'s newest round you sat, as a subselect."""
    return f"""(SELECT r.id FROM events r
                 WHERE r.application_id = {a}.id AND {sat_sql('r')}
                 ORDER BY r.occurred_at DESC, r.created_at DESC LIMIT 1)"""


def round_day_sql(r: str) -> str:
    """The day round event `r` fell on, as YYYY-MM-DD text: the day its
    email stated when that is date-shaped, else the event's own."""
    return f"""COALESCE(CASE WHEN {r}.payload->>'stated_date' ~ '{_ISO_DAY}'
                             THEN {r}.payload->>'stated_date' END,
                        to_char({r}.occurred_at, 'YYYY-MM-DD'))"""


def rating_owed_sql(a: str) -> str:
    """Application `a` owes a word on the newest round you sat. Rated means
    a rating on any event of the same ROUND (trace.rounds: one interview is
    several events), approximated here as one naming the same day or
    arriving within ROUND_SPAN_DAYS — a reminder after a rated invitation
    must not ask again."""
    return f"""(NOT EXISTS (SELECT 1 FROM events c WHERE c.application_id = {a}.id
                            AND c.type IN {_CLOSES})
        AND EXISTS (SELECT 1 FROM events r
                     WHERE r.id = {last_sat_sql(a)}
                       AND NOT EXISTS (SELECT 1 FROM events x
                                        WHERE x.application_id = {a}.id
                                          AND x.type IN {_RATED_TYPES} AND x.payload ? 'went'
                                          -- its own rating, or one on the same round — which
                                          -- a hand-filed round never shares (trace.own_round)
                                          AND (x.id = r.id OR {same_round_sql('x', 'r')}))
                       AND {round_day_sql('r')} < to_char(now(), 'YYYY-MM-DD')
                       AND NOT EXISTS (SELECT 1 FROM events o
                                        WHERE o.application_id = {a}.id AND o.type = 'offer'
                                          AND o.occurred_at > r.occurred_at)))"""


def ratings_owed(conn, user_id) -> list[dict]:
    """The applications owing a word on a round you sat, that round's day
    first: the record, the event to rate (`event_id`, `round_type`,
    `round_at`) and the day its email stated (`stated`, as written — web.py
    reads it, for the same reason the rule compares it as text)."""
    return conn.execute(f"""
        SELECT a.id, j.title_canonical,
               COALESCE((SELECT p.company_raw FROM postings p
                          WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                          ORDER BY p.captured_at DESC LIMIT 1),
                        j.company_norm) AS company_display,
               r.id AS event_id, r.type AS round_type, r.occurred_at AS round_at,
               r.payload->>'stated_date' AS stated
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        JOIN events r ON r.id = {last_sat_sql('a')}
        WHERE a.user_id = %s AND {rating_owed_sql('a')}
        ORDER BY {round_day_sql('r')}, r.occurred_at
    """, (user_id,)).fetchall()


def queue_count(conn, user_id, odds: ReplyOdds | None = None) -> int:
    """The nav pill: the rows on /follow-ups that carry a move — the rounds
    owed a word, the emails owed, the threads after a round, the
    applications worth a nudge — which is the page's row sections over
    rating_owed_sql, email_owed_sql and _QUEUE_SQL, in one statement because
    every page renders it (the full queue() is ~250 ms on the dev DB; this
    and the odds are ~100). Until 7 Oct 2026 it counted every unanswered
    application: 197 on the day, a number nobody could act on.
    tests/test_web.py holds it to the page's rows. `odds`: a caller that has
    read them already (the list, for its applied entry) passes them in."""
    quiet_after = (odds or reply_odds(conn, user_id)).quiet_after
    return conn.execute(f"""
        SELECT (SELECT count(*) FROM applications a
                 WHERE a.user_id = %(user_id)s AND {rating_owed_sql('a')})
             + (SELECT count(*) FROM applications a
                 WHERE a.user_id = %(user_id)s AND {email_owed_sql('a')})
             + (SELECT count(*) FROM ({_QUEUE_SQL}) w
                 WHERE w.kind <> 'unanswered'
                    OR (w.reachable AND (%(quiet)s::int IS NULL OR w.days_waiting < %(quiet)s)))
               AS n
    """, {"user_id": user_id, "days": config.REMINDER_DAYS,
          "quiet": quiet_after}).fetchone()["n"]


def lead_count(conn, user_id) -> int:
    """Inbound approaches awaiting the user's call, for the nav badge on
    `/inbound` — `awaiting_you_sql`, the predicate `web._LEADS_FIRST` pins by
    (24 Sep 2026; replies count since 28 Sep). Gated on status, not origin
    alone, for the same reason the pin is: origin is immutable, so a lead the
    user pursued would otherwise count as "awaiting your call" forever. A
    plain pill, not an amber one: the wait here is on the user, and amber is
    reserved for time passing unanswered on the other side (UI rule 1)."""
    return conn.execute(f"""
        SELECT count(*) AS n
        FROM applications a
        JOIN application_status s ON s.application_id = a.id
        WHERE a.user_id = %s AND {awaiting_you_sql('a', 's.status')}
    """, (user_id,)).fetchone()["n"]


# An application's JD extraction: the newest of the posting it was applied
# through, else the newest of any posting of its job. ONE lateral join, used by
# facts() below and the list query (web._list), so the row's visa tag and the
# /analytics comparison read the same extraction. Joins as `x` over `a`.
LATEST_EXTRACTION = """LEFT JOIN LATERAL (
            SELECT x.posting_id, x.visa_signal, x.visa_notes, x.work_mode, x.seniority,
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
    from an ATS's. `external` is only ever set on an `applied` event; `went`
    and `went_at` only on a round you sat (RATED_EVENTS)."""
    apps = conn.execute("""
        SELECT a.id, a.origin, a.resume_file, s.status,
               -- Awaiting your call: the pin's own predicate, so a square
               -- says what the /inbound row says.
               @AWAITING@ AS awaiting_you,
               j.company_norm, j.title_canonical,
               COALESCE(pc.company_raw, j.company_norm) AS company_display,
               p.platform, p.ats, p.posted_label, p.reposted,
               EXISTS (SELECT 1 FROM postings pj WHERE pj.job_id = a.job_id
                        AND COALESCE(pj.jd_text, '') <> '')                  AS has_jd,
               EXISTS (SELECT 1 FROM postings pj WHERE pj.job_id = a.job_id
                        AND (pj.salary_min IS NOT NULL OR pj.salary_raw IS NOT NULL)) AS has_salary,
               x.visa_signal, x.work_mode, x.seniority AS jd_seniority,
               COALESCE(x.tech, '{}')                                       AS tech,
               CASE WHEN s.status = 'rejected' THEN @SCREEN@ END            AS screen,
               @FORM_VISA@                                                  AS form_visa,
               @VISA_GROUP@                                                 AS visa_group,
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
    """.replace("@SCREEN@", screen_sql("a.id")).replace("@LATEST_EXTRACTION@", LATEST_EXTRACTION)
       .replace("@AWAITING@", awaiting_you_sql("a", "s.status"))
       .replace("@FORM_VISA@", answers.form_visa_sql("a.id"))
       .replace("@VISA_GROUP@", jd_visa_group_sql("x.visa_signal")),
        {"user_id": user_id}).fetchall()
    events = conn.execute("""
        SELECT e.id, e.application_id, e.type, e.source, e.occurred_at, e.created_at,
               CASE WHEN e.type = 'applied'
                    THEN (e.payload->>'external')::bool END  AS external,
               e.payload->>'reason'                          AS reason,
               (e.payload ? 'superseded_by')                 AS superseded,
               e.payload->>'closed'                          AS closed_as,
               e.payload->>'went'                            AS went,
               (e.payload->>'went_at')::timestamptz          AS went_at,
               e.payload->>'stated_date'                     AS stated_date,
               e.payload->>'round_is'                        AS round_is,
               e.payload->>'round_kind'                      AS round_kind,
               e.payload->>'invite_role'                     AS invite_role,
               em.extraction->>'platform'                    AS mail_platform
        FROM events e
        JOIN applications a ON a.id = e.application_id
        LEFT JOIN emails em ON em.id = e.source_email_id
        WHERE a.user_id = %(user_id)s
        ORDER BY e.occurred_at, e.created_at
    """, {"user_id": user_id}).fetchall()
    return apps, events
