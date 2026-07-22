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

# Gmail OAuth (design doc §6.2 / §11: readonly scope only).
GMAIL_CREDENTIALS_FILE = Path(os.environ.get("TRACKER_GMAIL_CREDENTIALS", "credentials.json"))
GMAIL_TOKEN_FILE = Path(os.environ.get("TRACKER_GMAIL_TOKEN", ".gmail_token.json"))

# Candidate pre-filter (§6.2). Domain match is suffix-based, so "jobs.lever.co"
# matches "lever.co". Grows over time — additions here are config, not code.
ALLOWLIST_DOMAINS = {
    "linkedin.com",
    "jobstreet.com", "jobstreet.co.id", "seek.com.au", "seek.com",
    "indeed.com",
    "greenhouse.io", "lever.co", "myworkday.com", "workday.com",
    "workable.com", "smartrecruiters.com", "ashbyhq.com", "icims.com",
    "bamboohr.com", "jobvite.com",
}

# Fallback net for direct employer mail from unknown domains. Subject-only
# check — the body is never sent to the LLM unless one of these hits or the
# sender is allowlisted (§11).
SUBJECT_KEYWORDS = [
    "your application", "application received", "application update",
    "interview", "candidacy", "next steps", "assessment", "coding challenge",
    "thank you for applying", "your candidature", "offer",
]

# Matching thresholds (design doc §8). Tune against the first backfill run.
COMPANY_TRGM_MIN = 0.6
AUTO_MATCH_SCORE = 0.75
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
COVER_MODEL = os.environ.get("TRACKER_COVER_MODEL", "claude-sonnet-4-6")

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
