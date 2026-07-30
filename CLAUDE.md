# Job Application Tracker

Personal job-application tracker for LinkedIn / JobStreet / Indeed. All four
phases built (Jul 2026) and test-driven. Full design rationale: `docs/design.md`
— read it before any structural change. Direction: **open-source release**, not
SaaS — `docs/open-source.md` (LinkedIn extension-fingerprinting risk, the
7-day OAuth token trap, release checklist). Feature priorities with the market
research behind them: `docs/features.md`. How mail gets in — Gmail IMAP +
app password is the default ingest path (shipped 28 Jul 2026), OAuth the
alternative for Workspace/Advanced Protection accounts:
`docs/email-ingest.md`. `docs/monetization.md` is
superseded but retained for its Gmail restricted-scope compliance analysis.

A fourth thing the extension captures as of 28 Jul 2026: the **screening
questions an apply form asks and the answers given** (`application_answers`,
migrations 009 + 010; `pipeline/answers.py` owns normalisation and the write).
Per application on its detail page, and grouped per *question* across every
application at `/answers` — the second view is the point, since the same
questions recur almost verbatim between employers. A form may ask the same
question more than once (a work-history **repeater**), so a row is identified
by (application, question_norm, **occurrence**) — see invariant #11.

## Commands

- **Run ALL tests: `./scripts/test.sh`** — creates a throwaway `tracker_test`
  DB, applies all migrations, runs the six suites in the required order
  (`test_email_ingest` before `test_phase4`, which must stay last). Run after
  every change; suites stub every LLM/embedding call and fake the IMAP socket
  entirely (zero API cost) and have caught every regression in this project
  so far.
- Serve UI: `python -m pipeline.cli serve` (http://127.0.0.1:8000)
- Worker: `python -m pipeline.cli work [--once]`
- Gmail: `python -m pipeline.cli auth` (IMAP app password, the default —
  prompts for address + hidden password, verifies before storing) or
  `auth --oauth` (legacy single-user desktop OAuth flow), then
  `backfill -m 12 [--email x]`, `sync` (the 15-min cron entry, exits non-zero
  if any account failed)
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
   Not every reply arrives on one of the three ingest paths — a recruiter
   rings, or messages on WhatsApp — so the detail page's timeline form files
   any of `web.py:_MANUAL_EVENTS` by hand, with `occurred_on` backdating it
   (`ingest.local_date_to_utc`, local noon) and `payload.reason` /
   `payload.channel` recording why it ended and where it came from
   (`_EVENT_REASONS` / `_EVENT_CHANNELS`; JSONB, no columns — the home
   `docs/features.md` §7 always intended). `_MANUAL_EVENTS` is a superset of
   `_OUTCOME_TYPES` by assertion, so the timeline form and `/applications/new`
   can't offer different outcomes. **`engaged`** (added 31 Jul 2026) is the
   status-driving type for an employer/recruiter reaching out directly (call,
   WhatsApp, follow-up questions) with no concrete next step yet — distinct
   from `viewed`, which `matcher.py` reserves for the passive, auto-detected
   "your application was viewed" email signal. `application_status`'s
   precedence ranks it strictly between the two (`viewed` < `engaged` <
   `interview_invite`), using gapped values (multiples of 10) so a future
   insertion doesn't force another renumbering. **A visa rejection does NOT auto-set
   `extractions.visa_signal`** — that stays a deliberate second click on the
   detail page's own select, scoped to that posting (decided 28 Jul 2026).
3. **postings ≠ jobs ≠ applications.** One application per (user, job). Dedup
   merges at the job level; `pipeline/dedup.py:merge_jobs` is the ONLY place
   records combine (moves events/artifacts/answers/emails/contacts before
   deleting — `application_answers` is UNIQUE per (application, question_norm,
   occurrence), so the loser's copy of a question the winner already answered
   is dropped, not moved; that drop matches on question_norm ALONE, so a loser
   with more repeat entries can't splice its spares into the winner's list).
   `pipeline/ingest.py:upsert_record` is its sibling on the write side — the
   ONLY place job/posting/application records are *created* from a capture
   (extension `/captures` and manual entry both call it); it owns the
   partial-unique-index lookup and the email_only job-reuse heuristic so
   those can't silently diverge between callers.
   `web.py:refile_email` is a DIFFERENT tool for a different problem — one
   misfiled email (a real-world company-name mismatch the matcher can't
   detect — an ATS confirmation branded differently than the employer you
   track, e.g. a parent company vs. the subsidiary you applied to), not two
   genuinely-duplicate jobs. It undoes
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
10. **Mail ingest has one orchestrator.** `pipeline/mailbox.py` owns candidate
    filtering, body storage, enqueueing, query building, and cursor
    persistence for every provider. A provider (`gmail_imap.ImapProvider`,
    `gmail_sync.GmailApiProvider`) only connects, searches, and returns the
    normalised `{id, sender, subject, body_text, received_at}` dict — `id` is
    `gmail_message_id` as lowercase hex, identical on both paths since
    IMAP's `X-GM-MSGID` (decimal) and the API's message id (hex) are the same
    64-bit value. Never copy `is_candidate` / `store_message` / a query
    builder into a provider — that is exactly how two ingest paths silently
    diverge, the same class of bug invariant #3 guards against elsewhere.
11. **A form label is not a question identifier.** Apply forms contain
    REPEATERS — a work-history section asks "Company / Industry / City" once
    per employer — so N fields legitimately share one label. `occurrence` (the
    field's index among same-labelled fields, migration 010) is what separates
    them; `question_norm` still groups them, which is what keeps `/answers`
    showing one "Industry" row across every employer and every application.
    Assigned in `answers.clean()` from arrival order, never sent by the client.
    The extension's store is keyed `question_norm#occurrence` for the same
    reason. **Never key an answer on the label alone** — that is precisely the
    bug that reduced a real multi-employer work history to one row per label
    (VANARSDEL, 27 Jul 2026), silently and with nothing logged.

## UI design system (redesigned 28 Jul 2026)

All CSS lives in one `<style>` block in `templates/base.html` — no build step,
no framework, no separate stylesheet. Fonts: **Archivo** (variable, `wdth`
axis) + **DM Mono**, from Google Fonts.

1. **Colour means exactly one thing: the state of the wait.** Blue
   (`--accent`) = someone engaged. Amber (`--age`) = time passing, unanswered.
   Rust (`--rejected`) = closed. Green (`--offer`) = an offer. **Everything
   else is grey on purpose.** The status tokens (`--interested`, `--applied`,
   `--viewed`, …) keep their names because templates index them by status
   string, but they map onto those four roles — they are NOT four more hues.
   Adding a colour so something stands out defeats the scheme: the page is
   achromatic at rest so live threads are the only things carrying chroma.
2. **The dark palette is written TWICE** — once under
   `@media (prefers-color-scheme: dark)` and once under
   `:root[data-theme="dark"]`, in that order, because both selectors have
   equal specificity and source order is what lets a light pin win on a dark
   OS. Change one, change the other. (A missed second block is exactly how a
   contrast fix half-landed during the redesign.)
3. **Hierarchy runs on Archivo's width axis, not only size/weight.**
   `font-stretch:118%` for nameplates and numerals, 100% for prose. To give
   something more presence, go wider before going bigger.
4. **`pipeline/trace.py` owns ALL trace geometry** and is pure — no DB, no
   template knowledge. Both the list and the detail page call
   `trace.build(rows, events_by_app, now, reminder_days)`, which annotates
   rows with `pts` / `tail` / `cap` / `silent_days` and returns the shared
   axis. Every trace on a page shares ONE axis (first event on record → now);
   that is what makes rows comparable, so never scale a row to its own span.
   The list fetches every event in one `= ANY(...)` query — one query per row
   to draw one screen is the N+1 this view would die of.
5. **A row's only horizontal mark is data.** There is deliberately no baseline
   rule per trace — an earlier draft had one and it competed with the tails,
   making the amber unreadable. Week gridlines and the "today" rule are the
   only chrome.
6. **Charts are single-series by construction.** One hue for magnitude, the
   panel title names the measure, no legend. **Never a dual-axis chart** — the
   weekly panels show applications and replies as two small multiples
   precisely because they differ ~10x, each scaled to its own peak and
   labelled as such.
7. **Never print a rate on a thin sample.** `analytics.MIN_RATE_N` (5) gates
   every per-dimension `response_rate`; below it the row shows counts and an
   em dash. "0% on n=16" describes the sample, not the technology.
8. The list sorts by **most recent activity** by default (`_SORTS` /
   `_DEFAULT_SORT` in `web.py`, changed 29 Jul 2026). It used to sort by
   silence — longest unanswered first — and that was called the page's whole
   argument, but on 52 real applications it put the single interview invite at
   row 36, under 34 rows of nothing happening: `last_activity ASC` sinks
   engaged threads by construction, because a reply IS recent activity. The
   needs-follow-up block above still makes the silence argument, with rows and
   a one-click action, so the list underneath no longer repeats it.
   `?sort=silence` keeps the old ordering for anyone who wants it.
   Two things ride with the default and NOT with an explicit sort choice
   (`leads_pinned` in the context): **inbound leads awaiting a decision**
   (`_LEADS_FIRST` — `origin='inbound' AND status='interested'`, gated on
   status because origin is immutable, so a lead you pursued must not stay
   pinned forever) lead the list under a `.tl-sep` label, since they have no
   applied event and every time-based sort was ranking them by a number that
   measures nothing; and a **tiebreaker** (`_TIEBREAK`), because a form date
   anchors at local noon so a day's backfill shares one instant to the second
   — 15 of those 52 rows sat in 3 tie groups with no defined order at all.
9. **The needs-follow-up block is work; the table below it is a record.** It
   gets real rows and a one-click `follow_up_sent` (posting with
   `redirect_to=/?fu=1` so the list shortens as you clear it), because on real
   data it IS the day's task list — 18 of 48 threads. Don't demote it back to a
   sentence of links. **Collapsed by default since 28 Jul 2026** — 18 rows of
   queue before the first trace taxed every visit that came to read rather
   than work, and the summary still states the count, so what folds away is
   the rows, not the fact. It stays open while you work it: `fu=1` on the
   query string is what `open`s the `<details>`, and the buttons redirect
   there, so the reload that shortens the block doesn't also close it. That
   URL param is the whole mechanism — no JS anywhere in these templates, and
   no stored preference.
10. **The name column takes the free space; the trace is capped** (`.tl`
   grid). Verified against 47 real applications: applied in one burst, so
   every trace is the same line at the same length while agency company/role
   names were being ellipsed. The trace still shares one axis (rule 4) and
   still earns its place on the detail page — it just doesn't get 40% of a
   list row to repeat "still nothing" 47 times.

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
- **JobStreet's split view puts 30 OTHER jobs' data next to the one you want.**
  The results list and the detail pane use different `data-automation` names
  for the same facts: the pane has `job-detail-salary` / `job-detail-location`
  (1 each, document-unique even on the search page), the cards have `jobSalary`
  (×18) and `jobLocation` (×30). A plain `querySelector` on the CARD name
  returns the first card's value — reading `$7,000–$10,000 / Paya Lebar` for a
  Central Region job paying `$10,000–$11,000`. Plausible, silent, wrong. Only
  ever use the `job-detail-*` names; never the unprefixed ones.
- **Never name a DOM-extracted variable `location`.** It shadows
  `window.location`, so any `location.pathname` read ABOVE it in the same scope
  hits the temporal dead zone and throws — killing the whole adapter. `node
  --check` passes it happily, because it's a runtime error, not a syntax one.
- **Salary is printed BESIDE the ad, not inside it** (migration 011). That's
  why `extractions.salary_min` has been null on jobs whose pay is plainly on
  the page: the LLM only ever sees `jd_text`. Platform-stated pay now lands in
  `postings.salary_*` — kept separate from `extractions` on purpose, since a
  figure the employer printed and one a model inferred are different kinds of
  evidence, and re-running a prompt must not change a published number.
  `pipeline/salary.py` owns parsing (invariant #4's rule applied to pay); the
  extension sends only the displayed string. **`salary_period` is load-bearing**
  — SEA quotes monthly where most markets quote annual, and this project's own
  answer bank already holds 10,800 (monthly) beside 128,000 (annual) for one
  person. Indonesia writes `15.000.000`, so that dot is a THOUSANDS separator;
  parsing it as a decimal turns 15 million into 15.
- **LinkedIn ships 3+ concurrent DOM layouts** (`/jobs/view/`, `/jobs/search-results/`,
  `/jobs/collections/recommended/`) with different CSS stability and even different
  `document.title` behavior — verify adapter changes live against more than one. Prefer
  matching DOM *shape* (e.g. a `<p>` with `·`-separated `<span>` children) over exact-text
  or fixed-position string splits; both broke on real listings.
- **Never let the capture popover hold the only copy of a capture.** Until
  28 Jul 2026 nothing was POSTed until the user answered "focused or generic?",
  so a real application vanished if they ignored the box for 45s or closed the
  Easy Apply modal (which destroys the iframe the popover renders in). Now an
  *unambiguous* apply — Easy Apply's final submit, popup capture — writes
  immediately with `focused = null` and the popover is a receipt;
  `POST /captures/{id}/tag` carries the tag/note afterwards. An *ambiguous*
  one — "Apply on company website", where you may never actually apply — still
  confirms first. Keep that split: it's about evidence, not UI taste. The
  receipt is relayed to frame 0 via the service worker
  (`chrome.tabs.sendMessage` — **requires host permission for the site**,
  hence `*://*.linkedin.com/*` in host_permissions; `activeTab` does NOT cover
  it, since a click on the page's own button isn't an activeTab invocation),
  with an in-frame fallback when the relay fails.
- **An apply control that NAVIGATES loses its receipt entirely.** JobStreet's
  is a real `<a>`: the click captures synchronously, but the POST is async via
  the service worker, so the page (and the content script waiting on the
  `.then()`) is torn down before the response lands. The record saves — the
  worker owns the fetch — and the receipt, with it the only offer to tag the
  application, never renders. Confirmed on a real apply 29 Jul 2026: record
  present, `focused = NULL`, no popover ever seen. The worker now stashes the
  receipt per tab (`background.js:stashReceipt`, 5-minute TTL) and the next
  content script to load claims it; rendering it in-page sends
  `tracker-receipt-shown` so the held copy can't fire twice. This is a class
  of bug, not a JobStreet quirk — any adapter whose apply control navigates has
  it, and LinkedIn only escapes because Easy Apply stays in-page.
- **An apply flow that spans PAGES must carry the job snapshot with it.**
  JobStreet defers like LinkedIn (`deferInternalApply`) so the applied time is
  the submit, not the opening click — but its flow is `/job/<id>` → `/apply` →
  `/apply/profile` → `/apply/review`, and only the LISTING shows the company.
  Verified on the real review page 29 Jul 2026: `getJob()` there returns the id
  (URL) and the title (`<h1>`), but company is a bare `<span>` with no
  `data-automation` on it or any ancestor. Capturing at submit alone would file
  "unknown company". So the opening click stashes the snapshot through the
  service worker (`background.js:stashPendingJob`, 2h TTL) and the submit
  merges it in, gaps only — the page in front of you always wins over a stale
  snapshot. Nothing is POSTed until the submit, so an abandoned flow expires
  unsent instead of leaving a phantom application.
  (This reverses the 28 Jul design, which captured on the opening click and
  corrected the time afterwards via `completed`. That existed because the
  submit hook was an unverified guess and a miss would have lost the
  application; the hook is now verified against the real page. The `completed`
  correction is KEPT server-side — it costs nothing and still fixes the time
  when an applied event already exists, e.g. from a confirmation email.)
- **An MV3 service worker can be killed mid-write, and `return false` is what
  invites it.** A message handler that returns false tells Chrome it is
  finished, so the worker may be terminated between an async handler's storage
  READ and its WRITE — and every message the extension sends from an apply
  click is sent milliseconds before the page navigates, the worst moment to be
  racing a shutdown. A real JobStreet apply on 29 Jul 2026 filed
  "unknown company": deferral worked, the submit captured, but the stashed job
  snapshot was simply not there. Two rules now: an async handler **returns
  `true` and calls `respond()`** (the open port is what keeps the worker
  alive), and **every write goes through `setLocal()`** so it can be awaited —
  an unawaited `chrome.storage.local.set` resolves nobody's promise, so even a
  correct-looking `.then()` fires before the data lands. Content-script side,
  send with the promise form (`.catch(() => {})`), not the callback.
- **`trim()` does not remove invisible characters, and platforms ship them
  inside button labels.** JobStreet's submit button reads
  `"⁠Submit application"` — a WORD JOINER glued to the front. It renders
  as nothing, `String.trim()` leaves it (format characters, category Cf, are
  not whitespace), and `=== "Submit application"` fails against a button that
  looks exactly right. Silent: no error, the capture simply never fires.
  `shared/capture.js:visibleText()` strips U+00AD/200B–200F/2060–2064/FEFF and
  folds all whitespace before comparing; every text match goes through it.
  Assume any exact-text hook needs this — NBSP inside a wrapped label is the
  same bug wearing a different hat.
- **`aria-labelledby` often names an element AND its own wrapper**, so joining
  every referenced element's text gives you the label twice: real captures
  stored `"Country Country"` and `"Location (city) Location (city)"`. Cosmetic
  in display, corrosive in the bank — `"City"` and `"City City"` normalise
  differently, so one question splits into two rows and stops grouping.
  `answers.js:labelFor()` now drops a part already contained in one it kept.
- **A capture-phase sweep reads the DOM BEFORE the page's own handler runs** —
  which is the entire point for a wizard (the step's fields are gone
  afterwards) and exactly wrong for a **typeahead**: clicking a suggestion
  doesn't advance anything, it writes the value asynchronously *after* your
  handler. A real capture stored `"singa"` as a city. The click listener now
  sweeps three times (capture phase, `setTimeout 0`, `setTimeout 300`); the
  extra passes are safe because a sweep only overwrites questions it can
  currently see, so an advanced step's stored answers are untouched.
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
- **`.\scripts\test.ps1` via the PowerShell tool exits 1 even when every suite
  passes.** `psql` writes a NOTICE ("role postgres is already a member of
  tracker_app") to stderr during migration, and PowerShell 5.1 wraps a native
  command's stderr in a NativeCommandError, flipping `$?` regardless of the
  real exit code. Run it as `powershell.exe -NoProfile -ExecutionPolicy Bypass
  -File scripts/test.ps1` from the **Bash** tool instead and grep for
  `^== `; the per-suite PASS/FAIL lines are the truth, not the exit status.
- **`%-d` / `%-m` strftime directives are glibc-only and raise `ValueError` on
  Windows.** Format with `%d` and `.lstrip("0")` instead (`trace.py:_ticks`,
  `analytics.weekly`). Sibling of the cp1252 gotcha below — both are ways a
  Linux-shaped one-liner dies natively.
- **Dark Reader in the dev Chrome profile defeats colour verification
  entirely — including `getComputedStyle`.** Its dynamic mode injects real
  overriding CSS, so resolved `background-color` comes back neutralised
  (`.funnel .seg`, `.legend i`, `.spark .bar` all read as the card colour
  while being correctly sized). `:root` custom properties, all geometry, and
  font checks DO survive. Verify a palette numerically from the authored
  hexes instead — computing WCAG contrast that way caught a real AA failure
  on `--withdrawn` (3.43:1) that looking never would have.
- **Windows consoles default to cp1252.** A Python one-liner printing
  non-ASCII (em-dash, curly quotes) via Bash/PowerShell can raise
  `UnicodeEncodeError` — `sys.stdout.reconfigure(encoding='utf-8',
  errors='replace')` first.
- **Migration filenames are hardcoded in FOUR places — there is no runner.**
  Adding `migrations/NNN_x.sql` also means editing `scripts/dev-setup.ps1`,
  `scripts/test.ps1`, `scripts/test.sh`, and the `psql -f` command block in
  `README.md`. Miss one and `test.ps1` resets its DB without the new column,
  so every page needing it 500s with no obvious cause. The dev DB is a fifth
  place, in a different sense: it is not reset between runs, so apply the file
  to it by hand (`MSYS_NO_PATHCONV=1 docker compose exec -T db psql -U postgres
  -d tracker -f /migrations/NNN_x.sql`) or the running dev server 500s while
  the suites stay green.
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
- **A scoped CSS selector does NOT protect you from a weaker rule setting a
  property it never mentions.** `.spark .bar` (chart bar) and `.bar` (the
  toolbar/button-row utility, `class="bar"` on forms) shared a name; the
  utility's `margin-bottom:.9rem` applied to every chart bar because the
  scoped rule only declares width/height/background. Result: every weekly bar
  floated 14px off its axis and the tallest one covered its own panel title —
  in both panels, always, not a data edge case. Specificity decides conflicts
  per-property, not per-rule. Chart element names now stay out of the utility
  namespace (`.spark .wk`). Measure geometry (`getBoundingClientRect`) when a
  chart looks subtly off; the overlap was invisible until the numbers came out.
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
- **OAuth publishing status "Testing" revokes refresh tokens after 7 days.**
  Google's docs are explicit: an external-user-type consent screen with
  publishing status `Testing` issues refresh tokens expiring in 7 days unless
  the only scopes are name/email/profile — `gmail.readonly` is not exempt.
  `README.md`'s "add yourself under Test users" leaves you exactly there, so
  Gmail sync dies weekly with `invalid_grant: Token has been expired or
  revoked`. Whether flipping to "In production" while *unverified* stops the
  clock is UNDOCUMENTED for restricted scopes (Google ties removal to
  *verified* + published; community reports say the flip works) — test it
  before asserting either way. Handle `invalid_grant` visibly regardless:
  tokens also die on Google-password change (Gmail scopes specifically), six
  months of disuse, and user revocation. See `docs/open-source.md` §2.
  **Handled as of 28 Jul 2026:** `GmailApiProvider.from_stored` catches
  `RefreshError` and raises `mailbox.MailboxAuthError`, the same type an IMAP
  login failure raises — one shared error path surfaces in the CLI, `sync`,
  and Settings for both credential kinds. The empirical production-status
  flip test itself was never run; this handling doesn't depend on its result.
- **`imaplib.IMAP4._command()` does zero quoting.** Every argument is
  concatenated onto the wire verbatim — `select("[Gmail]/All Mail")` sends
  two unquoted atoms and gets `BAD`, and an `X-GM-RAW` query containing
  `subject:"your application"` is a malformed quoted string unless escaped
  first. `gmail_imap.py`'s `_quote()` (backslash- and quote-escaping) wraps
  every mailbox name and search query; nothing reaches `conn.select()`/
  `conn.uid()` unquoted.
- **`imaplib.Internaldate2tuple()` returns `time.localtime(utc)`** — a NAIVE
  struct in the *host's* timezone, not the message's. Using it for
  `received_at` would silently shift every stored email by the host's UTC
  offset. `gmail_imap._parse_internaldate()` parses the `imaplib.InternalDate`
  regex groups directly and builds a tz-aware UTC datetime instead — verified
  against a real `+0800` INTERNALDATE stamp before this was trusted.
- **`UID SEARCH UID n:*` returns the highest existing UID even when `n`
  exceeds it** (RFC 3501 range semantics) — without a client-side
  `uid > last_uid` filter, every incremental sync would re-fetch the newest
  matching message forever. Invisible in practice (the insert is a no-op via
  `ON CONFLICT`) except as a permanent, silent bandwidth leak.
  `gmail_imap.ImapProvider.incremental_handles()` filters this client-side;
  `tests/test_email_ingest.py` asserts the stale UID is never fetched.
- **`BODY.PEEK[]` and `readonly=True` (EXAMINE, not SELECT) are both
  mandatory, together.** Either one missing marks the operator's mail
  `\Seen` — a live, unrecoverable side effect no fake can fully stand in for.
  Verified against a real inbox (28 Jul 2026): noted specific unread emails,
  ran backfill against them twice, confirmed still unread both times.
- **Two more places IMAP diverges silently from the Gmail API if copied
  carelessly:** the API returns headers already MIME-decoded, but raw IMAP
  `FETCH` does not — skip `email.header.decode_header` and a candidate email
  with an RFC 2047-encoded subject (non-ASCII, so Q-encoding replaces spaces
  with `_`) is invisible to `is_candidate`'s keyword match even though a
  human reading the same subject in Gmail would see the keyword plainly.
  Separately, `[Gmail]/All Mail` is a *localised* folder name (different per
  account language) — found via the `\All` SPECIAL-USE flag in `LIST`, never
  hard-coded, and deliberately All Mail rather than `INBOX` since that's the
  parity set with the API's `messages.list` (excludes Spam/Trash, includes
  archived and filter-routed mail — most accounts with job alerts route them
  somewhere other than INBOX).

## Environment

Python 3.12 · Postgres 15+ with `pgvector` + `pg_trgm` · `pip install -r requirements.txt`

Env vars: `ANTHROPIC_API_KEY` · `TRACKER_SECRET_KEY` (set ONCE, keep forever —
losing it orphans encrypted Gmail creds; since IMAP shipped this key can also
protect a full-mailbox app password rather than only a `gmail.readonly`
token, so its blast radius on leak/loss is strictly larger than before) ·
`TRACKER_DATABASE_URL` (default
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
- **JobStreet's apply flow — one real submit completed 29 Jul 2026, and it
  failed exactly as predicted.** A real apply (First Up Consultants ·
  Generative AI Engineer) came back with `company_norm = 'unknown company'`:
  deferral worked (applied time was the submit, no phantom record), but the
  job snapshot stashed at the opening click was gone by the time the submit
  merged it in. Root-caused to the MV3 service-worker race documented above
  (`return false` let the worker die mid-write) and fixed in the same session
  — the affected record was corrected by hand via `/edit`
  (`jobs.company_norm` had to be fixed too, not just `postings.company_raw`,
  or email matching would never have found this application). Also added
  since: `postings.salary_*` / `work_type` / `salary_match` capture
  (migration 011), verified extracting correctly on a live page.
  **Still unverified: one clean end-to-end submit with the race fix AND the
  salary fields both in place together.** Read it off the record afterwards —
  applied time should equal the submit, `company_display` must not be
  "unknown company", and `postings.salary_raw` should be populated if the ad
  showed a figure. `answerFormRoot()` is still a pure guess; the popup's
  "Recent form sweeps" line says whether it found a root at all. Note SEEK
  Quick apply may ask **no screening questions** (two real postings now, both
  asked none), so an empty answer capture is not by itself evidence of a bug.
- **Easy Apply screening-Q&A capture** (`extension/shared/answers.js`) — **met
  its first real wizard 27 Jul 2026** (VANARSDEL · AI Engineer, 11 answers
  stored). Two of the three open questions are now answered: the modal's
  fields are NOT in a closed shadow root (the sweep reached them, prefilled
  and untouched alike), and `answerFormRoot()`'s in-iframe assumption held.
  That run also found three real defects, all fixed 28 Jul — see the repeater
  invariant (#11), the doubled-`aria-labelledby` gotcha, and the typeahead /
  capture-phase gotcha.
  **Still open: textareas.** That form had them; not one was stored, and no
  code path between the DOM and the DB filters by type, so either the sweep
  never saw them (wrong root for that step) or it couldn't resolve their
  labels. Nothing in the record distinguishes the two, which is why the sweep
  now reports its own counts — controls found, kept, unlabelled, empty,
  disabled, textareas seen — into `chrome.storage.local.sweeps`, rendered
  under "Recent form sweeps" in the extension popup. **Next real apply with a
  textarea on the form: open the popup and read that line.** `0 textareas
  seen` means the root is wrong; a nonzero count with `N unlabelled` means
  `labelFor()` is.
- **The redesigned palette, on a real screen.** Layout, type, spacing,
  responsive behaviour and contrast were all verified (contrast numerically —
  0 WCAG AA failures in both themes); the *rendered colour* never was,
  because Dark Reader owns the dev browser (see Gotchas). Nobody has yet
  confirmed with their own eyes that the amber/blue/rust system reads the way
  it is supposed to. Check this before putting screenshots in a README.
- **Voyage embeddings live call** (`pipeline/embeddings.py`, ~25 lines) —
  never executed against the real API; verify model name/dimension (schema is
  `vector(1024)`) on first use.
- **Gmail web OAuth end-to-end** (`pipeline/gmail_oauth.py`) — flow code is
  tested for state/storage, not against Google.
- **Gmail API poller against a real inbox** — a 10-day test backfill (Jul 2026)
  already surfaced and fixed two real issues (see Gotchas): the
  `messages.list` newest-first ordering bug, and a same-employer/different-
  branding email that needed the new `refile_email` route rather than
  `merge_jobs`. Still unverified: the full `-m 12` window, `ALLOWLIST_DOMAINS`
  coverage, and match-threshold tuning against a larger real sample.
- **Gmail IMAP poller — verified against a real inbox 28 Jul 2026.**
  `backfill -d 14` against the author's real account: no crash on the real
  `X-GM-RAW` search (the quoting concern in the Gotchas above was the biggest
  unknown going in), 4 new candidates stored, zero collisions against the
  102 rows the prior OAuth backfill had already written (proving the
  hex-identity claim, not just asserting it), cursor came out
  `uidvalidity:uidnext-1`-shaped, two `sync` runs both reported 0 new, a
  deliberately wrong app password surfaced the same friendly message the
  fake predicted (`[AUTHENTICATIONFAILED] Invalid credentials (Failure)` —
  the real server's wire text matched the test fixture exactly), and noted
  unread emails stayed unread across two backfill passes. Still unverified:
  the full `-m 12` window on a real account (the 2,500 MB/day ceiling is not
  a real concern at this account's scale per a rough extrapolation, but
  never measured directly), and the **web** connect form
  (`/settings/gmail/imap`) against a real app password — only the CLI path
  (`cli auth`) was exercised live; the web route has FakeIMAP-suite and
  browser-screenshot coverage but no real-credential run.

## Immediate next tasks (in order)

1. **Real backfill: done.** Job search started 2026-07-16, so the `-m 12`
   (year-long) default never applied to this user — used `backfill -d 14`
   instead (`-d` overrides `-m` via `months = days/31`, cli.py). Actionable
   triage queue is clear. Concrete finding: an employer's "Welcome to Talent
   Community" autoresponder classifies as `recruiter_outreach` (scored 0.645
   vs. a real application to that same employer, just under
   `AUTO_MATCH_SCORE`) but is really a receipt tied to that application —
   first real candidate for a `email_classify_v2.txt` few-shot example. Still
   open: one inbound lead (a recruitment agency that withheld the client
   name) awaiting a company-name decision in the triage UI. Remaining from
   the original plan:
   tune match thresholds against real `emails.match_score` values, and watch
   for more `ALLOWLIST_DOMAINS` gaps as new mail arrives.
   **Note (28 Jul 2026):** switching this account to IMAP and re-running
   `backfill -d 14` (Step 7 of the IMAP rollout, see `docs/email-ingest.md`)
   added 4 more real candidate emails, still `pending` in `job_queue` —
   `work --once` hasn't been run against them yet, so `status` will show a
   nonzero queue depth until it is.
2. **OAuth token expiry — largely resolved.** See the 7-day gotcha above; IMAP
   + app password is now the default (`docs/email-ingest.md`, shipped and
   verified against a real inbox 28 Jul 2026), which has no refresh token to
   expire, and `invalid_grant`/`RefreshError` now surfaces as the same
   `mailbox.MailboxAuthError` an IMAP login failure does — handled visibly in
   the CLI, `sync`, and Settings, not a traceback. What's still genuinely
   open: the empirical test of whether flipping Google Cloud publishing
   status to "In production" while unverified stops the 7-day clock — never
   run, and this account no longer depends on the answer since it's on IMAP.
3. **Release blockers** (`docs/open-source.md` §11, ordered there): LICENSE
   (Apache-2.0 recommended), extension split into its own repo, decide
   whether CLAUDE.md ships. Fixture names and git history are both done
   (26 Jul 2026) — the history rewrite was needed for personal data, not
   secrets; §11 records the method and the three false-positive traps.
3b. **`applications.focused` — the fair trial (started 28 Jul 2026) now has
   data, and it's a different result than the trial was designed to detect.**
   It was null across all 45 applications when the entry point was mid-apply,
   as a blocker — the capture wouldn't save until you answered — so dismissing
   it was always the rational move. The save-first receipt fixed that (one
   optional click on an already-saved record), and by 29 Jul, 46 of 54
   applications had it set — **all 46 to `generic`, zero `tailored`**. That
   is population, which is what the entry-point fix was for, but it is a
   CONSTANT, not a split, so `analytics.by_focus` (gated on `length > 1`)
   still shows nothing.
   **Two readings, not yet distinguished:** either this user genuinely applies
   generically across the board (a real answer — cut `by_focus` and its
   artifact-existence COALESCE fallback, keep the toggle only if the user still
   wants to mark the rare tailored one), or a bulk write set most of them to
   `false` at once rather than one click at a time (an artifact, not a
   signal — `set_focused` writes `False` for ANY posted value that isn't the
   literal `"yes"`, so a stray or empty POST silently marks "generic").
   **Unresolved as of 29 Jul: check `events`/`captured_at` timestamps on the
   46 to tell which.** Don't act on this dimension (report it, cut it, trust
   it) until that's answered.
4. **First feature: follow-up drafting** (`docs/features.md` §3.1) — best
   evidence-to-effort ratio in the backlog, and `REMINDER_DAYS = 10` already
   matches the researched 7–10 business-day window. **Half-built as of 28 Jul:**
   the list's needs-follow-up block (UI rule 9) already surfaces the right set
   and logs a one-click `follow_up_sent`. What's missing is the draft itself —
   the block is where it belongs, next to the button that currently just marks
   it done.
5. **One real apply via the extension** on each platform; fix whichever
   adapter selectors have drifted. **LinkedIn: done** (27 Jul, VANARSDEL — three
   defects found and fixed). **JobStreet: two real applies, still not
   clean.** 28 Jul (Baldwin Recruitment) surfaced the navigating-apply receipt
   loss and the opening-click timestamp; 29 Jul (First Up Consultants)
   surfaced an MV3 service-worker race that lost the stashed company name
   entirely (`unknown company` on the record — corrected by hand). All three
   fixed same-day; salary/work-type capture added alongside. **One more real
   apply needed** to confirm the race fix holds — see known-untested.
   **Indeed: never exercised.** Keep this **load-unpacked only** — an
   unpacked extension has a random per-install ID, while a Chrome Web Store
   listing mints a stable public one that LinkedIn's extension-fingerprinting
   script enumerates (`docs/open-source.md` §3). Do not publish to the store
   during the author's active job search.
6. If enabling dedup: set `VOYAGE_API_KEY`, run `scan`, review duplicate
   bands in `/triage` on real cross-platform posts.
7. **Decide the email body retention policy** (design doc §14 open
   question #4, `migrations/001_init.sql:86-87`) — overdue by its own stated
   trigger: the comment's "revisit before multi-user" passed when
   `003_multi_tenant.sql` shipped RLS. Raw `emails.body_text` (recruiter
   names, salary figures, personal details) sits unencrypted-at-rest per-row
   with no retention policy. Decide: keep storing full bodies for
   reprocessing (current behavior), or store only `gmail_message_id` and
   re-fetch on demand. **Under the open-source direction this is ordinary
   hygiene, not a compliance gate** — it is the self-hoster's own data on
   their own disk. It was a hard gate only on the (superseded) hosted path,
   where Google's restricted-scope limited-use policy applies
   (`docs/monetization.md` §2.2). IMAP shipping makes "re-fetch on demand"
   more attractive than it was: `X-GM-MSGID` is a stable, searchable id, so a
   body can be recovered without depending on a UID that `UIDVALIDITY` can
   invalidate — see `docs/email-ingest.md` §9 q4. The IMAP work deliberately
   did not decide this; it remains open.
