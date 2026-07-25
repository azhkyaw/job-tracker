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
- **Fast syntax check before a full suite run:** `python -c "import ast;
  ast.parse(open('path/to/file.py', encoding='utf-8').read())"` — catches
  typos without a DB reset/migration cycle.
- **Suites always log full per-assertion output**, not just PASS/FAIL —
  `/tmp/<suite>.log` (WSL) or `$env:TEMP/<suite>.log` (native Windows), even
  on success.

## Architecture invariants — do not violate

1. **NEVER scrape or automate against the job platforms.** Data enters via
   three paths: the browser extension (user-initiated capture at apply
   time), Gmail parsing, and manual entry (`/applications/new` — for
   applications neither of the first two can reach: pre-date this system,
   the posting has expired, or the extension adapter has drifted). No
   stored platform credentials, anywhere, ever. This is the project's
   founding constraint (ToS + account-safety), not a preference.
2. **Status is an append-only event log** (`events`, real-world `occurred_at`).
   Current status is DERIVED via the `application_status` view. Never add a
   mutable status column; backfill inserts out of order safely.
3. **postings ≠ jobs ≠ applications.** One application per (user, job). Dedup
   merges at the job level; `pipeline/dedup.py:merge_jobs` is the ONLY place
   records combine (moves events/artifacts/emails/contacts before deleting).
   `pipeline/ingest.py:upsert_record` is its sibling on the write side — the
   ONLY place job/posting/application records are *created* from a capture
   (extension `/captures` and manual entry both call it); it owns the
   partial-unique-index lookup and the email_only job-reuse heuristic so
   those can't silently diverge between callers.
   `web.py:refile_email` is a DIFFERENT tool for a different problem — one
   misfiled email (a real-world company-name mismatch the matcher can't
   detect — an ATS confirmation branded differently than the employer you
   track, e.g. a parent company vs. a subsidiary), not two genuinely-duplicate jobs. It undoes
   only that email's own events (scoped by `source_email_id`) and re-files
   them elsewhere, deleting the source application only if that leaves it
   completely empty. Never reach for `merge_jobs` here — it moves *all* of a
   job's history, which would import the misfiled email's events as
   spurious duplicates (e.g. a second `applied` event) on the target
   application.
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
9. **`applications.origin`** (`applied` | `inbound` | `saved`) is immutable
   provenance, separate from derived status (invariant #2). `inbound` = a
   recruiter/employer approached the user about a role they did NOT apply
   to — `matcher.dispatch` NEVER auto-matches or auto-creates for
   classification `recruiter_outreach`; it always routes to triage's inbound
   lane, and only a human resolving it there (`action=lead`) creates the
   record. `_create_application`'s applied-event fabrication is gated on
   `classification != 'recruiter_outreach'`, not `== 'confirmation'` — a
   rejection/interview_invite with no prior record still implies the user
   applied; only recruiter_outreach doesn't.

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
- **LinkedIn ships 3+ concurrent DOM layouts** (`/jobs/view/`, `/jobs/search-results/`,
  `/jobs/collections/recommended/`) with different CSS stability and even different
  `document.title` behavior — verify adapter changes live against more than one. Prefer
  matching DOM *shape* (e.g. a `<p>` with `·`-separated `<span>` children) over exact-text
  or fixed-position string splits; both broke on real listings.
- **Extension: snapshot DOM data synchronously at the trigger event, not lazily.**
  `shared/capture.js`'s `capture()` used to read the job DOM inside the tag-popover's
  callback (fires whenever the human clicks, seconds later) — by then SPAs like LinkedIn's
  Easy Apply often already replaced the relevant DOM (e.g. an "application sent"
  confirmation swapping out the top card), silently losing data.
- **Git Bash mangles `/migrations/...`-style paths** in `docker compose exec` commands
  (rewrites the leading `/` to the Git install dir). Prefix with `MSYS_NO_PATHCONV=1`.
- **A bare date has no time-of-day — never default it to midnight.** Midnight
  UTC displays as the previous day for non-UTC users, and local midnight sits
  on the date boundary so it shifts if the user later changes timezone. The
  two ingest paths differ by what they legitimately know: an email HAS a real
  instant, so `matcher._event_time` borrows `received_at`'s time-of-day; a
  typed form date does not, so `ingest.local_date_to_utc` anchors it at local
  noon (`ingest.DEFAULT_TIME_OF_DAY`) rather than inventing the submission
  time — fabricated precision the user can't see or correct.
- **FastAPI `Form(...)` (no default) 422s on an empty-but-present field
  before your route body runs** — even one you meant to validate yourself
  with a friendly message. Use `Form("")` and validate manually (confirmed
  pre-existing on `/login`, not new).
- **Windows: `uvicorn --reload`'s process tree outlives a single `taskkill`.**
  The PID `netstat`/`Get-NetTCPConnection` reports often isn't the real
  root — cross-check via `Get-CimInstance Win32_Process -Filter
  "ProcessId=X"` and `taskkill //F //T //PID <true root>`, or the reloader
  just respawns a worker and the port stays bound.
- **Windows consoles default to cp1252.** A Python one-liner printing
  non-ASCII (em-dash, curly quotes) via Bash/PowerShell can raise
  `UnicodeEncodeError` — `sys.stdout.reconfigure(encoding='utf-8',
  errors='replace')` first.
- **Migration filenames are hardcoded in FOUR places — there is no runner.**
  Adding `migrations/NNN_x.sql` also means editing `scripts/dev-setup.ps1`,
  `scripts/test.ps1`, `scripts/test.sh`, and the two `psql -f` command
  blocks in `README.md`. Miss one and `test.ps1` resets its DB without the
  new column, so every page needing it 500s with no obvious cause.
- **A `TemplateResponse` context key can silently shadow a Jinja global of
  the same name.** `web.py` exposes per-request state (`theme()`, `dt`,
  `dtt`) as `templates.env.globals`/`filters`, reached via `request.state`
  in `@pass_context` functions — if any route's context dict reuses that
  name (e.g. `_settings_ctx`'s `"theme"` key shadowing the `theme()`
  global), Jinja resolves the local context first, and `{{ theme() }}`
  raises `TypeError: 'str' object is not callable`. Keep new
  global/filter names distinct from every context dict key.
- **Verifying an authenticated page via claude-in-chrome:**
  `document.cookie` silently can't overwrite an existing httponly session
  cookie, and the browser tool blocks `file://`. Fetch the rendered HTML
  with curl + a real session cookie, serve it via a local `python -m
  http.server`, then navigate/screenshot that. Mint the cookie directly via
  `pipeline.auth.create_session(conn, user_id)` in a one-off script rather
  than needing the real login password.
- **Jinja prints Python `None` as the literal string `"None"`**, not empty,
  when interpolated directly (`{{ x }}`). Bit us in
  `<input value="{{ e.extraction.company }}">` when `company` was `null` —
  rendered `value="None"`. Always `... or ''` on a value that can be `None`,
  not just a truthiness check on its container.
- **A flex segment's label can overflow invisibly.** `.funnel .seg` sizes via
  `flex-grow` off a real count with only a `min-width` floor, and `.funnel`
  has `overflow:hidden` — a long single-word label (e.g. "interviewing") on a
  low-count segment silently clipped instead of wrapping. Fixed with
  `overflow-wrap:anywhere` on `.funnel .seg .l`; same pattern could bite any
  new flex-sized-by-count UI.
- **Gmail's `messages.list` returns newest-first, but ingest is order-sensitive.**
  `gmail_sync.py`'s `backfill()` and `_window_fallback()` used to store/enqueue
  `classify_email` jobs in that (newest-first) order, and the worker claims
  `job_queue` FIFO — so during a real backfill a chronologically-later status
  email (e.g. a rejection) could get matched *before* the confirmation email
  that would have created its application. `matcher.find_match()` correctly
  found zero candidates (not a scoring miss — `emails.match_score` stays NULL
  on these, distinguishing them from a real low-confidence match) and the
  email landed in `/triage` for no real reason — reproduced on a real
  application (confirmation + rejection, ~4 days apart) on the first backfill.
  Fixed via `_list_message_ids()`, which collects every page before reversing
  so ingest always processes oldest-first. `incremental()`'s normal path
  (History API) was never affected — Gmail returns history records oldest-first
  already; only its cursor-expired fallback (`_window_fallback`, same
  `messages.list` call) shared the bug.

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
- **Gmail poller against a real inbox** — a 10-day test backfill (Jul 2026)
  already surfaced and fixed two real issues (see Gotchas): the
  `messages.list` newest-first ordering bug, and a same-employer/different-
  branding email that needed the new `refile_email` route rather than
  `merge_jobs`. Still unverified: the full `-m 12` window, `ALLOWLIST_DOMAINS`
  coverage, and match-threshold tuning against a larger real sample.

## Immediate next tasks (in order)

1. **Real backfill: done.** Job search started 2026-07-16, so the `-m 12`
   (year-long) default never applied to this user — used `backfill -d 14`
   instead (`-d` overrides `-m` via `months = days/31`, cli.py). Actionable
   triage queue is clear. Concrete finding: an employer's "Welcome to Talent
   Community" autoresponder classifies as `recruiter_outreach` (scored 0.645
   vs. a real application to that same employer, just under `AUTO_MATCH_SCORE`) but is really
   a receipt tied to that application — first real candidate for a
   `email_classify_v2.txt` few-shot example. Still open: one inbound lead
   (Beacon Search, agency withheld the client name) awaiting a
   company-name decision in the triage UI. Remaining from the original plan:
   tune match thresholds against real `emails.match_score` values, and watch
   for more `ALLOWLIST_DOMAINS` gaps as new mail arrives.
2. **One real apply via the extension** on each platform; fix whichever
   adapter selectors have drifted.
3. If enabling dedup: set `VOYAGE_API_KEY`, run `scan`, review duplicate
   bands in `/triage` on real cross-platform posts.
