"""Geometry for the application traces on the applications list.

Pure functions over rows already fetched by the caller — no DB access, no
template knowledge. Every trace on a page shares ONE axis (first event on
record → now) so rows are directly comparable: the same horizontal position
means the same day on every line, which is the whole point of the view.

Colour is assigned by *role*, not by event identity — see base.html's token
block. Neutral for things you did, blue for an employer engaging, rust for a
close, amber for the tail of an unanswered thread once it crosses
`config.REMINDER_DAYS`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Events that end a thread: the trace stops with a cap rather than running a
# waiting tail to today. Nothing is pending after these.
TERMINAL = {"rejected", "offer", "withdrawn"}

# event type -> status colour token in base.html
_ROLE = {
    "applied": "applied",
    "viewed": "viewed",
    "engaged": "viewed",
    "recruiter_outreach": "viewed",
    "interview_invite": "interview_invite",
    "offer": "offer",
    "rejected": "rejected",
    "withdrawn": "withdrawn",
    "follow_up_sent": "applied",
    "note": "applied",
}

# Things the user did rather than received — drawn hollow so the solid dots
# read as "someone else moved".
_OWN = {"follow_up_sent", "note", "applied"}

# The wait has a temperature (23 Sep 2026). Amber used to be binary — a tail
# either crossed `reminder_days` or it didn't — and on 277 real rows that put
# 150 of them in the same flat amber, a highlighted list rather than a scale.
# `heat()` grades it: 0 until the follow-up threshold, then linear to 100 at
# FULL_HEAT_DAYS. One number drives both the row's numeral and its tail
# through one color-mix in base.html, so the line and the figure always agree.
# Eight weeks is a judgement, not a measurement: it is where a thread has
# outlived every reply the author ever received (avg_days_to_resp is 4).
FULL_HEAT_DAYS = 56


def heat(silent_days, reminder_days: int) -> int:
    """0..100: how far a silence has run past the follow-up threshold."""
    if silent_days is None or silent_days < reminder_days:
        return 0
    span = max(FULL_HEAT_DAYS - reminder_days, 1)
    return min(100, round(100 * (silent_days - reminder_days) / span))


def role(event_type: str) -> str:
    """The colour role an event type draws in. An unmapped type falls back to
    neutral `applied` rather than erroring (.claude/rules/web-ui.md has the
    `recruiter_outreach` case that fell through here for a week)."""
    return _ROLE.get(event_type, "applied")


def live(last_type: str | None, silent_days, reminder_days: int) -> bool:
    """A wait is blue only while it is fresh AND someone else moved last; once
    it crosses the follow-up threshold it is a wait like any other and takes
    the heat. ONE definition, because two pages colour by it: the list's rail
    and tail (via build(), below) and /analytics' application squares
    (insights.py) — the same application must wear the same colour on both.
    It lived in applications.html until 25 Sep 2026."""
    return (last_type is not None and silent_days is not None
            and role(last_type) != "applied" and silent_days < reminder_days)


def _pct(value: float) -> str:
    return f"{max(0.0, min(100.0, value)):.3f}%"


def build(rows, events_by_app, now: datetime, reminder_days: int) -> dict:
    """Annotate `rows` in place with trace geometry; return axis metadata.

    `rows` need an `id`; `events_by_app` maps that id to a list of
    ``{"type", "occurred_at"}`` ordered oldest-first. Rows with no events at
    all (a saved job that was never applied to) get an empty trace rather
    than being dropped — they still occupy a line so the list stays a
    complete inventory.
    """
    stamps = [e["occurred_at"] for evs in events_by_app.values() for e in evs]
    t0 = min(stamps) if stamps else now - timedelta(days=14)
    # A day of headroom on the right keeps the "today" rule off the last dot.
    t1 = max(now, max(stamps) if stamps else now)
    span = (t1 - t0).total_seconds()
    if span <= 0:
        span = 86400.0
        t0 = t1 - timedelta(days=1)

    def x(when: datetime) -> float:
        return 100.0 * (when - t0).total_seconds() / span

    for r in rows:
        evs = events_by_app.get(r["id"], [])
        pts, tail, cap, silent = [], None, None, None
        for e in evs:
            pts.append({
                "x": _pct(x(e["occurred_at"])),
                "role": role(e["type"]),
                "hollow": e["type"] in _OWN and e["type"] != "applied",
                "label": f'{e["type"].replace("_", " ")} {e["occurred_at"]:%d %b %Y}',
            })
        if evs:
            last = evs[-1]
            if last["type"] in TERMINAL:
                cap = _pct(x(last["occurred_at"]))
            else:
                days = (now - last["occurred_at"]).days
                silent = max(days, 0)
                tail = {"a": _pct(x(last["occurred_at"])), "b": "100%",
                        "aging": silent >= reminder_days,
                        "role": role(last["type"])}
        r["pts"], r["tail"], r["cap"], r["silent_days"] = pts, tail, cap, silent
        r["heat"] = heat(silent, reminder_days)
        r["live"] = live(evs[-1]["type"] if evs else None, silent, reminder_days)
        r["trace_label"] = _describe(r, silent)

    return {"t0": t0, "t1": t1, "ticks": _ticks(t0, t1, span, x),
            "week": f"{100.0 * 7 * 86400 / span:.3f}%"}


def _describe(row, silent) -> str:
    """The native tooltip — this view has no JS, so `title` carries detail."""
    bits = [f'{row.get("company_display") or "?"} · {row.get("title_canonical") or "?"}']
    if row.get("applied_at"):
        bits.append(f'applied {row["applied_at"]:%d %b %Y}')
    elif row.get("origin") == "inbound":
        bits.append("inbound — not applied")
    if silent is not None:
        bits.append("no activity today" if silent == 0
                    else f"quiet {silent} day{'s' if silent != 1 else ''}")
    return " · ".join(bits)


def _ticks(t0: datetime, t1: datetime, span: float, x) -> list[dict]:
    """Axis labels: the start date, then month starts on a long search or
    week markers on a short one, and never anything crowding "today".

    Weekly labels thinned by two were the rule for every span until 23 Sep
    2026, and on the list — 12 weeks, trace capped near 20rem (UI rule 10) —
    the boxes of "2 Jul" and "16 Jul" measured 1px apart and "10 Sep" ran
    into "today". Month names are two to three letters and at most one per
    four weeks, so they fit whatever the column width; a thread of a few
    weeks on the detail page (full width) keeps its weeks.
    """
    weeks = max(1, int(span // (7 * 86400)))
    if weeks >= 8:
        return _month_ticks(t0, t1, x)
    every = 1 if weeks <= 10 else (2 if weeks <= 22 else 4)
    out, cur, i = [], t0, 0
    while cur <= t1:
        # Drop anything crowding the right edge — the axis ends in a "today"
        # label, and a week marker on top of it is unreadable.
        if i % every == 0 and x(cur) < 90:
            # %-d is glibc-only and raises on Windows — format wide, strip the
            # leading zero by hand.
            out.append({"x": _pct(x(cur)), "label": f"{cur:%d %b}".lstrip("0")})
        cur += timedelta(days=7)
        i += 1
    return out


def _month_ticks(t0: datetime, t1: datetime, x) -> list[dict]:
    """The start date, then the first of each month that falls comfortably
    inside the axis — not within 12% of the start label, not past 90% where
    the "today" label lives. It was 6% until 25 Sep 2026: a filtered list
    (`?visa=`) spanning 27 Jul to today put "Aug" at 7.1%, printed over
    "27 Jul", whose label takes about 12% of the list's capped trace column."""
    out = [{"x": _pct(0.0), "label": f"{t0:%d %b}".lstrip("0")}]
    year, month = t0.year, t0.month
    while True:
        month += 1
        if month > 12:
            month, year = 1, year + 1
        cur = datetime(year, month, 1, tzinfo=t0.tzinfo)
        if cur > t1:
            break
        pos = x(cur)
        if 12 <= pos < 90:
            out.append({"x": _pct(pos), "label": f"{cur:%b}"})
    return out
