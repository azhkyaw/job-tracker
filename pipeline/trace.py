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

import math
from datetime import date, datetime, timedelta

# Events that end a thread: the trace stops with a cap rather than running a
# waiting tail to today. Nothing is pending after these. An offer is NOT one
# (7 Oct 2026, migration 019): it is a round, and the thread it opens waits
# on you accepting or declining it, or on them withdrawing it or going quiet.
TERMINAL = {"rejected", "withdrawn"}


def closing(evs):
    """The event that ended the thread — its latest TERMINAL event — or None.

    Not "the last event, if it is terminal": things arrive after a close. A
    note filed on a rejection; a recruiter writing again after dropping you;
    and a rejection filed by hand for today, which a bare date anchors at
    local noon, BEFORE the approach email that came in that afternoon. On
    28 Sep 2026 9 of the 15 rejected inbound records ended on such an event
    and drew an open tail, four of them blue as if someone were engaged. The
    status never wavered: every TERMINAL type outranks every other in
    `application_status`'s precedence (migration 013), so a thread has a
    closing event exactly when its status is closed, and the trace and the
    status word now say the same thing. `evs` oldest-first, as build() takes
    them."""
    return next((e for e in reversed(evs) if e["type"] in TERMINAL), None)


# One interview is several `interview_invite` events — the invitation, the
# calendar notification, a reminder, the reply arranging it — so counting
# events overcounts rounds (8 Oct 2026, worklog task 76). A ROUND is the
# interview day: events whose email names the same day (`payload.stated_date`)
# are one round, and an event naming no day joins the round within this many
# days of its arrival, else starts one on its arrival day. Measured over the
# author's 19 interview threads on the day: right on 13, over by one on 4
# (a "Canceled event" mail filed as an invitation, a reply or availability
# mail far from its interview, and a CALL counted as a round — which is why
# `engaged` is left out below), the other 2 ambiguous in the mail itself.
# The number is shown beside the lines that produced it, so a wrong grouping
# is visible on the timeline, not silent in a statistic.
ROUND_SPAN_DAYS = 3
ROUND_EVENT = "interview_invite"
# Your word that an invitation line is NOT a round (`payload.round_is`,
# web.set_round_is): a reminder, a cancellation, a reply about the
# interview. The first real thread it was asked for derived 4 rounds and had
# sat 3 — a coding test, a recruiter screen, a technical — the fourth an
# availability reply naming the day after the technical; nothing in the
# mail tells that reply from an invitation. Excluded here and from every
# round the SQL anchors on (analytics.sat_sql).
NOT_A_ROUND = "none"
# Kinds of round (analytics.ROUND_KINDS) that are not rounds you reached: an
# automated behaviour questionnaire every applicant gets is a mechanism, like
# LinkedIn's screening timer, not progress (9 Oct 2026, two real threads).
# The whole ROUND wears the kind, so it is set once; its lines keep their
# label ("automated questionnaire") and leave every count.
NON_ROUND_KINDS = ("questionnaire",)
# What an invitation mail DOES (`payload.invite_role`, stage 4 —
# email_classifier.invite_detail, 9 Oct 2026): an invitation sets a round up,
# a reschedule moves one, a reminder confirms one, and these two are no round
# at all — the mails that made every over-count the day before.
NON_ROUND_ROLES = ("scheduling", "cancellation")


def role_of(e):
    """What the mail did (`payload.invite_role`), flat column or payload;
    None for a hand-filed line or one filed before the stage existed."""
    return e.get("invite_role") or (e.get("payload") or {}).get("invite_role")


def excluded(e) -> bool:
    """You said this line is not a round, or the mail said so itself."""
    return ((e.get("round_is") or (e.get("payload") or {}).get("round_is")) == NOT_A_ROUND
            or role_of(e) in NON_ROUND_ROLES)


def own_round(e) -> bool:
    """A round you filed by hand is a round of its own (9 Oct 2026): nobody
    hand-files a reminder, and the one real case was a recruiter screen
    filed on the day a test's invitation had named as its deadline — it
    merged into the test's round and the count stayed 2 for 3 sat."""
    return e.get("source") == "manual"


def _event_day(e, tz) -> tuple[date | None, date]:
    """(the day the email named, if date-shaped; the day it arrived)."""
    stated = e.get("stated_date") or (e.get("payload") or {}).get("stated_date")
    named = None
    if stated:
        try:
            named = date.fromisoformat(stated)
        except ValueError:
            named = None
    at = e["occurred_at"]
    return named, (at.astimezone(tz) if tz else at).date()


def rounds(evs, tz=None, counting_only: bool = True) -> list[dict]:
    """The interview rounds of one thread, oldest first, numbered from 1:
    ``{"n", "day", "named", "events", "went", "rate_event", "kind",
    "counts"}`` — the day, whether an email named it, the events in it, the
    rating any of them carries (`payload.went`, web.set_round_went), the id
    of the event the round's one "How did it go?" select posts to (the rated
    one, else the newest), its kind and whether it counts as a round you
    reached (not a NON_ROUND_KINDS kind). `evs` oldest-first; `tz` is the
    viewer's zone for an arrival day, UTC when None. Events may carry
    `payload` (the detail page) or flat `stated_date` / `went` /
    `round_kind` columns (analytics.facts, the list's fetch). Only the
    counting rounds are numbered and returned, unless `counting_only` is
    False (the detail page labels the others)."""
    out: list[dict] = []
    dateless = []

    def last_mail_round():
        return next((r for r in reversed(out) if not r.get("own")), None)

    for e in evs:
        if e["type"] != ROUND_EVENT or excluded(e):
            continue
        named, arrived = _event_day(e, tz)
        if own_round(e):
            out.append({"day": named or arrived, "named": named is not None, "events": [e], "own": True})
            continue
        role = role_of(e)
        last = last_mail_round()
        if role == "reschedule" and last is not None:
            # Moves the round last arranged: the round's day follows the mail.
            last["events"].append(e)
            if named is not None:
                last["day"], last["named"] = named, True
            continue
        if role == "reminder" and (named is not None or last is not None):
            # Confirms a round already arranged: the one naming its day, else
            # the last one — which takes the day if it had none.
            r = next((r for r in out if named is not None and r["day"] == named
                      and not r.get("own")), None) or last
            if r is None:
                out.append(r := {"day": named, "named": True, "events": []})
            elif named is not None and not r["named"]:
                r["day"], r["named"] = named, True
            r["events"].append(e)
            continue
        if named is None:
            if role is None:
                dateless.append((e, arrived))        # filed before the stage: nearest day, below
                continue
            # The stage read an invitation naming no day: a second mail for
            # the same test joins the round within the span of its arrival;
            # else its own round, from its arrival, which a later mail may
            # date — in order, so that mail finds it.
            near = [r for r in out if not r.get("own")
                    and abs((r["day"] - arrived).days) <= ROUND_SPAN_DAYS]
            if near:
                min(near, key=lambda r: abs((r["day"] - arrived).days))["events"].append(e)
            else:
                out.append({"day": arrived, "named": False, "events": [e]})
            continue
        r = next((r for r in out if r["day"] == named and not r.get("own")), None)
        if (r is None and last is not None and not last["named"]
                and abs((last["day"] - named).days) <= ROUND_SPAN_DAYS):
            # The mails arranging it came first and named no day: this one
            # names it (the bank thread's two invitations before the one
            # that locked the slot in, 9 Oct 2026).
            r = last
            r["day"], r["named"] = named, True
        if r is None:
            out.append(r := {"day": named, "named": True, "events": []})
        r["events"].append(e)
    for e, arrived in dateless:
        near = [r for r in out if not r.get("own") and abs((r["day"] - arrived).days) <= ROUND_SPAN_DAYS]
        if near:
            min(near, key=lambda r: abs((r["day"] - arrived).days))["events"].append(e)
        else:
            out.append({"day": arrived, "named": False, "events": [e]})
    # By day, then by the first event's time: a hand-filed round on the same
    # day as a mail-derived one is numbered by when each was first heard of.
    out.sort(key=lambda r: (r["day"], min(e["occurred_at"] for e in r["events"])))
    n = 0
    for r in out:
        r["events"].sort(key=lambda e: e["occurred_at"])
        rated = [e for e in r["events"] if _went_of(e)]
        pick = rated[-1] if rated else r["events"][-1]
        r["went"] = _went_of(pick)
        r["rate_event"] = pick.get("id")
        # The kind (analytics.ROUND_KINDS): the event the round is rated
        # through says it, else the newest event that names one.
        kinds = [e for e in r["events"] if kind_of(e)]
        r["kind"] = kind_of(pick) or (kind_of(kinds[-1]) if kinds else None)
        r["counts"] = r["kind"] not in NON_ROUND_KINDS
        if r["counts"]:
            n += 1
        r["n"] = n if r["counts"] else None
    return [r for r in out if r["counts"]] if counting_only else out


def _went_of(e):
    return e.get("went") or (e.get("payload") or {}).get("went")


def kind_of(e):
    """The kind an event names (`payload.round_kind`), flat column or payload."""
    return e.get("round_kind") or (e.get("payload") or {}).get("round_kind")


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

def _flag(e, key):
    """A payload key, flat (the list's fetch) or in `payload` (the detail page)."""
    return e.get(key) if key in e else (e.get("payload") or {}).get(key)


def own(e) -> bool:
    """Drawn hollow, so the solid marks read as "someone else moved" — the
    detail page's trace and the list's story both ask this.
    You did this, rather than received it: a follow-up, or a note you wrote
    — by hand, or in mail you sent. A note that came in mail THEY sent is
    theirs: the classifier files an employer's status update ("your
    application is under review") as a note, and on 9 Oct 2026 36 of the 57
    notes on the author's applications were exactly that, every one drawn as
    a hollow "you did this" mark until then."""
    if e["type"] == "follow_up_sent":
        return True
    return e["type"] == "note" and not (e.get("source") == "email" and not e.get("sent"))

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


def wait(evs, now: datetime, reminder_days: int) -> dict:
    """The state of a thread's wait, exactly as the list draws it: the days
    since its last event, unless it has closed at ANY point (closing()),
    their heat, and whether it is live. One reading for every surface that
    colours a thread — the list's rows (build(), below), its status bar
    (web._funnel's bands) and /analytics' squares (insights) — since the same
    application must wear the same colour on all three. `evs` oldest-first."""
    last = None if closing(evs) else (evs[-1] if evs else None)
    silent = max((now - last["occurred_at"]).days, 0) if last else None
    return {"silent_days": silent, "heat": heat(silent, reminder_days),
            "live": live(last["type"] if last else None, silent, reminder_days)}


def tone(status: str, is_live: bool) -> str:
    """The colour a thread wears, as a `t-<tone>` class in base.html: its own
    status once it has closed, or an offer (green, the one colour that is the
    thread's own rather than the state of its wait); otherwise the wait —
    `live` blue, or `wait`, grey to amber by its heat. `status` is the
    display status (confirmation reads as applied)."""
    if status in TERMINAL or status == "offer":
        return status
    return "live" if is_live else "wait"


# A status bar segment's runs, left to right, by how long each thread has
# been quiet, shortest first: the live (someone else moved, fresh), then the
# waits as their heat rises. So a segment reads in the order the legend's
# breakdown under `applied` does — fresh, inside the odds, past the odds —
# and the first look at a segment with a live thread is its blue.
_BAND_ORDER = {"live": 0, "wait": 1, "offer": 2, "rejected": 3, "withdrawn": 4}


def bands(threads) -> list[dict]:
    """A segment painted with its own threads (9 Oct 2026): `threads` is
    (tone, heat) pairs, one per application; the result is runs of one
    colour, [{"tone", "heat", "n"}], in _BAND_ORDER and, among the waits, by
    rising heat. A heat counts only on a wait: every other tone is one
    colour whatever its silence."""
    keyed = sorted(((t, h if t == "wait" else 0) for t, h in threads),
                   key=lambda th: (_BAND_ORDER.get(th[0], len(_BAND_ORDER)), th[1]))
    out: list[dict] = []
    for t, h in keyed:
        if out and out[-1]["tone"] == t and out[-1]["heat"] == h:
            out[-1]["n"] += 1
        else:
            out.append({"tone": t, "heat": h, "n": 1})
    return out


# ------------------------------------------------------------------ the story
# The list's middle column since 9 Oct 2026 (worklog task 86): each thread as
# the things that happened to it, in order, one named STATION each, the days
# between written on the line. It replaced a calendar trace on which 79% of
# rows were a dot and a tail restating the applied date and the days-quiet
# numeral, and where the answers, which come in the first days (the median
# rejection on day 3, 84% of first answers within a week), got 3.6px a day.
# The detail page keeps its calendar trace (build(), below): one thread, its
# own dates. Words are the caller's (web.py's, rule 15): story() groups and
# measures, `words(station)` names each station.
#
# Merged into the station before them: the receipt of an application, a
# round's further mails, and a run of one kind (three status mails are
# "update ×3"). The thread ends at its close (closing()), like the trace.

# The widest a story is drawn before its middle folds into "+N more": the
# list's story column at its 23rem cap, and about a phone's width.
STORY_BUDGET_PX = 340
# Every station a story can hold: the caller's words name each (web.py's, held
# to this registry by the web suite).
STATION_KEYS = ("applied", "approached", "saved", "viewed", "update", "touch", "reached",
                "round", "questionnaire", "own", "offer", "rejected", "withdrawn", "other", "more")
_MERGE_RUNS = {"viewed", "update", "touch", "reached", "own"}
# The stations a crowded story names last: what a round, an offer, the start
# and the close say matters more than a status mail or your own note.
_MINOR = {"viewed", "update", "touch", "reached", "own", "other"}


def _text_px(text: str) -> float:
    """IBM Plex Sans Condensed at the station label's .71rem, near enough to
    decide a fold; the CSS clips with a fade if a guess runs over."""
    return len(text) * 4.9 + 4


def _gap_px(days, compact: bool = False) -> int:
    """A connector a little longer for a longer gap, with room for its number;
    `compact`, only the room for its number (a crowded story's first give)."""
    if days is None:
        return 10
    return round(_text_px(f"{days}d") + (8 if compact else 2 * (4 + 3 * math.log2(1 + days))))


def tail_px(quiet) -> int:
    """The wait at the end of an open story, a little longer as it grows."""
    return round(12 + 7 * math.log2(1 + max(quiet, 0)))


_STARTS = ("applied", "confirmation", "recruiter_outreach", "interested")


def _story_order(evs, tz) -> list:
    """The events in the order the story tells them: a thread starts at its
    start and ends at its close. Two same-day orderings say otherwise on real
    records (9 Oct 2026): a status mail or a questionnaire minutes BEFORE the
    extension's capture of the application it answers (6 on the record), and
    a close filed by hand for a day, which anchors at local noon, before that
    afternoon's approach (3 on /inbound; the same trap closing() handles for
    the trace). So what sorts before the start on its own local day is told
    after it, and what sorts after the close on ITS day before it. Events on
    other days keep their order; anything a day after the close is dropped,
    since the thread had ended."""
    def day(e):
        return (e["occurred_at"].astimezone(tz) if tz else e["occurred_at"]).date()
    seq = list(evs)
    start = next((e for e in seq if e["type"] in _STARTS), None)
    if start is not None:
        i = next(k for k, e in enumerate(seq) if e is start)
        early = [e for e in seq[:i] if day(e) == day(start)]
        if early:
            rest = [e for e in seq if not any(e is x for x in early)]
            j = next(k for k, e in enumerate(rest) if e is start)
            seq = rest[: j + 1] + early + rest[j + 1:]
    end = closing(seq)
    if end is not None:
        i = next(k for k, e in enumerate(seq) if e is end)
        seq = seq[:i] + [e for e in seq[i + 1:] if day(e) == day(end)] + [end]
    return seq


def stations(evs, *, today: date, tz=None) -> list[dict]:
    """A thread's stations, oldest first: ``{"key", "events", "at", "end",
    "count", ...}``. `key` is one of applied / approached / saved (the
    thread's start, `start` True) / applied / viewed / update (their status
    mail) / touch / reached / round / questionnaire / own (your replies,
    follow-ups and notes, `own_words` in arrival order) / offer / rejected /
    withdrawn (the close) / other. A round carries `n`, `kind`, `day` and
    `upcoming` (its day is still to come). `evs` oldest-first, flat fetch or
    payload alike; `today` is the viewer's day."""
    seq = _story_order(evs, tz)
    end = closing(seq)
    all_rounds = rounds(evs, tz, counting_only=False)
    round_of = {id(e): r for r in all_rounds for e in r["events"]}
    out: list[dict] = []
    seen_rounds: set[int] = set()

    def add(key, e, **kw):
        last = out[-1] if out else None
        if last and key in _MERGE_RUNS and last["key"] == key:
            last["events"].append(e)
            last["end"], last["count"] = e["occurred_at"], last["count"] + 1
            if key == "own" and kw.get("own_word") not in last["own_words"]:
                last["own_words"].append(kw["own_word"])
            return
        s = {"key": key, "events": [e], "at": e["occurred_at"], "end": e["occurred_at"],
             "count": 1, "start": not out}
        if key == "own":
            s["own_words"] = [kw.pop("own_word")]
        s.update(kw)
        out.append(s)

    for e in seq:
        t = e["type"]
        if t in TERMINAL:
            if e is end:
                add(t, e)
            continue                                   # an earlier one is a recording artefact
        if not out:
            # The thread's start: the submission (or its receipt, on a record
            # an email started), the approach, or a capture you kept.
            if t in ("applied", "confirmation"):
                add("applied", e)
                continue
            if t == "recruiter_outreach":
                add("approached", e)
                continue
            if t == "interested":
                add("saved", e)
                continue
        if t in ("applied", "confirmation", "interested"):
            # You applied to what they started; any other is the receipt, a
            # re-capture or a save, which the start already says.
            if t == "applied" and not any(s["key"] == "applied" for s in out):
                add("applied", e)
            continue
        if t == ROUND_EVENT:
            r = round_of.get(id(e))
            if r is None:                              # excluded: no round, an engagement
                if not all_rounds:
                    add("touch", e)
                continue
            if id(r) in seen_rounds:
                continue
            seen_rounds.add(id(r))
            if r["counts"]:
                add("round", e, n=r["n"], kind=r["kind"], day=r["day"], upcoming=r["day"] > today)
            else:
                add("questionnaire", e, kind=r["kind"], day=r["day"])
            continue
        if t == "note" or t == "follow_up_sent":
            if not own(e):
                add("update", e)
            elif _flag(e, "emailed") != "not_needed":  # "no email needed" is no event of the thread's
                add("own", e, own_word=("followed_up" if t == "follow_up_sent"
                                        else "replied" if (_flag(e, "reply") or e.get("sent"))
                                        else "emailed" if _flag(e, "emailed") == "sent"
                                        else "note"))
            continue
        if t == "viewed":
            add("viewed", e)
        elif t == "engaged":
            add("touch", e)
        elif t == "recruiter_outreach":
            add("reached", e)
        elif t == "offer":
            add("offer", e)
        else:
            add("other", e)
    return out


def story(evs, *, today: date, tz=None, quiet=None, words, budget: int = STORY_BUDGET_PX) -> dict:
    """The list's story for one thread: ``{"stations": [...], "tail": px or
    None}``. Each station has stations()' keys plus `word`, `on` and `title`
    from `words(station)` — the caller's vocabulary — and, after the first,
    `link`: the connector before it, ``{"w": px, "days": whole days or None,
    "fold": bool}``. An open thread (not closed, `quiet` known) ends in a
    tail. Too wide for `budget`, it gives way in four steps, each only as
    far as it must: the lines lose their extra length; minor stations lose
    their words (_MINOR, oldest first); runs of them fold in place into one
    "+N" station (`key` "more", `folded` the stations in it); then the
    oldest of the middle folds. The start and the last station always stay,
    and no two folds sit side by side."""
    sts = stations(evs, today=today, tz=tz)
    for s in sts:
        s.update(words(s))
    open_ = closing(evs) is None and quiet is not None and bool(sts)
    tail = tail_px(quiet) if open_ else None

    def gap(a, b):
        days = (b["at"] - a["end"]).total_seconds() / 86400
        return round(days) if days >= 1 else None

    compact = False

    def width(seq):
        w = sum(max(16, _text_px(" ".join(x for x in (s["word"], s["on"]) if x))) for s in seq)
        for a, b in zip(seq, seq[1:]):
            w += 18 if a["key"] == "more" or b["key"] == "more" else _gap_px(gap(a, b), compact)
        return w + (tail or 0)

    # Too wide, a story gives up the least first. The lines' extra length
    # goes (their days are written on them); then the minor stations' names,
    # oldest first, keeping their mark, place and title. Folding first hid
    # all three rounds of a real thread inside "+5 more" while its status
    # mails kept their words (9 Oct 2026).
    compact = width(sts) > budget
    for s in sts[1:-1]:
        if width(sts) <= budget:
            break
        if s["key"] in _MINOR:
            s["word"], s["on"] = "", None

    def fold_in(i, j):
        """sts[i:j] become one "+N" station, in their place, taking in a fold
        on either side: two "+N" side by side say less than one."""
        while i > 1 and sts[i - 1]["key"] == "more":
            i -= 1
        while j < len(sts) - 1 and sts[j]["key"] == "more":
            j += 1
        inner = [x for s in sts[i:j] for x in (s["folded"] if s["key"] == "more" else [s])]
        more = {"key": "more", "folded": inner, "events": [e for s in inner for e in s["events"]],
                "at": inner[0]["at"], "end": inner[-1]["end"], "count": len(inner), "start": False}
        more.update(words(more))
        sts[i:j] = [more]

    # Still too wide: a run of minor stations folds where it stands, oldest
    # first, so the rounds on either side keep their place and their names.
    i = 1
    while width(sts) > budget and i < len(sts) - 1:
        j = i
        while j < len(sts) - 1 and sts[j]["key"] in _MINOR:
            j += 1
        if j - i >= 2:
            fold_in(i, j)
        i += 1
    # And then the oldest of the middle, whatever it is, two at a time; the
    # start and the close always stay, and a fold takes in any beside it.
    while width(sts) > budget and len(sts) > 3:
        fold_in(1, min(3, len(sts) - 1))
    for i, s in enumerate(sts):
        if i:
            prev = sts[i - 1]
            folds = "more" in (prev["key"], s["key"])
            days = None if folds else gap(prev, s)
            s["link"] = {"w": 18 if folds else _gap_px(days, compact), "days": days, "fold": folds}
        else:
            s["link"] = None
    return {"stations": sts, "tail": tail}


def build_stories(rows, events_by_app, now: datetime, reminder_days: int, tz, words_for) -> None:
    """The list's annotation, beside build()'s for the detail page: each row's
    wait (`silent_days`, `heat`, `live`, wait()'s one reading), its tooltip,
    and its `story`, named by `words_for(row)`. `events_by_app` oldest-first."""
    today = (now.astimezone(tz) if tz else now).date()
    for r in rows:
        evs = events_by_app.get(r["id"], [])
        w = wait(evs, now, reminder_days)
        r["silent_days"], r["heat"], r["live"] = w["silent_days"], w["heat"], w["live"]
        r["trace_label"] = _describe(r, w["silent_days"])
        r["story"] = story(evs, today=today, tz=tz, quiet=w["silent_days"], words=words_for(r))


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
                "hollow": own(e),
                "label": f'{e["type"].replace("_", " ")} {e["occurred_at"]:%d %b %Y}',
            })
        w = wait(evs, now, reminder_days)
        silent = w["silent_days"]
        end = closing(evs)
        if end:
            cap = _pct(x(end["occurred_at"]))
        elif evs:
            last = evs[-1]
            tail = {"a": _pct(x(last["occurred_at"])), "b": "100%",
                    "aging": silent >= reminder_days,
                    "role": role(last["type"])}
        r["pts"], r["tail"], r["cap"], r["silent_days"] = pts, tail, cap, silent
        r["heat"], r["live"] = w["heat"], w["live"]
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
