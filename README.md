# Job Application Tracker

A self-hosted tracker for a job search. It records each application at the
moment you apply — the job, its description, the screening questions the form
asked and the answers you gave — then reads your mailbox to follow what
happened next: confirmations, rejections, interview invitations, recruiters
approaching you, and the mail you sent back. Every reply becomes an event on
the right application's timeline; status is derived from that timeline, never
typed in. On top of it sit the pages a search actually needs: who to chase
today, how long replies really take, which resume did better, and what every
employer asked you.

It is built and used daily by one person through a live search (since July
2026), with a Claude model doing the reading by default or any open-weight
model on an OpenAI-compatible server if you prefer your mail never leave the
machine. Every server path is test-driven, including cross-tenant isolation at
raw SQL.

> **Status:** working software, release in preparation. There is no `LICENSE`
> file yet — Apache-2.0 is the intended license (`docs/open-source.md` §7) —
> so until one lands the usual default applies: all rights reserved. See
> [Known limits](#known-limits) for what has only ever run in the test suite.

## How data gets in — and what it never does

Three paths, and only three:

1. **The browser extension**, when *you* click apply on a page *you* opened.
2. **Your mailbox**, read over IMAP (or the Gmail API) for mail about your
   applications.
3. **Manual entry** (`/applications/new`) for whatever neither reaches: an
   application that pre-dates the tracker, a posting that has expired, a
   capture the extension missed.

**The tracker never scrapes or automates a job platform.** No background
visits, no scheduled fetches, no stored platform credentials, no actions taken
on your account. The extension reads the one page in front of you, once, on
your own click. This is the project's founding constraint — terms of service
and the safety of an account you are depending on during a search — and it is
what separates this from anything that drives a logged-in session for you.

## What it does

- **Capture at apply time.** The extension stores the job, its description,
  the displayed salary, the resume you picked and every screening question
  with your answer. It knows a work-history *repeater* asks the same question
  once per employer, and keeps each occurrence.
- **Reads your mail and files it.** A two-stage LLM pipeline classifies each
  message (a confirmation, a rejection, an interview invitation, a recruiter
  approaching you, or not job-related), extracts company / role / date /
  recruiter, and the matcher files it as an event on the right application —
  by the job's own id on its hiring system when the mail prints one, else by
  company and title. Mail *you* sent is classified too (an application by
  email, a follow-up, a reply, a withdrawal). Anything uncertain lands in
  **Triage** with the candidates it weighed and why it waited; nothing is
  guessed silently.
- **Status is a timeline.** `applied` → `viewed` → `engaged` → `interview` →
  `offer` → closed, derived from an append-only event log with real-world
  dates, so a backfilled email from last month lands in the right place. An
  offer is a round, not an end: the thread stays open until you decline it,
  they withdraw it, or it goes quiet. Every rejection can carry a *reason*,
  quoted verbatim from the email when the email states one.
- **Follow-ups** ("Your move"): the applications worth a nudge, the leads
  awaiting your reply, the rounds gone quiet, the listings that asked for your
  CV by email and are still owed it — sectioned by the move each row offers.
  The "gone quiet" cut-off is not a constant: it is the day past which fewer
  than 5% of *your own* applications ever heard back, from the same
  Kaplan-Meier curve the analytics page draws.
- **Analytics**: the reply curve (a waiting application is "not yet", never
  "never"), reply windows, cohorts, which resume file drew more answers, how
  each rejection ended (including LinkedIn's automatic sponsorship and form
  screens, derived from the timeline), and a visa matrix joining what the job
  description said, what the form asked, and what happened.
- **Answers**: every screening question you have ever been asked, grouped
  across employers — the same questions recur almost verbatim. Identity
  numbers, date of birth, race, religion, marital status, gender, veteran and
  disability status are withheld *in the browser* and stored as `(withheld)`;
  nationality and work authorisation stay, because the visa analysis reads
  them.
- **Job-description extraction**: languages, technologies, salary and the
  visa signal, each held to a verbatim quote of the JD ("no quote, no
  signal"), with a human verification step.
- **Cover letters** grounded only in the resume profile you paste into
  Settings — the prompt forbids inventing beyond it.
- **Duplicates**: embeddings plus title similarity merge the same job posted
  on two boards; the uncertain band goes to Triage as one card per pair, and
  an application filed twice (an employer's form beside a job board's record,
  minutes apart) is offered a merge there too.
- **Accounts**: multiple users, per-user API tokens and mail credentials
  encrypted at rest, Postgres row-level security under every request.

## Requirements

- Python 3.12
- PostgreSQL 15+ with the `pgvector` and `pg_trgm` extensions available
- An Anthropic API key — or an OpenAI-compatible model server instead
  ([Open-weight models](#open-weight-models-vllm-or-any-openai-compatible-server))
- Chrome (or another Manifest V3 browser) for the extension
- Optional: a Voyage AI key for embeddings (without it, duplicate detection
  is simply off); Node.js to run the extension's tests; Docker for the
  native-Windows development setup

## Quick start

```bash
# 1. Database. The migrations are numbered and append-only; apply them all,
#    in order. 003 creates the RLS role and sessions table every request
#    depends on, so a partial set will not start.
createdb tracker
for f in migrations/*.sql; do psql tracker -q -v ON_ERROR_STOP=1 -f "$f"; done

# 2. Python. The project is run with uv (no pyproject — uv is venv + pip here),
#    but a plain venv works the same way.
uv venv --python 3.12 && uv pip install -r requirements.txt
#    or:  python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# 3. Configuration. Everything is read from a gitignored .env at the repo root
#    (real environment variables win). The example is annotated.
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # -> TRACKER_SECRET_KEY
#    Set TRACKER_SECRET_KEY ONCE and keep it forever: it encrypts the stored
#    mail credentials, and with IMAP that is a full-mailbox app password, not a
#    read-only token. Losing it orphans every connected account.

# 4. Start the server and create your account in the browser.
uv run python -m pipeline.cli serve            # http://127.0.0.1:8000
#    -> /signup. Your per-user API token is shown ONCE on the Settings page
#       that follows; copy it for step 6.

# 5. Connect your mailbox (details in the next section).
uv run python -m pipeline.cli auth             # IMAP + app password, the default
uv run python -m pipeline.cli backfill -m 3    # reconstruct history from the inbox
uv run python -m pipeline.cli work --once      # drain the queue (the LLM calls happen here)

# 6. Extension: chrome://extensions -> Developer mode -> Load unpacked -> the
#    extension/ folder. In its Options page set the API base
#    (http://127.0.0.1:8000) and paste the token from step 4.
```

Native Windows without WSL (Docker Postgres, uv): `scripts/dev-setup.ps1`
does steps 1–2 once; `docs/windows-dev.md` has the rest, including how to run
the app from two machines against one managed Postgres.

## Connecting your mail

**IMAP with an app password is the default.** No Google Cloud project, no
OAuth consent screen, no token that expires. It needs 2-Step Verification on
the Google account; generate the password at `myaccount.google.com/apppasswords`
and run `cli auth` (it prompts for the address and the password with hidden
input, and verifies the login before storing anything). Users can also
connect from **Settings → Gmail** in the browser. Not available for Workspace
or school accounts, Advanced Protection, or security-key-only 2SV — use OAuth
for those.

**OAuth is the alternative.** Create a *Desktop app* OAuth client in Google
Cloud Console (Gmail API enabled, scope `gmail.readonly`), save it as
`credentials.json` in the repo root, add your account under *OAuth consent
screen → Test users*, then `cli auth --oauth` (prints a URL; no browser
launch, so it works over SSH/WSL). For a multi-user server, a *Web
application* client saved as `credentials-web.json` with redirect URI
`<TRACKER_BASE_URL>/oauth/gmail/callback` lets each user connect from
Settings.

**Read this before relying on OAuth:** a Google Cloud project whose consent
screen is at publishing status "Testing" issues refresh tokens that expire
after **7 days** for any scope beyond name/email/profile — `gmail.readonly`
does not qualify, so sync dies weekly with `invalid_grant` (reported visibly,
not as a traceback). A Workspace **Internal** app is exempt from both
verification and the 7-day expiry, which makes it the better path for
Workspace accounts specifically. Full analysis: `docs/email-ingest.md`,
`docs/open-source.md` §2.

**Which mail gets read.** By default a sender/subject pre-filter decides which
messages are candidates; only those are stored and classified, and a body
never reaches a model unless a rule hit. `TRACKER_INGEST_ALL=true` takes every
message instead — correct only for a mailbox that is *job-related only* — and
is paired with a retention rule: a message classified not-job-related has its
body purged.

## Day to day

```bash
uv run python -m pipeline.cli serve [--host H] [--port P]   # the web UI
uv run python -m pipeline.cli work [--once]                  # the queue worker
uv run python -m pipeline.cli sync                           # poll every connected mailbox
uv run python -m pipeline.cli status                         # queue health: mail waiting, dead jobs, last failure
uv run python -m pipeline.cli backfill -m 12 [--email x]     # reconstruct history (or -d 21 for days)
uv run python -m pipeline.cli scan                           # enqueue JD extraction / embeddings for the backlog
uv run python -m pipeline.cli passwd <email>                 # set a password, mint a token (bootstrap or recovery)
uv run python -m pipeline.cli renorm-answers [--apply]       # re-key stored answers after a change to norm_question
uv run python -m pipeline.cli redact-answers [--apply]       # re-apply the sensitive-answer rule (no undo)

# steady state: one cron entry and one long-running worker
*/15 * * * *  cd ~/job-tracker && uv run python -m pipeline.cli sync
#             uv run python -m pipeline.cli work
```

`sync` exits non-zero if any account failed, so a cron failure shows up in
your job runner's own alerting. The worker treats an environment failure —
credit balance, key, network, a 5xx, a local model server that is down — as
an **outage**: it pauses rather than retrying the queue into dead letters,
nothing is charged to the job, and the UI's header band says so.
`work --once` exits 1 on one.

## The web UI

Server-rendered FastAPI + Jinja, no JavaScript. Light and dark themes;
colour means exactly one thing (the state of the wait) and everything else is
grey.

| Page | What it is for |
|---|---|
| `/` | Your applications, with a shared-axis timeline per row, filters, sort, search |
| `/inbound` | Threads a recruiter or employer started (`origin = inbound`), kept on their own page |
| `/triage` | Mail the matcher would not file alone, with the records it weighed; identical mail from one sender as one card; applications filed twice; the week's less certain filings for a "Looks right" |
| `/follow-ups` | Your move: CV owed by email, worth a nudge, applied to again, gone quiet after a round, leads awaiting your reply; a confirmed bulk close for the ones past the odds |
| `/analytics` | The reply curve and window, cohorts, employers, which resume, how rejections ended, the visa matrix |
| `/answers` | Every screening question across every application, grouped by question |
| `/applications/{id}` | One application: timeline, events filed by hand, contacts, answers, JD extraction, cover letter |
| `/applications/new` | Manual entry — paste a job URL and the id is derived the way the extension derives it |
| `/settings` | Mail connection, API token, resume profile, password, timezone, theme, queue |

## The extension

`extension/` is a Manifest V3 extension (currently 0.29.0) that loads
unpacked. It captures when you submit an application:

- on the three job boards it has adapters for — LinkedIn (Easy Apply and the
  hand-off to an employer's site), JobStreet / SEEK, and Indeed;
- on the application forms of the hiring systems employers use, by content
  script: Greenhouse, Lever, Ashby, Workable, Workday, SuccessFactors
  (classic and the newer candidate experience), SmartRecruiters, iCIMS,
  JazzHR, Breezy, Darwinbox and Taleo. Oracle Recruiting Cloud, Phenom and
  Eightfold, which run under the employer's own domain, work once you enable
  that site;
- on **any other job page** from the toolbar popup: it reads the page's
  schema.org `JobPosting` and the application form by structure, and "Always
  capture on this site" registers the employer's career domain so later
  applies there are automatic.

When a job board hands you off to an employer's form, the form's submit is
filed onto the board's record (one application, not two) via the tab that
opened it. A capture that cannot read the page says so — a console warning, a
popover, and ring buffers of recent failures, provenance and form sweeps in
the popup — rather than saving a guess.

What it reads, what it sends and where, permission by permission:
`extension/PRIVACY.md`. Installing on a second device, and the one token per
account gotcha: `docs/extension-install.md`. The extension is deliberately
**not** on the Chrome Web Store; the reasoning is `docs/open-source.md` §3.

## Models and prompts

Every model call goes through `pipeline/llm.py`. Prompts are versioned files
in `prompts/`; a change is a new file and a bumped constant, and every stored
row records the prompt version and the model that produced it, so a swap is
attributable and selectively re-runnable.

| Stage | Default model | Prompt | Override |
|---|---|---|---|
| Classify an email | `claude-sonnet-5-5` | `email_classify_v2` / `email_classify_sent_v1` | `TRACKER_CLASSIFY_MODEL` |
| Extract company / role / date / recruiter | `claude-haiku-4-5-20251001` | `email_extract_v1` | `TRACKER_EXTRACT_MODEL` |
| Rejection reason, quoted from the mail | `claude-sonnet-5` | `rejection_reason_v1` | `TRACKER_REASON_MODEL` |
| JD extraction | `claude-sonnet-5`, effort `medium` | `jd_extract_v2` | `TRACKER_JD_MODEL`, `TRACKER_JD_EFFORT` |
| Cover letter | `claude-sonnet-5` | `cover_letter_v1` | `TRACKER_COVER_MODEL` |
| Embeddings (optional) | `voyage-3.5-lite` | — | `TRACKER_EMBED_MODEL` |

Each default was measured, not assumed — the JD stage on a hand-labelled gold
set (`docs/jd-extraction-models.md`), the others by replaying stored mail
(`scripts/replay_*.py`, `docs/llm-alternatives.md`). At one person's volume
the whole pipeline costs about US$5 a month.

### Open-weight models (vLLM, or any OpenAI-compatible server)

Routing is by model name: a `claude-*` name goes to the Anthropic API,
anything else to the server named by `TRACKER_LLM_BASE_URL`. So

```bash
vllm serve Qwen/Qwen3-8B --port 8001 \
     --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}'

export TRACKER_LLM_BASE_URL=http://127.0.0.1:8001/v1
export TRACKER_LLM_MODEL=Qwen/Qwen3-8B
```

runs every stage locally and your mail never leaves the machine;
`ANTHROPIC_API_KEY` can stay unset. Add `TRACKER_COVER_MODEL=claude-sonnet-5`
on top and only the cover letters go to Anthropic — the per-stage variables
override the shared default, so all-Claude, all-local and mixed are the same
configuration with no mode switch.

What the open-weight path does differently: the JSON stages ask the server
for a `json_object` at temperature 0 (schema validation and the repair retry
are unchanged — valid is not yet correct), and a leading `<think>…</think>`
block is stripped if a reasoning model emits one. Turn thinking off
server-side as above: `max_tokens` caps reasoning and answer together, and the
caps were sized for answers. Port 8001 because vLLM's default, 8000, is this
app's own UI. `TRACKER_LLM_API_KEY` if the server was started with
`--api-key`; `TRACKER_LLM_TIMEOUT_SECONDS` (default 600) for slow hardware;
`TRACKER_LLM_EXTRA_BODY` (a JSON object) is merged into every request. Any
server speaking `/v1/chat/completions` works — llama.cpp, Ollama, LM Studio;
vLLM is the one it was written against, and `docs/vllm-lab.md` is a staged
walkthrough of running one on a single GPU in Google Cloud, with
`scripts/replay_classify.py` to diff any model against the classifications
already on record before trusting it with new mail. Honest caveat: the
OpenAI-compatible backend is held to a fake server in `tests/test_llm.py` and
has not yet met a real vLLM.

## Configuration

All read from the environment, with `.env` at the repo root auto-loaded
(`.env.example` is annotated). `config.py` holds the thresholds — matching
scores, dedup bands, backoff — and is the place for anything that is not a
secret or a per-machine path.

| Variable | Purpose |
|---|---|
| `TRACKER_DATABASE_URL` | Postgres DSN (default `postgresql:///tracker`). For a managed Postgres use the **direct**, unpooled string: a transaction-mode pooler drops the `SET ROLE` RLS relies on and queries silently return nothing |
| `TRACKER_SECRET_KEY` | Signs sessions and encrypts stored mail credentials. Set once, keep forever |
| `ANTHROPIC_API_KEY` | For every `claude-*` stage; optional when every stage runs locally |
| `TRACKER_LLM_BASE_URL`, `TRACKER_LLM_MODEL`, `TRACKER_LLM_API_KEY`, `TRACKER_LLM_TIMEOUT_SECONDS`, `TRACKER_LLM_EXTRA_BODY` | An OpenAI-compatible model server, as above |
| `TRACKER_CLASSIFY_MODEL`, `TRACKER_EXTRACT_MODEL`, `TRACKER_REASON_MODEL`, `TRACKER_JD_MODEL`, `TRACKER_JD_EFFORT`, `TRACKER_COVER_MODEL`, `TRACKER_EMBED_MODEL` | Per-stage overrides |
| `VOYAGE_API_KEY` | Embeddings; absent means duplicate detection is off |
| `TRACKER_INGEST_ALL` | Take every message, skipping the candidate pre-filter (job-only mailboxes). Set it per machine, deliberately — it is the one flag that fails quietly when missing |
| `TRACKER_REMINDER_DAYS` | Days of silence after a round or a reply before a thread reaches `/follow-ups` (default 10) |
| `TRACKER_BASE_URL` | Public URL; needed for the Gmail web OAuth redirect, and an `https` value makes the session cookie `Secure` |
| `TRACKER_ALLOW_SIGNUP` | `0` closes `/signup` once your users are in (default open) |
| `TRACKER_API_TOKEN` | Legacy single-user extension token; stops working the moment a second account exists. Per-user tokens from Settings are the real thing |
| `TRACKER_GMAIL_CREDENTIALS`, `TRACKER_GMAIL_TOKEN`, `TRACKER_GMAIL_WEB_CREDENTIALS` | Paths for the OAuth alternative (defaults `credentials.json`, `.gmail_token.json`, `credentials-web.json`) |

Secrets files are gitignored: `.env`, `credentials.json`,
`credentials-web.json`, `.gmail_token.json`.

## Tests

```bash
./scripts/test.sh                                              # Linux / WSL
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1   # native Windows (Docker Postgres)
```

Either creates a throwaway `tracker_test` database, applies every migration,
runs the extension's Node tests and then the eight Python suites in the
required order — `test_integration` seeds what `test_web` reads,
`test_email_ingest` runs before `test_phase4`, and `test_phase4` is last
because it creates a second user, which retires the legacy token
`test_captures` relies on. Every suite stubs every model and embedding call
and the IMAP suite fakes the socket, so a full run costs nothing and needs no
network. Each suite logs every assertion to `/tmp/<suite>.log`
(`$env:TEMP` on Windows), pass or fail.

| Suite | Covers |
|---|---|
| `tests/test_extension.js` | Adapter frame geometries, the stale-pane guards, form roots and submit detection, label reading against fake DOMs for each hiring system's measured markup, URL → id rules shared with Python (`tests/job_urls.json`), the manifest against the vendor table |
| `test_llm` | Model routing, the OpenAI-compatible transport against a fake server, the outage classifier |
| `test_insights` | The analytics statistics and chart geometry, pure: Kaplan-Meier against a hand computation, Wilson bounds, the comparison registry looped |
| `test_integration` | Queue → worker → classifier → matcher → events, end to end: auto-match, create, triage, recruiter outreach never auto-filed, stated dates, backoff |
| `test_web` | Every page renders against real rows; triage resolve actions; the sort registry looped against every filter; follow-ups' sections |
| `test_captures` | `POST /captures`: auth, the single upsert path, enrichment, idempotency, answers and their occurrences, sensitive withholding |
| `test_phase3` | JD extraction chain and verification, dedup bands and merging, cover letters, analytics counts, reminders |
| `test_email_ingest` | The IMAP provider against a fake server: read-only select, `BODY.PEEK`, query escaping, ascending UID order, idempotent re-runs |
| `test_phase4` | Accounts, sessions, tokens; cross-tenant isolation through the web app *and* at raw SQL under the RLS role with no `WHERE` |

Five JSON fixtures in `tests/` hold rules that exist twice — once in Python,
once in the extension's JavaScript — so one case file keeps both
implementations honest: question normalisation, sensitive questions,
sponsorship answers, CV-by-email instructions, and job URL → id.

## Architecture in brief

The rules that every change must keep, with the reason for each. The full
statement is in `CLAUDE.md`; the design rationale as of July 2026 is
`docs/design.md`.

- **Status is an append-only event log.** `events` rows carry real-world
  `occurred_at`; the `application_status` view derives the current state by
  precedence, then recency. No mutable status column exists, so an email
  backfilled out of order lands correctly and a view fix corrects every
  record retroactively.
- **Postings ≠ jobs ≠ applications.** A job may be posted on several boards;
  you apply to a job once. `ingest.upsert_record` is the *only* place records
  are created from a capture (the extension and manual entry both call it),
  and `dedup.merge_jobs` is the only place two are combined. A wrong merge is
  silent and permanent and a wrong split is visible and reversible, so the
  thresholds prefer the recoverable failure.
- **`norm_company()` is the single source of truth** for company identity,
  including Southeast Asian corporate forms (Indonesian `PT`/`CV` prefixes
  among them). Never reimplemented in SQL.
- **A form label is not a question identifier.** Repeaters make N fields
  share one label; `(application, question_norm, occurrence)` identifies an
  answer, and the extension keys its store the same way.
- **Prompts and models are versioned per row.** New prompt = new file; model
  swaps are recorded, reasoned in a comment, and never mass re-run by
  default.
- **Provenance is separate from status.** `applications.origin` says who
  started the thread (you, or a recruiter), decides which page the record
  lives on, and is changed only by a human stating so.
- **Tenancy is Postgres row-level security.** Routes resolve the session on
  an admin connection, then run data queries as a `NOLOGIN` role with the
  user id in a GUC; every data table has a policy and every view is
  `security_invoker`. The worker and mail sync are trusted batch jobs.
- **One mail orchestrator.** `pipeline/mailbox.py` owns candidate filtering,
  storage, enqueueing and cursors for every provider; IMAP and the Gmail API
  only connect, search and return a normalised message.
- **The worker holds one transaction per job**: `FOR UPDATE SKIP LOCKED`
  claim, a savepoint around the handler, exponential backoff, dead-letter. A
  crash leaves the job pending; no cleanup logic is needed.
- **Migrations are append-only numbered files.** Never edit an applied one.

## Repository layout

```
pipeline/
  web.py              every route (FastAPI + Jinja, no JS)
  ingest.py           the one place job / posting / application rows are created
  matcher.py          files an email as an event: by the job's ATS id, then company + title
  mailbox.py          mail-ingest orchestrator; gmail_imap.py / gmail_sync.py are its providers
  email_classifier.py the two email stages + the rejection-reason stage, norm_company()
  jd_extraction.py    JD extraction with verbatim quotes (quotes.py), visa groups
  answers.py          screening-answer normalisation, sensitivity, resume promotion
  email_apply.py      does a JD ask for the CV by email? one rule in Python and SQL
  salary.py, joburl.py, dedup.py, embeddings.py, covers.py
  analytics.py        the counts the pages show; the follow-up queue; the one analytics fetch
  insights.py         every number on /analytics, pure (Kaplan-Meier, Wilson, cohorts, visa matrix)
  charts.py, trace.py the page geometry: flows, curves, timelines on one shared axis
  triage.py           what /triage shows: suggestions, runs, twins, the review strip
  llm.py              the one door to every model call; two backends; the outage classifier
  worker.py           the job queue
  auth.py, db.py, config.py, cli.py
  templates/          every page; all CSS is one block in base.html
extension/            MV3: manifest, adapters/{linkedin,jobstreet,indeed,generic}.js,
                      shared/{capture,answers,jobposting}.js, background.js, popup, options
migrations/           001 … 020, append-only
prompts/              versioned prompt files
tests/                eight Python suites, test_extension.js, five shared JSON fixtures
scripts/              test.sh / test.ps1, dev-setup.ps1, replay_*.py (model evaluation on
                      stored rows), audit_names.py (pre-publish scrub check), make_icons.py,
                      gcp/vllm-vm.* (the GPU VM for the local-model lab)
docs/                 the research and design records below
```

## Documentation

The docs are dated research and design records; where one disagrees with the
code, the code and `migrations/` win.

- `docs/design.md` — the design rationale (July 2026); read its header first,
  the reasoning holds while its inventories have aged
- `docs/open-source.md` — the release direction: the extension's
  account-safety risk, the OAuth token trap, license, pre-release checklist
- `docs/features.md` — feature priorities and the market research behind them
- `docs/email-ingest.md` — how mail gets in, and why IMAP became the default
- `docs/extension-install.md` — the extension on a second device
- `docs/career-sites.md` — capturing on employer sites and their hiring
  systems: an 18-vendor survey and the design that came of it
- `docs/jd-extraction-models.md` — which model and effort run the JD
  extractor, measured on a gold set
- `docs/llm-alternatives.md` — other LLM APIs against each stage: cost,
  compatibility, where the data goes
- `docs/vllm-lab.md` — open-weight models on a GPU VM, stage by stage
- `docs/windows-dev.md` — native Windows: Docker Postgres, uv, a managed dev DB
- `docs/worklog.md` — the dated task register with its measurements
- `CLAUDE.md` and `.claude/rules/` — the working notes for an AI pair: the
  invariants, and every gotcha learned the hard way, by area

## Deploying beyond localhost

Run behind TLS (Caddy, nginx, a PaaS); the session cookie becomes `Secure`
when `TRACKER_BASE_URL` is `https`. Set `TRACKER_ALLOW_SIGNUP=0` once your
users are in. Extension users enter the server's URL in Options and the
extension requests that origin's permission at save time.

**Not built, deliberately:** billing, email verification, password reset by
email, rate limiting, admin tooling. This is a personal tool heading for open
source, not a service (`docs/monetization-review.md` is the examination that
settled it).

## Known limits

Honest accounting of what has only run in the suite, as of October 2026.
`CLAUDE.md` → "Known-untested surfaces" keeps the live list.

- **The extension's adapters are best-effort against sites that redesign
  without notice.** Each hiring-system rule was measured on a live form, and
  real applications have been captured through Oracle, SuccessFactors,
  Greenhouse, SmartRecruiters, Phenom, Workday, Ashby, iCIMS, Darwinbox and
  Taleo — but Indeed has never been exercised, and several recent rules await
  their next real submit. Failures are loud, not silent; expect to read the
  popup's failure line on a new site.
- **The OpenAI-compatible backend** has never met a real vLLM, and the
  Voyage embeddings call has never run on real data (duplicate detection is
  tested, not yet exercised live).
- **Gmail web OAuth** and the IMAP connect form in Settings were built
  test-first and have not been driven by a real user; the CLI IMAP flow has.
- **Tuned on one search.** Matching thresholds, the follow-up odds and the
  analytics were measured against one person's applications in one market.
  They are configuration, not law — `config.py` says what each is and
  `scripts/replay_thresholds.py` replays the matcher's thresholds against
  every stored, scored email to show what a new value would have decided.

## Privacy and where data goes

Your mail bodies, job descriptions and answers live in *your* Postgres.
Candidate messages are sent to the model you configured — Anthropic by
default, your own server if you choose one — for classification and
extraction; nothing else sees them. Sensitive screening answers are withheld
in the browser and never reach the server. Non-candidate mail is never
stored; with `TRACKER_INGEST_ALL`, not-job-related bodies are purged after
classification. The extension talks only to the server you configured, with
your token (`extension/PRIVACY.md`).

If you publish a fork or a dataset, `scripts/audit_names.py` derives every
employer, contact and recruiter name from the database and greps the tracked
files for them (`--history` checks every commit); the tracked files here use
placeholder names throughout.

## Contributing

Issues and pull requests are welcome once the repository is public. Until
then: run the full suite after every change (`scripts/test.sh` /
`test.ps1`); keep the invariants above; a new migration is a new numbered
file *and* an entry in `scripts/test.sh`, `scripts/test.ps1` and
`scripts/dev-setup.ps1`; a prompt change is a new version file; and never
write a real employer, agency or person into a tracked file — use a
placeholder and the date.

## License

Not yet chosen in a `LICENSE` file. Apache-2.0 is the intended license
(`docs/open-source.md` §7 has the reasoning against MIT and AGPL). Until it
lands, this code is published for reading only.
