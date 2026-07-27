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
                "role": _ROLE.get(e["type"], "applied"),
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
                        "role": _ROLE.get(last["type"], "applied")}
        r["pts"], r["tail"], r["cap"], r["silent_days"] = pts, tail, cap, silent
        r["trace_label"] = _describe(r, silent)

    return {"t0": t0, "t1": t1, "ticks": _ticks(t0, t1, span, x),
            "week": f"{100.0 * 7 * 86400 / span:.3f}%"}


def _describe(row, silent) -> str:
    """The native tooltip — this view has no JS, so `title` carries detail."""
    bits = [f'{row.get("company_display") or "?"} · {row.get("title_canonical") or "?"}']
    if row.get("applied_at"):
        bits.append(f'applied {row["applied_at"]:%d %b %Y}')
    if silent is not None:
        bits.append("no activity today" if silent == 0
                    else f"quiet {silent} day{'s' if silent != 1 else ''}")
    return " · ".join(bits)


def _ticks(t0: datetime, t1: datetime, span: float, x) -> list[dict]:
    """Week markers, thinned so labels never collide on a long search."""
    weeks = max(1, int(span // (7 * 86400)))
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
