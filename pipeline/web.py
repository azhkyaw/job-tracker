"""Phase 1 web UI (design doc §6.6, bare version).

Four surfaces:
  /                    applications table + pipeline funnel strip
  /applications/{id}   detail: timeline, postings, contacts, linked emails
  /applications/new    manual entry — the third ingest path alongside the
                        extension and Gmail, for applications neither of
                        those can reach (pre-dates this system, or the
                        posting/extension has failed)
  /applications/{id}/edit  correct a record after the fact — the companion to
                        manual entry, for the typo you only notice later or
                        the listing URL you didn't have at entry time
  /triage               pending emails; resolve = link / create / ignore

Server-rendered Jinja + plain forms (POST-redirect-GET). No JS build step,
no framework — this is a single-user ops tool that must stay maintainable.
"""

from __future__ import annotations

from dataclasses import replace as _replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import hmac

import psycopg
from fastapi import FastAPI, Form, Header, HTTPException, Request
from jinja2 import pass_context
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import analytics, auth, config, db, dedup, gmail_imap, gmail_oauth, ingest, joburl, mailbox, matcher
from .email_classifier import norm_company

app = FastAPI(title="Job Tracker")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _zoneinfo(name: str | None) -> ZoneInfo:
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    return ZoneInfo("UTC")


# Everything is stored and compared in UTC (postgres timestamptz); these two
# filters are the ONLY place DISPLAY converts to the viewer's timezone —
# read from request.state.tz, set once per request by _login_user(). Business
# logic (analytics.py reminders, matcher.py day-difference scoring) must never
# use these or otherwise convert — only rendering should.
#
# The one sanctioned INPUT conversion is ingest.local_date_to_utc(), used by
# the manual-entry (/applications/new) and edit (/applications/{id}/edit)
# forms: a bare form date has no instant of its own, so it's anchored in the
# user's zone — at the time they typed, or local noon when they left the time
# blank — and stored UTC like everything else. That's parsing, not display or
# business logic — it doesn't contradict the rule above.
@pass_context
def _dt(context, v):
    if not v:
        return "—"
    tz = getattr(context["request"].state, "tz", None) or ZoneInfo("UTC")
    return v.astimezone(tz).strftime("%d %b %Y")


@pass_context
def _dtt(context, v):
    if not v:
        return "—"
    tz = getattr(context["request"].state, "tz", None) or ZoneInfo("UTC")
    return v.astimezone(tz).strftime("%d %b %Y %H:%M")


@pass_context
def _theme(context):
    """'auto' | 'light' | 'dark' for the data-theme attribute on <html>.
    login/signup never call _login_user (no session yet), so
    request.state.theme is unset there — falls back to 'auto', letting
    prefers-color-scheme decide rather than raising or forcing light."""
    request = context.get("request")
    if request is None:
        return "auto"
    return getattr(request.state, "theme", None) or "auto"


templates.env.filters["dt"] = _dt
templates.env.filters["dtt"] = _dtt
templates.env.globals["theme"] = _theme


class AuthRequired(Exception):
    pass


@app.exception_handler(AuthRequired)
def _auth_required(request: Request, exc: AuthRequired):
    return RedirectResponse("/login", status_code=303)


def _login_user(request: Request) -> dict:
    """Resolve the session cookie to a user (admin connection — the users and
    sessions tables are deliberately outside the RLS role's reach)."""
    with db.connect() as conn:
        user = auth.session_user(conn, request.cookies.get("session"))
    if user is None:
        raise AuthRequired()
    request.state.tz = _zoneinfo(user.get("timezone"))
    request.state.theme = user.get("theme") or "auto"
    return user

# Display collapses the applied-family; the event log keeps the distinction.
DISPLAY_STATUS = {"confirmation": "applied"}
FUNNEL_ORDER = ["interested", "applied", "viewed", "interview_invite",
                "offer", "rejected", "withdrawn"]
STATUS_LABEL = {"interview_invite": "interviewing"}


def _display(status: str) -> str:
    status = DISPLAY_STATUS.get(status, status)
    return STATUS_LABEL.get(status, status)


def _pending_count(conn) -> int:
    """Nav badge — actionable lane only. recruiter_outreach pending emails
    have their own inbound triage lane and their own count; surfacing them
    here would nag the nav badge with cold-pitch volume the user can't act on
    the same way as a rejection to file."""
    return conn.execute(
        "SELECT (SELECT count(*) FROM emails WHERE triage_state = 'pending' "
        "        AND classification IS DISTINCT FROM 'recruiter_outreach') + "
        "       (SELECT count(*) FROM duplicate_candidates WHERE state = 'pending') AS n"
    ).fetchone()["n"]


def _funnel(conn, user_id, origin: str | None = None) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.status, count(*) AS n
        FROM application_status s
        JOIN applications a ON a.id = s.application_id
        WHERE s.user_id = %(user_id)s
          AND (%(origin)s::text IS NULL OR a.origin = %(origin)s)
        GROUP BY s.status
        """, {"user_id": user_id, "origin": origin}).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        key = DISPLAY_STATUS.get(r["status"], r["status"])
        counts[key] = counts.get(key, 0) + r["n"]
    return [{"key": s, "label": STATUS_LABEL.get(s, s), "n": counts[s]}
            for s in FUNNEL_ORDER if counts.get(s)]


# --------------------------------------------------------------------------- applications

@app.get("/")
def applications(request: Request, deleted: str | None = None, origin: str | None = None):
    user = _login_user(request)
    origin = origin if origin in ("applied", "inbound", "saved") else None
    with db.connect_scoped(user["id"]) as conn:
        user_id = user["id"]
        rows = conn.execute(
            """
            SELECT a.id, a.focused, a.origin, j.company_norm, j.title_canonical, s.status,
                   COALESCE(
                     (SELECT p.company_raw FROM postings p
                       WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                       ORDER BY p.captured_at DESC LIMIT 1),
                     j.company_norm) AS company_display,
                   (SELECT min(occurred_at) FROM events e
                     WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at,
                   (SELECT max(occurred_at) FROM events e
                     WHERE e.application_id = a.id)                        AS last_activity,
                   (SELECT string_agg(DISTINCT p.platform, ', ')
                      FROM postings p WHERE p.job_id = a.job_id)          AS platforms
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            JOIN application_status s ON s.application_id = a.id
            WHERE a.user_id = %(user_id)s
              AND (%(origin)s::text IS NULL OR a.origin = %(origin)s)
            ORDER BY last_activity DESC NULLS LAST
            """, {"user_id": user_id, "origin": origin}).fetchall()
        for r in rows:
            r["status"] = _display(r["status"])
        return templates.TemplateResponse(request=request, name="applications.html", context={
            "rows": rows,
            "funnel": _funnel(conn, user_id, origin),
            "pending": _pending_count(conn),
            "reminders": analytics.reminders(conn, user_id),
            "reminder_days": config.REMINDER_DAYS,
            "deleted": deleted,
            "origin": origin,
        })


def _get_application(conn, app_id: str) -> dict:
    try:
        row = conn.execute(
            """
            SELECT a.id, a.user_id, a.job_id, a.focused, a.applied_via_posting_id,
                   j.company_norm, j.title_canonical, s.status,
                   COALESCE(
                     (SELECT p.company_raw FROM postings p
                       WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                       ORDER BY p.captured_at DESC LIMIT 1),
                     j.company_norm) AS company_display
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            JOIN application_status s ON s.application_id = a.id
            WHERE a.id = %s::uuid
            """, (app_id,)).fetchone()
    except psycopg.errors.InvalidTextRepresentation:
        row = None
    if row is None:
        raise HTTPException(404, "application not found")
    return row


# --------------------------------------------------------------------------- manual entry
#
# Declared here — BEFORE @app.get("/applications/{app_id}") — deliberately.
# Starlette matches routes in declaration order; "/applications/new" would
# otherwise be swallowed by the "{app_id}" path param below, and since
# _get_application() catches the resulting UUID-parse error, a route
# declared after it would silently 404 rather than error loudly.

_OUTCOME_TYPES = {"viewed", "interview_invite", "offer", "rejected", "withdrawn"}


def _manual_ctx(conn, user, tz, *, form, error=None, added=None, merged=False):
    return {
        "form": form, "error": error, "added": added, "merged": merged,
        "tz_label": user.get("timezone") or "UTC",
        "today": datetime.now(tz).strftime("%Y-%m-%d"),
        "pending": _pending_count(conn),
    }


@app.get("/applications/new")
def manual_entry_form(request: Request, company: str = "", title: str = "",
                       url: str = "", platform: str = "linkedin", location: str = "",
                       added: str | None = None, merged: str | None = None,
                       date: str | None = None):
    user = _login_user(request)
    tz = request.state.tz
    with db.connect_scoped(user["id"]) as conn:
        added_info = None
        if added:
            try:
                added_info = conn.execute(
                    """
                    SELECT a.id,
                           COALESCE(
                             (SELECT p.company_raw FROM postings p
                               WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                               ORDER BY p.captured_at DESC LIMIT 1),
                             j.company_norm) AS company_display,
                           j.title_canonical
                    FROM applications a JOIN jobs j ON j.id = a.job_id
                    WHERE a.id = %s::uuid
                    """, (added,)).fetchone()
            except psycopg.errors.InvalidTextRepresentation:
                added_info = None
        form = {"company": company, "title": title, "url": url,
                "platform": platform or "linkedin", "location": location,
                "applied_date": date or datetime.now(tz).strftime("%Y-%m-%d"),
                "applied_time": "", "outcome": "", "outcome_date": "",
                "outcome_time": "", "jd_text": "", "note": "",
                "focused": "", "external": "", "confirm": ""}
        return templates.TemplateResponse(
            request=request, name="manual_entry.html",
            context=_manual_ctx(conn, user, tz, form=form,
                                added=added_info, merged=bool(merged)))


@app.post("/applications/new")
def manual_entry_create(
    request: Request,
    # Form("") not Form(...) even for "required" fields — an empty submission
    # must reach our own validation below (friendly re-rendered error,
    # values preserved) rather than FastAPI's raw 422 JSON short-circuiting
    # the route before it runs.
    company: str = Form(""), title: str = Form(""), platform: str = Form(""),
    applied_date: str = Form(""), applied_time: str = Form(""), url: str = Form(""),
    location: str = Form(""), outcome: str = Form(""), outcome_date: str = Form(""),
    outcome_time: str = Form(""), jd_text: str = Form(""), note: str = Form(""),
    focused: str = Form(""), external: str = Form(""), confirm: str = Form(""),
    after: str = Form("view"),
):
    user = _login_user(request)
    tz = request.state.tz
    from psycopg.types.json import Json

    form = {"company": company, "title": title, "url": url, "platform": platform,
            "location": location, "applied_date": applied_date,
            "applied_time": applied_time, "outcome": outcome,
            "outcome_date": outcome_date, "outcome_time": outcome_time,
            "jd_text": jd_text, "note": note, "focused": focused,
            "external": external, "confirm": confirm}

    company_s, title_s, location_s = company.strip(), title.strip(), location.strip()
    company_norm = norm_company(company_s) or None
    outcome_s, note_s, jd_s = outcome.strip(), note.strip(), jd_text.strip()
    # Same three-state semantics as /captures' payload.focused: bool | None —
    # "" (unset) leaves focused untouched, anything else must be yes/no.
    focused_val = {"yes": True, "no": False}.get(focused.strip().lower())
    # Same concept as /captures' payload.external — True = redirected to the
    # employer's site to finish, False = handled on-platform (LinkedIn's
    # "Easy Apply", Indeed Apply, etc.). "" (unset) omits the key entirely,
    # matching how a captured application looks before this was tracked.
    external_val = {"yes": True, "no": False}.get(external.strip().lower())

    error = None
    applied_d = outcome_d = None
    applied_t = outcome_t = None            # None = borrow the submission time-of-day
    platform_job_id = canonical_url = None

    if not company_s:
        error = "Enter the company name."
    elif not company_norm:
        error = f'Enter the plain company name — "{company_s}" normalizes to nothing.'
    elif not title_s:
        error = "Enter the job title."
    elif platform not in ("linkedin", "jobstreet", "indeed", "other"):
        error = "Unknown platform."
    else:
        try:
            applied_d = datetime.strptime(applied_date, "%Y-%m-%d").date()
        except ValueError:
            error = "Enter a valid applied date."
        if error is None and applied_time.strip():
            try:
                applied_t = datetime.strptime(applied_time.strip(), "%H:%M").time()
            except ValueError:
                error = "Enter a valid applied time (HH:MM), or leave it blank."
        if error is None and applied_d > datetime.now(tz).date():
            error = "The applied date can't be in the future."
        if error is None and outcome_s:
            if outcome_s not in _OUTCOME_TYPES:
                error = "Unknown outcome."
            elif not outcome_date:
                error = "Pick a date for that outcome."
            else:
                try:
                    outcome_d = datetime.strptime(outcome_date, "%Y-%m-%d").date()
                except ValueError:
                    error = "Enter a valid outcome date."
                if error is None and outcome_time.strip():
                    try:
                        outcome_t = datetime.strptime(outcome_time.strip(), "%H:%M").time()
                    except ValueError:
                        error = "Enter a valid outcome time (HH:MM), or leave it blank."
                if error is None and outcome_d < applied_d:
                    error = "The outcome can't be dated before the application."
        if error is None:
            parsed_platform, platform_job_id, canonical_url = joburl.parse(url or None)
            if parsed_platform and parsed_platform != platform:
                error = (f"That looks like a {parsed_platform} URL, but you picked "
                         f"{platform}. Fix one of them.")

    with db.connect_scoped(user["id"]) as conn:
        if error:
            return templates.TemplateResponse(
                request=request, name="manual_entry.html",
                context=_manual_ctx(conn, user, tz, form=form, error=error),
                status_code=400)

        # Without a URL there's no reliable dedup key (postings_platform_job_uidx
        # only fires when platform_job_id is set) — so this is the one path
        # where a duplicate could slip in silently. One confirming click
        # closes it, consistent with "never a silent guess" elsewhere here.
        if not platform_job_id and not confirm:
            near_dup = conn.execute(
                """
                SELECT COALESCE(
                         (SELECT p.company_raw FROM postings p
                           WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                           ORDER BY p.captured_at DESC LIMIT 1), j.company_norm) AS company_display,
                       j.title_canonical,
                       (SELECT min(occurred_at) FROM events e
                         WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at
                FROM applications a JOIN jobs j ON j.id = a.job_id
                WHERE a.user_id = %s AND j.company_norm = %s AND j.title_canonical = %s
                """, (user["id"], company_norm, title_s)).fetchone()
            if near_dup:
                when = (near_dup["applied_at"].astimezone(tz).strftime("%d %b %Y")
                        if near_dup["applied_at"] else "an unknown date")
                form["confirm"] = "1"
                return templates.TemplateResponse(
                    request=request, name="manual_entry.html",
                    context=_manual_ctx(conn, user, tz, form=form, error=(
                        f'You already track "{near_dup["company_display"]} · '
                        f'{near_dup["title_canonical"]}" (applied {when}). '
                        "Submit again to add it anyway.")),
                    status_code=400)

        with conn.transaction():
            applied_at = ingest.local_date_to_utc(applied_d, tz, t=applied_t)
            outcome_at = None
            if outcome_d:
                outcome_at = ingest.local_date_to_utc(outcome_d, tz, t=outcome_t)
                if outcome_at <= applied_at:      # same-day: keep it strictly later
                    outcome_at = applied_at + timedelta(seconds=1)

            r = ingest.upsert_record(
                conn, user["id"], platform=platform, platform_job_id=platform_job_id,
                url=canonical_url, company=company_s, title=title_s,
                jd_text=jd_s or None, location=location_s or None,
                captured_via="manual", captured_at=applied_at)
            app_id = r["application_id"]

            has_applied = conn.execute(
                "SELECT 1 FROM events WHERE application_id = %s AND type = 'applied'",
                (app_id,)).fetchone()
            if has_applied is None:            # double-click / re-merge safe
                applied_payload = ({"external": external_val} if external_val is not None
                                   else {})
                conn.execute(
                    "INSERT INTO events (user_id, application_id, type, source, "
                    "occurred_at, payload) VALUES (%s, %s, 'applied', 'manual', %s, %s)",
                    (user["id"], app_id, applied_at, Json(applied_payload)))

            if outcome_at is not None:
                conn.execute(
                    "INSERT INTO events (user_id, application_id, type, source, "
                    "occurred_at, payload) VALUES (%s, %s, %s, 'manual', %s, %s)",
                    (user["id"], app_id, outcome_s, outcome_at, Json({})))

            if note_s:
                conn.execute(
                    "INSERT INTO events (user_id, application_id, type, source, "
                    "occurred_at, payload) VALUES (%s, %s, 'note', 'manual', %s, %s)",
                    (user["id"], app_id, applied_at, Json({"note": note_s})))

            if focused_val is not None:    # explicit tag always wins (§7), matches /captures
                conn.execute("UPDATE applications SET focused = %s WHERE id = %s",
                             (focused_val, app_id))

            merged = r["application_existed"]

    if after == "another":
        qs = f"added={app_id}&platform={platform}&date={applied_date}"
        if merged:
            qs += "&merged=1"
        return RedirectResponse(f"/applications/new?{qs}", status_code=303)
    return RedirectResponse(f"/applications/{app_id}", status_code=303)


@app.get("/applications/{app_id}")
def application_detail(request: Request, app_id: str, saved: str | None = None):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn:
        a = _get_application(conn, app_id)
        events = conn.execute(
            "SELECT type, source, occurred_at, payload FROM events "
            "WHERE application_id = %s ORDER BY occurred_at DESC", (a["id"],)).fetchall()
        postings = conn.execute(
            "SELECT platform, url, title, captured_via, captured_at, location, "
            "       posted_label, reposted, ats, jd_text "
            "FROM postings WHERE job_id = %s ORDER BY captured_at", (a["job_id"],)).fetchall()
        contacts = conn.execute(
            "SELECT id, name, role, url, approached, notes FROM contacts "
            "WHERE job_id = %s ORDER BY name", (a["job_id"],)).fetchall()
        emails = conn.execute(
            "SELECT id, sender, subject, received_at, classification FROM emails "
            "WHERE matched_application_id = %s ORDER BY received_at DESC", (a["id"],)).fetchall()
        extractions = conn.execute(
            """
            SELECT DISTINCT ON (x.posting_id) x.*, p.platform
            FROM extractions x JOIN postings p ON p.id = x.posting_id
            WHERE p.job_id = %s
            ORDER BY x.posting_id, x.extracted_at DESC
            """, (a["job_id"],)).fetchall()
        artifacts = conn.execute(
            "SELECT id, kind, content, model, created_at FROM artifacts "
            "WHERE application_id = %s ORDER BY created_at DESC", (a["id"],)).fetchall()
        # Most recent job only — a prior dead-lettered attempt shouldn't mask
        # a fresh retry the user kicked off after fixing whatever broke it.
        cover_job = conn.execute(
            "SELECT state, attempts, last_error FROM job_queue "
            "WHERE type = 'generate_cover_letter' AND payload->>'application_id' = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (str(a["id"]),)).fetchone()
        cover_error = None
        if cover_job and cover_job["last_error"]:
            # last_error is a full traceback (worker.py); the exception's own
            # message is the last line — the rest is noise for this audience.
            cover_error = cover_job["last_error"].strip().splitlines()[-1]
        return templates.TemplateResponse(request=request, name="application_detail.html", context={
            "a": a, "status": _display(a["status"]),
            "events": events, "postings": postings, "contacts": contacts,
            "emails": emails, "options": _application_options(conn, a["user_id"]),
            "extractions": extractions, "artifacts": artifacts,
            "cover_job": cover_job, "cover_error": cover_error,
            "cover_max_attempts": config.MAX_ATTEMPTS, "pending": _pending_count(conn),
            "saved": bool(saved),
        })


# --------------------------------------------------------------------------- edit
#
# Which table a field lands in follows what the field actually describes
# (invariant #3 — postings != jobs != applications):
#
#   company / title   the JOB's identity. Written to jobs AND to every posting
#     under it. Syncing the postings is deliberate, not sloppy: the page header
#     renders company via company_display, which prefers the newest posting's
#     company_raw over jobs.company_norm — so a jobs-only write would leave the
#     user's correction visibly ignored on the very page they came from. The
#     per-ad evidence that matters (jd_text, url, posted_label, ats,
#     captured_at) is never touched.
#   platform / url / location / jd_text   describe ONE ad, so they're written
#     only to the application's primary posting.
#   focused   belongs to the APPLICATION (how you approached it), not the ad.
#   applied date/time / external ("how you applied")   both live on the
#     'applied' EVENT, corrected in place — external as that event's payload
#     (its only key, from both /captures and manual entry). Fixing a mistyped
#     timestamp or a wrong yes/no is not the same as storing a mutable status
#     column — the log stays the single source of status, so invariant #2
#     holds.
#
# Editing jd_text re-runs the derived pipeline: extract_jd APPENDS a fresh
# extractions row (the detail page reads the newest per posting, so it
# supersedes without destroying the old one), and jd_embedding is nulled so the
# stale vector can't keep driving dedup — handle_extract_jd only chains
# embed_jd when the embedding is NULL.
#
# company_norm is always derived through norm_company() and never accepted from
# the form (invariant #4).

# Types that cannot legitimately predate the application itself — moving the
# applied date past any of them would make the timeline incoherent. note /
# follow_up_sent / recruiter_outreach are excluded: those genuinely can sit
# anywhere, including before you applied.
_POST_APPLY_TYPES = ("confirmation", "viewed", "interview_invite",
                     "offer", "rejected", "withdrawn")


def _primary_posting(conn, a) -> dict | None:
    """The posting an edit's per-ad fields apply to: the one the application
    was filed through, else the most recently captured."""
    return conn.execute(
        """
        SELECT * FROM postings
        WHERE job_id = %(job_id)s
        ORDER BY (id = %(applied_via)s) DESC, captured_at DESC
        LIMIT 1
        """,
        {"job_id": a["job_id"], "applied_via": a["applied_via_posting_id"]}).fetchone()


def _applied_event(conn, app_id) -> dict | None:
    """Earliest 'applied' event — the one matcher scores against (it reads
    min(occurred_at)), so it's the one an edit must move. Also the sole home
    of payload.external (invariant: this event's payload only ever holds that
    one key, from both /captures and manual entry) — carried along so an edit
    can prefill and correct it in place."""
    return conn.execute(
        "SELECT id, occurred_at, payload FROM events WHERE application_id = %s "
        "AND type = 'applied' ORDER BY occurred_at LIMIT 1", (app_id,)).fetchone()


def _edit_ctx(conn, a, user, tz, *, form, error=None):
    return {
        "a": a, "form": form, "error": error,
        "tz_label": user.get("timezone") or "UTC",
        "today": datetime.now(tz).strftime("%Y-%m-%d"),
        "pending": _pending_count(conn),
    }


@app.get("/applications/{app_id}/edit")
def edit_form(request: Request, app_id: str):
    user = _login_user(request)
    tz = request.state.tz
    with db.connect_scoped(user["id"]) as conn:
        a = _get_application(conn, app_id)
        p = _primary_posting(conn, a)
        ev = _applied_event(conn, a["id"])
        local = ev["occurred_at"].astimezone(tz) if ev else None
        form = {
            "company": a["company_display"], "title": a["title_canonical"],
            "platform": (p["platform"] if p else "linkedin"),
            "url": (p["url"] if p else "") or "",
            "location": (p["location"] if p else "") or "",
            "jd_text": (p["jd_text"] if p else "") or "",
            "focused": {True: "yes", False: "no"}.get(a["focused"], ""),
            "applied_date": local.strftime("%Y-%m-%d") if local else "",
            "applied_time": local.strftime("%H:%M") if local else "",
            "external": {True: "yes", False: "no"}.get(
                (ev["payload"] or {}).get("external") if ev else None, ""),
        }
        return templates.TemplateResponse(
            request=request, name="application_edit.html",
            context=_edit_ctx(conn, a, user, tz, form=form))


@app.post("/applications/{app_id}/edit")
def edit_application(
    request: Request, app_id: str,
    # Form("") not Form(...) — same reason as manual entry: an empty-but-present
    # field must reach our validation, not FastAPI's raw 422.
    company: str = Form(""), title: str = Form(""), platform: str = Form(""),
    url: str = Form(""), location: str = Form(""), jd_text: str = Form(""),
    focused: str = Form(""), applied_date: str = Form(""), applied_time: str = Form(""),
    external: str = Form(""),
):
    """Full-state submission: every field posts back and a blank one CLEARS the
    stored value (blanking the URL drops platform_job_id, and with it this
    record's dedup key; blanking focused returns it to 'not set', which the
    detail-page toggle can't do). Same contract as manual entry — the form
    always renders every field, so "absent" only ever means "the user emptied
    it", never "the user didn't mention it"."""
    user = _login_user(request)
    tz = request.state.tz

    form = {"company": company, "title": title, "platform": platform, "url": url,
            "location": location, "jd_text": jd_text, "focused": focused,
            "applied_date": applied_date, "applied_time": applied_time,
            "external": external}

    company_s, title_s, location_s = company.strip(), title.strip(), location.strip()
    jd_s = jd_text.strip()
    company_norm = norm_company(company_s) or None
    focused_val = {"yes": True, "no": False}.get(focused.strip().lower())
    external_val = {"yes": True, "no": False}.get(external.strip().lower())

    error = None
    applied_d, applied_t = None, None
    platform_job_id = canonical_url = None

    if not company_s:
        error = "Enter the company name."
    elif not company_norm:
        error = f'Enter the plain company name — "{company_s}" normalizes to nothing.'
    elif not title_s:
        error = "Enter the job title."
    elif platform not in ("linkedin", "jobstreet", "indeed", "other"):
        error = "Unknown platform."
    elif focused.strip() and focused_val is None:
        error = "Unknown focused value."
    elif external.strip() and external_val is None:
        error = "Unknown 'how you applied' value."
    elif not applied_date:
        error = "Enter the applied date."
    else:
        try:
            applied_d = datetime.strptime(applied_date, "%Y-%m-%d").date()
        except ValueError:
            error = "Enter a valid applied date."
        if error is None and applied_time.strip():
            try:
                applied_t = datetime.strptime(applied_time.strip(), "%H:%M").time()
            except ValueError:
                error = "Enter a valid applied time (HH:MM), or leave it blank."
        if error is None and applied_d > datetime.now(tz).date():
            error = "The applied date can't be in the future."
        if error is None:
            parsed_platform, platform_job_id, canonical_url = joburl.parse(url or None)
            if parsed_platform and parsed_platform != platform:
                error = (f"That looks like a {parsed_platform} URL, but you picked "
                         f"{platform}. Fix one of them.")

    with db.connect_scoped(user["id"]) as conn:
        a = _get_application(conn, app_id)
        if error:
            return templates.TemplateResponse(
                request=request, name="application_edit.html",
                context=_edit_ctx(conn, a, user, tz, form=form, error=error),
                status_code=400)

        primary = _primary_posting(conn, a)
        applied_at = ingest.local_date_to_utc(applied_d, tz, t=applied_t)

        # Moving the application later than something it caused is incoherent.
        clash_ev = conn.execute(
            "SELECT type, occurred_at FROM events WHERE application_id = %s "
            "AND type = ANY(%s) AND occurred_at < %s ORDER BY occurred_at LIMIT 1",
            (a["id"], list(_POST_APPLY_TYPES), applied_at)).fetchone()
        if clash_ev:
            error = (f"The timeline already has a {clash_ev['type']} on "
                     f"{clash_ev['occurred_at'].astimezone(tz).strftime('%d %b %Y')} — "
                     "the applied date can't be after it.")

        # postings_platform_job_uidx is (user_id, platform, platform_job_id):
        # pre-check so a collision is a friendly message naming the other
        # record, not an IntegrityError that aborts the transaction.
        if error is None and platform_job_id and primary is not None:
            clash = conn.execute(
                """
                SELECT a2.id, j2.title_canonical,
                       COALESCE(
                         (SELECT p2.company_raw FROM postings p2
                           WHERE p2.job_id = a2.job_id AND p2.company_raw IS NOT NULL
                           ORDER BY p2.captured_at DESC LIMIT 1),
                         j2.company_norm) AS company_display
                FROM postings p
                JOIN applications a2 ON a2.job_id = p.job_id
                JOIN jobs j2 ON j2.id = a2.job_id
                WHERE p.platform = %s AND p.platform_job_id = %s AND p.id <> %s
                LIMIT 1
                """, (platform, platform_job_id, primary["id"])).fetchone()
            if clash:
                error = (f'That URL already belongs to "{clash["company_display"]} · '
                         f'{clash["title_canonical"]}". Merge them instead of '
                         "pointing two records at one posting.")

        if error:
            return templates.TemplateResponse(
                request=request, name="application_edit.html",
                context=_edit_ctx(conn, a, user, tz, form=form, error=error),
                status_code=400)

        with conn.transaction():
            conn.execute(
                "UPDATE jobs SET company_norm = %s, title_canonical = %s WHERE id = %s",
                (company_norm, title_s, a["job_id"]))
            conn.execute(
                "UPDATE postings SET company_raw = %s, company_norm = %s, title = %s "
                "WHERE job_id = %s",
                (company_s, company_norm, title_s, a["job_id"]))
            if primary is not None:
                jd_changed = (jd_s or None) != primary["jd_text"]
                conn.execute(
                    "UPDATE postings SET platform = %s, platform_job_id = %s, "
                    "url = %s, location = %s, jd_text = %s WHERE id = %s",
                    (platform, platform_job_id, canonical_url,
                     location_s or None, jd_s or None, primary["id"]))
                if jd_changed:
                    # The old vector describes text that no longer exists —
                    # drop it so dedup can't score against it, and so
                    # handle_extract_jd's `jd_embedding IS NULL` guard lets
                    # embed_jd re-run.
                    conn.execute(
                        "UPDATE postings SET jd_embedding = NULL WHERE id = %s",
                        (primary["id"],))
                    if jd_s:
                        db.enqueue(conn, a["user_id"], "extract_jd",
                                   {"posting_id": str(primary["id"])})

            conn.execute("UPDATE applications SET focused = %s WHERE id = %s",
                         (focused_val, a["id"]))

            from psycopg.types.json import Json
            external_payload = Json({"external": external_val} if external_val is not None else {})
            ev = _applied_event(conn, a["id"])
            if ev:
                conn.execute(
                    "UPDATE events SET occurred_at = %s, payload = %s WHERE id = %s",
                    (applied_at, external_payload, ev["id"]))
            else:
                # No applied event to correct (e.g. a record still at
                # 'interested') — supplying a date here is how you log one.
                conn.execute(
                    "INSERT INTO events (user_id, application_id, type, source, "
                    "occurred_at, payload) VALUES (%s, %s, 'applied', 'manual', %s, %s)",
                    (a["user_id"], a["id"], applied_at, external_payload))

    return RedirectResponse(f"/applications/{app_id}?saved=1", status_code=303)


@app.post("/applications/{app_id}/focused")
def set_focused(request: Request, app_id: str, focused: str = Form(...)):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        a = _get_application(conn, app_id)
        conn.execute("UPDATE applications SET focused = %s WHERE id = %s",
                     (focused == "yes", a["id"]))
    return RedirectResponse(f"/applications/{app_id}", status_code=303)


@app.post("/applications/{app_id}/events")
def add_event(request: Request, app_id: str, type: str = Form(...), note: str = Form("")):
    if type not in ("follow_up_sent", "note", "withdrawn"):
        raise HTTPException(400, "unsupported manual event type")
    from psycopg.types.json import Json
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        a = _get_application(conn, app_id)
        conn.execute(
            "INSERT INTO events (user_id, application_id, type, source, occurred_at, payload) "
            "VALUES (%s, %s, %s, 'manual', now(), %s)",
            (a["user_id"], a["id"], type, Json({"note": note} if note else {})))
    return RedirectResponse(f"/applications/{app_id}", status_code=303)


# --------------------------------------------------------------------------- contacts
#
# Contacts (recruiters/interviewers) belong to the JOB (invariant #3 —
# postings != jobs != applications: several applications could in principle
# share a job), so every route here scopes by a["job_id"], not app_id
# directly — mirroring _primary_posting()'s pattern rather than adding a new
# lookup shape.

def _get_contact(conn, a, contact_id: str) -> dict:
    try:
        row = conn.execute(
            "SELECT id, job_id, name, role, url, source, approached, approached_at, notes "
            "FROM contacts WHERE id = %s::uuid AND job_id = %s",
            (contact_id, a["job_id"])).fetchone()
    except psycopg.errors.InvalidTextRepresentation:
        row = None
    if row is None:
        raise HTTPException(404, "contact not found")
    return row


def _contact_ctx(conn, a, contact, *, form, error=None):
    return {"a": a, "contact": contact, "form": form, "error": error,
            "pending": _pending_count(conn)}


@app.post("/applications/{app_id}/contacts")
def add_contact(
    request: Request, app_id: str,
    name: str = Form(""), role: str = Form(""), url: str = Form(""),
    approached: str = Form(""), notes: str = Form(""),
):
    user = _login_user(request)
    name_s = name.strip()
    if not name_s:
        raise HTTPException(400, "contact name is required")
    approached_val = approached.strip().lower() in ("yes", "on", "true", "1")
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        a = _get_application(conn, app_id)
        conn.execute(
            "INSERT INTO contacts (user_id, job_id, name, role, url, source, "
            "approached, approached_at, notes) "
            "VALUES (%s, %s, %s, %s, %s, 'manual', %s, "
            "CASE WHEN %s THEN now() ELSE NULL END, %s)",
            (user["id"], a["job_id"], name_s, role.strip() or None, url.strip() or None,
             approached_val, approached_val, notes.strip() or None))
    return RedirectResponse(f"/applications/{app_id}", status_code=303)


@app.get("/applications/{app_id}/contacts/{contact_id}/edit")
def edit_contact_form(request: Request, app_id: str, contact_id: str):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn:
        a = _get_application(conn, app_id)
        c = _get_contact(conn, a, contact_id)
        form = {"name": c["name"], "role": c["role"] or "", "url": c["url"] or "",
                "approached": "yes" if c["approached"] else "", "notes": c["notes"] or ""}
        return templates.TemplateResponse(
            request=request, name="contact_edit.html",
            context=_contact_ctx(conn, a, c, form=form))


@app.post("/applications/{app_id}/contacts/{contact_id}/edit")
def edit_contact(
    request: Request, app_id: str, contact_id: str,
    name: str = Form(""), role: str = Form(""), url: str = Form(""),
    approached: str = Form(""), notes: str = Form(""),
):
    user = _login_user(request)
    form = {"name": name, "role": role, "url": url, "approached": approached, "notes": notes}
    name_s = name.strip()
    approached_val = approached.strip().lower() in ("yes", "on", "true", "1")
    with db.connect_scoped(user["id"]) as conn:
        a = _get_application(conn, app_id)
        c = _get_contact(conn, a, contact_id)
        if not name_s:
            return templates.TemplateResponse(
                request=request, name="contact_edit.html",
                context=_contact_ctx(conn, a, c, form=form,
                                      error="Enter the contact's name."),
                status_code=400)
        with conn.transaction():
            conn.execute(
                """
                UPDATE contacts SET name = %(name)s, role = %(role)s, url = %(url)s,
                       notes = %(notes)s, approached = %(approached)s,
                       approached_at = CASE
                         WHEN %(approached)s AND NOT approached THEN now()
                         WHEN NOT %(approached)s THEN NULL
                         ELSE approached_at END
                WHERE id = %(id)s
                """,
                {"name": name_s, "role": role.strip() or None, "url": url.strip() or None,
                 "notes": notes.strip() or None, "approached": approached_val, "id": c["id"]})
    return RedirectResponse(f"/applications/{app_id}", status_code=303)


@app.post("/applications/{app_id}/contacts/{contact_id}/delete")
def delete_contact(request: Request, app_id: str, contact_id: str):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        a = _get_application(conn, app_id)
        c = _get_contact(conn, a, contact_id)
        conn.execute("DELETE FROM contacts WHERE id = %s", (c["id"],))
    return RedirectResponse(f"/applications/{app_id}", status_code=303)


def _deletion_summary(conn, app_id, job_id) -> dict:
    return conn.execute(
        """
        SELECT (SELECT count(*) FROM postings WHERE job_id = %(job_id)s)       AS n_postings,
               (SELECT count(*) FROM events WHERE application_id = %(app_id)s) AS n_events,
               (SELECT count(*) FROM contacts WHERE job_id = %(job_id)s)       AS n_contacts,
               (SELECT count(*) FROM artifacts WHERE application_id = %(app_id)s) AS n_artifacts,
               (SELECT count(*) FROM emails
                 WHERE matched_application_id = %(app_id)s)                    AS n_emails
        """, {"app_id": app_id, "job_id": job_id}).fetchone()


def _delete_application(conn, a) -> None:
    """Permanently removes one application and its job (applications is
    UNIQUE(user_id, job_id), so a job never outlives the one application it
    belongs to). Deletion order mirrors the FK graph child-to-parent:
    postings <- applications/events/extractions/duplicate_candidates,
    jobs <- postings/contacts.

    Emails matched onto this application are NOT deleted — they're unlinked
    and put back in triage ('never a silent guess' applies to undoing a
    match as much as making one; the email itself is real data the user
    didn't ask to lose). Pending job_queue rows for postings/application
    about to disappear are cleaned up so the worker doesn't dead-letter on a
    now-missing id.
    """
    job_id, app_id = a["job_id"], a["id"]
    conn.execute(
        "UPDATE emails SET matched_application_id = NULL, triage_state = 'pending' "
        "WHERE matched_application_id = %s", (app_id,))
    conn.execute(
        "DELETE FROM job_queue WHERE payload->>'posting_id' IN "
        "(SELECT id::text FROM postings WHERE job_id = %s) "
        "OR payload->>'application_id' = %s", (job_id, str(app_id)))
    conn.execute("DELETE FROM artifacts WHERE application_id = %s", (app_id,))
    conn.execute("DELETE FROM events WHERE application_id = %s", (app_id,))
    conn.execute(
        "DELETE FROM duplicate_candidates WHERE posting_a IN "
        "(SELECT id FROM postings WHERE job_id = %s) "
        "OR posting_b IN (SELECT id FROM postings WHERE job_id = %s)", (job_id, job_id))
    conn.execute(
        "DELETE FROM extractions WHERE posting_id IN "
        "(SELECT id FROM postings WHERE job_id = %s)", (job_id,))
    conn.execute("DELETE FROM contacts WHERE job_id = %s", (job_id,))
    conn.execute("DELETE FROM applications WHERE id = %s", (app_id,))
    conn.execute("DELETE FROM postings WHERE job_id = %s", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


@app.get("/applications/{app_id}/delete")
def delete_confirm(request: Request, app_id: str):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn:
        a = _get_application(conn, app_id)
        summary = _deletion_summary(conn, a["id"], a["job_id"])
        return templates.TemplateResponse(
            request=request, name="delete_confirm.html",
            context={"a": a, "summary": summary, "pending": _pending_count(conn)})


@app.post("/applications/{app_id}/delete")
def delete_application(request: Request, app_id: str):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        a = _get_application(conn, app_id)
        label = f"{a['company_display']} · {a['title_canonical']}"
        _delete_application(conn, a)
    from urllib.parse import quote
    return RedirectResponse(f"/?deleted={quote(label)}", status_code=303)


# --------------------------------------------------------------------------- triage

def _application_options(conn, user_id) -> list[dict]:
    """Every application for this user, for a link-to-application dropdown
    (triage's 'link' action and the emails table's 're-file' action)."""
    return conn.execute(
        """
        SELECT a.id, j.title_canonical,
               COALESCE(
                 (SELECT p.company_raw FROM postings p
                   WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                   ORDER BY p.captured_at DESC LIMIT 1),
                 j.company_norm) AS company_display
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.user_id = %s
        ORDER BY j.company_norm, j.title_canonical
        """, (user_id,)).fetchall()


@app.get("/triage")
def triage(request: Request, lane: str = "actionable"):
    lane = lane if lane in ("actionable", "inbound") else "actionable"
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn:
        user_id = user["id"]
        all_pending = conn.execute(
            """
            SELECT id, sender, subject, received_at, classification,
                   match_score, extraction
            FROM emails
            WHERE user_id = %s AND triage_state = 'pending'
            ORDER BY received_at DESC
            """, (user_id,)).fetchall()
        inbound = [e for e in all_pending if e["classification"] == "recruiter_outreach"]
        actionable = [e for e in all_pending if e["classification"] != "recruiter_outreach"]
        options = _application_options(conn, user_id)
        dupes = conn.execute(
            """
            SELECT d.id, d.title_sim, d.cosine_sim,
                   pa.title AS title_a, pa.platform AS plat_a,
                   COALESCE(pa.company_raw, ja.company_norm) AS comp_a,
                   pb.title AS title_b, pb.platform AS plat_b,
                   COALESCE(pb.company_raw, jb.company_norm) AS comp_b
            FROM duplicate_candidates d
            JOIN postings pa ON pa.id = d.posting_a
            JOIN postings pb ON pb.id = d.posting_b
            LEFT JOIN jobs ja ON ja.id = pa.job_id
            LEFT JOIN jobs jb ON jb.id = pb.job_id
            WHERE d.user_id = %s AND d.state = 'pending'
            ORDER BY d.cosine_sim DESC
            """, (user_id,)).fetchall()
        return templates.TemplateResponse(request=request, name="triage.html", context={
            "emails": inbound if lane == "inbound" else actionable,
            "options": options,
            "dupes": dupes if lane == "actionable" else [],
            "lane": lane,
            "actionable_n": len(actionable) + len(dupes),
            "inbound_n": len(inbound),
            "pending": _pending_count(conn),
        })


@app.post("/triage/{email_id}")
def resolve(request: Request, email_id: str, action: str = Form(...),
            application_id: str | None = Form(None), company: str = Form(""),
            lane: str = Form("actionable")):
    lane = lane if lane in ("actionable", "inbound") else "actionable"
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        try:
            email = conn.execute(
                "SELECT * FROM emails WHERE id = %s::uuid AND triage_state = 'pending'",
                (email_id,)).fetchone()
        except psycopg.errors.InvalidTextRepresentation:
            email = None
        if email is None:
            raise HTTPException(404, "pending email not found")
        user_id = email["user_id"]
        x = matcher.extraction_from_raw(email["extraction"])
        company_s = company.strip()
        if company_s:
            # Agency pitches often withhold the client's name (extraction.company
            # is null); the agency itself is who the user is actually in a
            # process with, so let the human supply/override it here rather
            # than hiding the create/lead actions entirely.
            x = _replace(x, company=company_s)

        if action == "ignore":
            conn.execute(
                "UPDATE emails SET triage_state = 'ignored', processed_at = now() "
                "WHERE id = %s", (email["id"],))
        elif action == "link":
            if not application_id:
                raise HTTPException(400, "pick an application to link to")
            a = _get_application(conn, application_id)
            matcher._append_event(conn, user_id, a["id"], email,
                                  email["classification"] or "other", x)
            conn.execute(
                "UPDATE emails SET matched_application_id = %s, "
                "triage_state = 'resolved', processed_at = now() WHERE id = %s",
                (a["id"], email["id"]))
        elif action == "create":
            if not (x.company or "").strip():
                raise HTTPException(400, "no company — enter one, link, or ignore instead")
            new_id = matcher._create_application(conn, user_id, email, x,
                                                email["classification"] or "confirmation")
            conn.execute(
                "UPDATE emails SET matched_application_id = %s, "
                "triage_state = 'resolved', processed_at = now() WHERE id = %s",
                (new_id, email["id"]))
        elif action == "lead":
            if not (x.company or "").strip():
                raise HTTPException(400, "no company — enter one, link, or ignore instead")
            new_id = matcher._create_application(conn, user_id, email, x,
                                                email["classification"] or "recruiter_outreach",
                                                origin="inbound")
            conn.execute(
                "UPDATE emails SET matched_application_id = %s, "
                "triage_state = 'resolved', processed_at = now() WHERE id = %s",
                (new_id, email["id"]))
        else:
            raise HTTPException(400, "unknown action")
    return RedirectResponse(f"/triage?lane={lane}", status_code=303)


def _get_email(conn, email_id: str) -> dict:
    try:
        row = conn.execute("SELECT * FROM emails WHERE id = %s::uuid", (email_id,)).fetchone()
    except psycopg.errors.InvalidTextRepresentation:
        row = None
    if row is None:
        raise HTTPException(404, "email not found")
    return row


def _job_is_empty(conn, job_id, app_id) -> bool:
    """True when nothing but the application row itself remains — the state
    an email-only 'create' (matcher._create_application) leaves behind once
    its one email's events are undone. Postings aren't part of the check: a
    bare captured posting with no event ever logged against it isn't real
    tracked history."""
    return conn.execute(
        """
        SELECT (SELECT count(*) FROM events WHERE application_id = %(app_id)s) = 0
           AND (SELECT count(*) FROM contacts WHERE job_id = %(job_id)s) = 0
           AND (SELECT count(*) FROM artifacts WHERE application_id = %(app_id)s) = 0
           AS empty
        """, {"app_id": app_id, "job_id": job_id}).fetchone()["empty"]


@app.post("/emails/{email_id}/refile")
def refile_email(request: Request, email_id: str, action: str = Form(...),
                 application_id: str | None = Form(None), redirect_to: str = Form("/triage")):
    """Undo one email's contribution and re-file it — unlike /triage/{id},
    this works on an email in ANY triage_state (pending, auto_matched, or
    already resolved), because the case this exists for — an email attached
    to the wrong application due to a real-world naming mismatch the system
    had no way to detect (see CLAUDE.md gotcha) — is usually only noticed
    after the fact, on the application detail page, not while it's pending.

    Scoped by source_email_id: only the event(s) THIS email caused are
    removed, so the rest of the old application's history is untouched —
    deliberately narrower than dedup.py:merge_jobs, which moves everything
    and would import this email's events as spurious duplicates on whatever
    it's re-filed to. If undoing those events leaves the old application
    with nothing else (the matcher._create_application 'backfill' case,
    where the whole job/posting/application existed only because of this one
    email), it's deleted too — see _job_is_empty."""
    if action not in ("link", "pending", "ignore"):
        raise HTTPException(400, "unknown action")
    if not (redirect_to == "/triage" or redirect_to.startswith("/applications/")):
        redirect_to = "/triage"
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        email = _get_email(conn, email_id)
        old_app_id = email["matched_application_id"]
        old_a = _get_application(conn, old_app_id) if old_app_id else None
        if old_app_id:
            conn.execute(
                "DELETE FROM events WHERE source_email_id = %s AND application_id = %s",
                (email_id, old_app_id))

        if action == "link":
            if not application_id:
                raise HTTPException(400, "pick an application to link to")
            a = _get_application(conn, application_id)
            x = matcher.extraction_from_raw(email["extraction"])
            matcher._append_event(conn, user["id"], a["id"], email,
                                  email["classification"] or "other", x)
            conn.execute(
                "UPDATE emails SET matched_application_id = %s, "
                "triage_state = 'resolved', processed_at = now() WHERE id = %s",
                (a["id"], email_id))
        elif action == "pending":
            conn.execute(
                "UPDATE emails SET matched_application_id = NULL, "
                "triage_state = 'pending', processed_at = now() WHERE id = %s",
                (email_id,))
        else:  # ignore
            conn.execute(
                "UPDATE emails SET matched_application_id = NULL, "
                "triage_state = 'ignored', processed_at = now() WHERE id = %s",
                (email_id,))

        if old_a is not None and _job_is_empty(conn, old_a["job_id"], old_a["id"]):
            # redirect_to may point at old_a's own page — that 404s once it's
            # gone, so fall back to the same banner-redirect the manual
            # /delete route uses.
            label = f"{old_a['company_display']} · {old_a['title_canonical']}"
            _delete_application(conn, old_a)
            if redirect_to == f"/applications/{old_a['id']}":
                from urllib.parse import quote
                redirect_to = f"/?deleted={quote(label)}"

    return RedirectResponse(redirect_to, status_code=303)


# --------------------------------------------------------------------------- captures (§6.1/§6.3)

class CaptureIn(BaseModel):
    platform: str                       # linkedin | jobstreet | indeed | other
    platform_job_id: str | None = None
    url: str | None = None
    company: str | None = None
    title: str | None = None
    jd_text: str | None = None
    trigger: str = "apply"              # apply | manual
    external: bool = False              # redirected to employer site to finish
    focused: bool | None = None
    note: str | None = None
    recruiter_name: str | None = None
    recruiter_url: str | None = None
    recruiter_role: str | None = None
    location: str | None = None
    posted_label: str | None = None     # platform's own relative-time text, e.g. "3 weeks ago"
    reposted: bool | None = None
    ats: str | None = None              # detected from an external apply's destination host


@app.post("/captures")
def captures(payload: CaptureIn, authorization: str | None = Header(None)):
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    with db.connect() as conn:
        user_id = auth.user_id_for_token(conn, supplied)
        if user_id is None and config.API_TOKEN and supplied \
                and hmac.compare_digest(supplied, config.API_TOKEN):
            # Legacy single-tenant token: honored only while exactly one
            # account exists, so the pre-Phase-4 setup keeps working.
            rows = conn.execute("SELECT id FROM users").fetchall()
            if len(rows) == 1:
                user_id = rows[0]["id"]
    if user_id is None:
        raise HTTPException(401, "bad or missing bearer token")
    if payload.platform not in ("linkedin", "jobstreet", "indeed", "other"):
        raise HTTPException(422, "unknown platform")
    if payload.trigger not in ("apply", "manual"):
        raise HTTPException(422, "unknown trigger")
    from psycopg.types.json import Json

    with db.connect_scoped(user_id) as conn, conn.transaction():
        r = ingest.upsert_record(
            conn, user_id, platform=payload.platform,
            platform_job_id=payload.platform_job_id, url=payload.url,
            company=payload.company, title=payload.title, jd_text=payload.jd_text,
            location=payload.location, posted_label=payload.posted_label,
            reposted=payload.reposted, ats=payload.ats, captured_via="extension",
            origin="applied" if payload.trigger == "apply" else "saved")
        job_id, posting_id, app_id = r["job_id"], r["posting_id"], r["application_id"]

        if payload.trigger == "apply":
            has_applied = conn.execute(
                "SELECT 1 FROM events WHERE application_id = %s AND type = 'applied'",
                (app_id,)).fetchone()
            if has_applied is None:            # double-click safe
                conn.execute(
                    "INSERT INTO events (user_id, application_id, type, source, "
                    "occurred_at, payload) VALUES (%s, %s, 'applied', 'extension', "
                    "now(), %s)",
                    (user_id, app_id, Json({"external": payload.external})))
        else:
            has_any = conn.execute(
                "SELECT 1 FROM events WHERE application_id = %s", (app_id,)).fetchone()
            if has_any is None:
                conn.execute(
                    "INSERT INTO events (user_id, application_id, type, source, "
                    "occurred_at, payload) VALUES (%s, %s, 'interested', 'extension', "
                    "now(), '{}')", (user_id, app_id))

        if payload.focused is not None:        # explicit tag always wins (§7)
            conn.execute("UPDATE applications SET focused = %s WHERE id = %s",
                         (payload.focused, app_id))
        if payload.note:
            conn.execute(
                "INSERT INTO events (user_id, application_id, type, source, "
                "occurred_at, payload) VALUES (%s, %s, 'note', 'extension', now(), %s)",
                (user_id, app_id, Json({"note": payload.note})))
        if payload.recruiter_name:
            conn.execute(
                """
                INSERT INTO contacts (user_id, job_id, name, url, role, source)
                SELECT %s, %s, %s, %s, %s, 'extension'
                WHERE NOT EXISTS (SELECT 1 FROM contacts
                                  WHERE job_id = %s AND name = %s)
                """,
                (user_id, job_id, payload.recruiter_name, payload.recruiter_url,
                 payload.recruiter_role, job_id, payload.recruiter_name))

        return {"application_id": str(app_id), "posting_id": str(posting_id),
                "created": r["created"], "enriched": r["enriched"]}


# --------------------------------------------------------------------------- phase 3 routes

@app.post("/extractions/{extraction_id}/verify")
def verify_extraction(request: Request, extraction_id: str,
                      application_id: str = Form(...),
                      languages: str = Form(""),
                      technologies: str = Form(""),
                      visa_signal: str = Form("")):
    """Human verification (§7): confirm as-is, or correct the key fields in
    the same action. Empty inputs keep the extracted values."""
    def _csv(v: str) -> list[str] | None:
        items = [x.strip() for x in v.split(",") if x.strip()]
        return items or None
    if visa_signal and visa_signal not in ("sponsors", "local_only", "unclear"):
        raise HTTPException(400, "bad visa_signal")
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        row = conn.execute("SELECT id FROM extractions WHERE id = %s::uuid",
                           (extraction_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "extraction not found")
        conn.execute(
            """
            UPDATE extractions SET
                languages    = COALESCE(%s, languages),
                technologies = COALESCE(%s, technologies),
                visa_signal  = COALESCE(NULLIF(%s, ''), visa_signal),
                verified     = true
            WHERE id = %s
            """,
            (_csv(languages), _csv(technologies), visa_signal, row["id"]))
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@app.post("/applications/{app_id}/cover-letter")
def request_cover_letter(request: Request, app_id: str):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        a = _get_application(conn, app_id)
        db.enqueue(conn, a["user_id"], "generate_cover_letter",
                   {"application_id": str(a["id"])})
    return RedirectResponse(f"/applications/{app_id}", status_code=303)


@app.post("/duplicates/{cand_id}")
def resolve_duplicate(request: Request, cand_id: str, action: str = Form(...)):
    if action not in ("confirm", "reject"):
        raise HTTPException(400, "unknown action")
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn, conn.transaction():
        try:
            dedup.resolve_candidate(conn, cand_id, confirm=(action == "confirm"))
        except ValueError as err:
            raise HTTPException(404, str(err))
    return RedirectResponse("/triage", status_code=303)


@app.get("/analytics")
def analytics_page(request: Request):
    user = _login_user(request)
    with db.connect_scoped(user["id"]) as conn:
        user_id = user["id"]
        return templates.TemplateResponse(request=request, name="analytics.html", context={
            "summary": analytics.summary(conn, user_id),
            "by_platform": analytics.by_platform(conn, user_id),
            "by_focus": analytics.by_focus(conn, user_id),
            "by_technology": analytics.by_technology(conn, user_id),
            "pending": _pending_count(conn),
        })


# --------------------------------------------------------------------------- accounts (§12)

def _set_session_cookie(resp, session_id: str):
    resp.set_cookie("session", session_id, httponly=True, samesite="lax",
                    max_age=auth.SESSION_DAYS * 86400,
                    secure=config.BASE_URL.startswith("https"))
    return resp


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={
        "error": None, "signup_allowed": config.ALLOW_SIGNUP})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    with db.connect() as conn, conn.transaction():
        user = conn.execute("SELECT * FROM users WHERE email = %s",
                            (email.strip().lower(),)).fetchone()
        if user is None or not auth.check_password(password, user["password_hash"]):
            return templates.TemplateResponse(
                request=request, name="login.html",
                context={"error": "Email and password don't match. If this account "
                                  "predates passwords, run: python -m pipeline.cli "
                                  f"passwd {email.strip().lower()}",
                         "signup_allowed": config.ALLOW_SIGNUP},
                status_code=401)
        sid = auth.create_session(conn, user["id"])
    return _set_session_cookie(RedirectResponse("/", status_code=303), sid)


@app.get("/signup")
def signup_page(request: Request):
    with db.connect() as conn:
        n_users = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
    if n_users and not config.ALLOW_SIGNUP:
        raise HTTPException(403, "signup is disabled on this server")
    return templates.TemplateResponse(request=request, name="signup.html",
                                      context={"error": None})


@app.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if len(password) < 8:
        return templates.TemplateResponse(
            request=request, name="signup.html",
            context={"error": "Password must be at least 8 characters."},
            status_code=400)
    with db.connect() as conn, conn.transaction():
        n_users = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
        if n_users and not config.ALLOW_SIGNUP:
            raise HTTPException(403, "signup is disabled on this server")
        if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
            return templates.TemplateResponse(
                request=request, name="signup.html",
                context={"error": "That email already has an account."},
                status_code=400)
        token, token_hash = auth.new_api_token()
        user = conn.execute(
            "INSERT INTO users (email, password_hash, api_token_hash) "
            "VALUES (%s, %s, %s) RETURNING *",
            (email, auth.hash_password(password), token_hash)).fetchone()
        sid = auth.create_session(conn, user["id"])
    resp = templates.TemplateResponse(request=request, name="settings.html",
                                      context=_settings_ctx(user, new_token=token,
                                                            msg="Account created — save your API token now; it is shown only once."))
    return _set_session_cookie(resp, sid)


@app.post("/logout")
def logout(request: Request):
    with db.connect() as conn, conn.transaction():
        auth.delete_session(conn, request.cookies.get("session"))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# --------------------------------------------------------------------------- settings

def _settings_ctx(user: dict, new_token: str | None = None, msg: str | None = None):
    gmail_kind, gmail_address = mailbox.describe_credential(user.get("gmail_credentials"))
    return {"email": user["email"],
            "resume_profile": user.get("resume_profile") or "",
            "timezone": user.get("timezone") or "",
            # NOT "theme" — that name collides with the Jinja global theme()
            # (base.html's data-theme="{{ theme() }}"): Jinja resolves a
            # context variable before a global of the same name, so a context
            # key "theme" would silently make base.html call a string.
            "theme_pref": user.get("theme") or "",
            "gmail_connected": bool(user.get("gmail_credentials")),
            # "unreadable": credentials exist but TRACKER_SECRET_KEY can no
            # longer decrypt them — without this, that state renders as a
            # plain "Connected" while sync silently fails every run.
            "gmail_kind": gmail_kind,             # "imap" | "oauth" | "unreadable" | None
            "gmail_address": gmail_address,       # IMAP address, or None
            "gmail_address_default": user["email"],
            "gmail_legacy_file": config.GMAIL_TOKEN_FILE.exists(),
            "gmail_web_configured": gmail_oauth.configured(),
            "new_token": new_token, "msg": msg, "pending": 0}


def _fresh_user(user_id) -> dict:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()


@app.get("/settings")
def settings_page(request: Request):
    user = _login_user(request)
    return templates.TemplateResponse(request=request, name="settings.html",
                                      context=_settings_ctx(user))


@app.post("/settings/profile")
def settings_profile(request: Request, resume_profile: str = Form("")):
    user = _login_user(request)
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE users SET resume_profile = NULLIF(%s, '') WHERE id = %s",
                     (resume_profile.strip(), user["id"]))
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/timezone")
def settings_timezone(request: Request, timezone: str = Form("")):
    user = _login_user(request)
    timezone = timezone.strip()
    if timezone:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            return templates.TemplateResponse(
                request=request, name="settings.html",
                context=_settings_ctx(user, msg=f'"{timezone}" is not a recognized '
                                      "timezone name (expected an IANA name like "
                                      "Asia/Singapore)."),
                status_code=400)
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE users SET timezone = NULLIF(%s, '') WHERE id = %s",
                     (timezone, user["id"]))
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/theme")
def settings_theme(request: Request, theme: str = Form("")):
    user = _login_user(request)
    theme = theme.strip()
    if theme not in ("", "light", "dark"):
        return templates.TemplateResponse(
            request=request, name="settings.html",
            context=_settings_ctx(user, msg=f'"{theme}" is not a known theme — '
                                  "pick follow-system, light, or dark."),
            status_code=400)
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE users SET theme = NULLIF(%s, '') WHERE id = %s",
                     (theme, user["id"]))
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/password")
def settings_password(request: Request, current: str = Form(...), new: str = Form(...)):
    user = _login_user(request)
    if not auth.check_password(current, user["password_hash"]):
        return templates.TemplateResponse(request=request, name="settings.html",
                                          context=_settings_ctx(user, msg="Current password is wrong."),
                                          status_code=400)
    if len(new) < 8:
        return templates.TemplateResponse(request=request, name="settings.html",
                                          context=_settings_ctx(user, msg="New password must be at least 8 characters."),
                                          status_code=400)
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                     (auth.hash_password(new), user["id"]))
    return templates.TemplateResponse(request=request, name="settings.html",
                                      context=_settings_ctx(user, msg="Password changed."))


@app.post("/settings/token")
def settings_token(request: Request):
    user = _login_user(request)
    token, token_hash = auth.new_api_token()
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE users SET api_token_hash = %s WHERE id = %s",
                     (token_hash, user["id"]))
    return templates.TemplateResponse(
        request=request, name="settings.html",
        context=_settings_ctx(user, new_token=token,
                              msg="New API token minted — the old one no longer works. "
                                  "Update the extension options now; it is shown only once."))


# --------------------------------------------------------------------------- gmail connect

@app.post("/settings/gmail/imap")
def gmail_imap_connect(request: Request, gmail_address: str = Form(""),
                       app_password: str = Form("")):
    # Form("") + manual validation, not Form(...) — an empty-but-present
    # field would 422 before this friendly message ever runs.
    user = _login_user(request)
    address = gmail_address.strip()
    password = "".join(app_password.split())  # Google displays it space-grouped
    if not address or not password:
        return templates.TemplateResponse(
            request=request, name="settings.html",
            context=_settings_ctx(user, msg="Enter both your Gmail address and "
                                  "a 16-character app password."),
            status_code=400)
    err = gmail_imap.verify(address, password)
    if err:
        return templates.TemplateResponse(request=request, name="settings.html",
                                          context=_settings_ctx(user, msg=err),
                                          status_code=400)
    import json as _json
    from datetime import datetime as _datetime, timezone as _timezone
    payload = _json.dumps({
        "kind": "imap", "provider": "gmail", "address": address,
        "app_password": password,
        "connected_at": _datetime.now(_timezone.utc).isoformat(),
    })
    with db.connect() as conn, conn.transaction():
        conn.execute("UPDATE users SET gmail_credentials = %s WHERE id = %s",
                     (auth.encrypt(payload), user["id"]))
    return templates.TemplateResponse(
        request=request, name="settings.html",
        context=_settings_ctx(_fresh_user(user["id"]),
                              msg=f"Gmail connected over IMAP as {address}. Sync picks it "
                                  f"up on the next run; run backfill to reconstruct history."))


@app.get("/oauth/gmail/start")
def gmail_start(request: Request):
    user = _login_user(request)
    if not gmail_oauth.configured():
        raise HTTPException(503, f"web OAuth client not configured — put the Google "
                                 f"web-application client JSON at {config.GMAIL_WEB_CREDENTIALS}")
    with db.connect() as conn, conn.transaction():
        url = gmail_oauth.start(conn, user["id"])
    return RedirectResponse(url, status_code=303)


@app.get("/oauth/gmail/callback")
def gmail_callback(request: Request, code: str | None = None,
                   state: str | None = None, error: str | None = None):
    user = _login_user(request)
    if error or not code:
        return templates.TemplateResponse(request=request, name="settings.html",
                                          context=_settings_ctx(user, msg=f"Gmail connect failed: {error or 'no code returned'}"))
    try:
        with db.connect() as conn, conn.transaction():
            gmail_oauth.finish(conn, user, code, state or "")
    except ValueError as err:
        return templates.TemplateResponse(request=request, name="settings.html",
                                          context=_settings_ctx(user, msg=str(err)),
                                          status_code=400)
    return templates.TemplateResponse(request=request, name="settings.html",
                                      context=_settings_ctx(_fresh_user(user["id"]),
                                                            msg="Gmail connected. Sync picks it up on the next run."))


@app.post("/settings/gmail/disconnect")
def gmail_disconnect(request: Request):
    user = _login_user(request)
    with db.connect() as conn, conn.transaction():
        mailbox.disconnect(conn, user["id"])
    return RedirectResponse("/settings", status_code=303)
