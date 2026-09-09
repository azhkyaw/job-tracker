# Job Application Tracker

Personal job-application tracker for LinkedIn / JobStreet / Indeed. All four
phases built (Jul 2026) and test-driven. Design rationale, as of Jul 2026:
`docs/design.md` — read it before any structural change, and read its header
first: the reasoning holds, but its schema and endpoint inventories have gone
stale (schema truth is `migrations/`, route truth is `pipeline/web.py`).
Direction: **open-source release**, not SaaS — `docs/open-source.md` (LinkedIn
extension-fingerprinting risk, the 7-day OAuth token trap, release checklist).
Feature priorities with the market research behind them: `docs/features.md`.
How mail gets in — Gmail IMAP + app password is the default ingest path
(shipped 28 Jul 2026), OAuth the alternative for Workspace/Advanced Protection
accounts: `docs/email-ingest.md`. WHICH mail gets in is a separate switch:
`config.INGEST_ALL` (env `TRACKER_INGEST_ALL`, and in `.env.example` since
2 Sep 2026 — invariant #10) takes everything, and is on for this author's
job-only mailbox. `docs/monetization.md` is
superseded but retained for its Gmail restricted-scope compliance analysis.
Installing the extension on a second device (an existing account, not a
fresh signup) — including the single-token-per-account gotcha regenerating
it silently breaks other devices with: `docs/extension-install.md`.
Open-weight models on an OpenAI-compatible server (vLLM first) go through
`pipeline/llm.py` (Key files); the hands-on lab that verifies it against a
real server on GCP, stage by stage with results recorded, is
`docs/vllm-lab.md` (started 8 Sep 2026; `scripts/gcp/vllm-vm.sh` is the VM,
`scripts/replay_classify.py` diffs any model against the ~300 stored
classifications, read-only). The lab lives in GCP project `vllm-lab-2609`,
and **the author provisions it by hand to learn** — assist by explaining
and verifying, never by running `gcloud` create/link/delete for them. As of
8 Sep it is blocked before its first VM: the billing account refuses a
fourth linked project and L4 quota is 0 everywhere; both are theirs to
clear.

A fourth thing the extension captures as of 28 Jul 2026: the **screening
questions an apply form asks and the answers given** (`application_answers`,
migrations 009 + 010; `pipeline/answers.py` owns normalisation and the write).
Per application on its detail page, and grouped per *question* across every
application at `/answers` — the second view is the point, since the same
questions recur almost verbatim between employers. A form may ask the same
question more than once (a work-history **repeater**), so a row is identified
by (application, question_norm, **occurrence**) — see invariant #11.
**Not every labelled control is a question.** The sweep can't tell a screening
question from the form's own chrome, so `answers.py:_control_kind()` filters
it: the resume picker is PROMOTED to `applications.resume_file` (migration
014, feeding `analytics.by_resume`), while "Mark job as a top choice" and
"Follow <employer>" are dropped. Match anchored patterns against
`question_norm`, never loose prefixes — a bare `follow` prefix also swallows
"Do you follow industry news to stay up to date?".

## Key files

- `pipeline/web.py` — all routes (FastAPI + Jinja, no JS)
- `pipeline/ingest.py` — the only place job/posting/application rows are created
- `pipeline/matcher.py` — matches inbound email to an application, fabricates events
- `pipeline/mailbox.py` — mail-ingest orchestrator shared by IMAP + Gmail API
- `pipeline/dedup.py` — the only place two jobs are merged (`merge_jobs`)
- `pipeline/trace.py` — pure timeline/axis geometry for list + detail pages
- `pipeline/analytics.py` — funnel, response-rate, weekly, reminders and
  rejection-reason queries
- `pipeline/llm.py` — the ONE door to every model call (8 Sep 2026). Two
  backends behind `Client.complete()`: Anthropic (default; the request is
  byte-identical to what the stages sent before, so every measured
  max_tokens/thinking note still holds) and OpenAI-compatible
  (`/v1/chat/completions` over the `httpx` already in requirements — vLLM,
  llama.cpp, Ollama, LM Studio). **Routing is by MODEL NAME, one rule**: a
  `claude-*` name is Anthropic's, anything else goes to
  `config.LLM_BASE_URL`; `config.LLM_MODEL` is the default for every stage
  (vLLM serves one model per process) and the per-stage `TRACKER_*_MODEL`
  variables override it, so all-Claude, all-local and mixed are the same
  config with no mode switch. A non-Claude name with no server configured is
  an `Unavailable` (an outage — the worker pauses; a typo must not dead-letter
  the queue). The JSON stages pass `json=True`: the open-weight backend turns
  that into `response_format: json_object` at temperature 0, Anthropic's
  ignores it on purpose. `outage_reason()` is the single failure classifier
  for BOTH backends — the worker imports neither SDK. Tested by
  `tests/test_llm.py` against an `httpx.MockTransport` fake server, no DB.
  **Never exercised against a real vLLM** as of 8 Sep 2026.
- `pipeline/worker.py` — the job queue: claim, savepoint, backoff, dead-letter
  and `_outage()` (invariant #7; the billing-outage case is in `.claude/rules/llm.md`)
- `pipeline/email_classifier.py` — `norm_company()` (invariant #4) and the
  per-stage model constants `CLASSIFY_MODEL` / `EXTRACT_MODEL` (invariant #5)
- `pipeline/answers.py` — screening-answer normalisation and the ONLY write to
  `application_answers` (invariant #11)
- `pipeline/salary.py` — parses the platform's displayed pay string (migration 011)
- `pipeline/covers.py` — cover letters; `load_profile()` reads `users.resume_profile`
- `pipeline/joburl.py` — paste-a-link job-id derivation for manual entry; mirrors
  the adapters' URL logic, and the adapters are the source of truth
- `pipeline/templates/` — every page; ALL CSS is one `<style>` block in `base.html`
- `extension/` — browser capture (LinkedIn/JobStreet/Indeed adapters + shared/)
- `migrations/` — append-only numbered schema files (invariant #8)
- `tests/` — seven Python suites + `test_extension.js` (Node, no DB), see Commands

## Where the rest of this file went (9 Sep 2026)

This file held 2,124 lines, most of it dated case history that only matters
when touching one part of the tree. That history now lives, VERBATIM, in
path-scoped rules that Claude Code loads when it reads a matching file, and
that can be Read directly at any time:

- `.claude/rules/extension.md` — `extension/**`: every capture, frame,
  shadow-DOM, `<dialog>`, popover, MV3 and label gotcha; the blind-capture
  repair and LevelDB-reading procedures; the extension's untested surfaces
- `.claude/rules/mail-ingest.md` — `gmail_*.py`, `mailbox.py`: IMAP, Gmail
  ordering, the OAuth 7-day trap, the pre-filter and `filter_query` gotchas
- `.claude/rules/matching.md` — `matcher.py`, `dedup.py`, `ingest.py`: the
  `COMPANY_TRGM_MIN` rescue, the margin, boilerplate suffixes, event dates
- `.claude/rules/web-ui.md` — `web.py`, `templates/**`, `trace.py`,
  `analytics.py`: the UI design system (rules 1-11) plus every Jinja, CSS,
  FastAPI and ORDER BY gotcha
- `.claude/rules/llm.md` — `llm.py`, `email_classifier.py`, `worker.py`,
  `prompts/**`: thinking/effort measurements, model versioning, the outage story
- `.claude/rules/database.md` — `migrations/**`, `db.py`, scripts: the
  four-places rule, CHECK names, gapped precedence, Neon pooler, view ordering
- `docs/worklog.md` — the dated task register (tasks 1-8 with their
  measurements); what is still open is summarised under "Open work" below

A rule file is tracked text, so the real-names rule (Commands) applies to it,
and `audit_names.py` covers it once it is `git add`ed. A path-scoped rule
arrives on the first matching file READ, not at launch — so when a task is
about one of those families and no file has been opened yet, Read the rule.

**Where new material goes.** A gotcha about one family goes in that family's
rule file, next to its relatives; only one that has nothing to do with any
single part of the tree (a shell, git, Windows or tooling trap) belongs in
this file's Gotchas. Dated task narrative goes to `docs/worklog.md`; "Open
work" below stays a list of what is open, not a history of what was done.

## Commands

- **Run ALL tests: `./scripts/test.sh`** — creates a throwaway `tracker_test`
  DB, applies all migrations, runs `test_extension.js` then the seven Python
  suites in the required order
  (`test_email_ingest` before `test_phase4`, which must stay last). Run after
  every change; suites stub every LLM/embedding call and fake the IMAP socket
  entirely (zero API cost) and have caught every regression in this project
  so far. **That "every LLM call" claim was false for `test_phase4` until
  8 Sep 2026**: its capture carries a `jd_text`, so `extract_jd` called the
  real API with the dummy key on every run and the 401 was buried as a retried
  job — invisible until a credential failure became an `Outage` that
  propagates out of the drain loop. When a suite enqueues a job type, stub
  that handler's LLM entry point too (top of `test_phase4.py`), and read an
  `Outage` escaping a test drain as exactly this finding.
- Serve UI: `uv run python -m pipeline.cli serve` (http://127.0.0.1:8000)
- Worker: `uv run python -m pipeline.cli work [--once]`. **`--once` exits 1 on an
  OUTAGE** — a failure that is about the environment, not the job (credit
  balance, key, network, 5xx; `worker._outage`): it costs the job nothing, and
  a continuous worker pauses `OUTAGE_PAUSE_SECONDS` instead of retrying the
  queue into dead letters. `status` prints the same queue health the UI's
  header band reads (`db.queue_health`): emails waiting, since when, dead
  jobs, the last failure's sentence.
- Gmail: `uv run python -m pipeline.cli auth` (IMAP app password, the default —
  prompts for address + hidden password, verifies before storing) or
  `auth --oauth` (legacy single-user desktop OAuth flow), then
  `backfill -m 12 [--email x]`, `sync` (the 15-min cron entry, exits non-zero
  if any account failed)
- Backlog: `uv run python -m pipeline.cli scan` (enqueue JD extraction/embeddings)
- Account bootstrap/recovery: `uv run python -m pipeline.cli passwd <email>`
- Requires Postgres running: `sudo service postgresql start` (WSL doesn't autostart)
- **Native Windows (no WSL):** see `docs/windows-dev.md` — Docker Postgres
  (`docker compose up -d`, port 55432) + uv-managed Python;
  `scripts/dev-setup.ps1` once, `scripts/test.ps1` to run suites.
  **The Docker ENGINE is often not running** (Desktop exits between sessions),
  and the failure reads as a psql error about dropping `tracker_test`, not as
  "Docker is down". Start it and wait for the daemon before the suite:
  `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"`, then
  `until docker info >/dev/null 2>&1; do sleep 3; done` (Bash tool,
  `run_in_background`), then `docker compose up -d`. The dev DB is Neon and is
  unaffected, so the app keeps working while the suites cannot run.
- **`uv run` is how you run anything here, and the `.venv` is PER MACHINE.**
  There is no `pyproject.toml`, so uv is venv+pip, not a project manager
  (`uv sync` / `uv add` do not apply): `uv run python -m pipeline.cli serve`
  discovers the repo-root `.venv`, and deps come from
  `uv pip install -r requirements.txt`. **A `.venv` from the OTHER machine is
  dead, and every symptom points somewhere else.** uv bakes the base
  interpreter's absolute path into the venv's `python.exe` trampoline, so a
  `pyvenv.cfg` reading `home = C:\Users\<other-machine>\...` under this machine's profile gives
  `(uv internal error) Failed to spawn the python child process: entity not
  found` — which never names the venv — and `uv run` then falls back SILENTLY
  to the bare uv-managed interpreter, where everything dies as
  `ModuleNotFoundError: No module named 'fastapi'`, i.e. it reads as a missing
  dependency. Editing `pyvenv.cfg` does NOT fix it (the path is inside the
  .exe); `uv venv --clear --python 3.12` + `uv pip install -r requirements.txt`
  does. Same class as `.env` diverging per machine (Environment, below) and as
  any script holding an absolute repo path — `scripts/replay_thresholds.py`
  carried `D:/projects/job-tracker` and could not run on this machine at all
  until 2 Sep 2026.
- **Never write a real employer, agency or person into a tracked file.** Name a
  real case by its date and a placeholder from the Northwind/Contoso family
  (`Northwind Labs`, `Contoso Markets`, `Jane Recruiter`), keeping whatever
  structural property the comment depends on — a short form inside a long one,
  a dropped parenthetical, an all-caps brand. The 26 Jul 2026 scrub had to be
  repeated on 2 Sep 2026: 37 commits of ordinary comment-writing had put about
  35 real names back, two of them people. `uv run python scripts/audit_names.py`
  derives the name set from the DEV database — every company, contact and
  recruiter ever stored, plus the author's own identifiers — and greps the
  tracked files; `--history` does the same over every blob and commit message
  and is the zero-survivors check after a rewrite. Two blind spots need the
  eye: a job browsed but never applied to, and an employer whose mail was not
  job-related, never reach the database. CLAUDE.md was scrubbed the same day
  with the same placeholders, so its case histories now read `Coho`,
  `Wingtip Talent Group`, `Tailspin Consulting` and so on — the dates are
  the real key to a case; the record itself is in the database.
  Three things the 2 Sep rewrite taught, for the next one. Verify a rewrite by
  the TIP'S TREE HASH: the tip is already scrubbed, so a correct rule set is a
  no-op on it, and any change is either a name the scrub missed or a rule that
  is too broad — it caught three misses, one of them an employer's mail domain
  the database cannot know. Names WRAP across lines (`Morgan` ending one line,
  `McKinley` starting the next — five such in history), which single-line
  rules and single-line sweeps both miss, so scan for the wrapped form and add
  fragment rules. And the scrub tooling's own docstrings, comments and commit
  messages are tracked text too: both put a name back during the very session
  that removed them. `git filter-repo` also drops the `origin` remote — re-add
  it before the force-push, and re-clone the other machine afterwards.
- **Dev DB shell:** the dev DB is Neon now (`docs/windows-dev.md` → Managed
  Postgres), reached via `TRACKER_DATABASE_URL` in `.env` same as the app.
  Local Docker Postgres is only `scripts/test.ps1`'s throwaway DB —
  `docker compose exec db psql` connects to the wrong database.
- **Quick read-only query against the dev DB:** `uv run python -c "from
  pipeline import db; ..."` — faster than a psql shell for inspecting real
  rows (dict results) while chasing a reported bug against live data; used
  this repeatedly to confirm bugs against real applications before fixing.
- **Repairing a capture that saved blind, or that saved nothing at all**: drive
  `POST /applications/{id}/edit` (or `/applications/new`) through `TestClient`,
  never hand-written UPDATEs, and repair BEFORE running `sync`. The full
  procedure and the four real cases: `.claude/rules/extension.md`.
- **Reading the extension's ring buffers WITHOUT the popup**: they are a LevelDB
  under `<profile>/Local Extension Settings/<id>/`, and the id derives from the
  extension path (this machine: `eeigcpnpbikhppjomninfjldeamiebba`). The
  derivation, the parsing rules and the traps: `.claude/rules/extension.md`.
- **Fast syntax check before a full suite run:** `python -c "import ast;
  ast.parse(open('path/to/file.py', encoding='utf-8').read())"` — catches
  typos without a DB reset/migration cycle.
- **Same idea for extension JS:** `node --check path/to/file.js` — catches
  syntax errors before reloading the unpacked extension. Won't catch
  runtime bugs (see the `location`-shadowing gotcha in `.claude/rules/extension.md`).
- **`node tests/test_extension.js`** — the only real tests `extension/` has;
  wired into both `scripts/test.*` ahead of the Python suites, and a missing
  `node` prints a visible SKIP. Frame geometries, the `readJob(doc, loc)`
  contract and how to validate a new case against the OLD code:
  `.claude/rules/extension.md`.
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
   `docs/features.md` §7 always intended). **A `reason` goes on ANY
   `rejected` event, whatever its source** (9 Sep 2026,
   `web.py:set_rejection_reason`): an emailed rejection's type and date are
   the email's facts and stay read-only, the reason is the user's annotation
   of it — 40 of 51 real rejections came by email and none could carry one.
   The list wears it in grey and filters on it (`?reason=`, with
   `unrecorded` as the tagging queue) and `/analytics` counts it. A visa
   non-proceed is a REASON on `rejected`, not a status of its own, by
   design — `.claude/rules/web-ui.md` rule 12 has the costing.
   `_MANUAL_EVENTS` is a superset of
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
   **There is NO inverse for a wrong merge.** `merge_jobs` combines; nothing
   splits. A job wrongly fused needs hand-written surgery (done once — Northwind
   Recruiting, 3 Aug 2026: an inbound recruiter pitch and an unrelated apply to
   the same agency, joined by `ENRICH_JOB_SQL` at 0.5588 title similarity).
   That asymmetry is why the enrich bar (`config.ENRICH_TITLE_MIN`, 0.85 since
   3 Aug) is set high rather than tuned to fit: a wrong SPLIT is visible and
   reversible via `merge_jobs`/the `/triage` duplicate band, a wrong MERGE is
   silent and permanent. Prefer the recoverable failure. One `company_norm`
   routinely covers several unrelated roles — every agency, and any employer
   hiring more than one person — so company agreement carries almost no
   information and the title is doing all the work.
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
   application. Used in anger on three at once (Contoso, Fabrikam, Litware,
   4 Aug 2026), where the email hadn't been *misfiled* so much as it had
   created its own record after the `COMPANY_TRGM_MIN` gate hid the real one —
   same remedy, since that record contained nothing but the email's own events
   and `_job_is_empty` therefore cleared it. 98 → 95 applications. Used a fourth
   time the same way (Wingtip Talent Group, 4 Aug 2026, 96 → 95) after the gate
   hid the real record from LinkedIn's own confirmation — same shape, same
   remedy. When a duplicate has an extension-captured twin, keep the EXTENSION
   record: it carries the `platform_job_id`, the JD and the answers, while the
   email-made one has none of them.
   **Driving `refile_email` without the UI:** it is a route, not a library
   function, so reach it through the real code path rather than hand-writing the
   UPDATEs — `starlette.testclient.TestClient(web.app, cookies={'session': sid})`
   with `sid` from `auth.create_session(conn, user_id)` runs the actual route
   against the dev DB. Snapshot the affected rows to JSON first; the route
   deletes events and may delete an application.
4. **`norm_company()`** in `pipeline/email_classifier.py` is the single source
   of truth for `company_norm`. Never reimplement it in SQL. It strips SEA
   corporate forms including Indonesian PT/CV *prefixes*.
5. **Prompts are versioned files** in `prompts/`. Changing one = new file
   (`*_v2.txt`) + bump the constant; every DB row records its prompt version
   so selective re-runs are possible. Never edit a version file in place.
   **The MODEL deserves the same discipline, and the schema already supports
   it** — `emails.model` (and `extractions.model`) records the model per row
   exactly as `prompt_version` does, so a model swap stays attributable and
   selectively re-runnable. Treat it like a prompt version: change the default,
   say why in a comment next to the constant, and leave existing rows on the
   old model rather than mass re-running. `CLASSIFY_MODEL` moved to
   `claude-sonnet-5` on 4 Aug 2026 (Haiku was reproducibly misclassifying ATS
   account-activation mail as `confirmation` — see the constant's comment for
   the measurement); `EXTRACT_MODEL` and `JD_MODEL` stay on Haiku, since every
   extraction inspected has been correct. A model change is NOT a substitute
   for a prompt fix where the prompt is genuinely underspecified: a stronger
   model infers the intended answer, a rule states it for every model.
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
    **`config.INGEST_ALL` (env `TRACKER_INGEST_ALL`, default False) bypasses
    the candidate pre-filter entirely** for a mailbox that is job-related only:
    `is_candidate` returns True immediately and `filter_query()` returns `""`.
    Every caller must DROP an empty predicate, never pass it on (see the
    `X-GM-RAW` gotcha). It is paired with a retention rule, not shipped alone —
    `worker.handle_classify_email` purges `body_text` on a `not_job_related`
    classification when the flag is on, so a widened net does not become a
    widened retention footprint. Turning it on without that purge is the
    mistake: the pre-filter was also the only thing enforcing "the body is
    never sent to the LLM unless a rule hits" (`config.py` §11 comment).
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

## UI design system

Lives in `.claude/rules/web-ui.md` (loads with `web.py` / `templates/`). The
rules that must survive any edit, one line each: colour means exactly one
thing, the state of the wait, and everything else is grey (blue engaged, amber
waiting, rust closed, green offer); the dark palette is written TWICE and both
copies change together; every trace on a page shares ONE axis; charts are
single-series, never dual-axis; never print a rate below `analytics.MIN_RATE_N`;
the app has no JS, ever.

## Gotchas learned the hard way in the original build

Cross-cutting ones only. The family-specific ones (extension, mail, matching,
web/UI, LLM, database) moved VERBATIM into `.claude/rules/` — see "Where the
rest of this file went" above.

- psycopg server-side binding cannot type a bare `%s IS NULL` — cast it
  (`%s::text IS NULL`). This bit us once in the matcher.
- **Same class, different operator: `Json(...)` binds as `json`, and Postgres
  has no `jsonb || json` operator** (9 Sep 2026). Every INSERT in this repo
  passes `Json(...)` into a `jsonb` column and gets away with it, because an
  assignment cast applies; CONCATENATION does not, so the first
  `SET payload = payload || %s` written here failed with `operator does not
  exist` and a "you might need explicit type casts" hint. Cast the parameter
  (`payload || %s::jsonb`). The general shape is the bullet above: psycopg
  types a parameter from the VALUE, not from where it lands, so any position
  that isn't a plain assignment or comparison needs the cast spelled out.
- **A registry of interchangeable things needs a test that LOOPS the registry.**
  `_SORTS` had four keys; the suite named three of them by hand and the fourth
  was broken for weeks. `tests/test_web.py` now iterates `web._SORTS` (crossed
  with `origin`/`status`/`q`, since one f-string ORDER BY is shared by every
  filter combination), so a new sort key is covered by existing. Same shape of
  gap to look for anywhere else a dict maps names to SQL or handlers.
- When patching code with scripts, ASSERT the anchor matched — a silent
  no-op replace shipped a broken build once; the tests caught it.
  **Assert the REPLACEMENT too.** A heredoc'd Python patcher adding migration
  014 to `test.sh`/`README.md` (3 Aug 2026) mangled `\\\n` into a literal `\n`:
  the anchor assertion passed and the script reported success, but the text it
  wrote was corrupt. Prefer the Edit tool over shell-heredoc patchers for
  anything containing backslashes. (Line endings are PER MACHINE under
  `core.autocrlf=true` — `git ls-files --eol CLAUDE.md` says what this working
  copy has; match it when constructing an Edit `old_string`.)
  **A second, unrelated reason to stop heredoc'ing a patcher: a LARGE inline
  heredoc is truncated mid-body** (9 Sep 2026). Two died the same session at
  roughly 7.5 KB of command text, both reporting bash's
  `unexpected EOF while looking for matching ''` — which points at quoting and
  is a lie: the same bytes, written to a file and run as `bash file.sh`, ran
  fine at 20 KB, and the `'''` blocks the message blames are innocent. So the
  failure scales with the COMMAND, not its content, and a patcher that worked
  at 5 KB will start failing as its docstrings grow. Write the script with the
  Write tool and run it by path — that also gets `python -c "import ast"` as a
  syntax check on the patcher itself before it touches the tree.
- **A file with MIXED line endings is committed as-is, and then every line of
  it is a diff** (8 Sep 2026). The repo stores LF and `core.autocrlf=true`
  hands out CRLF on checkout; git normalises a consistently-CRLF file back to
  LF on `add`, but a file whose new lines are LF among old CRLF ones (an
  appended `printf`, a patch written with `\n`) is left alone, so README.md
  landed as 578 changed lines for a 46-line addition and had to be amended.
  `git diff --stat` before committing is the check — a count near the file's
  length is this — and normalising the touched file to LF
  (`b.replace(b"\r\n", b"\n")`) before `git add` is the fix. The scrub
  history's tree-hash trick has the same shape: verify the size of a change
  against what it should be, not just that it applied.
- **`set -e` does not stop a chain inside Claude Code's Bash tool.** The
  harness wraps the command in a context where bash ignores `-e` (the same
  rule that disables it inside `&&`/`||` lists), so a failing `uv run python
  patcher.py` was followed by every later step and a commit was made from the
  wrong tree (8 Sep 2026; reset before push). Check `$?` after each step that
  matters, or chain with `&&` explicitly. `uv run` itself propagates the
  exit code correctly — verified.
- **Windows: `uvicorn --reload`'s process tree outlives a single `taskkill`.**
  The PID `netstat`/`Get-NetTCPConnection` reports often isn't the real
  root — cross-check via `Get-CimInstance Win32_Process -Filter
  "ProcessId=X"` and `taskkill //F //T //PID <true root>`, or the reloader
  just respawns a worker and the port stays bound.
- **Starting the dev server from Claude Code's Bash tool: use
  `run_in_background: true` + `TaskStop`, not `&` + `kill %1`.** The Bash
  tool's shell state doesn't persist between separate tool calls, so a
  `&`-backgrounded `uvicorn --reload` dies when that call's shell session
  ends — the next call finds nothing listening, which looks like a crash.
  `run_in_background` keeps it alive across calls; `TaskStop` tears down
  the whole reloader tree cleanly, sidestepping the taskkill/PID gotcha
  above too.
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
- **Windows consoles default to cp1252.** A Python one-liner printing
  non-ASCII (em-dash, curly quotes) via Bash/PowerShell can raise
  `UnicodeEncodeError` — `sys.stdout.reconfigure(encoding='utf-8',
  errors='replace')` first.
- **The suite inherits the developer's `.env`, because `config.py` auto-loads
  it.** A local `TRACKER_INGEST_ALL=true` made `test_email_ingest`'s
  pre-filter assertions pass VACUOUSLY and broke `backfill_query`'s
  byte-identity pin — and it stayed green until the next full run, because the
  flag was added between two runs. That suite now pins `config.INGEST_ALL =
  False` as its baseline right after the imports. Any future behaviour flag
  read from the environment needs the same treatment: a test that depends on
  ambient config is a test that means something different on each machine.
- **Debugging in claude-in-chrome has three traps that look like something
  else.** A hidden tab is FROZEN (rAF never fires, `innerText` reads `""`,
  screenshots time out — it is not the site rate-limiting you); the tool BLOCKS
  base64 and cookie-looking output and truncates at ~1,000 chars; Dark Reader
  may be neutralising colours. Probes and workarounds: `.claude/rules/extension.md`
  (tab freeze, output blocks, safe adapter testing) and `.claude/rules/web-ui.md`
  (Dark Reader, verifying an authenticated page).

## Environment

Python 3.12 · Postgres 15+ with `pgvector` + `pg_trgm` · `pip install -r requirements.txt`

Env vars: `ANTHROPIC_API_KEY` · `TRACKER_SECRET_KEY` (set ONCE, keep forever —
losing it orphans encrypted Gmail creds; since IMAP shipped this key can also
protect a full-mailbox app password rather than only a `gmail.readonly`
token, so its blast radius on leak/loss is strictly larger than before) ·
`TRACKER_DATABASE_URL` (default
`postgresql:///tracker`) · `VOYAGE_API_KEY` (optional; absent = dedup simply
off) · `TRACKER_API_TOKEN` (legacy single-user extension token; dies when a
second account exists) · `TRACKER_BASE_URL` (needed for Gmail web OAuth) ·
`TRACKER_INGEST_ALL` (invariant #10; job-only mailboxes) ·
`TRACKER_LLM_BASE_URL` / `TRACKER_LLM_MODEL` / `TRACKER_LLM_API_KEY` /
`TRACKER_LLM_TIMEOUT_SECONDS` / `TRACKER_LLM_EXTRA_BODY` (open-weight models
on an OpenAI-compatible server — `pipeline/llm.py` under Key files; with every
stage local, `ANTHROPIC_API_KEY` can stay unset).
Secrets files are gitignored: `credentials.json`, `credentials-web.json`,
`.gmail_token.json`, `.env`, `profile.md` (the last is no longer READ by
anything — see below — but stays in `.gitignore` so a stray copy from before
the change can't be committed).

**`.env` now carries BEHAVIOUR flags, not only credentials, and it is
per-machine** — this author runs the app from two machines against one shared
Neon DB (`docs/windows-dev.md`). A missing `TRACKER_DATABASE_URL` fails loudly;
a missing `TRACKER_INGEST_ALL` does not — that machine silently reverts to the
old pre-filter and drops job mail the other machine ingests, against the same
rows. When two machines behave differently, diff `.env` before diffing code,
and prefer `config.py` (in git, shared) over `.env` for anything that isn't a
secret or a genuinely per-machine path.

**The resume profile lives in `users.resume_profile` and nowhere else**
(since 21 Aug 2026). `covers.load_profile()` takes the column's value as an
argument and raises `covers.ProfileMissing` naming **Settings -> Resume
profile** when it is empty; `config.RESUME_PROFILE` / `TRACKER_RESUME_PROFILE`
and the `profile.md` file fallback are gone. That fallback was wrong twice
over. It was a **release blocker**: the file was the only route the error
message and the README ever named, while the column — the one with a UI — was
mentioned nowhere, so a self-hoster from a clean checkout hit `resume profile
not found at profile.md` with no way to learn what actually feeds the
generator (this author hit it too, on a real cover-letter job). And it was a
**tenancy hole**: one file, no `user_id`, so a second account that had not
filled in Settings would silently be handed the FIRST account's profile and
have their letter written from someone else's career — every other per-user
secret here is scoped by RLS (invariant #6), and a path on disk cannot be.
`tests/test_phase3.py` seeds the column in `_bootstrap_session()` and asserts
the missing-profile error names the page rather than a file.

`TRACKER_*`/`ANTHROPIC_API_KEY`/etc. auto-load from a gitignored `.env` at
repo root (`pipeline/config.py`, `override=False` — real shell vars still
win). No per-shell export needed for local dev.

## Known-untested surfaces (verify on first real contact)

Detail lives with each family's rule file; this is the index.

- **Extension** (`.claude/rules/extension.md`): every adapter selector is
  best-effort; JobStreet's race fix still wants one clean real submit; the
  0.9.0 `<dialog>` sweep and `getJob()`'s self-document fallback are UNVERIFIED
  on a real apply — read `doc_source` and the "Recent form sweeps" line on the
  next Easy Apply; `getRecruiter()` has one card of evidence; textareas have
  never been stored; Indeed has never been exercised.
- **Mail** (`.claude/rules/mail-ingest.md`): IMAP verified on a real inbox
  28 Jul 2026; the web IMAP connect form, Gmail web OAuth and the full `-m 12`
  window are not.
- **LLM** (`.claude/rules/llm.md`): `pipeline/llm.py`'s OpenAI-compatible
  backend has never met a real vLLM; the Voyage embeddings call has never run.
- **UI** (`.claude/rules/web-ui.md`): the palette has never been seen on a real
  screen without Dark Reader in the way.

## Open work (as of 9 Sep 2026)

The dated register behind each item, tasks 1-8 with their measurements, is
`docs/worklog.md`; read the matching entry before acting on one.

- **Bug, unfixed (2 Sep 2026):** `matcher._event_time` applies a stated
  `event_date` to interview invites, so the event lands on the INTERVIEW day
  rather than the day the invite arrived (4 of 37 invites; inflates
  `avg_days_to_resp`, stretches the trace axis past today). Fix shape: a stated
  date moves confirmations and rejections only; keep an interview date in the
  payload. Detail: `.claude/rules/matching.md`.
- **Follow-up drafting** on an age-capped queue: cap `/follow-ups` near 21
  days (133 qualify today, ~40 after the cap, and zero `follow_up_sent` has
  ever been filed), then build the draft next to the button. (worklog task 4)
- **Extension, next real Easy Apply:** verify 0.9.0 is live and the tab was
  opened after the reload; read `doc_source` and the sweep line. JobStreet still
  owes one clean submit with the race fix and salary capture together. (task 5)
- **Release blockers:** LICENSE (Apache-2.0 recommended), split the extension
  into its own repo, decide whether CLAUDE.md ships; `audit_names.py --history`
  is the pre-publish check. (task 3)
- **vLLM lab** (`docs/vllm-lab.md`): blocked at its §2 on the billing-account
  project limit and the L4 quota, both the author's to clear by hand. (task 8)
- **Email body retention** for job-related mail is undecided; 225
  `not_job_related` bodies (2.5 MB) could be cleared by one UPDATE, the author's
  call. (task 7)
- **Catch-up sweep** `backfill -d 21` under `INGEST_ALL`, deferred 7 Aug 2026
  (~200-400 classify calls, ~$2-3). (task 1)
- **Dedup and extraction verification have never run on real data** (0
  embeddings, 0 of 192 rows verified); exercise once or label experimental
  before release. (task 6)
- **Visa signal vs outcome is still two unjoined facts** (task 9): 22
  postings extracted `local_only`, all applied to, 6 rejected, 11 waiting —
  and 38 rejections wait to be tagged at `/?reason=unrecorded`.
