"""What the search is telling you: every number and chart on /analytics.

Pure functions over the rows `analytics.facts()` fetched, in the style of
`trace.py` — no database, no template knowledge — so every statistic here is
testable on hand-built rows (tests/test_insights.py) and the route is one
fetch and one call to `report()`.

The page's vocabulary, defined once here and used the same way in every panel:

  sent          an application the USER started (origin is not `inbound`) that
                has an `applied` event; its clock starts at the first one.
  approach      a record a recruiter started (origin `inbound`); its clock
                starts at its first event, the approach.
  heard back    any employer response (analytics.RESPONSE_TYPES) — exactly
                what the list's lede calls a reply, so the two agree.
  answered      a rejection or a round. Deliberately NOT "heard back": a
                "viewed" notice is LinkedIn's alone, so counting it scores
                every LinkedIn application up for a signal no other channel
                can send. Measured 25 Sep 2026 on 216 settled applications, it
                REVERSED a comparison: by heard-back, on-platform beat the
                employer's site 33% to 29%; by answered, the employer's site
                won 29% to 19%. Comparisons use answered.
  round         analytics.ROUND_EVENTS — an interview invitation, a call or
                message (`engaged`), or an offer.

Colour follows the list (UI rule 1): an application's square wears the colour
its day count wears on the list — `trace.live()` and `trace.heat()` decide
it, not a second rule here.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import analytics, charts, trace
from .ingest import UNKNOWN_COMPANY

RESPONSE = frozenset(analytics.RESPONSE_TYPES)
ROUND = frozenset(analytics.ROUND_EVENTS)
ANSWER = ROUND | {"rejected"}
CLOSED = frozenset({"rejected", "offer", "withdrawn"})

# A comparison counts only applications at least this old: a younger one has
# not had its chance to be answered, and counting it as "not answered" would
# score every recent week down. Measured 25 Sep 2026 (Kaplan-Meier over 277
# sent applications): by day 14, 93% of every reply that ever came had come,
# and 80% of rejections. A longer window buys little and costs every
# application of the last weeks.
SETTLED_DAYS = 14

# Fewer observed replies than this and the timing panel says so instead of
# drawing a curve — a "longest wait" set by three replies is an anecdote.
MIN_TIMING_N = 10

# How many values a many-valued comparison shows, and the share of
# applications above which a title word is the baseline itself rather than a
# contrast ("engineer" was in 245 of 277 titles).
TOP_TECH = 12
TOP_WORDS = 10
COMMON_WORD_SHARE = 0.75

# The employers table: companies applied to at least twice, the most first.
EMPLOYER_MIN = 2
EMPLOYER_ROWS = 20

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def days(delta: timedelta) -> float:
    return delta.total_seconds() / 86400.0


def days_text(x) -> str:
    """A duration in days as a reader says it: "10 hours", "3 days",
    "4.1 days", "22 days". A Jinja filter (web.py) as well as a helper."""
    if x is None:
        return "—"
    if x < 1:
        h = max(1, round(x * 24))
        return f"{h} {_plural(h, 'hour')}"
    r = round(x) if x >= 10 or abs(x - round(x)) < 0.05 else round(x, 1)
    return f"{r:g} {_plural(r, 'day')}"


def quantile(xs, q: float):
    """Linear-interpolated quantile of an unsorted list; None when empty."""
    if not xs:
        return None
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n. Chosen over the plain
    normal interval because it stays inside 0..1 and behaves at 0 of 7 —
    exactly the rows a comparison of a job search is full of."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def kaplan_meier(pairs) -> list[tuple[float, float]]:
    """Cumulative share that has had the event, by time: [(t, F(t))] at each
    time an event happened, starting (0, 0). `pairs` are (duration, observed):
    an application that has heard back is observed at its lag; one still
    waiting is CENSORED at its age — it counts as "no reply yet" for as long
    as it has existed and then leaves the pool, rather than as "never". That
    is the whole reason for this estimator: the plain rate counts yesterday's
    application as a failure, and a search sends most of its applications
    recently."""
    pairs = sorted(pairs, key=lambda p: (p[0], not p[1]))
    at_risk, surv, out = len(pairs), 1.0, [(0.0, 0.0)]
    i = 0
    while i < len(pairs):
        t, d, gone = pairs[i][0], 0, 0
        while i < len(pairs) and pairs[i][0] == t:
            d += 1 if pairs[i][1] else 0
            gone += 1
            i += 1
        if d and at_risk:
            surv *= 1 - d / at_risk
            out.append((t, 1 - surv))
        at_risk -= gone
    return out


def cdf(curve, t: float) -> float:
    """F(t) from a kaplan_meier() curve: the last step at or before t."""
    v = 0.0
    for tt, f in curve:
        if tt > t:
            break
        v = f
    return v


def still_chance(curve, t: float, horizon: float | None = None) -> float:
    """Of the applications still silent at t, the share that heard back
    later — within `horizon` more days, or ever (to the curve's last step)."""
    f_t = cdf(curve, t)
    if f_t >= 1:
        return 0.0
    f_end = curve[-1][1] if horizon is None else cdf(curve, t + horizon)
    return max(0.0, (f_end - f_t) / (1 - f_t))


# ------------------------------------------------------------------- facts

def build_facts(apps, events, now: datetime, reminder_days: int) -> list[dict]:
    """One dict per application: its fetched columns plus every derived fact
    the panels read. Times are UTC-aware; `days` are floats."""
    by_app = defaultdict(list)
    for e in events:
        by_app[e["application_id"]].append(e)
    out = []
    for a in apps:
        evs = sorted(by_app.get(a["id"], []), key=lambda e: (e["occurred_at"], e["created_at"]))
        f = dict(a)
        f["events"] = evs

        def first(types, evs=evs):
            return next((e for e in evs if e["type"] in types), None)

        applied = first({"applied"})
        f["applied_at"] = applied["occurred_at"] if applied else None
        f["external"] = applied["external"] if applied else None
        f["applied_source"] = applied["source"] if applied else None
        f["inbound"] = a["origin"] == "inbound"
        f["sent"] = not f["inbound"] and applied is not None
        # A recruiter's thread starts at the approach even if the user applied
        # later; the user's starts at the submission.
        if f["inbound"] or applied is None:
            f["start"] = evs[0]["occurred_at"] if evs else None
        else:
            f["start"] = f["applied_at"]

        sig, ans, rnd = first(RESPONSE), first(ANSWER), first(ROUND)
        f["signal_at"] = sig["occurred_at"] if sig else None
        f["signal_type"] = sig["type"] if sig else None
        f["answer_at"] = ans["occurred_at"] if ans else None
        f["round_at"] = rnd["occurred_at"] if rnd else None
        f["offer"] = any(e["type"] == "offer" for e in evs)

        # The closing rejected event: the newest carrying a reason, else the
        # newest — rejection_reasons()/rejection_ends() and the list's `rr`
        # LATERAL pick the same one, so a count here is a count there.
        rej = [e for e in evs if e["type"] == "rejected"]
        close = (max(rej, key=lambda e: (e["reason"] is not None, e["occurred_at"], e["created_at"]))
                 if rej else None)
        f["close"] = close
        f["reason"] = close["reason"] if close else None
        f["how"] = analytics.rejected_how(f["reason"], rnd is not None) if close else None
        ended = first(CLOSED)
        f["ended_at"] = ended["occurred_at"] if ended else None
        f["superseded"] = any(e["type"] == "withdrawn" and e["superseded"] for e in evs)

        start = f["start"]
        f["age"] = days(now - start) if start else None
        f["lag"] = (max(0.0, days(f["signal_at"] - start))
                    if start and f["signal_at"] else None)

        # The wait, exactly as the list draws it (trace.build): silent days
        # since the last event unless that event closed the thread.
        last = evs[-1] if evs else None
        silent = (max((now - last["occurred_at"]).days, 0)
                  if last and last["type"] not in trace.TERMINAL else None)
        f["silent_days"] = silent
        f["heat"] = trace.heat(silent, reminder_days)
        f["live"] = trace.live(last["type"] if last else None, silent, reminder_days)
        status = "applied" if a["status"] == "confirmation" else a["status"]
        f["state"] = status
        # A closed thread is closed whatever came after it (a note filed on a
        # rejection draws a tail on the list, but nobody is being waited on).
        f["tone"] = status if status in CLOSED else ("live" if f["live"] else "wait")
        out.append(f)
    return out


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def _phrase(f) -> str:
    """What became of it, in the list's words (UI rule 15) — a square's title."""
    s = f["state"]
    by = "they approached" if f["inbound"] else "you applied"
    if s == "offer":
        text = "offer"
    elif s == "rejected":
        if f["round_at"]:
            text = "rejected after a round"
        elif f["start"] and f["close"]:
            n = round(max(0.0, days(f["close"]["occurred_at"] - f["start"])))
            text = f"rejected {n} {_plural(n, 'day')} after {by}"
        else:
            text = "rejected"
    elif s == "withdrawn":
        text = "you applied again" if f["superseded"] else "withdrawn"
    else:
        text = {"interview_invite": "interviewing", "engaged": "they reached out",
                "viewed": "viewed", "interested": "awaiting your call"}.get(s, "applied")
    if f["silent_days"] is not None and s not in CLOSED:
        n = f["silent_days"]
        text += f", quiet {n} {_plural(n, 'day')}"
    return text


def unit(f) -> dict:
    """One application as one square: where it links, what colour it wears,
    whether it reached a round (drawn as a circle), and its title."""
    who = f.get("company_display") or f.get("company_norm") or "?"
    return {"id": f["id"], "tone": f["tone"], "heat": f["heat"],
            "round": f["round_at"] is not None,
            "title": f"{who} — {f.get('title_canonical') or '?'}: {_phrase(f)}"}


# Square order inside a row, so a row reads as runs of colour: the live and
# the good first, then the closed, then the waits from coolest to hottest.
_TONE_ORDER = {"offer": 0, "live": 1, "rejected": 2, "withdrawn": 3, "wait": 4}


def _unit_key(f):
    return (_TONE_ORDER[f["tone"]], f["round_at"] is None, f["heat"],
            f["start"].timestamp() if f["start"] else 0.0)


# ---------------------------------------------------------------- headline

def headline(facts, now: datetime) -> dict:
    mine = [f for f in facts if not f["inbound"]]
    sent = [f for f in mine if f["sent"]]
    inbound = [f for f in facts if f["inbound"]]
    starts = [f["start"] for f in facts if f["start"]]
    first = min(starts) if starts else None
    span_days = days(now - first) if first else 0
    weeks = max(span_days / 7, 1)
    # Counted the way analytics.summary(inbound=False) counts the list's lede:
    # a reply on any record the user started, over those with a submission.
    heard = sum(1 for f in mine if f["signal_at"])
    r_sent = sum(1 for f in sent if f["round_at"])
    r_in = sum(1 for f in inbound if f["round_at"])
    return {
        "since": first, "days": int(span_days), "weeks": round(span_days / 7),
        "sent": len(sent), "per_week": round(len(sent) / weeks),
        "heard": heard,
        "heard_pct": round(100 * heard / len(sent)) if sent else None,
        "answered": sum(1 for f in sent if f["answer_at"]),
        "rounds_sent": r_sent, "rounds_inbound": r_in,
        "per_round_sent": round(len(sent) / r_sent) if r_sent else None,
        "per_round_inbound": round(len(inbound) / r_in) if r_in else None,
        "approaches": len(inbound),
        "offers": sum(1 for f in facts if f["offer"]),
        "live": sum(1 for f in facts if f["state"] in ("engaged", "interview_invite")),
    }


# ------------------------------------------------------------------ timing

def reply_curve(facts) -> list[tuple[float, float]]:
    """Kaplan-Meier over every sent application: heard back (observed at its
    lag) or still silent (censored at its age, or at the day it closed)."""
    pairs = []
    for f in facts:
        if not f["sent"]:
            continue
        if f["lag"] is not None:
            pairs.append((f["lag"], True))
        else:
            end = f["ended_at"] or None
            dur = days(end - f["start"]) if end else f["age"]
            pairs.append((max(0.0, dur), False))
    return kaplan_meier(pairs)


def reply_window(facts) -> int | None:
    """The longest any sent application has waited to hear anything, in whole
    days — rounded UP, so nothing that history says might still answer is
    called over. None below MIN_TIMING_N replies. A property of the user's
    own record, not a constant: one reply on day 40 moves it to 40."""
    lags = [f["lag"] for f in facts if f["sent"] and f["lag"] is not None]
    if len(lags) < MIN_TIMING_N:
        return None
    return math.ceil(max(lags))


def timing(facts) -> dict | None:
    """How long hearing back takes: the curve, its landmarks, the chance a
    silent application still hears, and what the waiting ones should expect."""
    lags = [f["lag"] for f in facts if f["sent"] and f["lag"] is not None]
    if len(lags) < MIN_TIMING_N:
        return None
    curve = reply_curve(facts)
    window = reply_window(facts)
    x_max = max(28, 7 * math.ceil((window + 7) / 7))
    plateau = curve[-1][1]

    kinds = []
    for label, types in (("LinkedIn's “viewed” notice", {"viewed"}),
                         ("a rejection", {"rejected"}),
                         ("a round", ROUND)):
        xs = [f["lag"] for f in facts if f["sent"] and f["signal_type"] in types]
        if xs:
            kinds.append({"label": label, "n": len(xs), "median": quantile(xs, .5)})

    waiting = [f for f in facts if f["sent"] and f["signal_at"] is None
               and f["state"] not in CLOSED]
    inside = [f for f in waiting if f["age"] <= window]
    expect = sum(still_chance(curve, f["age"]) for f in waiting)
    week = sum(still_chance(curve, f["age"], 7) for f in waiting)

    still = [(d, still_chance(curve, d)) for d in range(0, x_max + 1)]
    return {
        "n": sum(1 for f in facts if f["sent"]), "heard": len(lags),
        "median": quantile(lags, .5), "p90": quantile(lags, .9),
        "window": window, "x_max": x_max, "plateau": plateau,
        "by": {d: cdf(curve, d) for d in (1, 3, 7, 14, 28)},
        "still": {d: still_chance(curve, d) for d in (0, 7, 14, 21, 28)},
        "kinds": kinds,
        "waiting": len(waiting), "inside": len(inside),
        "expect": expect, "expect_week": week,
        "curve": charts.curve(curve, x_max, marks=[
            ("median", quantile(lags, .5)), ("window", window)]),
        "still_curve": charts.curve(still, x_max, step=True, marks=[("window", window)]),
    }


# ------------------------------------------------------------- comparisons

_AGE = re.compile(r"(\d+)\+?\s*(minute|hour|day|week|month|year)s?\b", re.I)
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def listing_age_days(label: str | None) -> float | None:
    """The platform's relative "posted" label as days — coarse on purpose
    (migration 004 keeps it unparsed for exactly that reason); a bucket is all
    a comparison needs. "Reposted 2 weeks ago" and "30+ days ago" parse."""
    if not label:
        return None
    if re.search(r"\b(just now|today)\b", label, re.I):
        return 0.0
    m = _AGE.search(label)
    return int(m[1]) * _UNIT_DAYS[m[2].lower()] if m else None


def _bucket(value, bounds):
    for label, upper in bounds:
        if value < upper:
            return label
    return None


AGE_BUCKETS = (("under a day", 1), ("1 to 6 days", 7), ("1 to 2 weeks", 21),
               ("3 to 4 weeks", 30), ("a month or more", math.inf))
FORM_BUCKETS = (("1 to 4 fields", 5), ("5 to 9 fields", 10), ("10 or more", math.inf))
HOUR_BUCKETS = (("after midnight", 6), ("in the morning", 12),
                ("in the afternoon", 18), ("in the evening", 24))
VISA_WORDS = {"sponsors": "says it sponsors", "unclear": "says nothing",
              "local_only": "wants locals only"}
MODE_WORDS = {"hybrid": "hybrid", "onsite": "on site", "remote": "remote"}
PLATFORM_WORDS = {"linkedin": "LinkedIn", "jobstreet": "JobStreet", "indeed": "Indeed",
                  "other": "another site"}


def _single(value_of, order=None, limit=None):
    """A comparison over one value per application. `order` fixes the rows
    (an ordinal scale reads in its own order, never by count); otherwise the
    biggest first, up to `limit`."""
    def rows(pool, tz):
        groups = defaultdict(list)
        for f in pool:
            v = value_of(f, tz)
            if v is not None:
                groups[v].append(f)
        if order:
            return [(k, groups[k]) for k in order if groups.get(k)]
        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0])))
        return ranked[:limit] if limit else ranked
    return rows


_SEP = re.compile(r"[-_ .]")


def distinct_names(labels: list[str]) -> list[str]:
    """File names as they differ: the prefix they all share (cut back to a
    word boundary) and the extension dropped. Resumes are named after their
    owner, so the part that tells two apart is the END — and a narrow column
    truncates the end, which on the author's data showed two identical
    labels. A name that would come out empty is left whole."""
    if not labels:
        return []
    cut = 0
    if len(labels) > 1:
        common = labels[0]
        for l in labels[1:]:
            while not l.startswith(common):
                common = common[:-1]
        n = len(common)
        if all(len(l) > n and _SEP.match(l[n]) for l in labels):
            cut = n               # the shared part ends exactly at a boundary
        else:
            seps = [m.end() for m in _SEP.finditer(common)]
            cut = seps[-1] if seps else 0
    out = []
    for l in labels:
        short = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", l[cut:]).strip("-_ .")
        out.append(short or l)
    return out


def _technology(pool, tz):
    """Technologies the job description names — one row per technology, an
    application in every row it names. Spelled as it is most often written
    ("LangChain", not "Langchain")."""
    groups, spell = defaultdict(list), defaultdict(Counter)
    for f in pool:
        seen = set()
        for t in f.get("tech") or []:
            key = t.strip().lower()
            if key and key not in seen:
                seen.add(key)
                groups[key].append(f)
                spell[key][t.strip()] += 1
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_TECH]
    return [(spell[k].most_common(1)[0][0], fs) for k, fs in ranked]


_WORD = re.compile(r"[a-z0-9][a-z0-9.#+]*|\.net", re.I)


def _title_words(pool, tz):
    """Words in the job title, found rather than listed: every word in at
    least MIN_RATE_N titles and at most COMMON_WORD_SHARE of them (a word in
    nearly every title IS the baseline), with words that always appear
    together merged into one phrase in title order ("full" + "stack" is
    "full stack"). No hand-kept list of role families to fall out of date."""
    if not pool:
        return []
    where = defaultdict(set)
    pos = defaultdict(list)
    for f in pool:
        words = [w.lower().rstrip(".") for w in _WORD.findall(f.get("title_canonical") or "")]
        for i, w in enumerate(words):
            if len(w) > 1:
                where[w].add(f["id"])
                pos[w].append(i)
    ceiling = COMMON_WORD_SHARE * len(pool)
    keep = {w: ids for w, ids in where.items()
            if analytics.MIN_RATE_N <= len(ids) <= ceiling}
    phrases = defaultdict(list)
    for w, ids in keep.items():
        phrases[frozenset(ids)].append(w)
    by_id = {f["id"]: f for f in pool}
    rows = []
    for ids, ws in phrases.items():
        ws.sort(key=lambda w: sum(pos[w]) / len(pos[w]))
        rows.append((" ".join(ws), [by_id[i] for i in ids]))
    rows.sort(key=lambda r: (-len(r[1]), r[0]))
    return rows[:TOP_WORDS]


def _local(dt: datetime, tz) -> datetime:
    """A time as the viewer lived it. Grouping by calendar day or hour is a
    presentation of WHEN something happened, and a Singapore user's 00:30
    submission is Monday for them and Sunday in UTC — on this author's data
    about 110 of 229 extension-captured submissions fall in that gap. Only
    grouping converts; every duration here is tz-free arithmetic on UTC."""
    return dt.astimezone(tz) if tz else dt


# The comparison registry. Every entry is a sentence-case title and a rows
# function; tests/test_insights.py loops the registry, so a new dimension is
# covered by existing tests (CLAUDE.md: "a registry needs a test that loops it").
DIMENSIONS = [
    {"key": "external", "title": "How you applied",
     "rows": _single(lambda f, tz: None if f["external"] is None else
                     ("on the employer's site" if f["external"] else "on the platform"),
                     order=["on the platform", "on the employer's site"])},
    {"key": "listing_age", "title": "How old the listing was",
     "rows": _single(lambda f, tz: (lambda d: None if d is None else _bucket(d, AGE_BUCKETS))(
         listing_age_days(f.get("posted_label"))),
         order=[b for b, _ in AGE_BUCKETS])},
    {"key": "reposted", "title": "Whether the listing was a repost",
     "rows": _single(lambda f, tz: None if f.get("reposted") is None else
                     ("reposted" if f["reposted"] else "first posting"),
                     order=["first posting", "reposted"])},
    {"key": "form", "title": "How long the form was",
     "note": "Fields answered in the extension's capture, where it saw the form.",
     "rows": _single(lambda f, tz: _bucket(f["n_answers"], FORM_BUCKETS) if f.get("n_answers") else None,
                     order=[b for b, _ in FORM_BUCKETS])},
    {"key": "resume", "title": "Which resume you sent", "shorten": distinct_names,
     "rows": _single(lambda f, tz: f.get("resume_file"))},
    {"key": "visa", "title": "What the job description said about visas",
     "rows": _single(lambda f, tz: VISA_WORDS.get(f.get("visa_signal")),
                     order=list(VISA_WORDS.values()))},
    {"key": "work_mode", "title": "Where the work is",
     "rows": _single(lambda f, tz: MODE_WORDS.get(f.get("work_mode")),
                     order=list(MODE_WORDS.values()))},
    {"key": "title_words", "title": "Words in the job title", "rows": _title_words},
    {"key": "technology", "title": "Technology the job description names", "rows": _technology},
    {"key": "ats", "title": "The employer's hiring system",
     "rows": _single(lambda f, tz: f.get("ats") if f.get("ats") not in (None, "unknown") else None)},
    {"key": "platform", "title": "The platform",
     "rows": _single(lambda f, tz: PLATFORM_WORDS.get(f.get("platform") or "", None))},
    {"key": "weekday", "title": "The day you applied",
     "rows": _single(lambda f, tz: WEEKDAYS[_local(f["applied_at"], tz).weekday()],
                     order=list(WEEKDAYS))},
    {"key": "hour", "title": "The hour you applied",
     "note": "Submissions the extension saw, at the minute it saw them; a date typed "
             "into a form has no hour.",
     "rows": _single(lambda f, tz: _bucket(_local(f["applied_at"], tz).hour, HOUR_BUCKETS)
                     if f["applied_source"] == "extension" else None,
                     order=[b for b, _ in HOUR_BUCKETS])},
]


def compare(facts, now: datetime, tz) -> dict:
    """Answered rate per value of each dimension, over SETTLED applications,
    with a 95% Wilson interval, against the overall rate. Below MIN_RATE_N a
    row shows counts and no rate (UI rule 7). A row `stands_out` when its
    whole interval clears the overall rate — and with sixty-odd rows, a
    couple will by chance, which the page says beside them."""
    pool = [f for f in facts if f["sent"] and f["age"] >= SETTLED_DAYS]
    n = len(pool)
    k = sum(1 for f in pool if f["answer_at"])
    base = k / n if n else None
    groups, top = [], base or 0.0
    for dim in DIMENSIONS:
        rows = []
        found = dim["rows"](pool, tz)
        shown = dim.get("shorten", lambda ls: ls)([str(label) for label, _ in found])
        for (label, members), short in zip(found, shown):
            m = len(members)
            got = sum(1 for f in members if f["answer_at"])
            thin = m < analytics.MIN_RATE_N
            lo, hi = wilson(got, m)
            row = {"label": short, "full": str(label), "n": m, "k": got,
                   "rounds": sum(1 for f in members if f["round_at"]),
                   "rate": None if thin else got / m,
                   "lo": lo, "hi": hi, "thin": thin,
                   "stands_out": (not thin and base is not None
                                  and (lo > base or hi < base))}
            if not thin:
                top = max(top, hi)
            rows.append(row)
        if rows:
            groups.append({"key": dim["key"], "title": dim["title"],
                           "note": dim.get("note"), "rows": rows})
    scale = min(1.0, math.ceil(top * 10) / 10) if top else 1.0

    def x(v):
        return f"{100 * v / scale:.2f}%"
    for g in groups:
        for r in g["rows"]:
            r["x"], r["x_lo"], r["x_hi"] = x(r["rate"] or 0), x(r["lo"]), x(min(r["hi"], scale))
    findings = sorted(
        ({"group": g["title"], **r} for g in groups for r in g["rows"] if r["stands_out"]),
        key=lambda r: -abs(r["rate"] - base))
    rated = sum(1 for g in groups for r in g["rows"] if not r["thin"])
    step = 10 if scale <= .5 else 20
    return {"n": n, "k": k, "base": base, "x_base": x(base) if base is not None else None,
            "scale": scale, "tick": f"{step / scale:.3f}%",
            "ticks": [{"x": x(t / 100), "label": f"{t}%"}
                      for t in range(0, round(scale * 100) + 1, step)],
            "groups": groups, "findings": findings[:6],
            "rows": sum(len(g["rows"]) for g in groups), "rated": rated,
            # A 95% interval clears a true rate one time in twenty by chance,
            # so this many "findings" are expected if nothing mattered at all.
            "chance": round(0.05 * rated),
            "settled_days": SETTLED_DAYS}


# ------------------------------------------------------------------ cohorts

def week_of(dt: datetime, tz) -> date:
    d = _local(dt, tz).date()
    return d - timedelta(days=d.weekday())


def _day_label(d: date) -> str:
    return f"{d:%d %b}".lstrip("0")


def cohorts(facts, now: datetime, tz) -> dict:
    """Every sent application as a square, one row per week it was sent,
    oldest week first and empty weeks kept (a week with nothing sent is a real
    zero, and dropping it would compress the search into a lie about pace).
    The approaches follow as one row, in the order they arrived."""
    sent = [f for f in facts if f["sent"]]
    rows = []
    if sent:
        by_week = defaultdict(list)
        for f in sent:
            by_week[week_of(f["applied_at"], tz)].append(f)
        cur, last = min(by_week), week_of(now, tz)
        while cur <= last:
            fs = sorted(by_week.get(cur, []), key=_unit_key)
            rows.append({"week": cur, "label": _day_label(cur),
                         "units": [unit(f) for f in fs], "n": len(fs),
                         "heard": sum(1 for f in fs if f["signal_at"]),
                         "rounds": sum(1 for f in fs if f["round_at"])})
            cur += timedelta(days=7)
    inbound = sorted((f for f in facts if f["inbound"] and f["start"]), key=lambda f: f["start"])
    return {"rows": rows, "peak": max((r["n"] for r in rows), default=0),
            "approaches": [unit(f) for f in inbound],
            "approach_rounds": sum(1 for f in inbound if f["round_at"]),
            "tones": Counter(f["tone"] for f in facts if f["start"])}


# ---------------------------------------------------------------------- flow

FLOW_ORDER = ("applied", "viewed", "engaged", "interview_invite", "offer",
              "rejected", "withdrawn", "interested")


def flow(facts, window: int | None, status_word, how_words) -> dict | None:
    """Where every record stands: who started it -> its status (the list's
    funnel, both pages together) -> for the two big statuses, why. A waiting
    application splits at the reply window (still inside the longest wait on
    record, or past it); a rejection splits by analytics.rejected_how, the
    list's `how` chips. Band hrefs go to the list page that shows exactly
    that band's rows."""
    recs = [f for f in facts if f["start"]]
    if not recs:
        return None
    sources = [("sent", "You started", [f for f in recs if not f["inbound"]], "/"),
               ("inbound", "Recruiters started", [f for f in recs if f["inbound"]], "/inbound")]
    nodes, links = [], []
    for key, label, fs, _ in sources:
        if fs:
            nodes.append({"id": key, "col": 0, "value": len(fs), "label": label, "tone": "ink"})
    for s in FLOW_ORDER:
        fs = [f for f in recs if f["state"] == s]
        if not fs:
            continue
        nodes.append({"id": s, "col": 1, "value": len(fs), "label": status_word(s),
                      "tone": s})
        for key, label, src, page in sources:
            v = sum(1 for f in src if f["state"] == s)
            if v:
                links.append({"src": key, "dst": s, "value": v, "tone": s,
                              "href": f"{page}?status={s}",
                              "title": f"{label}, now {status_word(s)}: {v}"})
        children = []
        if s == "applied" and window is not None:
            inside = sum(1 for f in fs if f["age"] <= window)
            children = [("inside", f"within {window} days", inside, "wait"),
                        ("past", f"quiet past {window} days", len(fs) - inside, "stale")]
        elif s == "rejected":
            count = Counter(f["how"] for f in fs)
            children = [(f"how_{k}", w, count.get(k, 0), "rejected") for k, w in how_words.items()]
        for cid, clabel, v, tone in children:
            if v:
                nodes.append({"id": cid, "col": 2, "value": v, "label": clabel,
                              "tone": tone, "parent": s})
                links.append({"src": s, "dst": cid, "value": v, "tone": tone,
                              "title": f"{status_word(s)}, {clabel}: {v}"})
    return charts.flow(nodes, links)


# ----------------------------------------------------------------- employers

def employers(facts) -> dict:
    """Companies tried more than once, as rows of squares: the per-employer
    answer history docs/features.md §3.3 names as the ghost-job signal only a
    system with an outcome log can compute."""
    groups = defaultdict(list)
    for f in facts:
        if f["start"] and f["company_norm"] != UNKNOWN_COMPANY:
            groups[f["company_norm"]].append(f)
    multi = [fs for fs in groups.values() if len(fs) >= EMPLOYER_MIN]
    rows = []
    for fs in multi:
        fs.sort(key=lambda f: f["start"])
        rows.append({"name": fs[-1].get("company_display") or fs[-1]["company_norm"],
                     "q": fs[-1]["company_norm"],
                     "n": len(fs), "units": [unit(f) for f in fs],
                     "heard": sum(1 for f in fs if f["signal_at"]),
                     "answered": sum(1 for f in fs if f["answer_at"]),
                     "rounds": sum(1 for f in fs if f["round_at"]),
                     "last": fs[-1]["start"]})
    rows.sort(key=lambda r: (-r["n"], -r["last"].timestamp()))
    silent = [r for r in rows if r["heard"] == 0]
    return {"rows": rows[:EMPLOYER_ROWS], "more": max(0, len(rows) - EMPLOYER_ROWS),
            "count": len(rows), "records": sum(r["n"] for r in rows),
            "silent": len(silent), "silent_records": sum(r["n"] for r in silent),
            "companies": len(groups)}


# ------------------------------------------------------------ how it ended

CLOSERS = (("linkedin", "LinkedIn's letter"), ("other_email", "another email"),
           ("by_hand", "filed by hand"))


def rejection_lags(facts) -> dict | None:
    """Days from applying to the rejection, one dot per rejected application
    the user sent, in a row per closer — the closing event rejection_ends()
    reads, split the way its columns are. Dots that land together stack, so
    a pile is visible as a pile: LinkedIn's letter measured a wall at 3 days
    on 25 Sep 2026 (27 letters, median 3.0, none sooner than 2.9) — a
    timer, not a reader."""
    rows = {k: [] for k, _ in CLOSERS}
    for f in facts:
        if not (f["sent"] and f["state"] == "rejected" and f["close"]):
            continue
        c = f["close"]
        key = ("by_hand" if c["source"] != "email" else
               "linkedin" if c["mail_platform"] == "linkedin" else "other_email")
        rows[key].append((max(0.0, days(c["occurred_at"] - f["start"])), f))
    lags = [lag for v in rows.values() for lag, _ in v]
    if not lags:
        return None
    x_max = max(14, 7 * math.ceil(max(lags) / 7))
    out = []
    for key, label in CLOSERS:
        pts = sorted(rows[key], key=lambda p: p[0])
        if not pts:
            continue
        levels, dots = Counter(), []
        for lag, f in pts:
            b = round(lag / x_max * 100)       # stack within 1% of the axis
            dots.append({"x": f"{100 * lag / x_max:.2f}%", "level": levels[b],
                         "id": f["id"],
                         "title": f"{f.get('company_display') or '?'} — "
                                  f"{f.get('title_canonical') or '?'}: {lag:.1f} days"})
            levels[b] += 1
        out.append({"key": key, "label": label, "n": len(pts),
                    "median": quantile([p[0] for p in pts], .5),
                    "stack": max(levels.values()), "dots": dots})
    ticks = [{"x": f"{100 * t / x_max:.2f}%", "label": str(t)} for t in range(0, x_max + 1, 7)]
    return {"rows": out, "x_max": x_max, "ticks": ticks}


# -------------------------------------------------------------------- rhythm

def calendar(facts, now: datetime, tz) -> dict | None:
    """Two calendars of the search, week columns by weekday rows: what you
    sent each day, and the news that came back (every employer response
    event). Each scaled to its own peak (UI rule 6) and labelled with it."""
    sent = Counter(_local(f["applied_at"], tz).date() for f in facts if f["sent"])
    news = Counter(_local(e["occurred_at"], tz).date()
                   for f in facts for e in f["events"] if e["type"] in RESPONSE)
    dates = list(sent) + list(news)
    if not dates:
        return None
    today = _local(now, tz).date()
    start = min(dates)
    start -= timedelta(days=start.weekday())
    peak_s, peak_n = max(sent.values(), default=0), max(news.values(), default=0)

    def level(n, peak):
        return 0 if not n or not peak else round(18 + 82 * math.sqrt(n / peak))
    weeks, cur = [], start
    while cur <= today:
        cells = []
        for i in range(7):
            d = cur + timedelta(days=i)
            if d > today:
                cells.append(None)
                continue
            cells.append({"date": d, "sent": sent.get(d, 0), "news": news.get(d, 0),
                          "ls": level(sent.get(d, 0), peak_s), "ln": level(news.get(d, 0), peak_n),
                          "label": f"{WEEKDAYS[i][:3]} {_day_label(d)}"})
        # A month is named over the column holding its 1st, and the first
        # column names wherever the calendar starts.
        first_of = next((c["date"] for c in cells if c and c["date"].day == 1), None)
        month = (f"{first_of:%b}" if first_of else
                 None if weeks else f"{cells[0]['date']:%b}")
        weeks.append({"cells": cells, "month": month})
        cur += timedelta(days=7)
    busiest = max(sent.items(), key=lambda kv: (kv[1], kv[0])) if sent else None
    return {"weeks": weeks, "peak_sent": peak_s, "peak_news": peak_n,
            "active": len(sent), "span": (today - min(dates)).days + 1,
            "busiest": {"date": busiest[0], "n": busiest[1]} if busiest else None}


def hours(facts, tz) -> dict | None:
    """When you apply and when answers arrive, by hour of the viewer's day:
    submissions the extension timed, employer news that came by email."""
    mine = Counter(_local(f["applied_at"], tz).hour for f in facts
                   if f["sent"] and f["applied_source"] == "extension")
    theirs = Counter(_local(e["occurred_at"], tz).hour for f in facts for e in f["events"]
                     if e["type"] in RESPONSE and e["source"] == "email")
    if not mine and not theirs:
        return None

    def bars(c):
        peak = max(c.values(), default=0)
        return {"peak": peak, "n": sum(c.values()),
                "bars": [{"h": h, "n": c.get(h, 0),
                          "pct": f"{100 * c.get(h, 0) / peak:.1f}%" if peak else "0%"}
                         for h in range(24)]}
    return {"mine": bars(mine), "theirs": bars(theirs)}


# ------------------------------------------------------------------ coverage

def coverage(facts) -> list[dict]:
    """What this page can see: for each property a panel reads, how many of
    the sent applications carry it. A comparison over 63 recorded hiring
    systems is a different claim from one over 277, and the reader should
    not have to guess which."""
    sent = [f for f in facts if f["sent"]]
    rejected = [f for f in facts if f["close"]]
    rows = [
        ("How you applied", sum(1 for f in sent if f["external"] is not None), len(sent)),
        ("The job description", sum(1 for f in sent if f.get("has_jd")), len(sent)),
        ("Its extraction (visa, technology)", sum(1 for f in sent if f.get("extracted")), len(sent)),
        ("The listing's age", sum(1 for f in sent if listing_age_days(f.get("posted_label")) is not None), len(sent)),
        ("Work mode", sum(1 for f in sent if f.get("work_mode")), len(sent)),
        ("The form's fields", sum(1 for f in sent if f.get("n_answers")), len(sent)),
        ("Which resume", sum(1 for f in sent if f.get("resume_file")), len(sent)),
        ("The hiring system", sum(1 for f in sent if f.get("ats") not in (None, "unknown")), len(sent)),
        ("The salary", sum(1 for f in sent if f.get("has_salary")), len(sent)),
        ("Why a rejection closed", sum(1 for f in rejected if f["reason"]), len(rejected)),
    ]
    return [{"label": l, "k": k, "n": n, "pct": f"{100 * k / n:.1f}%" if n else "0%"}
            for l, k, n in rows if n]


# -------------------------------------------------------------------- report

def report(apps, events, now: datetime, tz, reminder_days: int,
           status_word=lambda s: s, how_words=None) -> dict:
    """The whole page. `status_word` and `how_words` are the list's own
    vocabulary (web._display, analytics.HOW_LABELS), passed in so this module
    never imports the web layer and the words cannot differ between pages."""
    facts = build_facts(apps, events, now, reminder_days)
    if not any(f["start"] for f in facts):
        return {"empty": True}
    window = reply_window(facts)
    return {
        "empty": False,
        "head": headline(facts, now),
        "cohorts": cohorts(facts, now, tz),
        "flow": flow(facts, window, status_word, how_words or analytics.HOW_LABELS),
        "timing": timing(facts),
        "compare": compare(facts, now, tz),
        "employers": employers(facts),
        "lags": rejection_lags(facts),
        "calendar": calendar(facts, now, tz),
        "hours": hours(facts, tz),
        "coverage": coverage(facts),
        "window": window,
        "reminder_days": reminder_days,
        "full_heat_days": trace.FULL_HEAT_DAYS,
    }
