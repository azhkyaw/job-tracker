"""/analytics' statistics and geometry — pure, no database.

insights.py and charts.py take rows and return numbers and coordinates, so
every claim the page makes is checked here on hand-built rows: the
Kaplan-Meier curve against a hand computation (and against the plain rate it
exists to replace), Wilson bounds, the listing-age parser, the colour a
square wears against trace.live()/heat(), the comparison registry LOOPED
(CLAUDE.md: a registry needs a test that loops it), the flow's conservation
of every record, and calendar bucketing in the viewer's zone.

The page's numbers agreeing with the SQL the LIST counts by (summary,
rejection_ends) is checked against a real database in tests/test_web.py.

Run:  python3 tests/test_insights.py
"""

import itertools
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import analytics, answers, charts, insights, jd_extraction, trace   # noqa: E402
from pipeline.ingest import UNKNOWN_COMPANY               # noqa: E402


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
SGT = ZoneInfo("Asia/Singapore")
_ids = itertools.count(1)


def app(**kw):
    a = {"id": f"a{next(_ids)}", "origin": "applied", "resume_file": None,
         "status": "applied", "company_norm": "northwind labs",
         "title_canonical": "Backend Engineer", "company_display": "Northwind Labs",
         "platform": "linkedin", "ats": None, "posted_label": None, "reposted": None,
         "has_jd": True, "has_salary": False, "visa_signal": None, "work_mode": None,
         "tech": [], "extracted": False, "n_answers": 0}
    a.update(kw)
    return a


def ev(a, type_, when, **kw):
    e = {"application_id": a["id"], "type": type_, "source": "email",
         "occurred_at": when, "created_at": when, "external": None, "reason": None,
         "superseded": False, "mail_platform": None}
    e.update(kw)
    return e


def ago(days_, hours=0):
    return NOW - timedelta(days=days_, hours=hours)


def facts_of(apps, events, reminder_days=10):
    return insights.build_facts(apps, events, NOW, reminder_days)


# ------------------------------------------------------------------ helpers

print("wilson")
lo, hi = insights.wilson(0, 10)
check("0 of 10 starts at 0 and stays inside 0..1", lo == 0.0 and 0.25 < hi < 0.31, (lo, hi))
lo, hi = insights.wilson(5, 10)
check("5 of 10 is symmetric about a half", abs((lo + hi) / 2 - 0.5) < 1e-9 and 0.2 < lo < 0.26, (lo, hi))
check("no sample is the whole range", insights.wilson(0, 0) == (0.0, 1.0))
check("more data, narrower interval",
      insights.wilson(50, 100)[1] - insights.wilson(50, 100)[0]
      < insights.wilson(5, 10)[1] - insights.wilson(5, 10)[0])

print("kaplan-meier")
curve = insights.kaplan_meier([(1, True), (2, False), (3, True), (4, True)])
want = [(0.0, 0.0), (1, 0.25), (3, 0.625), (4, 1.0)]
check("matches the hand computation (a censored one leaves the pool)",
      all(t == wt and abs(f - wf) < 1e-9 for (t, f), (wt, wf) in zip(curve, want))
      and len(curve) == len(want), curve)
curve = insights.kaplan_meier([(2, True), (2, False), (5, True)])
check("an event and a censoring at the same time: the censored one was still at risk",
      abs(curve[1][1] - 1 / 3) < 1e-9 and curve[-1][1] == 1.0, curve)
# The reason the estimator exists: two replied in a day, two were sent
# twelve hours ago. The plain rate says half; nobody has been ignored.
curve = insights.kaplan_meier([(1, True), (1, True), (0.5, False), (0.5, False)])
check("yesterday's application is 'not yet', not 'never'", curve[-1][1] == 1.0, curve)
check("cdf reads the last step at or before t",
      insights.cdf(want, 3.5) == 0.625 and insights.cdf(want, 0.5) == 0.0)
check("still_chance: of those silent at 1, the share that heard later",
      abs(insights.still_chance(want, 1) - (1.0 - 0.25) / 0.75) < 1e-9
      and insights.still_chance(want, 4) == 0.0)
check("still_chance within a horizon",
      abs(insights.still_chance(want, 1, horizon=2) - (0.625 - 0.25) / 0.75) < 1e-9)
# F: .25 at day 1, .3 at day 3, .32 at day 5 (the end). Of those silent at
# day 1, (.32-.25)/.75 = 9% still hear back; at day 3, .02/.7 = 3%.
_qc = [(0.0, 0.0), (1, .25), (3, .3), (5, .32)]
check("quiet_after: the first day from the threshold on where under 5% still hear back",
      insights.quiet_after(_qc, 1) == 3 and insights.quiet_after(_qc, 4) == 4
      and insights.quiet_after(_qc, 1, chance=.5) == 1
      and insights.quiet_after([], 10) is None and insights.QUIET_CHANCE == 0.05,
      [insights.quiet_after(_qc, d) for d in (1, 4)])

print("quantile and days_text")
check("median of an even list interpolates", insights.quantile([1, 2, 3, 4], .5) == 2.5)
check("quantile of nothing is None", insights.quantile([], .5) is None)
check("durations read as a person says them",
      insights.days_text(0.44) == "11 hours" and insights.days_text(3.0004) == "3 days"
      and insights.days_text(4.06) == "4.1 days" and insights.days_text(21.6) == "22 days"
      and insights.days_text(1) == "1 day" and insights.days_text(None) == "—",
      [insights.days_text(x) for x in (0.44, 3.0004, 4.06, 21.6, 1)])

print("listing age")
for label, want_days in [("2 weeks ago", 14), ("Reposted 3 days ago", 3), ("30+ days ago", 30),
                         ("1 month ago", 30), ("5 hours ago", 5 / 24), ("Just now", 0.0),
                         (None, None), ("sometime", None)]:
    got = insights.listing_age_days(label)
    check(f"{label!r} -> {want_days}",
          got == want_days if want_days is None else abs(got - want_days) < 1e-9, got)
check("buckets are inclusive at the bottom: a week is '1 to 2 weeks'",
      insights._bucket(7, insights.AGE_BUCKETS) == "1 to 2 weeks"
      and insights._bucket(30, insights.AGE_BUCKETS) == "a month or more")

# ----------------------------------------------------------------- facts

print("facts: clocks, answers, the closing event")
a1 = app()
a2 = app(origin="inbound", status="interview_invite")
a3 = app(status="rejected")
events = [
    ev(a1, "applied", ago(20), source="extension", external=False),
    ev(a1, "confirmation", ago(20)),
    ev(a1, "viewed", ago(19)),
    ev(a2, "recruiter_outreach", ago(12)),
    ev(a2, "applied", ago(11), source="manual", external=True),
    ev(a2, "interview_invite", ago(3)),
    ev(a3, "applied", ago(30), source="extension", external=True),
    ev(a3, "rejected", ago(27), mail_platform="linkedin"),
    ev(a3, "rejected", ago(26), source="manual", reason="visa"),
]
fa = {f["id"]: f for f in facts_of([a1, a2, a3], events)}
f1, f2, f3 = fa[a1["id"]], fa[a2["id"]], fa[a3["id"]]
check("a sent application's clock starts at its submission",
      f1["sent"] and f1["start"] == ago(20) and abs(f1["lag"] - 1) < 1e-9, f1["lag"])
check("viewed is heard back but not answered",
      f1["signal_type"] == "viewed" and f1["answer_at"] is None)
check("an approach is not 'sent', and its clock starts at the approach",
      not f2["sent"] and f2["start"] == ago(12) and f2["round_at"] == ago(3))
check("the closing rejection is the newest carrying a reason (rejection_reasons' rule)",
      f3["reason"] == "visa" and f3["close"]["source"] == "manual" and f3["how"] == "visa")
check("the how-bucket is analytics.rejected_how's",
      analytics.rejected_how(None, True) == "after_round"
      and analytics.rejected_how(None, False) == "no_round"
      and analytics.rejected_how("visa", True) == "visa")
check("a screen is the mechanism and wins over any reason recorded on it",
      analytics.rejected_how("visa", False, "sponsorship") == "sponsorship_screen"
      and analytics.rejected_how(None, False, "form") == "form_screen"
      and analytics.rejected_how("visa", False, None) == "visa")
check("every bucket rejected_how can return has a label, in the chips' fixed order",
      {analytics.rejected_how(r, h, sc) for r in (None, "visa") for h in (True, False)
       for sc in (None, "form", "sponsorship")} == set(analytics.HOW_LABELS)
      and list(analytics.HOW_LABELS)[-1] == "no_round")
screened = app(status="rejected", screen="sponsorship")
fs_ = {f["id"]: f for f in facts_of([screened], [
    ev(screened, "applied", ago(30), source="extension"),
    ev(screened, "rejected", ago(27), mail_platform="linkedin", reason="visa")])}
check("facts carry the fetched screen into the bucket",
      fs_[screened["id"]]["how"] == "sponsorship_screen")

print("how a round went: your rating beside what came of it (8 Oct 2026)")
b0 = app()                      # no round sat
b1 = app(status="rejected")     # rated badly before the rejection
b2 = app(status="withdrawn")    # two invitations, the later rated well, then silence
b3 = app(status="engaged")      # a call, unrated, waiting
b4 = app(status="rejected")     # rated after the rejection: hindsight
b5 = app(status="offer")        # rated well, then an offer
b6 = app(status="withdrawn")    # rated mixed, then you declined
events = [
    ev(b0, "applied", ago(20)),
    ev(b1, "applied", ago(30)), ev(b1, "interview_invite", ago(20), went="badly", went_at=ago(18)),
    ev(b1, "rejected", ago(10)),
    ev(b2, "applied", ago(40)), ev(b2, "interview_invite", ago(30)),
    ev(b2, "interview_invite", ago(29), went="well", went_at=ago(27)),
    ev(b2, "withdrawn", ago(2), source="manual", closed_as="went_quiet"),
    ev(b3, "applied", ago(12)), ev(b3, "engaged", ago(4)),
    # A rejection filed by hand at noon; the interview's own mail that evening.
    ev(b4, "applied", ago(30)), ev(b4, "rejected", ago(10), source="manual"),
    ev(b4, "interview_invite", ago(10, hours=-6), went="badly", went_at=ago(1)),
    ev(b5, "applied", ago(30)), ev(b5, "interview_invite", ago(20), went="well", went_at=ago(19)),
    ev(b5, "offer", ago(5)),
    ev(b6, "applied", ago(30)), ev(b6, "engaged", ago(20), went="mixed", went_at=ago(19)),
    ev(b6, "withdrawn", ago(15), source="manual", closed_as="declined"),
]
fb = {f["id"]: f for f in facts_of([b0, b1, b2, b3, b4, b5, b6], events)}


def went_of(b):
    f = fb[b["id"]]
    return f["went"], f["went_next"], f["went_hindsight"]


check("rated before the rejection: badly, rejected, no hindsight",
      went_of(b1) == ("badly", "rejected", False), went_of(b1))
check("the newest rated round is the anchor; silence after it is 'went quiet'",
      went_of(b2) == ("well", "quiet", False), went_of(b2))
check("an unrated call is waiting, with no word", went_of(b3) == (None, "waiting", False), went_of(b3))
check("a close filed before the interview's own mail is still what came of it, and a rating "
      "after that close is hindsight", went_of(b4) == ("badly", "rejected", True), went_of(b4))
check("an offer after it is a further round", went_of(b5) == ("well", "progressed", False), went_of(b5))
check("declining is a close of your own, not silence", went_of(b6) == ("mixed", "ended", False), went_of(b6))
check("no round sat: nothing to say", went_of(b0) == (None, None, False), went_of(b0))
check("every outcome _went can name has a column, and the fixture reaches each",
      {f["went_next"] for f in fb.values() if f["went_next"]} == set(insights.NEXT_LABELS),
      {f["went_next"] for f in fb.values()})
iv = insights.interviews(list(fb.values()))
check("the panel: rows are the words then the unrated, columns the outcomes, in order",
      [r["key"] for r in iv["rows"]] == [*analytics.WENT_LABELS, "unrated"]
      and [c["key"] for c in iv["cols"]] == list(insights.NEXT_LABELS), iv["rows"])


def cell(rk, ck):
    return next(c for r in iv["rows"] if r["key"] == rk for c in r["cells"] if c["next"] == ck)


check("each record sits in exactly its cell, as a square linking to it",
      cell("badly", "rejected")["n"] == 2 and cell("well", "quiet")["n"] == 1
      and cell("unrated", "waiting")["n"] == 1 and cell("well", "progressed")["n"] == 1
      and cell("mixed", "ended")["n"] == 1 and cell("well", "rejected")["n"] == 0
      and {u["id"] for u in cell("badly", "rejected")["units"]} == {b1["id"], b4["id"]},
      [(r["key"], [c["n"] for c in r["cells"]]) for r in iv["rows"]])
check("the totals: six records with a round, five rated, one in hindsight, and the margins sum",
      iv["n"] == 6 and iv["rated"] == 5 and iv["hindsight"] == 1
      and sum(r["n"] for r in iv["rows"]) == 6 and sum(c["n"] for c in iv["cols"]) == 6,
      {k: iv[k] for k in ("n", "rated", "hindsight")})
check("no record with a round sat: no panel", insights.interviews([fb[b0["id"]]]) is None)
print("rounds: one interview is several events (8 Oct 2026)")
# The real shapes: an invitation and its calendar notification naming one
# day; a test then an interview on two days; a reply arranging an interview,
# dateless, days before it; an assessment with no day at all, weeks apart; a
# call, which is not a round.
r1 = app(status="interview_invite")
r_evs = [
    ev(r1, "applied", ago(60)),
    ev(r1, "interview_invite", ago(50)),                                   # an assessment, no day named
    ev(r1, "interview_invite", ago(31), stated_date=ago(30).date().isoformat()),
    ev(r1, "interview_invite", ago(30), stated_date=ago(30).date().isoformat(), went="well"),
    ev(r1, "interview_invite", ago(22)),                                   # the reply arranging the next
    ev(r1, "interview_invite", ago(21), stated_date=ago(20).date().isoformat()),
    ev(r1, "engaged", ago(10)),                                            # a call
    ev(r1, "offer", ago(5)),
]
rds = trace.rounds(r_evs)
check("three rounds: the assessment, the interview with its notification, the next interview",
      [r["n"] for r in rds] == [1, 2, 3] and [len(r["events"]) for r in rds] == [1, 2, 2]
      and [r["named"] for r in rds] == [False, True, True]
      and [r["day"] for r in rds] == [ago(50).date(), ago(30).date(), ago(20).date()],
      [(r["n"], r["day"], len(r["events"]), r["named"]) for r in rds])
check("a round's rating is the one on any of its events, and the select goes there; an unrated "
      "round's goes to its newest event",
      rds[1]["went"] == "well" and rds[1]["rate_event"] is None
      and rds[0]["went"] is None and rds[2]["went"] is None)
check("a call and an offer are not rounds; a thread without an invitation has none",
      all(e["type"] == "interview_invite" for r in rds for e in r["events"])
      and trace.rounds([ev(r1, "applied", ago(3)), ev(r1, "engaged", ago(1))]) == [])
check("a dateless event joins the nearest round within ROUND_SPAN_DAYS, on either side",
      len(trace.rounds([ev(r1, "interview_invite", ago(9), stated_date=ago(5).date().isoformat()),
                        ev(r1, "interview_invite", ago(8)),                # 3 days before: joins
                        ev(r1, "interview_invite", ago(1))])) == 2)         # 4 days after: its own
check("a date-shaped string that is no date is dateless, not an error",
      len(trace.rounds([ev(r1, "interview_invite", ago(9), stated_date="2026-02-31"),
                        ev(r1, "interview_invite", ago(8))])) == 1)
check("a round's events are ordered oldest first whatever order they came in",
      [e["occurred_at"] for e in trace.rounds(list(reversed(r_evs)))[1]["events"]] == [ago(31), ago(30)])
check("a round you filed by hand is its own, even on a day a mail invitation named, and a dateless "
      "mail never joins it; same day, numbered by when each was first heard of",
      [(r["n"], r.get("own", False), len(r["events"])) for r in trace.rounds([
          ev(r1, "interview_invite", ago(20), stated_date=ago(10).date().isoformat()),   # a test due ago(10)
          ev(r1, "interview_invite", ago(10), source="manual"),                           # a screen that day
          ev(r1, "interview_invite", ago(9))])]                                           # a reminder: joins the test
      == [(1, False, 2), (2, True, 1)]
      and insights._same_round(ev(r1, "interview_invite", ago(10), source="manual"),
                               ev(r1, "interview_invite", ago(10))) is False)
check("a round's kind is the rated event's, else the newest event's that names one",
      trace.rounds([ev(r1, "interview_invite", ago(9), stated_date=ago(5).date().isoformat(), round_kind="screen"),
                    ev(r1, "interview_invite", ago(8), stated_date=ago(5).date().isoformat())])[0]["kind"] == "screen"
      and trace.rounds([ev(r1, "interview_invite", ago(9), round_kind="screen"),
                        ev(r1, "interview_invite", ago(8), round_kind="technical")])[0]["kind"] == "technical"
      and trace.rounds([ev(r1, "interview_invite", ago(9), went="well", round_kind="screen"),
                        ev(r1, "interview_invite", ago(8), round_kind="technical")])[0]["kind"] == "screen"
      and trace.rounds([ev(r1, "interview_invite", ago(9), payload={"round_kind": "final"})])[0]["kind"] == "final"
      and set(analytics.ROUND_KINDS) >= {"test", "screen", "technical"})
q_evs = [ev(r1, "applied", ago(30)),
         ev(r1, "interview_invite", ago(20), stated_date=ago(15).date().isoformat()),          # a questionnaire's invitation
         ev(r1, "interview_invite", ago(16), stated_date=ago(15).date().isoformat(), round_kind="questionnaire"),  # its reminder, so labelled
         ev(r1, "interview_invite", ago(8), round_kind="technical", went="well")]
q_all = trace.rounds(q_evs, counting_only=False)
check("a questionnaire is labelled but not counted: its round keeps its lines and loses its number, "
      "the technical is round 1 of 1, and _went anchors past the questionnaire's lines",
      [(r["counts"], r["n"], r["kind"], len(r["events"])) for r in q_all]
      == [(False, None, "questionnaire", 2), (True, 1, "technical", 1)]
      and [r["n"] for r in trace.rounds(q_evs)] == [1]
      and insights._went(q_evs)["went"] == "well"
      and insights._went(q_evs[:3])["went_next"] is None
      and set(trace.NON_ROUND_KINDS) < set(analytics.ROUND_KINDS),
      [(r["counts"], r["n"], r["kind"], len(r["events"])) for r in q_all])
# What the mail did (stage 4's role, 9 Oct 2026): a reschedule moves the
# round last arranged, a reminder confirms the one naming its day (or the
# last one, which takes the day), and scheduling chatter or a cancellation
# is no round at all — the four mails that made every over-count.
role_evs = [
    ev(r1, "applied", ago(40)),
    ev(r1, "interview_invite", ago(30), invite_role="scheduling"),                        # "share your availability"
    ev(r1, "interview_invite", ago(29), invite_role="invitation"),                        # sets it up, names no day
    ev(r1, "interview_invite", ago(21), invite_role="reminder", stated_date=ago(20).date().isoformat()),
    ev(r1, "interview_invite", ago(19), invite_role="cancellation", stated_date=ago(20).date().isoformat()),
    ev(r1, "interview_invite", ago(18), invite_role="reschedule", stated_date=ago(10).date().isoformat()),
    ev(r1, "interview_invite", ago(11), invite_role="reminder", stated_date=ago(10).date().isoformat()),
    ev(r1, "interview_invite", ago(5), invite_role="invitation", stated_date=ago(2).date().isoformat()),
]
rr_ = trace.rounds(role_evs)
check("roles: one round moved once, then a second — the chatter and the cancellation never counted",
      [(r["n"], r["day"], len(r["events"])) for r in rr_]
      == [(1, ago(10).date(), 4), (2, ago(2).date(), 1)]
      and trace.excluded(role_evs[1]) and trace.excluded(role_evs[4])
      and not trace.excluded(role_evs[2]) and trace.role_of(role_evs[5]) == "reschedule",
      [(r["n"], r["day"], len(r["events"])) for r in rr_])
check("a reminder with no round before it is the round; a legacy line with no role groups as before",
      [r["day"] for r in trace.rounds([ev(r1, "interview_invite", ago(9), invite_role="reminder",
                                           stated_date=ago(5).date().isoformat())])] == [ago(5).date()]
      and len(trace.rounds([ev(r1, "interview_invite", ago(9), stated_date=ago(5).date().isoformat()),
                            ev(r1, "interview_invite", ago(8))])) == 1)
check("_went anchors past the chatter and the cancellation, and a scheduling line is no further round",
      insights._went(role_evs)["went_next"] == "waiting"
      and insights._went(role_evs[:3] + [ev(r1, "interview_invite", ago(5), invite_role="scheduling")])
      ["went_next"] == "waiting")
check("a line you said is not a round leaves the rounds, flat column or payload, and leaves "
      "_went's anchor too",
      len(trace.rounds(r_evs[:2] + [dict(r_evs[2], round_is="none")] + r_evs[3:])) == 3
      and len(trace.rounds([ev(r1, "interview_invite", ago(9), payload={"round_is": "none"}),
                            ev(r1, "interview_invite", ago(1))])) == 1
      and insights._went([ev(r1, "applied", ago(9)),
                          ev(r1, "interview_invite", ago(5), went="well"),
                          ev(r1, "interview_invite", ago(2), round_is="none")])["went"] == "well"
      and insights._went([ev(r1, "applied", ago(9)),
                          ev(r1, "interview_invite", ago(2), round_is="none")])["went_next"] is None)
fr = {f["id"]: f for f in facts_of([r1, b0], r_evs + [ev(b0, "applied", ago(20))])}
check("facts carry the rounds and their count; none without an invitation",
      fr[r1["id"]]["n_rounds"] == 3 and len(fr[r1["id"]]["rounds"]) == 3 and fr[b0["id"]]["n_rounds"] == 0)
dp = insights.depth(list(fr.values()) + list(fb.values()))
check("how far you got: rows by rounds reached (3+ capped), the interviews panel's columns, "
      "each thread once",
      dp is not None and [r["key"] for r in dp["rows"]] == ["1", "2", "3"]
      and [c["key"] for c in dp["cols"]] == list(insights.NEXT_LABELS)
      and next(r for r in dp["rows"] if r["key"] == "3")["n"] == 1
      and sum(r["n"] for r in dp["rows"]) == dp["n"] == 1 + sum(1 for f in fb.values() if f["n_rounds"])
      and dp["rounds"] == 3 + sum(f["n_rounds"] for f in fb.values()),
      [(r["key"], r["n"]) for r in dp["rows"]])
check("no thread with a round: no grid", insights.depth([fr[b0["id"]]]) is None)

check("round_fate: the list's bucket over the two facts — every (word, outcome) pair lands in "
      "the registry, and the registry is reached in full",
      {analytics.round_fate(w, n) for w in (None, *analytics.WENT_LABELS) for n in insights.NEXT_LABELS}
      == set(analytics.ROUND_FATES)
      and analytics.round_fate("badly", "quiet") == analytics.round_fate("mixed", "quiet") == "lost_quiet"
      and analytics.round_fate("well", "quiet") == "unexplained"
      and analytics.round_fate(None, "quiet") == "quiet"
      and analytics.round_fate("well", "rejected") == "rejected"
      and analytics.round_fate(None, None) is None
      and all(f["round_fate"] == analytics.round_fate(f["went"], f["went_next"]) for f in fb.values())
      and set(analytics.LOST_FATES) < set(analytics.ROUND_FATES),
      {(w, n): analytics.round_fate(w, n) for w in (None, "well") for n in insights.NEXT_LABELS})

print("sponsorship: what an answer told the employer")
import json                                                   # noqa: E402
from pipeline import answers                                  # noqa: E402
_cases = json.load(open(Path(__file__).parent / "sponsorship_answers.json",
                        encoding="utf-8"))["cases"]
for c in _cases:
    check(f"{c['q'][:48]!r} -> {c['a'][:24]!r}: {c['needs']}",
          answers.declares_sponsorship(c["q"], c["a"]) is c["needs"])

print("email apply: a JD asking for the CV by email")
from pipeline import email_apply                              # noqa: E402
_ea_cases = json.load(open(Path(__file__).parent / "email_apply.json",
                           encoding="utf-8"))["cases"]
for c in _ea_cases:
    _f = email_apply.instruction(c["jd"])
    # ascii(): a case holds an emoji, and a Windows console is cp1252.
    check(f"{ascii(c['jd'][:56])}: {'owed' if c['owed'] else 'nothing owed'}",
          (_f is not None) is c["owed"] and (_f["to"] if _f else []) == c["to"]
          and (_f is None or _f["sentence"] in c["jd"]), _f)
check("a quoted sentence keeps its own full stop, and only its own",
      email_apply.instruction("Hi. Send your CV to a@b.example. Thanks!")["sentence"]
      == "Send your CV to a@b.example."
      and email_apply.instruction("Send your CV to a@b.example\nThanks")["sentence"]
      == "Send your CV to a@b.example")
check("a mailto names every address and the role, and needs no script",
      email_apply.mailto({"to": ["a@x.example", "b@x.example"]}, "AI Engineer & Lead")
      == "mailto:a@x.example,b@x.example?subject=Application%3A%20AI%20Engineer%20%26%20Lead")

print("facts: the colour of a square is the colour of its row on the list")
fresh_reply = app(status="viewed")
old_wait = app()
mine_last = app()
closed_note = app(status="rejected")
evs = [
    ev(fresh_reply, "applied", ago(8), source="extension"), ev(fresh_reply, "viewed", ago(2)),
    ev(old_wait, "applied", ago(40), source="extension"),
    ev(mine_last, "applied", ago(30), source="extension"), ev(mine_last, "viewed", ago(25)),
    ev(mine_last, "follow_up_sent", ago(2), source="manual"),
    ev(closed_note, "applied", ago(30), source="extension"), ev(closed_note, "rejected", ago(20)),
    ev(closed_note, "note", ago(1), source="manual"),
]
ff = {f["id"]: f for f in facts_of([fresh_reply, old_wait, mine_last, closed_note], evs)}
check("someone else moved last and it is fresh: live",
      ff[fresh_reply["id"]]["tone"] == "live"
      and trace.live("viewed", 2, 10))
check("a long silence is a wait with the list's heat",
      ff[old_wait["id"]]["tone"] == "wait"
      and ff[old_wait["id"]]["heat"] == trace.heat(40, 10) > 0)
check("the user moved last: not live, however fresh",
      ff[mine_last["id"]]["tone"] == "wait" and not trace.live("follow_up_sent", 2, 10))
check("closed is closed, even with a note after it: no silence is counted",
      ff[closed_note["id"]]["tone"] == "rejected" and ff[closed_note["id"]]["silent_days"] is None
      and ff[closed_note["id"]]["heat"] == 0 and ff[closed_note["id"]]["live"] is False,
      ff[closed_note["id"]])

print("trace: a thread ends at its close, whatever arrives after it (28 Sep 2026)")
# The three real shapes on /inbound: a note after a close; a recruiter writing
# again after dropping you; a rejection filed by hand for today, anchored at
# local noon BEFORE that afternoon's approach email. Each drew an open tail —
# blue when fresh — on a record whose status read rejected.
_shapes = {
    "note after": [("applied", ago(30)), ("rejected", ago(20)), ("note", ago(1))],
    "they wrote again": [("recruiter_outreach", ago(9)), ("rejected", ago(8)),
                         ("recruiter_outreach", ago(2))],
    "same-day, by hand": [("rejected", ago(0.3)), ("recruiter_outreach", ago(0.1))],
}
for name, seq in _shapes.items():
    r = {"id": name}
    trace.build([r], {name: [{"type": t, "occurred_at": at} for t, at in seq]}, NOW, 10)
    close_at = max(at for t, at in seq if t in trace.TERMINAL)
    check(f"{name}: capped at the close, no tail, not live, no heat, every event still drawn",
          r["cap"] is not None and r["tail"] is None and r["live"] is False
          and r["heat"] == 0 and r["silent_days"] is None and len(r["pts"]) == len(seq)
          and trace.closing([{"type": t, "occurred_at": at} for t, at in seq])["occurred_at"] == close_at,
          {k: r[k] for k in ("cap", "tail", "live", "heat", "silent_days")})
_open = {"id": "open"}
trace.build([_open], {"open": [{"type": "recruiter_outreach", "occurred_at": ago(2)}]}, NOW, 10)
check("an open approach still draws its live tail (the guard is the close, not the type)",
      _open["tail"] is not None and _open["cap"] is None and _open["live"] is True)
print("facts: an approach in the words the /inbound row uses (28 Sep 2026)")
_waiting = app(status="interested", origin="inbound", awaiting_you=True)
_answered = app(status="interested", origin="inbound", awaiting_you=False)
_declined = app(status="withdrawn", origin="inbound")
_quiet = app(status="withdrawn", origin="inbound")
_fp = {f["id"]: f for f in facts_of([_waiting, _answered, _declined, _quiet], [
    ev(_waiting, "recruiter_outreach", ago(3)),
    ev(_answered, "recruiter_outreach", ago(9)), ev(_answered, "note", ago(8), source="manual"),
    ev(_declined, "recruiter_outreach", ago(9)),
    ev(_declined, "withdrawn", ago(8), source="manual", closed_as="declined"),
    ev(_quiet, "recruiter_outreach", ago(30)),
    ev(_quiet, "withdrawn", ago(2), source="manual", closed_as="went_quiet")])}
check("a lead is awaiting your call until you reply, then it is one you answered",
      insights._phrase(_fp[_waiting["id"]]).startswith("awaiting your call")
      and insights._phrase(_fp[_answered["id"]]).startswith("you replied"),
      [insights._phrase(_fp[x["id"]]) for x in (_waiting, _answered)])
check("an approach you closed says how, not “withdrawn”",
      insights._phrase(_fp[_declined["id"]]) == "you declined"
      and insights._phrase(_fp[_quiet["id"]]) == "they went quiet"
      and _fp[_quiet["id"]]["tone"] == "withdrawn",
      [insights._phrase(_fp[x["id"]]) for x in (_declined, _quiet)])
check("closing() names the LATEST close when a thread has two",
      trace.closing([{"type": "rejected", "occurred_at": ago(9)},
                     {"type": "withdrawn", "occurred_at": ago(3)},
                     {"type": "note", "occurred_at": ago(1)}])["type"] == "withdrawn"
      and trace.closing([{"type": "applied", "occurred_at": ago(3)}]) is None
      and trace.closing([]) is None)
check("an offer is a round, not a close (7 Oct 2026): closing() passes over it, a close after "
      "it ends the thread, and the one list of open rounds holds it",
      trace.closing([{"type": "offer", "occurred_at": ago(3)}]) is None
      and trace.closing([{"type": "offer", "occurred_at": ago(9)},
                         {"type": "withdrawn", "occurred_at": ago(3)}])["type"] == "withdrawn"
      and "offer" not in insights.CLOSED and "offer" in analytics.OPEN_ROUND)
_offer = app(status="offer")
_fo = facts_of([_offer], [ev(_offer, "applied", ago(30), source="extension"),
                          ev(_offer, "offer", ago(16))])[0]
check("an offer in hand: not ended, green, counted, and waiting in the list's words",
      _fo["ended_at"] is None and _fo["tone"] == "offer" and _fo["offer"]
      and _fo["silent_days"] == 16 and insights._phrase(_fo) == "offer, quiet 16 days",
      (_fo["ended_at"], _fo["tone"], insights._phrase(_fo)))
check("the headline counts it among the threads in a round now, and as an offer",
      insights.headline([_fo], NOW)["live"] == 1 and insights.headline([_fo], NOW)["offers"] == 1)
check("trace.build sets the same `live` the template now reads",
      trace.build([{"id": "x"}], {"x": [{"type": "viewed", "occurred_at": ago(2)}]}, NOW, 10) is not None)
row = {"id": "x"}
trace.build([row], {"x": [{"type": "viewed", "occurred_at": ago(2)}]}, NOW, 10)
check("... and it is live for a fresh reply", row["live"] is True)
axis = trace.build([{"id": "y"}], {"y": [{"type": "applied",
                                          "occurred_at": datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc)}]},
                   NOW, 10)
first_month = [float(t["x"].rstrip("%")) for t in axis["ticks"][1:]]
check("no month label crowds the start label: 27 Jul to 25 Sep drops 'Aug' at 7%",
      [t["label"] for t in axis["ticks"]][:2] == ["27 Jul", "Sep"] and all(x >= 12 for x in first_month),
      axis["ticks"])

# ----------------------------------------------------------------- timing

print("timing: the reply window and the forecast")
apps_t, evs_t = [], []
for i in range(12):                    # twelve replies, the slowest on day 9.5
    a = app()
    apps_t.append(a)
    evs_t += [ev(a, "applied", ago(40), source="extension"),
              ev(a, "rejected", ago(40 - (0.5 + i * 0.75)))]
for d in (2, 5, 20, 39):              # four still silent, of four ages
    a = app()
    apps_t.append(a)
    evs_t.append(ev(a, "applied", ago(d), source="extension"))
ft = facts_of(apps_t, evs_t)
check("the window is the longest wait on record, rounded up",
      insights.reply_window(ft) == math.ceil(0.5 + 11 * 0.75), insights.reply_window(ft))
check("fewer than MIN_TIMING_N replies: no window, no timing panel",
      insights.reply_window(ft[:5]) is None and insights.timing(ft[:5]) is None)
t = insights.timing(ft)
check("the waiting pool and the ones inside the window",
      t["waiting"] == 4 and t["inside"] == 2, (t["waiting"], t["inside"]))
check("nobody past the window is expected to hear, somebody inside is",
      0 < t["expect"] < 2 and t["expect_week"] <= t["expect"], (t["expect"], t["expect_week"]))
check("the curve's marks are the median and the window",
      [m["name"] for m in t["curve"]["marks"]] == ["median", "window"])

# ------------------------------------------------------------- comparisons

print("comparisons: every dimension in the registry produces rows")
rich, rich_ev = [], []
labels = ["1 day ago", "3 days ago", "2 weeks ago", "3 weeks ago", "2 months ago"]
for i in range(60):
    a = app(company_norm=f"co {i % 7}", company_display=f"Co {i % 7}",
            title_canonical=["Senior Full Stack Engineer", "Full Stack Engineer",
                             "Backend Engineer", "AI Engineer", ".NET Developer"][i % 5],
            resume_file=["resume-a.pdf", "resume-b.pdf"][i % 2],
            posted_label=labels[i % 5], reposted=bool(i % 3 == 0),
            visa_signal=["sponsors", "unclear", "no_sponsorship"][i % 3],
            work_mode=["hybrid", "onsite", "remote"][i % 3],
            ats=["workday", "greenhouse"][i % 2], platform="linkedin",
            tech=["Python", "LangChain" if i % 3 else "langchain"], extracted=True,
            n_answers=[2, 7, 12][i % 3])
    rich.append(a)
    t0 = ago(20 + i % 10, hours=i % 24)
    rich_ev.append(ev(a, "applied", t0, source="extension", external=bool(i % 2)))
    if i % 4 == 0:
        rich_ev.append(ev(a, "rejected", t0 + timedelta(days=3), mail_platform="linkedin"))
fr = facts_of(rich, rich_ev)
cmp_ = insights.compare(fr, NOW, SGT)
keys = {g["key"] for g in cmp_["groups"]}
for dim in insights.DIMENSIONS:
    check(f"dimension {dim['key']!r} has rows", dim["key"] in keys, sorted(keys))
check("the base rate is answered over settled", cmp_["n"] == 60 and cmp_["k"] == 15, (cmp_["n"], cmp_["k"]))
words = {r["label"] for g in cmp_["groups"] if g["key"] == "title_words" for r in g["rows"]}
check("co-occurring title words merge in title order", "full stack" in words, words)
check("a word in most titles is the baseline, not a row", "engineer" not in words, words)
tech = {r["label"] for g in cmp_["groups"] if g["key"] == "technology" for r in g["rows"]}
check("technology is spelled as most often written, and counted once per application",
      "LangChain" in tech and "langchain" not in tech, tech)
ages = [r["label"] for g in cmp_["groups"] if g["key"] == "listing_age" for r in g["rows"]]
check("an ordinal scale keeps its own order, not count order",
      ages == ["1 to 6 days", "1 to 2 weeks", "3 to 4 weeks", "a month or more"]
      or ages == [b for b, _ in insights.AGE_BUCKETS if b in ages], ages)

young = [app()]
young_ev = [ev(young[0], "applied", ago(3), source="extension")]
cy = insights.compare(facts_of(young, young_ev), NOW, SGT)
check("an application younger than SETTLED_DAYS is not counted", cy["n"] == 0 and cy["groups"] == [])

thin_apps = [app(ats="lever") for _ in range(3)] + [app(ats="ashby") for _ in range(8)]
thin_ev = [ev(a, "applied", ago(30), source="extension") for a in thin_apps]
thin_ev += [ev(a, "rejected", ago(28)) for a in thin_apps[3:9]]
ct = insights.compare(facts_of(thin_apps, thin_ev), NOW, SGT)
ats_rows = {r["label"]: r for g in ct["groups"] if g["key"] == "ats" for r in g["rows"]}
check("below MIN_RATE_N a row has counts and no rate (UI rule 7)",
      ats_rows["lever"]["thin"] and ats_rows["lever"]["rate"] is None and ats_rows["lever"]["n"] == 3)
check("at or above it, a rate", ats_rows["ashby"]["rate"] == 6 / 8)
check("a row stands out only when its whole interval clears the base",
      all(r["stands_out"] == (not r["thin"] and (r["lo"] > ct["base"] or r["hi"] < ct["base"]))
          for r in ats_rows.values()))
check("the chance count is a twentieth of the rated rows", ct["chance"] == round(0.05 * ct["rated"]))

print("findings: every row that clears, one line per comparison, the biggest sample first")


def frow(label, n, out=False, thin=False):
    return {"label": label, "n": n, "stands_out": out, "thin": thin}


fgroups = [   # registry order; the shape of 4 Oct 2026's real page
    {"key": "ats", "title": "hiring system", "rows": [
        frow("ashby", 8, out=True), frow("workday", 7, out=True),
        frow("greenhouse", 12), frow("lever", 1, thin=True)]},
    {"key": "reposted", "title": "repost", "rows": [
        frow("first posting", 147, out=True), frow("reposted", 41, out=True)]},
    {"key": "external", "title": "how you applied", "rows": [
        frow("on the platform", 197), frow("on the employer's site", 47, out=True)]},
    {"key": "weekday", "title": "day", "rows": [
        frow(d, n, out=d in ("Tuesday", "Thursday")) for d, n in
        (("Monday", 37), ("Tuesday", 43), ("Wednesday", 48), ("Thursday", 84))]},
    {"key": "visa", "title": "visa", "rows": [
        frow("no sponsorship", 10, out=True), frow("sponsors", 4, thin=True)]},
    {"key": "platform", "title": "platform", "rows": [frow("LinkedIn", 241)]},
    {"key": "hour", "title": "hour", "rows": [frow("in the afternoon", 8, out=True)]},
]
fl = insights.findings(fgroups)
check("ordered by the biggest sample that cleared, registry order breaking a tie",
      [x["key"] for x in fl] == ["reposted", "weekday", "external", "visa", "ats", "hour"],
      [(x["key"], x["n"]) for x in fl])
check("no cap: all nine rows that clear are on a line, and a comparison with none has no line",
      sum(1 for x in fl for r in x["rows"] if r["stands_out"]) == 9
      and "platform" not in {x["key"] for x in fl})
by_key = {x["key"]: [r["label"] for r in x["rows"]] for x in fl}
check("a two-valued comparison prints both sides, in its own order",
      by_key["reposted"] == ["first posting", "reposted"]
      and by_key["external"] == ["on the platform", "on the employer's site"], by_key)
check("...weighted by the side that cleared, not by the bigger side",
      next(x["n"] for x in fl if x["key"] == "external") == 47)
check("a side too thin to rate is not printed beside it", by_key["visa"] == ["no sponsorship"])
check("a many-valued comparison prints only the rows that clear, in its own order",
      by_key["weekday"] == ["Tuesday", "Thursday"] and by_key["ats"] == ["ashby", "workday"], by_key)
for name, cc in (("the rich fixture", cmp_), ("the thin one", ct)):
    outs = sorted((g["key"], r["label"]) for g in cc["groups"] for r in g["rows"] if r["stands_out"])
    lined = sorted((x["key"], r["label"]) for x in cc["findings"] for r in x["rows"] if r["stands_out"])
    check(f"compare() puts every row that clears on exactly one line, and counts them ({name})",
          lined == outs and cc["cleared"] == len(outs), (lined, outs))
check("file names shed the prefix they share and their extension, at a word boundary",
      insights.distinct_names(["Jane-Doe-resume-AI.pdf", "Jane-Doe-resume-dotnet.pdf",
                               "Jane-Doe_Software_Resume.pdf"])
      == ["resume-AI", "resume-dotnet", "Software_Resume"],
      insights.distinct_names(["Jane-Doe-resume-AI.pdf", "Jane-Doe-resume-dotnet.pdf",
                               "Jane-Doe_Software_Resume.pdf"]))
check("... never cutting inside a word, and a lone name keeps all but its extension",
      insights.distinct_names(["resume-ai.pdf", "resume-aws.pdf"]) == ["ai", "aws"]
      and insights.distinct_names(["cv.pdf"]) == ["cv"])
res = {r["label"]: r["full"] for g in cmp_["groups"] if g["key"] == "resume" for r in g["rows"]}
check("the comparison shows the short name and keeps the full one for its title",
      res == {"a": "resume-a.pdf", "b": "resume-b.pdf"}, res)

print("comparisons: day and hour are the viewer's")
late = app()   # 00:30 Monday in Singapore is 16:30 Sunday UTC
late_ev = [ev(late, "applied", datetime(2026, 8, 30, 16, 30, tzinfo=timezone.utc), source="extension")]
cl = insights.compare(facts_of([late], late_ev), NOW, SGT)
day = [r["label"] for g in cl["groups"] if g["key"] == "weekday" for r in g["rows"]]
hour = [r["label"] for g in cl["groups"] if g["key"] == "hour" for r in g["rows"]]
check("the weekday is Monday in Singapore, not Sunday in UTC", day == ["Monday"], day)
check("and the hour is after midnight", hour == ["after midnight"], hour)
manual = app()
manual_ev = [ev(manual, "applied", ago(30), source="manual")]
cm = insights.compare(facts_of([manual], manual_ev), NOW, SGT)
check("a typed date has no hour: no hour row for it",
      not [g for g in cm["groups"] if g["key"] == "hour"])

print("comparisons: the month you applied is a trend, oldest first")


def month_rows(apps_at, tz=SGT):
    ma = [app() for _ in apps_at]
    me = [ev(a, "applied", t, source="extension") for a, t in zip(ma, apps_at)]
    cm_ = insights.compare(facts_of(ma, me), NOW, tz)
    return [(r["label"], r["n"]) for g in cm_["groups"] if g["key"] == "month" for r in g["rows"]]


utc = timezone.utc
check("the first comparison in the registry, so the page opens on the trend",
      insights.DIMENSIONS[0]["key"] == "month")
mr = month_rows([datetime(2026, 7, 20, 3, tzinfo=utc)] + [datetime(2026, 8, d, 3, tzinfo=utc) for d in (3, 4, 5)])
check("months run oldest first, not by count", mr == [("July", 1), ("August", 3)], mr)
mr = month_rows([datetime(2026, 8, 31, 16, 30, tzinfo=utc)])          # 00:30 1 Sep in Singapore
check("the viewer's zone decides the month", mr == [("September", 1)], mr)
mr = month_rows([datetime(2026, 8, 31, 16, 30, tzinfo=utc)], tz=None)
check("...which in UTC is still August", mr == [("August", 1)], mr)
mr = month_rows([datetime(2025, 12, 20, 3, tzinfo=utc), datetime(2026, 1, 10, 3, tzinfo=utc)])
check("a search spanning two years names the year", mr == [("Dec 2025", 1), ("Jan 2026", 1)], mr)
mr = month_rows([ago(3), ago(20)])
check("only settled applications count, so the newest month may be missing",
      mr == [(f"{ago(20).astimezone(SGT):%B}", 1)], mr)

# ----------------------------------------------------------------- cohorts

print("cohorts")
c1, c2 = app(), app()
# c1 is 00:30 Monday 31 Aug in Singapore — 16:30 Sunday 30 Aug in UTC.
co_ev = [ev(c1, "applied", datetime(2026, 8, 30, 16, 30, tzinfo=timezone.utc), source="extension"),
         ev(c2, "applied", datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc), source="extension")]
co = insights.cohorts(facts_of([c1, c2], co_ev), NOW, SGT)
check("rows run week by week with the empty week kept, through this week",
      [r["label"] for r in co["rows"]] == ["31 Aug", "7 Sep", "14 Sep", "21 Sep"]
      and [r["n"] for r in co["rows"]] == [1, 0, 1, 0], [(r["label"], r["n"]) for r in co["rows"]])
utc_co = insights.cohorts(facts_of([c1], co_ev[:1]), NOW, None)
check("without a zone the same submission is the previous week (why the zone matters)",
      utc_co["rows"][0]["label"] == "24 Aug", utc_co["rows"][0]["label"])
c3, c4 = app(status="viewed"), app(status="rejected")
i1, i2 = app(origin="inbound", status="engaged"), app(origin="inbound", status="interested")
co3_ev = [ev(c3, "applied", ago(30), source="extension"), ev(c3, "viewed", ago(29)),
          ev(c4, "applied", ago(30), source="extension"), ev(c4, "rejected", ago(27)),
          ev(i1, "recruiter_outreach", ago(20)), ev(i1, "engaged", ago(19)),
          ev(i2, "recruiter_outreach", ago(18))]
co3 = insights.cohorts(facts_of([c3, c4, i1, i2], co3_ev), NOW, SGT)
wk = [r for r in co3["rows"] if r["n"]]
check("a week counts answered, and LinkedIn's viewed notice is not an answer (heard back is gone)",
      len(wk) == 1 and wk[0]["n"] == 2 and wk[0]["answered"] == 1 and wk[0]["rounds"] == 0
      and "heard" not in wk[0], wk)
check("the approaches row counts its own answers and rounds",
      len(co3["approaches"]) == 2 and co3["approach_answered"] == 1 and co3["approach_rounds"] == 1,
      (co3["approach_answered"], co3["approach_rounds"]))

# ------------------------------------------------------------------- flow

print("flow: every record is conserved")
fl_apps = [app(status="applied"), app(status="applied"), app(status="viewed"),
           app(status="rejected"), app(status="rejected"),
           app(origin="inbound", status="rejected"), app(origin="inbound", status="interested")]
fl_ev = []
for i, a in enumerate(fl_apps):
    if a["origin"] == "inbound":
        fl_ev.append(ev(a, "recruiter_outreach", ago(15 + i)))
    else:
        fl_ev.append(ev(a, "applied", ago([5, 50, 20, 30, 30][i]), source="extension"))
    if a["status"] == "viewed":
        fl_ev.append(ev(a, "viewed", ago(19)))
    if a["status"] == "rejected":
        fl_ev.append(ev(a, "rejected", ago(10), reason="visa" if i == 3 else None))
fl_facts = facts_of(fl_apps, fl_ev)
flow = insights.flow(fl_facts, 34, lambda s: s, analytics.HOW_LABELS)
nodes = {n["id"]: n for n in flow["nodes"]}
col = lambda c: [n for n in flow["nodes"] if n["col"] == c]          # noqa: E731
check("sources and statuses both sum to every record",
      sum(n["value"] for n in col(0)) == sum(n["value"] for n in col(1)) == len(fl_apps))
check("a status's children sum to it",
      nodes["inside"]["value"] + nodes["past"]["value"] == nodes["applied"]["value"]
      and sum(nodes[f"how_{k}"]["value"] for k in analytics.HOW_LABELS if f"how_{k}" in nodes)
      == nodes["rejected"]["value"])
check("waiting splits at the window", nodes["inside"]["value"] == 1 and nodes["past"]["value"] == 1)
check("each band from a source links the list page that shows exactly its rows",
      {l["href"] for l in flow["links"] if l["src"] == "inbound"}
      == {"/inbound?status=rejected", "/inbound?status=interested"})
for n in flow["nodes"]:
    outs = [l for l in flow["links"] if l["src"] == n["id"]]
    ins = [l for l in flow["links"] if l["dst"] == n["id"]]
    for side, ls, key in (("out", outs, "y0"), ("in", ins, "y1")):
        if ls:
            spans = sorted((l[key], l[key] + l["h"]) for l in ls)
            check(f"bands {side} of {n['id']} tile the node without overlap",
                  abs(spans[0][0] - n["y"]) < 1e-6 and abs(spans[-1][1] - (n["y"] + n["h"])) < 1e-6
                  and all(abs(a[1] - b[0]) < 1e-6 for a, b in zip(spans, spans[1:])), spans)
check("labels in a column are at least LABEL_GAP apart",
      all(abs(a - b) >= charts.LABEL_GAP - 1e-6
          for c in (0, 1, 2) for a, b in itertools.combinations(
              [n["label_y"] for n in col(c)], 2)))
check("no records, no flow", insights.flow([], 34, str, analytics.HOW_LABELS) is None)

print("charts: a curve")
cv = charts.curve([(0.0, 0.0), (2, 0.1), (5, 0.3)], 14, marks=[("m", 2), ("none", None)])
check("a step path from the origin, carried flat to the end",
      cv["line"].startswith("M0.00,100.00") and cv["line"].endswith("H100"), cv["line"])
check("the top is the next clean tenth", cv["top"] == 30, cv["top"])
check("a mark with no position is dropped", [m["name"] for m in cv["marks"]] == ["m"])
check("x ticks are weeks", [x["label"] for x in cv["xticks"]] == ["0", "7", "14"])

# -------------------------------------------------------- employers, endings

print("employers")
emp = [app(company_norm="contoso markets", company_display="Contoso Markets") for _ in range(3)]
emp += [app(company_norm=UNKNOWN_COMPANY) for _ in range(4)]
emp += [app(company_norm="fabrikam")]
emp_ev = [ev(a, "applied", ago(30 - i), source="extension") for i, a in enumerate(emp)]
e = insights.employers(facts_of(emp, emp_ev))
check("only employers tried more than once, never the unknown placeholder",
      [r["name"] for r in e["rows"]] == ["Contoso Markets"] and e["count"] == 1, e["rows"])
check("an employer that never answered counts as silent",
      e["silent"] == 1 and e["silent_records"] == 3)
viewed_only = [app(company_norm="tailspin consulting", company_display="Tailspin Consulting")
               for _ in range(2)]
answered_once = [app(company_norm="wingtip talent group", company_display="Wingtip Talent Group")
                 for _ in range(2)]
more_ev = [ev(a, "applied", ago(30), source="extension") for a in viewed_only + answered_once]
more_ev += [ev(viewed_only[0], "viewed", ago(29)), ev(answered_once[0], "rejected", ago(27))]
e2 = insights.employers(facts_of(emp + viewed_only + answered_once, emp_ev + more_ev))
rows2 = {r["name"]: r for r in e2["rows"]}
check("a viewed notice is not an answer: that employer is silent, one that rejected is not",
      e2["silent"] == 2 and e2["silent_records"] == 5
      and rows2["Tailspin Consulting"]["answered"] == 0 and rows2["Wingtip Talent Group"]["answered"] == 1,
      (e2["silent"], e2["silent_records"]))
check("...and only the one that sent nothing at all is unseen, a subset of the silent",
      e2["unseen"] == 1 and e2["unseen"] <= e2["silent"], e2["unseen"])

print("rejection lags")
lag_apps = [app(status="rejected") for _ in range(4)]
lag_ev = []
for i, a in enumerate(lag_apps):
    lag_ev.append(ev(a, "applied", ago(30), source="extension"))
    lag_ev.append(ev(a, "rejected", ago(27), mail_platform="linkedin") if i < 3
                  else ev(a, "rejected", ago(20), source="manual"))
lg = insights.rejection_lags(facts_of(lag_apps, lag_ev))
rows = {r["key"]: r for r in lg["rows"]}
check("closers split the way rejection_ends' columns do",
      rows["linkedin"]["n"] == 3 and rows["by_hand"]["n"] == 1 and "other_email" not in rows)
check("dots on the same day stack", [d["level"] for d in rows["linkedin"]["dots"]] == [0, 1, 2]
      and rows["linkedin"]["stack"] == 3)
check("no screen fetched, none counted", lg["screened"] == 0)
scr = [app(status="rejected", screen=s_) for s_ in ("sponsorship", "form", "sponsorship")]
scr_ev = [e for a in scr for e in (ev(a, "applied", ago(30), source="extension"),
                                   ev(a, "rejected", ago(27), mail_platform="linkedin"))]
lg2 = insights.rejection_lags(facts_of(scr, scr_ev))
check("the lags count the screens among your rejections",
      lg2["screened"] == 3 and lg2["screened_sponsorship"] == 2, (lg2["screened"], lg2["screened_sponsorship"]))

# ------------------------------------------------------------------ rhythm

print("calendar and hours")
cal_a = app()
cal_ev = [ev(cal_a, "applied", datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc), source="extension"),
          ev(cal_a, "rejected", datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc))]
cal = insights.calendar(facts_of([cal_a], cal_ev), NOW, SGT)
cells = [c for w in cal["weeks"] for c in w["cells"] if c]
sent_day = [c["date"].isoformat() for c in cells if c["sent"]]
check("a 01:00 Singapore submission falls on its Singapore day", sent_day == ["2026-09-21"], sent_day)
check("days after today are blank, not zero",
      cal["weeks"][-1]["cells"][-1] is None and cells[-1]["date"].isoformat() == "2026-09-25")
check("the first column names its month", cal["weeks"][0]["month"] == "Sep")
h = insights.hours(facts_of([cal_a], cal_ev), SGT)
check("hours are the viewer's", h["mine"]["bars"][1]["n"] == 1 and h["theirs"]["bars"][10]["n"] == 1)

# ------------------------------------------------------------ visa matrix

print("visa, at a glance")
vm_apps = [app(form_visa="needs", visa_group="restricts"), app(form_visa="needs", visa_group="nothing"),
           app(form_visa="no_form"), app(origin="inbound", form_visa="needs", visa_group="restricts")]
vm_ev = [ev(a, "applied" if a["origin"] != "inbound" else "recruiter_outreach", ago(20), source="extension")
         for a in vm_apps]
vm = insights.visa_matrix(facts_of(vm_apps, vm_ev))
grid = {(c["form"], c["visa"]): c["n"] for r in vm["rows"] for c in r["cells"]}
check("rows are the form's buckets and columns the JD's, in their fixed order",
      [r["key"] for r in vm["rows"]] == list(answers.FORM_VISA)
      and [c["key"] for c in vm["cols"]] == list(jd_extraction.VISA_GROUPS))
check("every record the user started is in exactly one cell; recruiters' are not",
      vm["n"] == 3 and sum(grid.values()) == 3 and grid[("needs", "restricts")] == 1
      and grid[("no_form", "nothing")] == 1, grid)
check("a record with no fetched buckets counts as no form, JD silent",
      grid[("no_form", "nothing")] == 1)
check("column totals add up", sum(c["n"] for c in vm["cols"]) == 3)
check("nobody started, no matrix", insights.visa_matrix(facts_of(vm_apps[3:], vm_ev[3:])) is None)

# ---------------------------------------------------------------- report

print("report")
check("an empty search is one sentence, not a page of zeros",
      insights.report([], [], NOW, SGT, 10) == {"empty": True})
r = insights.report(rich, rich_ev, NOW, SGT, 10)
check("a full search renders every panel it has data for",
      not r["empty"] and r["head"]["sent"] == 60 and r["cohorts"]["rows"] and r["flow"]
      and r["compare"]["groups"] and r["coverage"])
check("the headline counts heard-back the way analytics.summary(inbound=False) does",
      r["head"]["heard"] == sum(1 for f in fr if not f["inbound"] and f["signal_at"]))

print("ALL insights tests pass")
