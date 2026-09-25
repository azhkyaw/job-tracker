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

from pipeline import analytics, charts, insights, trace   # noqa: E402
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

print("sponsorship: what an answer told the employer")
import json                                                   # noqa: E402
from pipeline import answers                                  # noqa: E402
_cases = json.load(open(Path(__file__).parent / "sponsorship_answers.json",
                        encoding="utf-8"))["cases"]
for c in _cases:
    check(f"{c['q'][:48]!r} -> {c['a'][:24]!r}: {c['needs']}",
          answers.declares_sponsorship(c["q"], c["a"]) is c["needs"])

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
check("closed is closed, even with a note after it",
      ff[closed_note["id"]]["tone"] == "rejected" and ff[closed_note["id"]]["silent_days"] == 1)
check("trace.build sets the same `live` the template now reads",
      trace.build([{"id": "x"}], {"x": [{"type": "viewed", "occurred_at": ago(2)}]}, NOW, 10) is not None)
row = {"id": "x"}
trace.build([row], {"x": [{"type": "viewed", "occurred_at": ago(2)}]}, NOW, 10)
check("... and it is live for a fresh reply", row["live"] is True)

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
