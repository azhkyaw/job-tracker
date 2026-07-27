# Job Application Tracker

All four phases of the tracker described in `job-tracker-design.md`: the email
backbone (schema, classifier prompts, two-stage LLM pipeline, Gmail poller
with historical backfill, queue worker, §8 matcher), the web UI, the browser
extension with its authenticated `/captures` endpoint, and the intelligence
layer — JD extraction with human verification, embeddings + duplicate
detection with job merging, cover letter generation, funnel analytics, and
follow-up reminders — plus Phase 4 productization: accounts with sessions,
per-user API tokens and Gmail credentials (encrypted at rest), per-user resume
profiles, and Postgres row-level security under every request. Every server
path is test-driven, including cross-tenant isolation at raw SQL.

Three ways data gets in: the extension (capture at apply time), Gmail parsing
(reconstructs history from confirmation/rejection emails), and manual entry
(`/applications/new`) for whatever neither of those can reach — applications
that pre-date this system, an expired posting, or a drifted extension
selector. Never scraping the platforms is the one non-negotiable constraint
across all three.

## Contents

```
migrations/001_init.sql          Full schema (10 tables + derived-status view)
migrations/002_gmail_sync_state.sql  Cursor storage for incremental sync
migrations/003_multi_tenant.sql  Accounts, sessions, RLS role + policies
prompts/email_classify_v1.txt    Stage 1: job-related? what type?
prompts/email_extract_v1.txt     Stage 2: company / role / date / recruiter
prompts/jd_extract_v1.txt        JD -> languages/tech/salary/visa signal
prompts/cover_letter_v1.txt      Grounded cover letter drafting
pipeline/config.py               Allowlist, thresholds, backoff — all env-overridable
pipeline/db.py                   psycopg 3 helpers, enqueue
pipeline/email_classifier.py     Both LLM stages + norm_company()
pipeline/mailbox.py              Provider-agnostic ingest: candidate filter, storage,
                                 query building, cursor persistence (docs/email-ingest.md)
pipeline/gmail_imap.py           Gmail IMAP + app password provider — the default
pipeline/gmail_sync.py           Gmail API + OAuth provider — the alternative for
                                 Workspace / Advanced Protection accounts
pipeline/matcher.py              §8 scoring, auto-match / create / triage dispatch
pipeline/ingest.py               job+posting+application upsert — the shared write
                                 path behind /captures AND manual entry
pipeline/joburl.py               paste-a-link -> (platform, platform_job_id), mirrors
                                 the extension adapters' URL/id derivation
pipeline/jd_extraction.py        JD structured extraction (§9)
pipeline/embeddings.py           Voyage seam; absent key = dedup simply off
pipeline/dedup.py                §10 blocking/thresholds + job merging
pipeline/covers.py               Cover letters from your resume profile
pipeline/analytics.py            Funnel SQL over the event log + reminders
pipeline/auth.py                 Passwords, sessions, API tokens, encryption
pipeline/gmail_oauth.py          Per-user Connect Gmail (web OAuth flow, the alternative)
pipeline/worker.py               SKIP LOCKED claim loop, savepoints, backoff
pipeline/cli.py                  auth / backfill / sync / work / serve / status
pipeline/web.py                  FastAPI app: applications, detail, manual entry, triage
pipeline/templates/              base / applications / detail / manual_entry / triage (Jinja)
extension/                       MV3 extension: manifest, shared capture core,
                                 per-site adapters, background worker, popup/options
tests/test_integration.py        End-to-end with LLM stages stubbed
tests/test_web.py                UI render + triage resolve actions
tests/test_captures.py           /captures auth, upsert, enrichment, idempotency
tests/test_phase3.py             Extraction chain, dedup bands, covers, analytics
tests/test_email_ingest.py       Gmail IMAP provider against a fake server: protocol
                                 invariants, hex-identity, ordering, idempotency
tests/test_phase4.py             Accounts, tokens, RLS isolation (run LAST)
```

## Setup

```bash
# 1. Database (PostgreSQL 15+ with pgvector and pg_trgm available). All seven
#    migrations are required — 003 is what creates the tracker_app RLS role
#    and sessions table that every request now depends on.
createdb tracker
psql tracker -f migrations/001_init.sql -f migrations/002_gmail_sync_state.sql \
             -f migrations/003_multi_tenant.sql -f migrations/004_posting_listing_meta.sql \
             -f migrations/005_posting_ats.sql -f migrations/006_user_timezone.sql \
             -f migrations/007_user_theme.sql -f migrations/008_application_origin.sql

# 2. Python deps. google-api-python-client/google-auth-oauthlib are only
#    needed for the OAuth alternative in step 3b — safe to skip if you're
#    IMAP-only.
pip install anthropic "psycopg[binary]" google-api-python-client google-auth-oauthlib \
            fastapi "uvicorn[standard]" jinja2 python-multipart
export ANTHROPIC_API_KEY=sk-ant-...
export TRACKER_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
#    ^ set ONCE and keep forever — losing it orphans encrypted Gmail creds/sessions.
#    This secret now also protects an IMAP app password (full mailbox access:
#    read, modify, delete, and send), not just a read-only OAuth token — treat
#    it accordingly.

# 3. Gmail — IMAP with an app password (the default; no Google Cloud project,
#    no OAuth consent screen, no token-expiry trap). Requires 2-Step
#    Verification on your Google account; NOT available for Workspace/school
#    accounts, Advanced Protection, or security-key-only 2-Step Verification
#    (use 3b instead). Generate one at myaccount.google.com/apppasswords,
#    then:
python -m pipeline.cli auth        # prompts for address + the app password (hidden input)

# 4. Start the server, then create your account in the browser.
python -m pipeline.cli serve
#    -> open http://127.0.0.1:8000/signup. This mints your per-user API token,
#    shown once on the Settings page that follows — copy it now.

# 5. Extension install: chrome://extensions -> Developer mode -> Load unpacked
#    -> select the extension/ folder. Open its Options page, set the API base
#    (http://127.0.0.1:8000) and paste the token from step 4.
```

### 3b. Gmail OAuth (alternative — Workspace / Advanced Protection accounts)

```bash
# Create a Desktop-app OAuth client in Google Cloud Console (Gmail API
# enabled, scope gmail.readonly), download credentials.json into the project
# root. The app is unverified, so also add your Google account under OAuth
# consent screen -> Test users, or auth fails with "Error 403: access_denied".
python -m pipeline.cli auth --oauth   # prints a URL — WSL-friendly, no browser launch
```

**Read this before relying on it:** a Google Cloud project with an external
consent screen at publishing status "Testing" issues refresh tokens that
expire after **7 days**, unless the only scopes requested are name/email/
profile — `gmail.readonly` doesn't qualify, so sync will die weekly with
`invalid_grant` (surfaced visibly, not a traceback — see `docs/email-ingest.md`
§8). Whether flipping to "In production" while unverified stops the clock is
undocumented for restricted scopes. If you have Google Workspace, an
**Internal** user-type app is exempt from both verification and the 7-day
expiry — the better path for Workspace accounts specifically. Full analysis:
`docs/open-source.md` §2, `docs/email-ingest.md`.

## Run

```bash
python -m pipeline.cli backfill -m 12   # reconstruct history from the inbox
python -m pipeline.cli work --once      # drain the queue (LLM calls happen here)
python -m pipeline.cli status           # sanity counts
python -m pipeline.cli serve            # web UI at http://127.0.0.1:8000

# steady state: two cron entries
*/15 * * * *  cd ~/job-tracker && python -m pipeline.cli sync
# plus one long-running worker:  python -m pipeline.cli work
# after adding keys or backlog:  python -m pipeline.cli scan
```

`sync` iterates every connected account, reports each one's provider kind
(`[imap]`/`[gmail_api]`), and exits non-zero if any account failed — so a
cron failure is visible in your job runner's own alerting rather than
silently dying.

No confirmation email and the posting's gone (applied before this existed,
notifications were off, or the extension missed it)? Open
`/applications/new` — same dedup-aware write path as `/captures`, so pasting
a URL for something you've already captured enriches it instead of
duplicating.

## Test

```bash
./scripts/test.sh          # Linux/WSL — creates a throwaway tracker_test DB,
                            # applies every migration, runs all six suites
scripts\test.ps1           # native Windows equivalent (Docker Postgres, uv)
```

Both scripts are the single source of truth for suite order — `test_email_ingest`
must run before `test_phase4`, which must run last (it creates a second user,
which kills the legacy single-token fallback `test_captures` depends on).
Suites stub every LLM/embedding call and the IMAP suite fakes the network
socket entirely, so a full run costs zero API calls.

Covers: auto-match with event provenance, the create/backfill path (including
stated event dates beating received timestamps, and recruiter contact capture),
pending triage (no silent guesses), non-job mail ignored, failure backoff,
manual entry (timezone-anchored dates, URL-based merge instead of duplication,
and the near-duplicate confirm guard), and the Gmail IMAP provider's protocol
invariants and hex-identity guarantee against a fake server.

## Design notes worth knowing before writing more code

- **Status is derived, never stored.** Query `application_status`; write rows
  to `events` with real-world `occurred_at`. Backfill inserts out of order safely.
- **`norm_company()` is the single source of truth** for `company_norm`
  everywhere. Handles SEA corporate forms including Indonesian PT/CV prefixes.
- **The worker holds one transaction per job** (claim + process). A crash
  rolls back to 'pending' — no stale-'running' cleanup needed. Fine at this
  volume; revisit at multi-user.
- **Prompt versions are file names.** New prompt = new file + bump the
  constant; old rows keep their version string for selective re-runs.
- **Thresholds in `config.py` are first-draft.** Tune `AUTO_MATCH_SCORE` /
  `COMPANY_TRGM_MIN` against your real backfill — the design doc expects this.
- Non-candidate mail is never stored and never reaches an LLM; bodies are
  retained for candidates (reprocessing) — revisit retention before multi-user.

## Extension: what to expect on first use

The three adapters use layered fallback selectors against DOMs I could not
verify live — expect to adjust a selector or two on first contact, exactly as
the design doc's risk table predicts. Failures are loud: a console warning, a
popover error, and a ring buffer of recent failures shown in the extension
popup. Adapters are deliberately thin (one file per site); everything else is
shared. Captures that hit an existing email-only record enrich it in place
rather than creating a duplicate; full cross-posting dedup arrives in Phase 3.

## Phase 3 setup

Two additions beyond Phases 1–2:

- **Resume profile** for cover letters: write `profile.md` (your real
  background, markdown, your words) or set `TRACKER_RESUME_PROFILE`. Letters
  are grounded in this file only — the prompt forbids invention.
- **Embeddings** (optional but required for dedup): set `VOYAGE_API_KEY`
  (voyage-3.5-lite, 1024-dim to match the schema). Without it everything else
  works; dedup simply never runs. After adding the key later, run
  `python -m pipeline.cli scan` to embed the backlog.

Dedup semantics: auto-merge needs cosine >= 0.95 AND title similarity >= 0.5;
the 0.88–0.95 band goes to triage as one card per job pair; confirming merges
the jobs and their applications' histories. Thresholds live in `config.py` and
are meant to be tuned against your real data.

## Phase 4: accounts, tenancy, and deployment

**Upgrading an existing single-user install:** apply migration 003, set
`TRACKER_SECRET_KEY` (sessions and stored Gmail tokens depend on it —
generate once, keep forever), then run
`python -m pipeline.cli passwd your@email.com` to set your password and mint
your per-user API token (update the extension options with it). Your data,
Gmail token file, and `TRACKER_API_TOKEN` fallback keep working while exactly
one account exists; the legacy env token dies the moment a second user signs
up.

**How tenancy works:** page routes resolve the session on an admin connection,
then run every data query as the NOLOGIN role `tracker_app` with
`app.user_id` set — Postgres row-level security scopes all eleven data tables
even for queries with no WHERE clause, and the `application_status` view is
`security_invoker` so it can't be used to sneak past. Users and sessions
tables stay admin-only. The worker and Gmail sync run as trusted admin batch
jobs.

**Per-user Gmail (multi-user):** users connect from Settings — IMAP with an
app password by default, or OAuth if a *Web application* OAuth client is
configured (Google Cloud Console, redirect URI
`<TRACKER_BASE_URL>/oauth/gmail/callback`, saved as `credentials-web.json`,
with `TRACKER_BASE_URL` set). Both kinds share one Fernet-encrypted column
(`users.gmail_credentials`), discriminated by a `"kind"` field in the stored
JSON — `pipeline/mailbox.provider_for_user()` is the single dispatch point;
`sync` iterates every connected account regardless of kind. Single-user
installs can keep the CLI flows — `cli auth` (IMAP) or `cli auth --oauth`
(Desktop client) — nothing to change.

**Deploying beyond localhost:** run behind TLS (Caddy/nginx or Fly.io) — the
session cookie switches to Secure automatically when `TRACKER_BASE_URL` is
https. Set `TRACKER_ALLOW_SIGNUP=0` after your users are in. Extension users
on a remote server enter its URL in Options; the extension requests that
origin's permission at save time (`optional_host_permissions`). Store listing
material lives in `extension/PRIVACY.md`.

**Not built (deliberately):** billing, email verification, password reset by
email, rate limiting, and admin tooling — the next real steps if this becomes
a paid product, none of which touch the schema.
