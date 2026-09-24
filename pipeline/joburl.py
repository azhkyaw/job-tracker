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
    re.compile(r"_([a-z]{0,5}[0-9]{3,}(?:-[0-9]+)?)$"),
    re.compile(r"^([0-9]{4,})-"),
    re.compile(r"^([0-9]+)$"),
    re.compile(r"^((?=[a-z0-9]*[0-9])(?=[a-z0-9]*[a-z])[a-z0-9]{8,40})$"),
)


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
    for seg in reversed(segs):
        if token:
            break
        for rx in _SEGMENT_IDS:
            m = rx.search(seg)
            if m:
                token = m.group(1)
                break
    if not token:
        if not segs:
            return None
        query = "&".join(f"{k}={v}" for k, v in
                         sorted((k, v.lower()) for k, v in params if not k.startswith("utm_")))
        token = "/".join(segs) + (f"?{query}" if query else "")
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
        # linkedin.js:74-76 — currentJobId query param, else /jobs/view/<id>.
        job_id = (qs.get("currentJobId") or [None])[0]
        if not job_id:
            m = re.search(r"/jobs/view/(\d+)", parsed.path)
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
