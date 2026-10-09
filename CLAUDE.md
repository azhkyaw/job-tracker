# Job Application Tracker

Personal job-application tracker for LinkedIn / JobStreet / Indeed. All four
phases built (Jul 2026) and test-driven. Direction: **open-source release**,
not SaaS (`docs/open-source.md`).

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
014, feeding `/analytics`' which-resume comparison), while "Mark job as a top choice" and
"Follow <employer>" are dropped. Match anchored patterns against
`question_norm`, never loose prefixes — a bare `follow` prefix also swallows
"Do you follow industry news to stay up to date?".
**Some answers are withheld, not stored** (24 Sep 2026): an identity number,
date of birth or age, race, religion, marital status, gender, veteran status
or disability keeps its QUESTION and gets the answer `(withheld)` —
`answers.is_sensitive()`, mirrored by `extension/shared/answers.js` so the
value never leaves the browser, both held to `tests/sensitive_questions.json`.
Nationality and work authorisation stay recorded: the visa analysis reads them.

## Docs map

Read the matching doc before working in its area. They are dated research
and design records; the code and `migrations/` win where they disagree.

- `docs/design.md` — before any structural change: the design rationale as
  of Jul 2026. Read its header first: the reasoning holds, but its schema
  and endpoint inventories have gone stale (schema truth is `migrations/`,
  route truth is `pipeline/web.py`).
- `docs/open-source.md` — the release direction: LinkedIn
  extension-fingerprinting risk, the 7-day OAuth token trap, the release
  checklist and the history-rewrite lessons (its §11).
- `docs/features.md` — feature priorities, with the market research behind
  them.
- `docs/email-ingest.md` — how mail gets in: Gmail IMAP + app password is
  the default path (shipped 28 Jul 2026), OAuth the alternative for
  Workspace/Advanced Protection accounts. WHICH mail gets in is a separate
  switch: `config.INGEST_ALL` (env `TRACKER_INGEST_ALL`, and in
  `.env.example` since 2 Sep 2026 — invariant #10) takes everything, and is
  on for this author's job-only mailbox.
- `docs/extension-install.md` — installing the extension on a second device
  (an existing account, not a fresh signup), including the
  single-token-per-account gotcha: regenerating the token silently breaks
  the other devices.
- `docs/career-sites.md` — capturing on employer career sites and their ATS
  forms, beyond the three platforms: the measured gap, an 18-vendor survey
  of how job pages expose a job, and the plan (phases A-C built 24 Sep 2026).
- `docs/jd-extraction-models.md` — which Claude model and effort run the JD
  extractor, measured on a hand-labelled gold set (25 Sep 2026).
- `docs/llm-alternatives.md` — other LLM APIs against the Claude stages
  (3 Oct 2026): cost at this volume (~US$5/month, so not a reason to
  switch), which providers `pipeline/llm.py`'s OpenAI-compatible backend
  reaches unchanged and the code changes the rest need (402/408 filed as
  job failures, `temperature` not overridable), and where each sends the
  data. Its §13 replayed Sonnet 5.5 the same day: classify moved to it,
  JD extraction and the rejection reason stay on Sonnet 5.
- `docs/vllm-lab.md` — open-weight models on an OpenAI-compatible server
  (vLLM first) go through `pipeline/llm.py` (Key files); this is the
  hands-on lab that verifies it against a real server on GCP, stage by
  stage with results recorded (started 8 Sep 2026; `scripts/gcp/vllm-vm.sh`
  is the VM, `scripts/replay_classify.py` diffs any model against the ~700
  classified emails that keep a body, read-only). The lab lives in GCP
  project `vllm-lab-2609`, and **the author provisions it by hand to
  learn** — assist by explaining and verifying, never by running `gcloud`
  create/link/delete for them. As of 8 Sep it is blocked before its first
  VM: the billing account refuses a fourth linked project and L4 quota is 0
  everywhere; both are theirs to clear.
- `docs/windows-dev.md` — native Windows: Docker Postgres for the suites,
  uv-managed Python, the Neon dev DB.
- `docs/monetization.md` — superseded, retained for its Gmail
  restricted-scope compliance analysis.
- `docs/monetization-review.md` — whether and how this could earn money,
  re-examined 30 Sep 2026 with sources and with unit costs measured on
  this install. Read its §3 first: the author's right to earn in Singapore
  decides the rest. Confirms `docs/open-source.md` and says what would
  reopen the question.
- `docs/worklog.md` — the dated task register (tasks 1-78 with their
  measurements); what is still open is summarised under "Open work" below.

**Path-scoped rules.** Dated case history that only matters when touching
one part of the tree lives VERBATIM in `.claude/rules/` (split out of this
file on 9 Sep 2026, when it held 2,124 lines). Claude Code loads a rule on
the first READ of a matching file, not at launch — so when a task is about
one of these families and no file has been opened yet, Read the rule. Any
of them can be Read directly at any time.

- `.claude/rules/extension.md` — `extension/**`: every capture, frame,
  shadow-DOM, `<dialog>`, popover, MV3 and label gotcha; the blind-capture
  repair and LevelDB-reading procedures; the extension's untested surfaces
- `.claude/rules/mail-ingest.md` — `gmail_*.py`, `mailbox.py`: IMAP, Gmail
  ordering, the OAuth 7-day trap, the pre-filter and `filter_query`
  gotchas, what the provider dict's `sent` and `id` are
- `.claude/rules/matching.md` — `matcher.py`, `dedup.py`, `ingest.py`: the
  `COMPANY_TRGM_MIN` rescue, the margin, boilerplate suffixes, event dates,
  `refile_email` in practice
- `.claude/rules/web-ui.md` — `web.py`, `templates/**`, `trace.py`,
  `analytics.py`, `insights.py`, `charts.py`: the UI design system (rules
  1-20), every Jinja, CSS, FastAPI and ORDER BY gotcha, and invariant #2 in
  detail
- `.claude/rules/llm.md` — `llm.py`, `email_classifier.py`,
  `jd_extraction.py`, `covers.py`, `worker.py`, `prompts/**`:
  thinking/effort measurements, the model history, the outage story, why
  the resume profile is a column
- `.claude/rules/database.md` — `migrations/**`, `db.py`, scripts: the
  four-places rule, CHECK names, gapped precedence, Neon pooler, view ordering

A rule file is tracked text, so the real-names rule (Commands) applies to it,
and `audit_names.py` covers it once it is `git add`ed.

**Where new material goes.** A gotcha about one family goes in that family's
rule file, next to its relatives; only one that has nothing to do with any
single part of the tree (a shell, git, Windows or tooling trap) belongs in
this file's Gotchas. Dated task narrative goes to `docs/worklog.md`; "Open
work" below stays a list of what is open, not a history of what was done.
An invariant keeps its RULE here and its case history in the rule file.

## Key files

- `pipeline/web.py` — all routes (FastAPI + Jinja, no JS)
- `pipeline/ingest.py` — the only place job/posting/application rows are created
- `pipeline/matcher.py` — matches an email to an application and files its event:
  an employer's outcome for mail received, what the USER did for mail they
  sent (`EVENT_TYPE`'s `sent_*` rows, migration 016 — never an employer's outcome).
  First, by the job's own id on its hiring system when the mail names exactly
  one (`match_by_ats_id`, migration 018: by lookup, behind the same company
  gate), then by company and title
- `pipeline/mailbox.py` — mail-ingest orchestrator shared by IMAP + Gmail API;
  `body_from_parts()` is the ONE decision of which MIME part is the body
  (the HTML alternative, since 23 Sep 2026 — `.claude/rules/mail-ingest.md`
  has the four senders whose `text/plain` part is not the mail)
- `pipeline/dedup.py` — the only place two jobs are merged (`merge_jobs`)
- `pipeline/trace.py` — pure timeline/axis geometry for list + detail pages,
  and `rounds()` (8 Oct 2026): the interview ROUNDS of a thread, grouped
  from its invitation events by the day each named — one interview is
  several events — read by the detail page, the list's tag and `/analytics`.
  `wait()` and `tone()` (9 Oct 2026) are the ONE reading of a thread's wait
  and colour, shared by the rows, `/analytics`' squares and the list's
  status bar, whose segments `bands()` paints with their own rows
- `pipeline/analytics.py` — the counts the LIST pages show (summary,
  rejection reasons and endings, reminders), `reapplications` (the follow-up
  queue's "you applied again" suggestion; `web.mark_reapplied` files it),
  `queue()` (7 Oct 2026: `/follow-ups` sectioned by the MOVE each row
  offers — after a round, worth a nudge, applied to again, gone quiet,
  nobody to write to — off `reply_odds`, the SAME Kaplan-Meier curve
  `/analytics` draws, from one query; `insights.quiet_after` is the cut),
  `ratings_owed` (8 Oct 2026: the page's first section, a round you sat
  that has passed unrated on an open thread, `rating_owed_sql` the ONE
  rule), `round_fate_sql` (the same day: what came of the round you sat,
  ONE bucket per application — `ROUND_FATES` — the list's `?interviews=`
  filter and the head's figures; `round_fate` is its Python twin over
  `insights._went`, held equal by the web suite; a stated reason in
  `STOP_REASONS` makes a rejection "stopped, not the interview", never
  lost, off `closing_reason_sql`, the ONE closing-reason rule) and
  `queue_count` (the nav pill, ONE statement, the rows that carry a move),
  `awaiting_you_sql` (the ONE "awaiting your call" rule for an inbound lead:
  no reply of yours since they last wrote, `reply_sql`/`theirs_sql`; the pin,
  the pill, the lede and the queue's lead rows all read it, 28 Sep 2026), and
  `facts()`, the one fetch `/analytics` is drawn from. "How it ended"
  (`rejected_how`) derives LinkedIn's automatic knockout from the timeline:
  a LinkedIn letter inside `SCREEN_HOURS` of the submit is a
  `sponsorship_screen` or `form_screen`, and a screen outranks any recorded
  reason (25 Sep 2026). `LATEST_EXTRACTION` is the one lateral join to a
  posting's newest JD extraction, shared with `web.py`
- `pipeline/insights.py` — every number on `/analytics` (25 Sep 2026), pure
  like `trace.py`: the Kaplan-Meier reply curve (a waiting application is
  "not yet", never "never"), Wilson intervals, the reply window, forecast,
  cohorts, employers, `visa_matrix` (the form's visa bucket × the JD's
  visa group, each cell linking to the list rows it counts), and
  `DIMENSIONS`, the comparison registry the pure
  suite loops, and `interviews` (8 Oct 2026: each round you sat by your own
  rating of it, `payload.went`, against what came of it; a rating filed
  after the outcome is counted as hindsight) and `depth` (the same grid by
  rounds reached, `trace.rounds`). Two words kept apart everywhere: HEARD BACK (any response,
  the list's "reply") and ANSWERED (a rejection or a round — LinkedIn's
  "viewed" notice is not one, and counting it reversed a comparison)
- `pipeline/charts.py` — the analytics page's flow and curve geometry (marks
  in a stretched SVG, words in HTML over it)
- `pipeline/triage.py` — what `/triage` shows beside the box to file mail
  by hand (7 Oct 2026), each rule ONCE so the page and the nav pill agree:
  `suggest` (the records the matcher weighed, off `matcher.scored_candidates`,
  the list `find_match` decides on, with why the email waited), `runs` (one
  sender's identical mail as one card, bulk actions confirmed on a page),
  `twins` (an application filed twice: the employer's form beside the
  board's popover record, minutes apart; `merge_twin` folds onto the BOARD
  record, which dedup's own band would not), `review` (the matcher's own
  less certain filings of the week; "Looks right" is `emails.reviewed_at`,
  migration 020) and `nearest` (the fallback list's order, never a filter)
- `pipeline/llm.py` — the ONE door to every model call (8 Sep 2026). Two
  backends behind `Client.complete()`: Anthropic (default; the request is
  byte-identical to what the stages sent before, so every measured
  max_tokens/thinking note still holds — `effort` is opt-in and only the JD
  stage sets one, 25 Sep 2026) and OpenAI-compatible
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
  per-stage model constants `CLASSIFY_MODEL` / `EXTRACT_MODEL` /
  `REASON_MODEL` (invariant #5). Stage 3, `rejection_reason()` (25 Sep 2026),
  runs on `rejection` mail only: the reason the email STATES, with the
  sentence that states it (`pipeline/quotes.py`, the JD stage's check), or
  none. Its own prompt, so the extraction stays byte-identical;
  `scripts/replay_reasons.py` grades and backfills it. Stage 4,
  `invite_detail()` (9 Oct 2026, `INVITE_MODEL`), runs on `interview_invite`
  mail only, the same way: what the mail DOES (invitation / reschedule /
  cancellation / reminder / scheduling), the kind of round
  (`INVITE_KINDS`, a subset of `analytics.ROUND_KINDS`) and the day it
  names resolved against its own date; `matcher._append_event` files them
  on the event (`invite_role`, `round_kind` + `kind_source`,
  `stated_date`), `trace.rounds` reads the roles, and
  `scripts/replay_invites.py` grades it against the author's own labels
- `pipeline/jd_extraction.py` — the JD extractor (`extract` / `store`), on
  prompt `jd_extract_v2` since 25 Sep 2026 (`docs/jd-extraction-models.md`).
  `VISA_SIGNALS_BY_VERSION` keeps each version's vocabulary, since v1 rows
  remain. v2 records only what the JD states, and `quoted_in` holds each
  signal to a verbatim quote of the JD ("no quote, no signal": one repair
  turn, then `unclear`). `VISA_GROUPS` / `visa_group_sql` fold the signals
  into restricts / sponsors / nothing for the list filter and `/analytics`
- `pipeline/answers.py` — screening-answer normalisation and the ONLY write to
  `application_answers` (invariant #11); `renorm()` re-keys stored rows when
  `norm_question` changes, which `extension/shared/answers.js:normKey`
  mirrors (`tests/question_norms.json` holds the two together).
  `declares_sponsorship` is ONE rule in Python and SQL (its regexes run in
  both dialects), held to `tests/sponsorship_answers.json`; `FORM_VISA` /
  `form_visa_sql` bucket each application by what its form said about visas
- `pipeline/email_apply.py` — does a JD ask for the CV BY EMAIL (4 Oct
  2026)? One sentence holding an address and a CV word, and not also
  offering the apply button. One rule in Python (`instruction()`, which
  quotes the sentence) and SQL (`asks_by_email_sql`), held together by
  `tests/email_apply.json`. `analytics.email_owed_sql` is the ONE "still
  owed" rule (applied, no response, no sent mail, no "I emailed it" / "Not
  needed", `web.mark_emailed`), read by the detail page, `/follow-ups`' first
  section, the list's nudge and the capture receipt
- `pipeline/salary.py` — parses the platform's displayed pay string (migration 011)
- `pipeline/covers.py` — cover letters; `load_profile()` reads `users.resume_profile`
- `pipeline/joburl.py` — paste-a-link job-id derivation for manual entry; mirrors
  the adapters' URL logic, and the adapters are the source of truth
- `pipeline/templates/` — every page; ALL CSS is one `<style>` block in `base.html`
- `extension/` — browser capture (LinkedIn/JobStreet/Indeed adapters + shared/);
  `shared/jobposting.js` reads ANY job page off its schema.org JobPosting and
  owns the one ATS vendor table; `adapters/generic.js` is what the popup
  injects on a site with no adapter AND the static content script on ATS
  hosts, where it finds the application form by structure and captures on
  its submit (`docs/career-sites.md`; its posting id `<host>/<token>` is
  `joburl.generic_id` in Python, both held to `tests/job_urls.json`)
- `migrations/` — append-only numbered schema files (invariant #8)
- `tests/` — eight Python suites + `test_extension.js` (Node, no DB), see Commands

## Commands

- **Run ALL tests.** On native Windows (both machines), from the Bash tool:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1`,
  then grep its output for `^== ` (not via the PowerShell tool: Gotchas);
  on Linux/WSL, `./scripts/test.sh`. Either creates a throwaway `tracker_test`
  DB, applies all migrations, runs `test_extension.js` then the eight Python
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
- Extension icons: `uv run --with pillow python scripts/make_icons.py
  [--preview sheet.png]` regenerates `extension/icons/` from its geometry;
  the design and the concepts it beat are in its docstring
- Answer keys: `uv run python -m pipeline.cli renorm-answers [--apply]` after any
  change to `norm_question` (dry run by default; `.claude/rules/extension.md`);
  `redact-answers [--apply]` after any change to `is_sensitive` (dry run by
  default, prints questions never values, and `--apply` has no undo)
- Account bootstrap/recovery: `uv run python -m pipeline.cli passwd <email>`
- Linux/WSL only: Postgres must be running (`sudo service postgresql start`;
  WSL doesn't autostart). Native Windows runs the suites on Docker, next bullet.
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
  **Its third blind spot is the SHORT form** (a company's first word, where
  the database stores two, is only a weak hit among ~1,600 lines): before
  committing, grep the diff's ADDED lines for the short names of the records
  the session touched: `git diff -U0 | grep '^+' | grep -iE 'name1|name2'`.
  **The fourth is NUMBERS**: a real requisition, a Workday legal entity's
  company code (the "1234" of "1234 … SG Svc Pte Ltd Company") or a tenant id identifies
  the employer as surely as its name, and the audit greps names only. Use
  made-up ones (`R00123456`, `1234`, `431`) and add the session's real ids
  to that diff grep (8 Oct 2026: one code and two requisitions were caught
  there, in a comment, a test and a rule file).
  Lessons for the next history rewrite (verify by the tip's tree hash, names
  that wrap across lines, the scrub tooling's own text, `filter-repo`
  dropping `origin`): `docs/open-source.md` §11.
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
   mutable status column; backfill inserts out of order safely. Replies no
   ingest path reaches (a call, a WhatsApp message) are filed by hand on the
   detail page's timeline form (`web.py:_MANUAL_EVENTS`, backdated, with
   `payload.reason` / `payload.channel` in JSONB — no columns). **A `reason`
   goes on ANY `rejected` event, whatever its source**, and a visa
   non-proceed is a REASON on `rejected`, never a status of its own. When
   the rejection email itself STATES the reason, the pipeline files it
   (`email_classifier.rejection_reason`, quoted verbatim or none, marked
   `payload.reason_source = 'email'` with its quote); otherwise the reason is
   the user's annotation, and the why-select overrides either. The same rule
   on the other close: an inbound approach YOU end ("Not for me", "They went
   quiet") is `withdrawn` + `payload.closed` (+ `why`), never a type of its
   own, and so is your own application gone quiet after a ROUND ("They went
   quiet" only, never before an interview or a person engaging, 29 Sep
   2026; never "rejected", which would count as an answer). Your reply to a
   recruiter is a `note` + `payload.reply` whose
   effect (whose move it is) is DERIVED, `analytics.awaiting_you_sql`
   (28 Sep 2026; web-ui.md rule 17). **How a round you sat went is YOUR
   annotation on the round** (8 Oct 2026): `payload.went` — well / mixed /
   badly, `analytics.WENT_LABELS` — with `went_at`, on any
   `interview_invite` whatever its source (a person getting in touch,
   `engaged`, is not a round you sat since 9 Oct 2026: `trace.rounds`' rule)
   (`web.set_round_went`, the door `set_rejection_reason` is), never a
   reason on the close and never a status; `/analytics` reads it beside
   what came of the round, and a rating filed after the outcome counts as
   hindsight (web-ui.md rule 20). The round's kind (`round_kind`), your
   "not a round" (`round_is`) and what the mail did (`invite_role`, stage
   4) sit on the invitation event the same way — qualifiers in JSONB,
   never a type or a status.
   **`engaged`** (a person reaching out, no next step yet) ranks strictly
   between `viewed` — `matcher.py`'s passive, auto-detected signal only —
   and `interview_invite`, in gapped precedence values. **An offer is a
   round, not a close** (7 Oct 2026, migration 019): it ranks between
   `interview_invite` and the two closes, so the thread stays open — and
   reaches `/follow-ups` when quiet — until you decline it (`withdrawn` +
   `closed=declined`, with `why`; the one "Not for me" allowed on your own
   application), they withdraw it (`rejected`, with a reason) or it goes
   quiet. An offer that was made still counts as one. **A visa rejection
   does NOT auto-set `extractions.visa_signal`.** The full text, with the
   why of each rule: `.claude/rules/web-ui.md` → "Invariant #2 in detail".
3. **postings ≠ jobs ≠ applications.** One application per (user, job). Dedup
   merges at the job level; `pipeline/dedup.py:merge_jobs` is the ONLY place
   records combine (moves events/artifacts/answers/emails/contacts before
   deleting — `application_answers` is UNIQUE per (application, question_norm,
   occurrence), so the loser's copy of a question the winner already answered
   is dropped, not moved; that drop matches on question_norm ALONE, so a loser
   with more repeat entries can't splice its spares into the winner's list;
   the job's ATS id, migration 018, moves to a winner without one).
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
   application. When a duplicate has an extension-captured twin, keep the EXTENSION
   record: it carries the `platform_job_id`, the JD and the answers, while the
   email-made one has none of them. Its four real uses (4 Aug 2026)
   and how to drive it without the UI (the real route through `TestClient`,
   snapshot first — it deletes events): `.claude/rules/matching.md` →
   "`refile_email` in practice".
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
   old model rather than mass re-running. Which stage runs which model, why
   each swap happened, and the one deliberate mass re-run (every JD under
   `jd_extract_v2`, 25 Sep 2026): `.claude/rules/llm.md` → "Model history".
   A model change is NOT a substitute for a prompt fix where the prompt is
   genuinely underspecified: a stronger model infers the intended answer, a
   rule states it for every model.
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
9. **`applications.origin`** (`applied` | `inbound` | `saved`) is
   provenance, separate from derived status (invariant #2), and since
   24 Sep 2026 it decides which list page a record lives on (`/` or
   `/inbound`). `inbound` = a recruiter/employer STARTED the thread, whether
   or not the user applied afterwards. **The system never derives or changes
   it** — no status, event or arriving email moves it, so a record never
   changes page as its thread progresses. **The one door is a human stating
   who started the thread**: "A recruiter approached me first", on the
   timeline form or on manual entry (`web._take_origin`), which files the
   approach AND flips origin in one action, refuses a date after the
   application, and is undone exactly by deleting it (`payload.origin_was`).
   That door exists because a recruiter's WhatsApp message or call never
   reaches an ingest path: the tracker first hears of the thread when the
   user acts on it, and filed it as their own application (two real cases,
   the 27 Aug and 3 Sep 2026 threads). `dedup.merge_jobs` does not reconcile
   origin, and never has — the winner keeps its own.
   `matcher.dispatch` NEVER auto-matches or auto-creates for
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
    normalised `{id, sender, subject, body_text, received_at, sent}` dict
    (what `sent` and `id` are, and why: `.claude/rules/mail-ingest.md`).
    Never copy `is_candidate` / `store_message` / a query
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
waiting, rust closed, green offer) — and since 23 Sep 2026 amber is GRADED by
`trace.heat()` through one `--heat` variable per row, never a second amber;
the dark palette is written TWICE and both copies change together; every
trace on a page shares ONE axis; charts are single-series, never dual-axis;
never print a rate below `analytics.MIN_RATE_N`; words on a page come from
`web.EVENT_LABELS` / `SOURCE_LABELS`, never an event type or a `source` value;
Newsreader carries words and IBM Plex Sans Condensed carries numbers, labels
and controls, and nothing is monospace but real code; the app has no JS, ever.

## Gotchas (cross-cutting)

Cross-cutting ones only. The family-specific ones (extension, mail, matching,
web/UI, LLM, database) moved VERBATIM into `.claude/rules/` — see Docs map →
"Path-scoped rules" above.

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
  **And assert the new text is ABSENT before inserting** (4 Oct 2026): an
  insert BEFORE an anchor leaves the anchor in place, so a second run of
  the patcher, there a `;`-chained retry, doubled 58 lines of a rule file
  silently; counting occurrences caught it.
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
  **Don't check for CR bytes with `grep` here; it gives the wrong answer both ways**
  (25 Sep 2026, probed). Git for Windows' grep (GNU 3.0) strips CR before
  matching unless given `-U`, so `grep -c $'\r'` reads 0 on a CRLF file. And
  inside `$(...)` in the Bash tool, `$'\r'` expands to an EMPTY string, so
  the same pattern matches every line. The same session saw both: one check
  said every staged file carried CRs, the next said none did, and neither
  was true. Count bytes in Python instead, `blob.count(b"\r")` over
  `git cat-file blob <commit>:<path>`, and run it against a CRLF control
  file too, to prove the check can see one.
  **Git Bash's `sed -i` rewrites a whole CRLF file as LF** (28 Sep 2026: a
  one-token `sed` on `tests/test_web.py`). Uniformly LF, so the diff stayed
  the right size and git only warned "LF will be replaced by CRLF"; but it is
  why that warning appears, and the Edit tool then keeps LF in that file.
- **`set -e` does not stop a chain inside Claude Code's Bash tool.** The
  harness wraps the command in a context where bash ignores `-e` (the same
  rule that disables it inside `&&`/`||` lists), so a failing `uv run python
  patcher.py` was followed by every later step and a commit was made from the
  wrong tree (8 Sep 2026; reset before push). Check `$?` after each step that
  matters, or chain with `&&` explicitly. `uv run` itself propagates the
  exit code correctly — verified.
- **One commit per issue from a working tree that holds several, without
  `git add -p`** (interactive, unavailable here; done twice on 24 Sep 2026).
  A file only one issue touched is a plain `git add`. For a file several
  touched, build each commit's version from the FINAL text by undoing the
  later issues' edits in a script (assert every anchor matches exactly once,
  `ast.parse` the result, print the diff size per stage), store it with
  `git hash-object -w --no-filters` and `git update-index --cacheinfo
  100644,<sha>,<path>`; the working tree never changes. Blobs must be LF,
  since the index is (`--no-filters` stores the bytes as given). A clean tree
  after the last commit proves the commits sum to what was tested; each
  INTERMEDIATE commit still needs its own suite run, in a worktree:
  `git worktree add --detach <dir> <sha>`, a `.venv` junction to the real
  one (`New-Item -ItemType Junction`), and `COMPOSE_PROJECT_NAME=job-tracker`
  — Compose names its project after the directory, so from `wt-x` test.ps1
  reports `service "db" is not running`, which reads as Docker being down.
  Delete the JUNCTION before `git worktree remove`
  (`[IO.Directory]::Delete($j, $false)`): a recursive delete that follows it
  empties the real `.venv`.
  **A NEW migration is invisible to a worktree's suite run** (28 Sep 2026):
  the Docker container mounts the MAIN tree's `./migrations` read-only, so
  test.ps1 dies with `psql: error: /migrations/0NN_x.sql: No such file`.
  Copy the file into the main tree's `migrations/` for the run and delete it
  after; left there, it blocks the merge as an untracked file.
  **A hunk two issues both rewrote needs its own text at EVERY stage it
  exists in** (25 Sep 2026, three commits). The builder there fell back to
  HEAD's text for any stage missing from a hand-written hunk, so it built
  commit B with commit A's edit to one CLAUDE.md paragraph reverted. It was
  caught before the push: before the LAST commit, the diff that remains must
  be that issue's lines only. Read its REMOVED lines; a file the last issue
  never touched that still shows as modified is exactly this. Amend the
  earlier commit while it is unpushed.
  **Cheaper when the issues are built one after another** (28 Sep 2026,
  three commits): run the full suite after each issue and copy its touched
  files aside (`stageN/`) before starting the next. The builder then takes
  each intermediate commit's CODE from its snapshot and cuts back only the
  shared docs (asserted anchors, and no stage naming a helper a later stage
  added), and a byte comparison of each commit's blobs with its snapshot,
  LF-normalised, proves it is exactly what that suite run tested: no
  worktree run needed.
  **When the tree also holds the author's own uncommitted work** (30 Sep
  2026): build a file both touch (CLAUDE.md) from HEAD plus your asserted
  edits, never from the working tree, and the proof that the commits sum
  to what was tested becomes "only the author's hunks stay unstaged". To
  land the branch, `git switch master` refuses (that file differs between
  the commits and is modified); `git fetch . <branch>:master` fast-forwards
  the ref, refusing anything else, and the switch then touches nothing. A
  worktree run's per-suite log (`$TEMP/<suite>.log`, overwritten each run)
  says which tree it tested, by its check count.
  **With master CHECKED OUT** (4 Oct 2026), `git fetch . <sha>:master`
  refuses; after committing an intermediate state in a worktree,
  `git reset <sha>` (mixed) moves master and the index to it and leaves the
  working tree, holding the later issue's edits, exactly as it was.
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
- **Don't edit the tree while `test.ps1` runs** (4 Oct 2026). Each suite is
  its own Python process that imports `pipeline/` as it starts, and Jinja
  re-reads a changed template, so an edit mid-run tests a mix of trees. Run
  it with `run_in_background` and touch only `docs/` and CLAUDE.md meanwhile.
- **The suites share ONE user in ONE database, so a record one suite leaves
  moves another suite's numbers** (8 Oct 2026). A new `test_integration`
  fixture (one unanswered application) failed `test_web`'s "worth a
  nudge" check: `/follow-ups` reads the Kaplan-Meier reply curve over every
  application the user has, and over ~38 of them one more moved
  `quiet_after` to 10 days, so a 12-day row went "gone quiet". HEAD passed
  the same check, which is how the cause was told from the clock. A fixture
  that is not the subject of a later suite deletes its records after its
  checks (path 3k's do); when a suite fails on numbers it never touched,
  look at what the suites before it added.
- **`%-d` / `%-m` strftime directives are glibc-only and raise `ValueError` on
  Windows.** Format with `%d` and `.lstrip("0")` instead (`trace.py:_ticks`,
  `insights._day_label`). Sibling of the cp1252 gotcha below — both are ways a
  Linux-shaped one-liner dies natively.
- **Windows consoles default to cp1252.** A Python one-liner printing
  non-ASCII (em-dash, curly quotes) via Bash/PowerShell can raise
  `UnicodeEncodeError` — `sys.stdout.reconfigure(encoding='utf-8',
  errors='replace')` first.
- **Git Bash converts a POSIX path in a native program's ARGUMENTS, never
  inside a script's text** (4 Oct 2026). `uv run python x.py /c/projects/a`
  receives `C:/projects/a`, but `/c/projects/a` written inside a heredoc'd
  script reaches Windows Python as is, which reads `C:\c\projects\a`: a
  FileNotFoundError on a path `ls` just showed. Write `C:/projects/…` in scripts.
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
  (Dark Reader, verifying an authenticated page, a `#fragment`-only
  navigate that never reloads the page). Two plain limits (30 Sep
  2026): the tool reaches only its own tab group, so a tab the author
  already has open (a half-filled form) is out of reach until they drag it
  into the "Claude" group, where it keeps its state; and `javascript_tool`
  awaits a top-level `await`, not a promise an async IIFE returns (that
  comes back as `{}`). And a page call sent alongside `navigate`, or right
  after it, fails with "Can't interact with browser-internal or
  unparseable URLs" while the page is still loading (twice on 7 Oct 2026):
  the URL is fine, so send the next call on its own.

## Environment

Python 3.12 · Postgres 15+ with `pgvector` + `pg_trgm` ·
`uv pip install -r requirements.txt` (Commands → `uv run`)

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
and the `profile.md` file fallback are gone. Never bring the file back: it
was a release blocker and a tenancy hole (one file, no `user_id`). The
story, and the test that pins it: `.claude/rules/llm.md` → "Why the resume
profile is a column".

`TRACKER_*`/`ANTHROPIC_API_KEY`/etc. auto-load from a gitignored `.env` at
repo root (`pipeline/config.py`, `override=False` — real shell vars still
win). No per-shell export needed for local dev.

## Known-untested surfaces (verify on first real contact)

Detail lives with each family's rule file; this is the index.

- **Extension** (`.claude/rules/extension.md`, whose Known-untested section
  says per build what to read on first real contact): every adapter
  selector is best-effort; JobStreet's race fix wants one clean submit;
  Indeed never exercised; `getRecruiter()` has one card of evidence;
  textareas never stored from Easy Apply. Builds 0.9.0-0.29.0 carry
  pieces proven only in tests or by rules run in a live page, waiting on:
  the next Easy Apply (0.9.0's `<dialog>` sweep, `getJob()`'s
  self-document fallback and `doc_source`, 0.10.1's
  `normKey`, 0.23.1's radio rows, 0.25.1's stale-pane checks from a search
  page); the next external apply (0.26.0's kept opener, seen only
  refusing a career site's hop to its ATS, 7 Oct); a Greenhouse job
  board with a picked country (0.23.0); LinkedIn → SmartRecruiters
  (0.24.1); an ATS form with drawn checkboxes or switches (0.24.0); a
  Phenom apply (0.25.2); an enabled Eightfold site (0.25.0); a
  SuccessFactors candidate experience with an upload (0.22.0, 0.26.1); the
  next iCIMS apply (0.27.1-0.27.2); a listing that asks for the CV by
  email (0.27.0's receipt); the next form with a multiple-choice
  checkbox question or a legendless captioned fieldset, Ashby's (0.27.3);
  the next Darwinbox apply (0.28.0: a Submit that shows its word once,
  its confirmation dialog, fields named beside their web components);
  the next blank type-ahead dropdown and the next field a page fills and
  empties (0.28.1, 0.28.3: no live-region text or icon glyph as an answer,
  no answer kept for a field left blank); the next Taleo apply (0.29.0:
  seen only on re-opened applications, task 74).
- **Mail** (`.claude/rules/mail-ingest.md`): IMAP verified on a real inbox
  28 Jul 2026; the web IMAP connect form, Gmail web OAuth and the full `-m 12`
  window are not.
- **LLM** (`.claude/rules/llm.md`): `pipeline/llm.py`'s OpenAI-compatible
  backend has never met a real vLLM; the Voyage embeddings call has never run.
  `email_classify_v2` (8 Oct, task 71) was replayed on every recruiter
  request and 102 job emails, never on non-job mail, whose bodies are
  purged: a stray `recruiter_outreach` in triage is how a false positive
  would show. Stage 4, `invite_detail_v1` (9 Oct, task 77), was replayed
  over the 60 stored invitation mails against the author's labels and
  backfilled; it has never read a mail arriving live through the worker.
- **Matching** (`.claude/rules/matching.md`): `stated_abbreviation` and
  `workday_tenant` (8 Oct, tasks 69-70) and the three-character id rule
  (task 68) were replayed over stored mail; none has filed a NEW email
  yet. The next confirmation signed "Long Name (ABBR)", or from a Workday
  tenant printing a record's id, should file itself, `match_score` 1.0
  for the id.
- **UI** (`.claude/rules/web-ui.md`): the 23 Sep palette's DARK theme was
  seen natively on 7 Oct (no Dark Reader) on the two lists and
  `/follow-ups`, and holds; its LIGHT theme is still verified numerically
  only. (tasks 13, 62)
  The approach panel ("Answered them?", "Close this approach") and
  `/follow-ups`' lead rows (28 Sep, task 38), and its round rows and the
  detail page's "Heard nothing since?" (29 Sep, task 40), were rendered
  through TestClient against real data only, never looked at.
  `/triage`'s redraw (7 Oct, task 64) was seen in the dark theme on real
  data: the inbound run, its confirmation page (opened, not sent) and the
  review strip. No twin was pending on the day, so the "Filed twice?" band
  and its merge have run only in the suite. Its runs keyed by sender NAME
  (8 Oct, task 72) and a lead's edit form with no applied date (task 73)
  were rendered through TestClient against real data, not looked at.
  So were the round rating's four surfaces (8 Oct, task 75): the "How did
  it go?" select on a round's timeline line, `/follow-ups`' first section
  of the same name, `/analytics`' "Your interviews, as you rated them"
  (a five-column grid on the visa matrix's CSS, phone width untested), and
  the lists' head — a row of figures since 9 Oct (`.figures`), each a
  link into its `?interviews=` filter, with its note. And the rounds' (task 76): the "round 2 of 4" line
  labels with their "not a round" buttons, the detail badge, the list's
  "N rounds" tag, and "How far you got" on `/analytics`. The status bar
  painted with its own rows and its two-group legend (task 79) were seen
  in both themes at desktop width, never at phone width.

## Open work (as of 9 Oct 2026)

The dated register behind each item, tasks 1-78 with their measurements, is
`docs/worklog.md`; read the matching entry before acting on one.

- **Interview rounds, their kinds, and what came of them** (tasks 75-78,
  8-9 Oct): a round you sat takes your rating (`payload.went`, the day
  after, on `/follow-ups`' first section), a kind, "not a round" per
  line; stage 4 (`invite_detail_v1`) reads every invitation mail's role,
  kind and day, backfilled over the 60 stored ones so all 18 threads read
  the counts the author corrected by hand. The lists' head is a row of
  figures. The author's clicks: rate each interview the morning after (5
  were scheduled 9-29 Oct); the 4 Aug and 11 Sep inbound rounds are
  unrated. Never met live: stage 4 on a mail arriving through the worker
  (the stage's one bias in the replay, "screen" where the author said
  technical or hiring manager, is a prompt note for a v2 once there is
  more evidence); the "How did it go?" rows; the figures row and the
  rounds' surfaces in a browser, either theme. Not built: `/analytics` by
  kind ("where it stops" — kinds now sit on 24 of 26 rounds), the
  calendar UID at ingest (future mail only, both providers, invariant
  #10), and a reschedule rule where a later invitation follows a
  cancellation on one thread.

- **Follow-up drafting**, now on a page that has a place for it (task 61,
  7 Oct): `/follow-ups` is "Your move", sectioned by the move each row
  offers, and the cap task 4 asked for is derived — `insights.quiet_after`,
  the day under 5% of the author's applications ever heard back (23 on
  7 Oct), so 150 of 192 unanswered rows became one count with one confirmed
  bulk close (the pill reads 19 against 197), and the 6 "worth a nudge"
  rows name the recruiter to message and the odds. Still to build on it:
  the draft itself beside each nudge
  (a mailto body for a thread, a copyable two-liner for a LinkedIn contact;
  template first, no model), and one card per agency with one click filing
  N follow-ups (33 employers held 85 of the rows). Five `follow_up_sent`
  are on record, one of them the chase that drew an answer, after an
  interview; no cold nudge ever has. (worklog tasks 4, 61)
- **Listings that asked for the CV by email** (task 57, 4 Oct): 8
  applications owed the email on the day, listed first on `/follow-ups`;
  each is the author's "I emailed it", "Not needed" or an email sent.
- **The 7 Oct audit's leftovers** (tasks 65-66). The author's: one
  application whose job board reported a failed delivery after "sent"
  (the mail was ignored in triage on 30 Sep; check the board), and
  whether to delete the option-as-question checkbox rows on 7 older
  applications, whose questions were never stored. Code, not built:
  Workday answers stored as the platform's internal ids (a 30-character
  hex value as the answer), and a cookie banner's checkboxes stored as
  answers on an Eightfold form. Sync and the worker had stopped at 07:28
  UTC that day, and again on 8 Oct (newest mail 02:45 UTC, the queue idle
  from 04:00): check the cron.
- **The legal-entity duplicates** (task 64, 7 Oct): the review strip's two
  rows on its first day were two applications filed twice on 2 Oct (a
  confirmation started a record beside the extension's Workday capture
  under a legal entity's name), repaired the same day. The class is now
  three cases in four days (`.claude/rules/matching.md`, the abbreviation
  bullet): re-measure its three candidate fixes. The "Filed twice?"
  band's merge has not met a live twin yet; read the record it opens on
  the first. A fourth shape, 8 Oct (task 69), mail signed "Long Name
  (ABBR)" against records named ABBR, is handled since by
  `matcher.stated_abbreviation`; its reverse is not. And Workday mail that
  prints its record's id now matches it from the record's own tenant,
  whatever the names (task 70, `matcher.workday_tenant`).
- **Recruiter approaches waiting in triage** (tasks 71-72, 8 Oct): every
  LinkedIn connection request since the search began was re-fetched and
  re-classified under `email_classify_v2`; 7 recruiters' requests (Aug to
  Oct) wait in the Approaches lane, one card each, for the author's "Track
  as lead" or "Ignore". A request names no role; one card's extraction
  missed its company, so only its own card's company box files it.
- **Extension, next real Easy Apply:** verify the current build (0.29.0 on
  8 Oct) is live on BOTH machines and the tab was opened after the
  reload; read `doc_source` and the sweep line, and check that the radio
  rows read Yes/No, not the question (task 42). From a search page, a
  `stale_pane` with `named` is 0.25.1's new check firing: the record then
  reads "unknown company" with the right id, and its confirmation needs
  re-filing onto it (task 48). The three records repaired that day still
  have no JD: re-capture from the popup on each job page. JobStreet still owes one
  clean submit with the race fix and salary capture together. (tasks 5, 18)
- **Employer career sites** (`docs/career-sites.md`, §16 the 28 Sep
  redesign; the per-apply history is worklog tasks 24-58 and
  `.claude/rules/extension.md`). Real applies have been captured through
  Oracle, SuccessFactors (classic and candidate experience, `sapsf.com`
  too), Greenhouse, SmartRecruiters, Phenom, Workday, Ashby and iCIMS;
  the first Darwinbox apply (8 Oct) was missed and filed by hand, its
  fixes in 0.28.0 (worklog task 67); an apply from a Career Site Builder
  site that was not enabled filed nameless and JD-less the same day (task
  68), and that site's listing names no employer even when enabled: only
  its footer does, and its tab title ends in the tenant code, which 0.28.2
  now refuses as a name;
  each fix's "what to read on the next apply" is in extension.md's
  Known-untested. Still NOT run live: the popup's injection on a page
  with no adapter, enabling an employer's site and then applying there
  (Eightfold's waits on the author's enable), and 0.15.0's receipt asking
  for a missing company. Not built: a Workday consent step's "Submit"
  (missed 1 Oct, task 46), an email-side rescue that names a nameless
  record from its confirmation (replay first), and task 37's two offers: a
  form page's weak reads yielding to an exact stash, and a triage band for
  a board record and an ATS capture of one job minutes apart, keeping the
  board's (such twins have recurred since). Not investigated: why a
  3 Oct LinkedIn → Workday handoff did not bind (twins merged 4 Oct).
- **Release blockers:** LICENSE (Apache-2.0 recommended), split the extension
  into its own repo, decide whether CLAUDE.md ships, and whether
  `docs/monetization-review.md` does (its §3 is the author's own work-pass
  situation, and the history keeps it once pushed); `audit_names.py --history`
  is the pre-publish check. (task 3)
- **vLLM lab** (`docs/vllm-lab.md`): blocked at its §2 on the billing-account
  project limit and the L4 quota, both the author's to clear by hand; no
  activity since 9 Sep. Its replay corpus changed under it on 23 Sep: 68
  stub bodies were rewritten after they were classified, so a candidate that
  disagrees on one may be right (`.claude/rules/llm.md`). (tasks 8, 10)
- **Email body retention** for job-related mail is undecided; 225
  `not_job_related` bodies (2.5 MB) could be cleared by one UPDATE, the author's
  call. (task 7)
- **Catch-up sweep** `backfill -d 21` under `INGEST_ALL`, deferred 7 Aug 2026
  (~200-400 classify calls, ~$2-3). (task 1)
- **Dedup and extraction verification have never run on real data** (0
  embeddings; 0 of 264 v2 extractions and 0 of 263 v1 verified on 25 Sep —
  the v2 eval's 66 hand labels live outside the DB, see below); exercise
  once or label experimental before release. (task 6)
- **Visa signal vs outcome** (task 9) is joined on `/analytics` since
  25 Sep ("What the job description said about visas"), re-extracted the
  same day under `jd_extract_v2` on Sonnet 5 (task 33). Answered 4 of 9
  where the JD says no sponsorship, 1 of 5 where it says it sponsors, and 38
  of 192 where it says nothing. The finding that matters is elsewhere: only
  4 of the 12 LinkedIn sponsorship screens had a JD saying so, so the form's
  question predicts that knockout and the JD mostly does not. Still open:
  39 of 57 rejected applications carry no reason (25 Sep), but 21 of those
  are LinkedIn's automatic screens (`how=sponsorship_screen` /
  `form_screen`, task 32). The timeline explains them and no person gave a
  reason. The ones worth tagging are `how=no_round&reason=unrecorded` (16).
  Every inbound rejection now has one: two recruiter replies that stated a
  visa reason were filled from the email, beside a third tagged by hand
  in August (task 35).
  All three kinds of evidence meet on `/analytics`' "Visa, at a glance"
  (task 34), each cell opening its rows on the list. Two JD label calls wait
  on the author, both left at the rubric's default
  (`docs/jd-extraction-models.md` §9). Its eval harness and gold labels sit
  outside the repo in `job-tracker-snapshots/jd-eval-2026-09-25/`: re-run
  them after any change to `jd_extract_*` or `JD_MODEL`.
- **Open inbound leads are the author's clicks too** (task 38; 9 awaited
  the author's call on 28 Sep, 2 on 4 Oct): each lead's page takes "I replied" (then it waits on them and, silent
  `REMINDER_DAYS` later, reaches `/follow-ups`) or a close ("Not for me",
  "They went quiet"). Not built: placing a
  same-day event from the general timeline form after the day's last event.
- **Round rows in `/follow-ups` are the author's clicks** (task 40; 2 on
  29 Sep, 4 on 4 Oct): a thread an interview or a person opened, silent
  `REMINDER_DAYS`; "Followed up" to chase (the clock restarts) or "They
  went quiet" to close. Since 7 Oct an offer in hand is one of these
  (task 60; 1 on the day, the search's first offer, silent since the
  author declined to relocate on 22 Sep): "Not for me" with the why, or
  "They went quiet". Decided 7 Oct (task 61): a never-answered application
  closes as "went quiet" too, but only past the odds and only through the
  confirmed bulk close on `/follow-ups` — never from its own page before
  then.
- **The 24 Sep data audit's leftovers are the author's clicks, not code**
  (tasks 19-21): 23 rows (7 Oct) in `/follow-ups`' "Applied to again" band,
  each with "Same role, close", or all at once with the quiet ones through
  the bulk close (the studio's four were left undecided on purpose, and
  stay out of the bulk only if the author leaves them out — it lists what
  it will close); 3 blind extension captures (29 Jul,
  18 Aug, 20 Aug) that re-capturing from the popup on each job page fixes;
  16 approaches in triage's inbound lane on 4 Oct (task 22's two held-back
  confirmations and the 24 Sep sent reply are resolved). Sync had not run since 01:45 that day — check the
  15-minute cron on whichever machine runs it. Decided and NOT open: one
  company under two names — where one name contains the other the matcher
  now handles it (task 23); names sharing no word (a parent brand, an
  agency's client) land in triage by choice — rejections filed twice
  (cosmetic), answers that differ between employers (the author's own). Offered, not built: a popup
  "Capture this job as applied" button (task 20).
- **Heat ceiling** (task 13): `trace.FULL_HEAT_DAYS` = 56 is a judgement.
  It now has a measurement beside it: `/analytics` shows the reply window.
  That is the longest any application waited to hear anything: 34 days on
  25 Sep (task 31), 55 on 4 Oct after one rejection reached a hand-dated
  application (task 59). Replay over the waiting rows before tuning, and
  note the window moves as replies arrive. The author's call, not changed.
  Offered 7 Oct and NOT taken (task 62): ending the amber at `/follow-ups`'
  odds cut (`insights.quiet_after`, 23 days that day) so rows past it go
  grey — 111 open rows sat at 100% heat. Don't re-propose it as new.
