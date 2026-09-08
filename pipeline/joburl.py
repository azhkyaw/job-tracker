"""Paste-a-link job-id derivation for manual entry (pipeline/web.py's
/applications/new). Python mirror of the id-derivation logic in
extension/adapters/{linkedin,jobstreet,indeed}.js — those three files are the
source of truth for DOM/URL shape (LinkedIn ships 3+ concurrent layouts; see
.claude/rules/extension.md), keep this in sync with them, not the other way round.

Deriving platform_job_id is what lets a manually-entered record converge
with a later extension re-capture or backfill instead of duplicating —
postings_platform_job_uidx is keyed on (user_id, platform, platform_job_id).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_JOBSTREET_SUFFIXES = ("jobstreet.com", "jobstreet.com.sg", "jobstreet.com.my",
                       "jobstreet.co.id", "seek.com.au", "seek.com")
_INDEED_SUFFIXES = ("indeed.com",)
_LINKEDIN_SUFFIXES = ("linkedin.com",)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def parse(url: str | None) -> tuple[str | None, str | None, str | None]:
    """-> (platform, platform_job_id, canonical_url).

    platform/platform_job_id are None when the URL doesn't match a known
    platform or shape (a careers page, an expired /jobs/collections/ link,
    a bare domain) — perfectly legal, postings_platform_job_uidx is a
    partial index so unlimited NULL-id postings coexist. canonical_url falls
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

    return None, None, url
