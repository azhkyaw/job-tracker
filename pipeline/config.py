"""Central configuration. Everything env-overridable; defaults suit single-user v1."""

from __future__ import annotations

import os
from pathlib import Path

# Dev convenience: load a gitignored .env at the project root if present, so the
# CLI/server pick up TRACKER_* vars without sourcing a shell script first.
# Real environment variables take precedence (override=False) — so test.ps1 and
# CI, which set TRACKER_DATABASE_URL explicitly, are unaffected. Absent
# python-dotenv (or absent .env) is a silent no-op.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

DATABASE_URL = os.environ.get("TRACKER_DATABASE_URL", "postgresql:///tracker")

# Bearer token for the extension -> /captures endpoint (§6.3). Generate one:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
API_TOKEN = os.environ.get("TRACKER_API_TOKEN", "")

# Gmail OAuth (design doc §6.2 / §11: readonly scope only). Also the scope
# list for the web OAuth flow (gmail_oauth.py) — defined here, not in
# gmail_sync.py, so neither Gmail module needs to import the other for it.
GMAIL_CREDENTIALS_FILE = Path(os.environ.get("TRACKER_GMAIL_CREDENTIALS", "credentials.json"))
GMAIL_TOKEN_FILE = Path(os.environ.get("TRACKER_GMAIL_TOKEN", ".gmail_token.json"))
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Candidate pre-filter (§6.2). Domain match is suffix-based, so "jobs.lever.co"
# matches "lever.co". Grows over time — additions here are config, not code.
ALLOWLIST_DOMAINS = {
    "linkedin.com",
    "jobstreet.com", "jobstreet.co.id", "seek.com.au", "seek.com",
    "indeed.com",
    "greenhouse.io", "lever.co", "myworkday.com", "workday.com",
    "workable.com", "smartrecruiters.com", "ashbyhq.com", "icims.com",
    "bamboohr.com", "jobvite.com",
    # SG/SEA-specific platforms
    "mycareersfuture.gov.sg", "glints.com", "jobsdb.com",
    # more ATS vendors, unverified against any real inbox yet — trim/extend
    # once a full backfill shows which senders actually show up
    "teamtailor.com", "breezy.hr", "recruitee.com", "personio.com",
    "taleo.net", "successfactors.com", "avature.net",
}

# Fallback net for direct employer mail from unknown domains. Subject-only
# check — the body is never sent to the LLM unless one of these hits or the
# sender is allowlisted (§11).
SUBJECT_KEYWORDS = [
    "your application", "application received", "application update",
    "interview", "candidacy", "next steps", "assessment", "coding challenge",
    "thank you for applying", "your candidature", "offer",
    # rejection phrasing — was missing entirely; this is the one case this
    # list exists for (a rejection from a non-allowlisted, direct employer
    # domain would otherwise never match on `from:` either)
    "unfortunately", "regret to inform", "not moving forward",
    "not selected", "decided not to proceed", "other candidates",
]

# Bypass the two filters above entirely and ingest EVERY message in the window.
#
# The pre-filter is a guess about which mail matters, and it is wrong in a
# direction that costs real data: it drops silently, before the message is ever
# fetched, so a miss leaves no row, no log line, and nothing to notice. Measured
# on the author's real mailbox (7 Aug 2026) it had already eaten a Tailspin
# Consulting REJECTION — sender `humans@tailspin-consulting.com` (an employer's
# own domain, so not allowlistable without an unbounded per-employer list), and
# a subject reading "Thank you for your Full-Stack Developer application to
# Tailspin Consulting", which matches none of SUBJECT_KEYWORDS: "your
# application" is split by the role title, and the "not moving forward" phrasing
# that WOULD have matched is in the body, one line below the only line
# is_candidate reads. The application sat at `interview_invite` — the tracker
# asserting an open thread on a role that had closed. A false row is worse than
# a missing one, and no keyword list survives that subject.
#
# Cost is not the counterargument: classify is ~3,810 input tokens on Sonnet 5,
# ~$0.008/email, against 16.6 stored/day. Nor is precision — 225 of 399 stored
# emails already classify not_job_related, so the filter is not buying a clean
# corpus, only a randomly holed one.
#
# Default False because it is only correct for a mailbox that is job-related
# only. Everyone else's inbox has their bank and their doctor in it, and this
# also switches off the "the body is never sent to the LLM unless a rule hits"
# property the comments above rely on. When True, worker.handle_classify_email
# purges body_text on anything classified not_job_related, so the widened net
# does not become a widened retention footprint (design doc §14 q4).
INGEST_ALL = os.environ.get("TRACKER_INGEST_ALL", "").lower() in ("1", "true", "yes")

# Matching thresholds (design doc §8). Tune against the first backfill run.
COMPANY_TRGM_MIN = 0.6
AUTO_MATCH_SCORE = 0.75

# How alike two titles must be before a live capture is allowed to attach
# itself to an email_only stub for the same company instead of creating its
# own job (ingest.ENRICH_JOB_SQL). Was 0.5, hardcoded in that SQL; measured
# against 52 real applications on 3 Aug 2026 after it silently fused a Northwind
# Recruiting recruiter pitch ("Senior Software Engineer (AI & LLMOps)") with an
# unrelated LinkedIn apply ("Senior AI Engineer") at 0.5588 similarity.
#
# What the real data says: the heuristic had fired exactly ONCE, and that once
# was the false merge. Meanwhile 11 company_norms already cover 2-3 genuinely
# different roles each — five recruitment agencies, plus employers hiring for
# several openings at once — so
# "same company, roughly similar title" is a weak signal by construction here.
# Of the 15 same-company pairs of distinct jobs, 6 clear 0.50 and 3 still
# clear 0.70; one employer's "AI-Native Engineer" vs "Senior AI-Native Engineer"
# scores 0.7170, which is the shape trigrams cannot judge — one word decides
# the role and it barely moves the number.
#
# 0.85 leaves only the two 1.0000 pairs eligible, and both of those are real
# duplicates that SHOULD combine. It is set deliberately high rather than
# "tuned to fit" because the errors are not symmetric: a wrong SPLIT is
# visible and reversible (dedup.merge_jobs, the /triage duplicate band), while
# a wrong MERGE is silent, corrupts provenance, and has no inverse in this
# codebase at all — the Northwind Recruiting one needed hand-written surgery.
# Prefer the recoverable failure.
ENRICH_TITLE_MIN = 0.85
AUTO_MATCH_MARGIN = 0.15
DATE_DECAY_DAYS = 60
W_TITLE, W_DATE, W_PLATFORM = 0.5, 0.3, 0.2

# Worker retry policy (§6.4): exponential backoff, then dead-letter.
MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30

BACKFILL_MONTHS_DEFAULT = 12

# --- Phase 3 -----------------------------------------------------------------

# JD extraction (small-to-mid model) and cover letters (larger; §9).
JD_MODEL = os.environ.get("TRACKER_JD_MODEL", "claude-haiku-4-5-20251001")
COVER_MODEL = os.environ.get("TRACKER_COVER_MODEL", "claude-sonnet-5")

# Candidate profile used for cover letters — a markdown file you maintain
# by hand (design doc §9); never re-derived per call.
RESUME_PROFILE = Path(os.environ.get("TRACKER_RESUME_PROFILE", "profile.md"))

# Embeddings (Voyage; §7 note on vector(1024)). Without a key, embed/dedup
# jobs are simply never enqueued — everything else works.
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
EMBED_MODEL = os.environ.get("TRACKER_EMBED_MODEL", "voyage-3.5-lite")
EMBED_DIM = 1024
EMBED_MAX_CHARS = 30_000

# Dedup thresholds (§10). Auto-merge needs BOTH cosine and title agreement;
# the pending band goes to the triage queue.
DEDUP_AUTO_COS = 0.95
DEDUP_PENDING_COS = 0.88
DEDUP_AUTO_TITLE = 0.5

REMINDER_DAYS = int(os.environ.get("TRACKER_REMINDER_DAYS", "10"))

# --- Phase 4 -----------------------------------------------------------------

# Signing/encryption root for sessions and stored Gmail credentials. REQUIRED
# in any real deployment; the ephemeral dev fallback means sessions and stored
# Gmail tokens do not survive a restart.
SECRET_KEY = os.environ.get("TRACKER_SECRET_KEY", "")
if not SECRET_KEY:
    import secrets as _secrets
    SECRET_KEY = _secrets.token_urlsafe(32)

# Public base URL (needed for the Gmail web OAuth redirect).
BASE_URL = os.environ.get("TRACKER_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Open signup on/off (first account can always be created).
ALLOW_SIGNUP = os.environ.get("TRACKER_ALLOW_SIGNUP", "1") == "1"

# Web-application OAuth client for multi-user Gmail connect (distinct from the
# Desktop client used by the single-user CLI flow).
GMAIL_WEB_CREDENTIALS = Path(os.environ.get("TRACKER_GMAIL_WEB_CREDENTIALS",
                                            "credentials-web.json"))
