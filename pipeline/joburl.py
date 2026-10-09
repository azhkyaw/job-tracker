"""Paste-a-link job-id derivation for manual entry (pipeline/web.py's
/applications/new). Python mirror of the id-derivation logic in
extension/adapters/{linkedin,jobstreet,indeed}.js and, for every other site,
extension/shared/jobposting.js:idFrom — those files are the source of truth
for DOM/URL shape (LinkedIn ships 3+ concurrent layouts; see
.claude/rules/extension.md), keep this in sync with them, not the other way round.

Deriving platform_job_id is what lets a manually-entered record converge
with a later extension re-capture or backfill instead of duplicating —
postings_platform_job_uidx is keyed on (user_id, platform, platform_job_id).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, parse_qsl, unquote, urlparse, urlsplit, urlunsplit

_JOBSTREET_SUFFIXES = ("jobstreet.com", "jobstreet.com.sg", "jobstreet.com.my",
                       "jobstreet.co.id", "seek.com.au", "seek.com")
_INDEED_SUFFIXES = ("indeed.com",)
_LINKEDIN_SUFFIXES = ("linkedin.com",)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


# extension/shared/jobposting.js:idFrom, rule for rule — its comment has the
# reasoning, and tests/job_urls.json holds the two to one list. [0-9] rather
# than \d: Python's \d matches every script's digits and JavaScript's does not.
_ID_PARAMS = ("gh_jid", "jobid", "job_id", "job", "jid", "pid", "reqid",
              "req_id", "requisitionid", "career_job_req_id", "jk", "id")
_SEGMENT_IDS = (
    re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"),
    re.compile(r"-([0-9a-f]{32})$"),
    re.compile(r"_((?:[a-z]{1,5}-)*[a-z]{0,5}-?[0-9]{3,}(?:-[0-9]+)*)$"),
    re.compile(r"^([0-9]{4,})-"),
    re.compile(r"^([0-9]+)$"),
    re.compile(r"^((?=[a-z0-9]*[0-9])(?=[a-z0-9]*[a-z])[a-z0-9]{8,40})$"),
)
# jobposting.js:APPLY_SEGMENTS: where a job's address turns into its apply
# flow. The id is looked for before it; the flow's tail (Oracle's
# `…/job/2087/apply/section/1`) only when nothing before it is id-shaped.
_APPLY_SEGMENTS = ("apply", "application")
# jobposting.js:FLOW_STEP_PARAM: on an apply flow's address, the parameter
# naming the wizard's step is the flow's state and stays out of the fallback
# id (Phenom's `…/apply?jobSeqNo=…&step=N&stepname=…`, 2 Oct 2026).
_FLOW_STEP_PARAM = re.compile(r"step")
# jobposting.js:JOB_PARAM / JOB_PARAM_VALUE: a parameter NAMED for the job
# (Phenom's `jobSeqNo`, 3 Oct 2026) with an id-shaped value, tried only where
# the rules above found nothing. fullmatch for JavaScript's ^…$, ASCII so
# IGNORECASE cannot admit the Kelvin sign as a `k`.
_JOB_PARAM = re.compile(
    r"(job|req|requisition|posting|vacancy|opening)[_-]?(seq|post|posting|req)?[_-]?"
    r"(id|no|num|number|code|ref)")
_JOB_PARAM_VALUE = re.compile(r"(?=[a-z0-9_-]*[0-9])[a-z0-9_-]{4,64}", re.I | re.A)


# jobposting.js:TENANT_PARAM: a hiring system serving many employers from one
# host names the employer in a parameter, and each numbers its requisitions on
# its own, so the tenant goes into the token (`<host>/<tenant>/<id>`).
_TENANT_PARAM = (("successfactors.com", "company"), ("successfactors.eu", "company"),
                 ("sapsf.com", "company"), ("sapsf.eu", "company"))
_TENANT_SHAPE = re.compile(r"^[a-z0-9_-]{1,40}$", re.IGNORECASE)


def _tenant(host: str, params: list[tuple[str, str]]) -> str | None:
    for suffix, name in _TENANT_PARAM:
        if host == suffix or host.endswith("." + suffix):
            hit = next((v for k, v in params if k == name and _TENANT_SHAPE.match(v)), None)
            return hit.lower() if hit else None
    return None


def ats_tenant(ats_job_id: str | None) -> str | None:
    """The employer's tenant inside a hiring-system job id that carries one:
    `career2.successfactors.eu/litwarebk/51234` -> "litwarebk" (P2's
    `<host>/<tenant>/<id>`). None for any other id, including a SuccessFactors
    id whose visit never named its tenant (`<host>/<id>`)."""
    parts = (ats_job_id or "").split("/")
    if len(parts) != 3 or not parts[1]:
        return None
    host = parts[0]
    return parts[1] if any(host == s or host.endswith("." + s) for s, _ in _TENANT_PARAM) else None


def generic_id(url: str | None) -> str | None:
    """A posting's id on any site that is not one of the three platforms:
    `<host>/<token>`, or None for a URL that names no page (a bare site, a
    non-http scheme, garbage). See jobposting.js:idFrom for the rules."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    params = [(k.lower(), v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    token = None
    for name in _ID_PARAMS:
        hit = next((v for k, v in params if k == name and v and len(v) <= 64), None)
        if hit:
            token = hit.lower()
            break
    segs = [s for s in (unquote(x).lower() for x in parts.path.split("/")) if s]
    cut = next((i for i, s in enumerate(segs) if s in _APPLY_SEGMENTS), len(segs))
    for seg in [*reversed(segs[:cut]), *reversed(segs[cut + 1:])]:
        if token:
            break
        for rx in _SEGMENT_IDS:
            m = rx.search(seg)
            if m:
                token = m.group(1)
                break
    if not token:
        token = next((v.lower() for k, v in params
                      if _JOB_PARAM.fullmatch(k) and _JOB_PARAM_VALUE.fullmatch(v)), None)
    if not token:
        if not segs:
            return None
        in_flow = cut < len(segs)
        query = "&".join(f"{k}={v}" for k, v in
                         sorted((k, v.lower()) for k, v in params
                                if not k.startswith("utm_")
                                and not (in_flow and _FLOW_STEP_PARAM.search(k))))
        token = "/".join(segs) + (f"?{query}" if query else "")
    else:
        tenant = _tenant(host, params)
        if tenant:
            token = f"{tenant}/{token}"
    return f"{host}/{token}"[:300]


def parse(url: str | None) -> tuple[str | None, str | None, str | None]:
    """-> (platform, platform_job_id, canonical_url).

    Any page off the three platforms is platform 'other' with generic_id()'s
    `<host>/<token>` — an employer's career site or its ATS, the same id the
    extension derives when it captures that page (docs/career-sites.md §10).
    platform/platform_job_id are None when the URL names no job at all (a
    bare domain, garbage) or is a platform page without an id (an expired
    /jobs/collections/ link) — perfectly legal, postings_platform_job_uidx is
    a partial index so unlimited NULL-id postings coexist. canonical_url falls
    back to the input url unchanged in that case.
    """
    if not url or not url.strip():
        return None, None, None
    url = url.strip()
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None, None, url
    host = (parsed.hostname or "").lower()
    qs = parse_qs(parsed.query)

    if _host_matches(host, _LINKEDIN_SUFFIXES):
        # linkedin.js — currentJobId query param, else the job page's own
        # path, /jobs/view/<id>/ or /jobs/view/<slug>-<id>/ (linkedin.js:viewId,
        # held to tests/linkedin_urls.json): an address in the slug form gave
        # a capture no job id on 8 Oct 2026.
        job_id = (qs.get("currentJobId") or [None])[0]
        if not job_id:
            m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?=[/?#]|$)", parsed.path)
            job_id = m.group(1) if m else None
        canonical = f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else url
        return "linkedin", job_id, canonical

    if _host_matches(host, _JOBSTREET_SUFFIXES):
        # jobstreet.js:27-28 — /job/<id> path, else ?jobId= (search split-view).
        m = re.search(r"/job/(\d+)", parsed.path)
        job_id = m.group(1) if m else (qs.get("jobId") or [None])[0]
        origin = f"{parsed.scheme}://{parsed.netloc}"
        canonical = f"{origin}/job/{job_id}" if job_id else url
        return "jobstreet", job_id, canonical

    if _host_matches(host, _INDEED_SUFFIXES):
        # indeed.js:25-26 — ?jk= or ?vjk=.
        job_id = (qs.get("jk") or qs.get("vjk") or [None])[0]
        origin = f"{parsed.scheme}://{parsed.netloc}"
        canonical = f"{origin}/viewjob?jk={job_id}" if job_id else url
        return "indeed", job_id, canonical

    absolute = url if "://" in url else f"https://{url}"
    job_id = generic_id(absolute)
    if job_id:
        s = urlsplit(absolute)
        return "other", job_id, urlunsplit((s.scheme, s.netloc, s.path, s.query, ""))
    return None, None, url
