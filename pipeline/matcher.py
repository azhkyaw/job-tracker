"""Email -> application matching (design doc §8) and record creation.

dispatch() is the single entry point the worker calls after stage-2
extraction. Outcomes:

  auto    — confident match: event appended, email auto_matched
  create  — confirmation (or the user's own emailed application,
            sent_application) with no candidate: new job + application +
            email_only posting created (the backfill path; posting has no JD
            text, which is the derived "needs_enrichment" state)
  pending — everything uncertain: email lands in the triage queue, no writes
            to applications/events (never a silent guess)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import config
from .email_classifier import Extraction, norm_company
from .ingest import UNKNOWN_TITLE

# Classification type -> event type. status_update refines via status_detail.
EVENT_TYPE = {
    "confirmation": "confirmation",
    "rejection": "rejected",
    "interview_invite": "interview_invite",
    "recruiter_outreach": "recruiter_outreach",
    "other": "note",
    # Mail the USER sent (migration 016). Every one is something the user did —
    # none is an employer's outcome, so a reply in an interview thread can no
    # longer file as the employer inviting them (11 of 26 real sent emails did,
    # before 24 Sep 2026). A reply changes no status; a follow-up is the
    # follow_up_sent the reminders queue has always been waiting for.
    "sent_application": "applied",
    "sent_follow_up": "follow_up_sent",
    "sent_reply": "note",
    "sent_withdrawal": "withdrawn",
}

# Classifications that may mint a record when nothing matches: an employer's
# receipt, or the user's own application sent by email. Either is proof an
# application exists; everything else waits in triage for a human.
_CREATES = {"confirmation", "sent_application"}

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

# The company gate. Word containment — one name's words inside the other's,
# either way round — has been part of it since 24 Sep 2026; until then it was
# only rescue rule 2 (below), which runs when the gate admits NOBODY, so one
# sibling name clearing the gate hid the record containment would have found.
# Measured that day, all 432 filed emails replayed against the live records
# (placeholder names, real measurements): mail under an employer's short name
# could no longer reach the two applications stored as "wide world group" once
# two others were stored as "wide world"; a recruiter's "margie consulting
# group" reached two UNRELATED consulting groups (similarity >= 0.6) and never
# its own record, stored under that name plus a tagline; and mail from an
# agency's "trey talent" auto-matched a REJECTED lead of that name while the
# live interview thread, stored "trey talent x woodgrove bank", was not even
# a candidate. Admitting containment in the gate
# changed 5 decisions: 2 wrong -> right, 2 triage -> right, 1 right -> triage
# (two name forms of one agency then compete on the margin), none wrong.
# On 21 Aug the same change measured +1/-1 — `.claude/rules/matching.md` had
# asked for a re-measure if suppression grew, and it had.
_CANDIDATES_SQL = _CANDIDATES_BASE + """(j.company_norm = %(company)s
       OR similarity(j.company_norm, %(company)s) >= %(cmin)s
       OR string_to_array(j.company_norm, ' ') @> string_to_array(%(company)s, ' ')
       OR string_to_array(j.company_norm, ' ') <@ string_to_array(%(company)s, ' '))
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
#
# Measured a second time, 23 Sep 2026, after rule 1 DID make the silent wrong
# match the paragraph above only feared. A recruiter's mail from "Woodgrove"
# (company_sim 0.417 against the extension's "Woodgrove Southeast Asia",
# under the gate) carried the title "Full Stack AI Engineer" — the exact title
# of an unrelated agency's application (company_sim 0.077). Rule 2 admitted
# the real Woodgrove record, rule 1 admitted the agency, and because company
# similarity is only a gate the TITLE decided it: 1.0 against 0.575 (the real
# posting reads "Full Stack Engineer – Generative AI & Agentic AI"), 0.875
# against 0.662, a 0.213 margin over the 0.15 bar. Four emails of a live
# interview thread landed on the agency, which then read `interview_invite`
# for an interview it never gave while the real application read
# `confirmation`. The margin is no defence when the true record's title is
# worded differently from the mail's.
#
# What every REAL rule-1 rescue had in common, and the stranger did not: the
# two company names shared a word — contoso/"Contoso Markets",
# fabrikam/"Fabrikam", litware/"Litware International". So rule 1 now also
# requires one shared word of config.RESCUE_SHARED_WORD_MIN letters (the
# tokens norm_company leaves behind — "x", "&", "pte", "the", "ai", "it" —
# are all shorter). Replayed over all 427 stored, extracted emails against
# that day's DB, comparing decisions with the rule as it stood: 11 change.
# 7 silent wrong auto-matches become triage items (the four Woodgrove mails,
# and three older mails a human had already filed by hand that the old rule
# would today send to an agency holding the same title); 2 hand-resolved
# emails become correct auto-matches (their same-titled strangers — 15 and
# 16 of them — no longer break the margin); and 2 correct auto-matches become
# triage items (a subsidiary's coding-test mails, whose parent-company sender
# shares no word with the subsidiary's name). "Drop the rule-1 candidates
# whenever rule 2 admits anyone" was measured alongside and is strictly
# weaker: same gains, but it leaves the fourth Woodgrove mail — whose
# extraction read "Woodgrove Singapore", neither a subset nor a superset of
# "woodgrove southeast asia" — on the agency.
#
# Since 24 Sep 2026 rule 2 lives in the gate itself (see _CANDIDATES_SQL), so
# this runs only when no name is even contained in another's and carries rule
# 1 alone — kept here, rule 2 could only find rows the gate had already
# admitted. Rule 1 stays a RESCUE on purpose: admitting "same title, any
# company that shares a word" to the gate was the variant measured as much
# worse on 21 Aug (29 correct matches lost to same-titled strangers).
_CANDIDATES_RESCUE_SQL = (_CANDIDATES_BASE + """(
          -- rule 1: the same title, exactly, AND one company word in common
          -- (a stranger with no company resemblance cannot be rescued by its
          -- title alone — see "measured a second time" above)
          %(title)s::text IS NOT NULL
          AND lower(btrim(coalesce(j.title_canonical, '')))
            = lower(btrim(%(title)s))
          AND EXISTS (SELECT 1 FROM unnest(string_to_array(j.company_norm, ' ')) AS w(word)
                      WHERE length(w.word) >= __WMIN__
                        AND w.word = ANY(string_to_array(%(company)s, ' ')))
      )
""").replace("__WMIN__", str(int(config.RESCUE_SHARED_WORD_MIN)))


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
        # The company gate admitted nobody, not even by word containment — fall
        # back to an exact title under a name that shares a word, so a
        # rebranded sender can still reach its own application rather than
        # silently minting a second one. This can only ADD candidates where
        # there were none; AUTO_MATCH_SCORE and AUTO_MATCH_MARGIN still decide
        # the outcome. See _CANDIDATES_RESCUE_SQL.
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

def _event_time(email_row) -> datetime:
    """When an email's event happened: when the email arrived — or, for mail
    the user sent, when they sent it; `received_at` holds either. Always.

    A date the email STATES goes in the event's payload as `stated_date`
    (_append_event), never into its time. Until 24 Sep 2026 a stated date won
    (design doc §8), and the extractor's `event_date` is "a date FOR THE EVENT
    it describes" (email_extract_v1 rule 4) — which for an interview invite is
    the INTERVIEW day, for a rejection may be the day the application was
    submitted, and for a LinkedIn "new activity" digest was a misread
    "Applied on 29 Jul" (as 2 Jul). Measured on the real DB that day: 14 of
    445 received-mail events sat off their email's arrival — ten invites
    pushed 1-21 days ahead (two into the future, which stretches the trace
    axis past "today" and inflates avg_days_to_resp), a rejection filed a
    month back on the apply day, where it sorted BEFORE its own applied event,
    and three notes, two of them before the search began. Mail the user sent
    had been exempted hours earlier for the same reason (a reply confirming a
    slot, filed three weeks in the future). No reading of the date survives
    every case, because nothing says which event the date is FOR; arrival is
    the one fact the email always carries.

    This is also find_match's date signal, scored against the application's
    applied_at. Replayed over the 14 emails the change could move: 2
    decisions changed, one each way and neither wrong — a hand-linked
    interview invite (dated 20 days ahead) would now auto-match its own
    application, and a rejection quoting its submission date would now wait
    in triage (0.735 against the 0.75 bar) instead of matching on that date:
    it is scored like every other rejection that arrives a month late."""
    return email_row["received_at"]


def _event_type(classification: str, extraction: Extraction) -> tuple[str, dict]:
    if classification == "status_update":
        if extraction.status_detail == "viewed":
            return "viewed", {}
        return "note", {"status_detail": extraction.status_detail}
    return EVENT_TYPE[classification], {}


def _append_event(conn, user_id, application_id, email_row, classification,
                  extraction: Extraction) -> None:
    etype, payload = _event_type(classification, extraction)
    if extraction.event_date:
        # What the email said, kept beside the event rather than moving it
        # (_event_time) — the detail page shows it when it is still ahead of
        # the email's arrival: the interview an invitation is for.
        payload["stated_date"] = extraction.event_date
    if etype == "applied" and conn.execute(
            "SELECT 1 FROM events WHERE application_id = %s AND type = 'applied'",
            (application_id,)).fetchone():
        # A resume the user emails for a role already on record — applied on the
        # platform, then sent to the recruiter too — belongs to that
        # application; it does not start a second one. Two `applied` events on
        # one record is the recording artefact the analytics would count as two
        # starts, so the message files as a note on the timeline instead.
        etype = "note"
    from psycopg.types.json import Json
    conn.execute(
        """
        INSERT INTO events (user_id, application_id, type, source, occurred_at,
                            source_email_id, payload)
        VALUES (%s, %s, %s, 'email', %s, %s, %s)
        """,
        (user_id, application_id, etype,
         _event_time(email_row), email_row["id"], Json(payload)),
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
    title = extraction.role_title or UNKNOWN_TITLE
    platform = extraction.platform if extraction.platform in ("linkedin", "jobstreet", "indeed") else "other"
    occurred_at = _event_time(email_row)

    job = conn.execute(
        "INSERT INTO jobs (user_id, company_norm, title_canonical) VALUES (%s, %s, %s) RETURNING id",
        (user_id, company_norm, title),
    ).fetchone()
    posting = conn.execute(
        """
        INSERT INTO postings (user_id, job_id, platform, company_raw, company_norm,
                              title, ats, captured_via, captured_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'email_only', %s)
        RETURNING id
        """,
        # company_norm beside company_raw, as ingest.upsert_record stores it:
        # dedup reads the posting's own, and until 24 Sep 2026 29 of 32
        # email-made postings had none.
        (user_id, job["id"], platform, extraction.company, company_norm or None,
         extraction.role_title, extraction.ats, occurred_at),
    ).fetchone()
    app = conn.execute(
        """
        INSERT INTO applications (user_id, job_id, applied_via_posting_id, origin)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (user_id, job["id"], posting["id"], origin),
    ).fetchone()
    # Not when the email's OWN event is the application (the user's resume,
    # sent_application): _append_event below files that `applied`, and a
    # fabricated one alongside it would be the same start recorded twice.
    if classification != "recruiter_outreach" and EVENT_TYPE.get(classification) != "applied":
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
    occurred_at = _event_time(email_row)

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
                and classification in _CREATES \
                and norm_company(extraction.company or ""):
            result = MatchResult("create",
                                 _create_application(conn, user_id, email_row, extraction,
                                                     classification))
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
