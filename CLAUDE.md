# Job Application Tracker

Personal job-application tracker for LinkedIn / JobStreet / Indeed. All four
phases built (Jul 2026) and test-driven. Full design rationale: `docs/design.md`
— read it before any structural change.

## Commands

- **Run ALL tests: `./scripts/test.sh`** — creates a throwaway `tracker_test`
  DB, applies all migrations, runs the five suites in the required order.
  Run after every change; suites stub every LLM/embedding call (zero API cost)
  and have caught every regression in this project so far.
- Serve UI: `python -m pipeline.cli serve` (http://127.0.0.1:8000)
- Worker: `python -m pipeline.cli work [--once]`
- Gmail: `python -m pipeline.cli auth` (single-user desktop flow),
  `backfill -m 12 [--email x]`, `sync` (the 15-min cron entry)
- Backlog: `python -m pipeline.cli scan` (enqueue JD extraction/embeddings)
- Account bootstrap/recovery: `python -m pipeline.cli passwd <email>`
- Requires Postgres running: `sudo service postgresql start` (WSL doesn't autostart)
- **Native Windows (no WSL):** see `docs/windows-dev.md` — Docker Postgres
  (`docker compose up -d`, port 55432) + uv-managed Python;
  `scripts/dev-setup.ps1` once, `scripts/test.ps1` to run suites.

## Architecture invariants — do not violate

1. **NEVER scrape or automate against the job platforms.** Data enters ONLY
   via the browser extension (user-initiated capture at apply time) and Gmail
   parsing. No stored platform credentials, anywhere, ever. This is the
   project's founding constraint (ToS + account-safety), not a preference.
2. **Status is an append-only event log** (`events`, real-world `occurred_at`).
   Current status is DERIVED via the `application_status` view. Never add a
   mutable status column; backfill inserts out of order safely.
3. **postings ≠ jobs ≠ applications.** One application per (user, job). Dedup
   merges at the job level; `pipeline/dedup.py:merge_jobs` is the ONLY place
   records combine (moves events/artifacts/emails/contacts before deleting).
4. **`norm_company()`** in `pipeline/email_classifier.py` is the single source
   of truth for `company_norm`. Never reimplement it in SQL. It strips SEA
   corporate forms including Indonesian PT/CV *prefixes*.
5. **Prompts are versioned files** in `prompts/`. Changing one = new file
   (`*_v2.txt`) + bump the constant; every DB row records its prompt version
   so selective re-runs are possible. Never edit a version file in place.
6. **Tenancy:** request routes resolve the session on an admin connection,
   then run data queries via `db.connect_scoped(user_id)` (SET ROLE
   tracker_app + `app.user_id` GUC → Postgres RLS). `users`/`sessions` are
   admin-only by design (auth bootstrap). Any NEW data table needs: a
   `user_id` column, GRANT to tracker_app, ENABLE RLS + `tenant_isolation`
   policy (copy the pattern in `migrations/003_multi_tenant.sql`). Any NEW
   view needs `security_invoker = true` or it silently bypasses RLS.
7. **Worker:** one transaction per job (`FOR UPDATE SKIP LOCKED` claim,
   savepoint around the handler, exponential backoff → dead-letter). A crash
   leaves the job `pending`; no cleanup logic exists or is needed. Worker and
   Gmail sync are trusted admin batch jobs (no RLS) — keep them that way.
8. **Migrations are append-only numbered files**; never edit an applied one.

## Gotchas learned the hard way in the original build

- psycopg server-side binding cannot type a bare `%s IS NULL` — cast it
  (`%s::text IS NULL`). This bit us once in the matcher.
- Starlette `TemplateResponse` must use keyword form
  (`request=`, `name=`, `context=`).
- When patching code with scripts, ASSERT the anchor matched — a silent
  no-op replace shipped a broken build once; the tests caught it.
- Matching/dedup thresholds in `pipeline/config.py`
  (`AUTO_MATCH_SCORE`, `COMPANY_TRGM_MIN`, `DEDUP_*`) are FIRST DRAFTS,
  deliberately untuned — tune against real backfill data, not intuition.
- `emails.match_score` stores the best candidate score even for `pending`
  rows — that's the tuning dataset.
- **Shadow DOM breaks click delegation:** `shared/capture.js`'s apply-detection
  listener must use `ev.composedPath()`, never `ev.target.closest(...)` —
  LinkedIn's Easy Apply renders its controls inside a shadow root, which
  retargets `ev.target` to the shadow host for any listener outside that tree.

## Environment

Python 3.12 · Postgres 15+ with `pgvector` + `pg_trgm` · `pip install -r requirements.txt`

Env vars: `ANTHROPIC_API_KEY` · `TRACKER_SECRET_KEY` (set ONCE, keep forever —
losing it orphans encrypted Gmail creds) · `TRACKER_DATABASE_URL` (default
`postgresql:///tracker`) · `VOYAGE_API_KEY` (optional; absent = dedup simply
off) · `TRACKER_API_TOKEN` (legacy single-user extension token; dies when a
second account exists) · `TRACKER_BASE_URL` (needed for Gmail web OAuth).
Secrets files are gitignored: `credentials.json`, `credentials-web.json`,
`.gmail_token.json`, `.env`, `profile.md`.

`TRACKER_*`/`ANTHROPIC_API_KEY`/etc. auto-load from a gitignored `.env` at
repo root (`pipeline/config.py`, `override=False` — real shell vars still
win). No per-shell export needed for local dev.

## Known-untested surfaces (verify on first real contact)

- **Extension DOM selectors** (`extension/adapters/*.js`) — best-effort against
  unverified live DOMs; WILL need adjustment. Failures surface loudly: console
  warning, popover error, ring buffer in the extension popup. Fix = edit one
  thin adapter file, reload the unpacked extension, **and hard-refresh any
  already-open tab** — reloading the extension does not re-inject content
  scripts into tabs opened before the reload; the stale script keeps running.
- **Voyage embeddings live call** (`pipeline/embeddings.py`, ~25 lines) —
  never executed against the real API; verify model name/dimension (schema is
  `vector(1024)`) on first use.
- **Gmail web OAuth end-to-end** (`pipeline/gmail_oauth.py`) — flow code is
  tested for state/storage, not against Google.
- **Gmail poller against a real inbox** — that's the next task:

## Immediate next tasks (in order)

1. **Real backfill:** `auth` → `backfill -m 12` → `work --once` → open
   `/triage`. Expect: missing ATS senders → add domains to
   `ALLOWLIST_DOMAINS` in `pipeline/config.py`; mis-scored matches → tune
   thresholds using real `emails.match_score` values; misclassified emails →
   harvest as few-shot examples into a new prompt version.
2. **One real apply via the extension** on each platform; fix whichever
   adapter selectors have drifted.
3. If enabling dedup: set `VOYAGE_API_KEY`, run `scan`, review duplicate
   bands in `/triage` on real cross-platform posts.
