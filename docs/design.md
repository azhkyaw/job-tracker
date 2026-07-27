# Job Application Tracker — Design Document

**Author:** AZ
**Status:** Draft v1
**Date:** 21 July 2026
**Scope:** Single-user build first; multi-user productization as a later phase

---

## 1. Summary

A personal system that tracks job applications across LinkedIn, JobStreet, and Indeed without scraping any of them. Data enters through two capture paths — a browser extension that grabs the job description at the moment of application, and a Gmail ingestion service that parses confirmation and status emails — and flows through an LLM extraction pipeline into a single Postgres database. A dashboard provides status tracking, extraction verification, duplicate resolution, follow-up reminders, and funnel analytics.

The deliberate architectural bet: **email is the source of truth for status; the extension is the source of truth for job content.** Neither path automates against the job platforms, so there is no ToS exposure and no risk to the live LinkedIn account, and the same acquisition model scales legally to other users later.

## 2. Background and problem

Applications are currently spread across three platforms with no unified view. The platforms themselves are poor systems of record: their in-app statuses rarely update (rejections and interview invites arrive by email, often from the employer's ATS rather than the platform), postings are taken down within weeks (destroying the JD text needed for later analysis), and the same role is frequently posted on multiple platforms — sometimes by agencies under a different company name — making it hard to know what has already been applied to.

The obvious approach — scheduled scraping or unofficial APIs — is the wrong one:

- None of the three platforms offers an applicant-side API.
- Scheduled automation of an authenticated LinkedIn session is precisely the pattern LinkedIn's bot detection targets. The realistic failure mode is account restriction during an active job search.
- Indeed sits behind aggressive Cloudflare protection; JobStreet (SEEK) offers no public applicant API.
- A multi-user version of the scraping approach would require holding other users' platform credentials — a security and legal liability that forecloses the productization path.

## 3. Goals

- Unified record of every application across the three platforms, including retroactive backfill of the existing application history from Gmail.
- Per-application: extracted primary languages/technologies (with a human verification step), focused-vs-generic flag, submission date, status timeline, recruiter contact and outreach state, linked cover letter, and duplicate resolution across platforms.
- Visa signal extraction (EP/COMPASS indicators for Singapore, work-permit indicators for Thailand) as a first-class field, usable as a filter before investing prep time.
- Funnel analytics: response rate by platform, by focused/generic, by tech stack; time-to-first-response.
- Follow-up reminders driven by the status timeline.
- Zero automation against the job platforms. No stored platform credentials, ever.
- Schema and auth designed so multi-tenancy is a migration, not a rewrite.

### Non-goals (v1)

- Auto-submission of applications or any write action against the platforms.
- Job discovery/recommendation (the system tracks jobs the user has already engaged with).
- Mobile app (mobile applications are covered by the email path; see §6.2).
- Multi-user hosting, billing, or extension store distribution (Phase 4 concerns, designed-for but not built).

## 4. Requirements

**Functional**

- FR1: Capture full JD text, company, title, URL, platform job ID, and user tags at apply time via browser extension on LinkedIn, JobStreet, and Indeed.
- FR2: Ingest job-search-related emails from Gmail (three platforms plus common ATS senders), classify them, extract structured fields, and attach them to application records.
- FR3: Backfill historical applications from existing Gmail history on first run.
- FR4: LLM-extract languages, technologies, seniority, salary (if listed), work mode, and visa signals from each JD; store alongside raw text with a `verified` flag.
- FR5: Detect cross-platform duplicate postings and link them to a single canonical job; surface borderline cases for manual confirmation.
- FR6: Generate targeted cover letter drafts from the JD plus the user's resume profile; store as artifacts linked to the application.
- FR7: Dashboard with kanban-by-status, table view with verification workflow, triage queue (unmatched emails, borderline duplicates), reminders, and funnel analytics.

**Non-functional**

- NFR1: Single-user load — tens of applications per week, hundreds of emails per day scanned. No horizontal scaling needed in v1.
- NFR2: Email-to-record latency under 15 minutes (polling interval).
- NFR3: LLM cost ceiling of a few dollars/month at single-user volume: small model for classification, larger model only for cover letters.
- NFR4: All components runnable on one small VPS/Fly.io instance; no managed queue or cache dependencies.
- NFR5: Extension capture failures must be loudly observable (see §13) — silent adapter breakage is the primary operational risk.

**Constraints**

- One developer, evenings/weekends, actively job-hunting (the system must deliver value in week one — hence the phasing in §12).
- Existing environment: Ubuntu under WSL, Claude Code workflow, Python-comfortable.

## 5. High-level architecture

```
┌────────────────────┐        ┌────────────────────┐
│  Browser extension │        │   Gmail watcher    │
│  (capture on apply)│        │  (poll / backfill) │
└─────────┬──────────┘        └─────────┬──────────┘
          │  POST /captures             │  raw emails
          ▼                             ▼
┌─────────────────────────────────────────────────┐
│                  Ingest API                     │
│         (auth, validation, enqueue)             │
└─────────┬───────────────────────────┬───────────┘
          ▼                           ▼
┌────────────────────┐        ┌────────────────────┐
│    LLM workers     │        │   Dedup service    │
│ classify / extract │        │ blocking + cosine  │
│ embed / cover ltrs │        │    similarity      │
└─────────┬──────────┘        └─────────┬──────────┘
          ▼                             ▼
┌─────────────────────────────────────────────────┐
│           Postgres + pgvector                   │
│   postings · jobs · applications · events ·     │
│   contacts · artifacts · emails · job queue     │
└─────────────────────┬───────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────┐
│   Dashboard (kanban, verify, triage, funnel)    │
└─────────────────────────────────────────────────┘
```

Everything is one deployable unit: an API process, a worker process, and Postgres. The queue is a Postgres table. There is deliberately no Redis, no SQS, no Pub/Sub in v1.

## 6. Component design

### 6.1 Browser extension (Manifest V3)

**Structure:** one thin content-script adapter per platform (`linkedin.ts`, `jobstreet.ts`, `indeed.ts`) plus a shared core (capture payload schema, API client, quick-tag popover UI). Adapters own only two things: detecting the apply action and extracting fields from the DOM. Everything else lives in shared code, so a platform redesign breaks one small file.

**Capture trigger:** listener on the platform's apply action — LinkedIn Easy Apply modal submission, JobStreet/Indeed apply buttons. External-redirect applies ("Apply on company website") also trigger capture, tagged `external_redirect`, since the confirmation email will arrive from the employer's ATS rather than the platform.

**Payload:** full JD text, company, title, canonical URL, platform job ID, platform name, captured-at timestamp, plus the quick-tag popover inputs: focused/generic (single tap, skippable), recruiter name/URL if visible on the page, optional free-text note.

**Save-first, tag-second (revised Jul 2026).** The original design held the capture in the popover until the user answered "focused or generic?", then sent everything at once. That made a tag prompt the gatekeeper for a real application: ignoring the popover for 45 seconds, or closing the Easy Apply modal (which destroys the iframe the popover was rendered in), silently discarded the record with nothing written anywhere. Inverted: an unambiguous apply — Easy Apply's final submit, or an explicit popup capture — POSTs immediately with `focused = null`, and the popover becomes a *receipt* whose tag and note go to `POST /captures/{id}/tag` afterwards. Losing the receipt now costs an optional tag, not the application. The one case that still asks first is the external-redirect apply, where the click genuinely isn't evidence of an application. The receipt is relayed to the tab's top frame via the service worker (`chrome.tabs.sendMessage`, needs host permission for the site) so it outlives the modal, falling back to in-frame rendering when that isn't available.

**Screening Q&A (added Jul 2026):** where the apply form is in-page (LinkedIn Easy Apply today), the extension also records the questions it asked and the answers given, sent as `answers[]` on the same capture payload and stored in `application_answers`. Two properties of these wizards shape the implementation, which lives entirely in `extension/shared/answers.js`: each step *replaces* the previous step's fields, so answers are accumulated on every click in the capture phase rather than read once at submit; and most answers are *prefilled* by the platform from the last application, so a DOM sweep — not a change listener — has to be the primary source. Adapters opt in with one method (`answerFormRoot()`); everything else is shared. It reads what the user is about to send anyway, at the moment they send it — the §6.1 "explicitly out" line below is unaffected.

**Manual capture:** a toolbar action captures the current job page without an apply event, for "interested but not yet applied" records (status `interested`).

**Explicitly out:** background scraping, session automation, scheduled page visits, any DOM interaction the user didn't initiate. The extension only ever reads the page the user is already on, at the moment they act.

### 6.2 Gmail ingestion

**Auth:** OAuth 2.0 with `gmail.readonly` scope only. Token stored server-side, encrypted at rest.

**Sync:** poll the Gmail History API every 15 minutes from a stored `historyId` cursor. Push notifications via Pub/Sub are deferred until multi-user (NFR2 makes polling sufficient).

**Candidate filter (cheap, pre-LLM):** sender-domain allowlist — linkedin.com, jobstreet.com / seek.com.au / seek.com, indeed.com, plus common ATS senders: greenhouse.io, lever.co, myworkday.com, workable.com, smartrecruiters.com, ashbyhq.com, icims.com — plus a fallback subject-keyword net ("application", "interview", "your candidacy", etc.) to catch direct employer mail. The allowlist is config, expected to grow.

**Two-stage LLM pass (see §9):** stage one classifies (job-related? type: confirmation / rejection / interview_invite / recruiter_outreach / status_update / other), stage two extracts structured fields (company, role title, date, platform if identifiable, ATS if identifiable). Non-job email is dropped after stage one and never sent to stage two.

**Matching:** extracted fields are matched to an existing application (see §10). Confident matches append an event to the record. Low-confidence results go to the dashboard triage queue for a one-click manual link — never a silent guess.

**Backfill:** first run walks Gmail history back a configurable window (default 12 months) through the same pipeline. This reconstructs the existing application history — including applications that predate the system — and is the single highest-value feature at launch. Applications discovered only via email are created in a `needs_enrichment` state (they have status history but no JD text). This same path covers applications submitted from a phone, where the extension isn't present.

### 6.3 Ingest API

Deliberately thin: authenticate, validate, write, enqueue. All intelligence lives in workers.

| Endpoint | Purpose |
|---|---|
| `POST /captures` | Extension payload → create posting + application, enqueue extraction |
| `POST /emails/sync` | Cron trigger for Gmail poll (also runnable manually) |
| `GET /applications` | List/filter for dashboard |
| `PATCH /applications/{id}` | Status overrides, focused flag, verification updates |
| `POST /applications/{id}/events` | Manual event entry (e.g. "followed up with recruiter") |
| `POST /applications/{id}/artifacts` | Attach/generate cover letter, prep notes |
| `POST /matches/{id}/resolve` | Triage queue resolution (link email / confirm duplicate) |
| `GET /analytics/funnel` | Aggregates for the funnel view |

Auth in v1 is a single bearer token (extension and cron both use it). The handler signature takes `user_id` from day one; v1 hardcodes one user row.

### 6.4 Worker pipeline

A worker process polls the `job_queue` table using `SELECT … FOR UPDATE SKIP LOCKED` (or pg-boss, which wraps the same mechanism). Job types:

- `classify_email` → stage-one LLM call
- `extract_email` → stage-two LLM call + matching
- `extract_jd` → structured extraction from captured JD (languages, technologies, seniority, salary, work mode, visa signals) → writes `extractions` JSON with `verified = false`
- `embed_jd` → embedding into pgvector (runs after `extract_jd`)
- `dedup_scan` → candidate generation + similarity check for a new posting (§11)
- `generate_cover_letter` → on-demand from dashboard; larger model; output stored as artifact
- `reminders_scan` → nightly; emits reminder events per §6.6

Failures retry with exponential backoff (max 5); poisoned jobs land in a dead-letter state visible in the dashboard.

### 6.5 Dedup service

Logically separate, physically the same worker process. See §11 for the algorithm. Confirmed duplicates re-point `posting.job_id` to the canonical job; the applications and event history of both postings then naturally roll up under one job.

### 6.6 Dashboard

FastAPI + HTMX + a lightweight component library (or Next.js if a separate frontend feels worth it — decision deferred, HTMX is the default for one maintainer). Views:

- **Kanban** by derived status (latest event per application): interested → applied → viewed → interviewing → offer / rejected / stale.
- **Table** with inline verification: extraction fields shown with a confirm/edit control that flips `verified`.
- **Triage queue:** unmatched emails and borderline duplicate pairs, each resolvable in one click.
- **Job detail:** JD text, extraction panel, status timeline, contacts, artifacts, duplicate links.
- **Funnel:** response rate and time-to-first-response, split by platform, focused/generic, and top extracted technologies. This is where the focused-vs-generic tag earns its keep — it answers whether targeted prep measurably improves response rate.
- **Reminders:** applications with an `applied` event older than N days (default 10), no response event, and no follow-up event.

## 7. Data model

Every table carries `user_id` (FK to `users`) from day one. Abridged DDL:

```sql
users(id, email, created_at)

-- A specific ad on a specific platform
postings(
  id, user_id, job_id NULL,          -- FK to canonical job, set by dedup
  platform,                          -- linkedin | jobstreet | indeed | other
  platform_job_id, url,
  company_raw, company_norm, title,
  jd_text, jd_embedding vector(1024),
  location NULL,                     -- listing's stated location, as shown
  posted_label NULL,                 -- platform's own relative-time text,
                                      -- e.g. "3 weeks ago" (not parsed to a
                                      -- date: granularity is too fuzzy)
  reposted NULL,                     -- boolean; NULL = platform didn't say
  ats NULL,                          -- greenhouse | lever | workday | ...;
                                      -- learned from ATS confirmation emails
                                      -- (email_classifier.py Extraction.ats)
  captured_via,                      -- extension | email_only | manual
  captured_at
)

-- Canonical role after dedup; 1..n postings per job
jobs(id, user_id, company_norm, title_canonical, created_at)

-- The user's action against a canonical job
applications(
  id, user_id, job_id,
  applied_via_posting_id NULL,
  focused BOOLEAN NULL,              -- null = infer from artifacts
  created_at
)

-- Append-only status timeline; current status is derived, never stored
events(
  id, user_id, application_id,
  type,        -- applied | confirmation | viewed | rejected |
               -- interview_invite | recruiter_outreach | follow_up_sent |
               -- offer | withdrawn | note
  source,      -- email | extension | manual | system
  occurred_at, source_email_id NULL, payload JSONB
)

extractions(
  id, user_id, posting_id,
  languages TEXT[], technologies TEXT[],
  seniority, salary_min, salary_max, currency,
  work_mode,                          -- onsite | hybrid | remote
  visa_signal,                        -- sponsors | local_only | unclear
  visa_notes,
  verified BOOLEAN DEFAULT false,
  model, extracted_at
)

contacts(id, user_id, job_id, name, role, url, source, approached BOOLEAN,
         approached_at NULL, notes)

artifacts(id, user_id, application_id, kind,  -- cover_letter | prep_note
          content, model NULL, created_at)

emails(id, user_id, gmail_message_id, sender, subject, received_at,
       classification, extraction JSONB,
       matched_application_id NULL, triage_state)

duplicate_candidates(id, user_id, posting_a, posting_b,
                     title_sim, cosine_sim, state)  -- auto | confirmed | rejected | pending

job_queue(id, user_id, type, payload JSONB, state, run_after,
          attempts, last_error)
```

Design notes:

- **postings ≠ jobs ≠ applications** is the load-bearing separation. Duplicates merge at the job level without losing per-posting provenance; an application accrues events regardless of which platform's posting triggered them.
- **Status is an event log**, never a mutable column. Late-arriving email appends rather than overwrites; funnel analytics (time-to-response) fall out of timestamps for free; the kanban column is `max(events)` by a type-precedence rule.
- **`focused` is nullable**: an explicit tag wins; null falls back to "has a cover_letter or prep_note artifact."
- **Embedding dimension** matches the chosen embedding model (1024 for voyage-3.5-lite / similar; finalize at implementation and treat as a migration if changed).

## 8. Email → application matching (algorithm)

Given stage-two extraction `(company, title, date, type)`:

1. Normalize company (lowercase, strip legal suffixes — Pte Ltd, Co., Ltd., Inc — collapse whitespace/punctuation).
2. Candidate set: applications for the same `user_id` where `company_norm` matches exactly, or trigram similarity ≥ 0.6.
3. Score candidates: title trigram similarity (weight 0.5) + date proximity to the application's `applied` event (weight 0.3, decaying over 60 days) + platform agreement if identifiable (0.2).
4. Score ≥ 0.75 with a ≥ 0.15 margin over the runner-up → auto-attach event.
5. Otherwise → triage queue. A `confirmation`-type email with no candidate at all **creates** a new application in `needs_enrichment` (this is the backfill path).

Thresholds are config; expect to tune them against the first backfill run, which doubles as the test corpus.

## 9. LLM pipeline

| Task | Model class | Notes |
|---|---|---|
| Email classification | Small/cheap (Haiku-class) | High volume, low difficulty; strict JSON out |
| Email field extraction | Small/cheap | Only runs on job-classified mail |
| JD extraction | Small-to-mid | JSON schema output; includes visa-signal reasoning |
| Cover letter draft | Larger (Sonnet-class) | On-demand only; takes JD + resume profile + tone constraints |
| Embeddings | Dedicated embedding model | JD text, truncated to model limit |

Practices: every extraction prompt demands JSON-only output validated against a schema, with one repair-retry on parse failure; prompts are versioned in the repo and the version is recorded on each extraction row; misclassifications corrected in triage are periodically harvested as few-shot examples; the resume profile used for cover letters is a maintained markdown file, not re-derived per call.

Cost control: classification runs on every candidate email but candidates are pre-filtered by sender allowlist, so realistic volume is a handful of stage-two calls per day. Cover letters are the only expensive calls and are user-initiated.

## 10. Duplicate detection (algorithm)

On each new posting with an embedding:

1. **Blocking:** candidate postings for the same user where `company_norm` matches (exact or trigram ≥ 0.6) **or** JD embedding cosine similarity ≥ 0.90 (catches agency reposts under a different company name — common in the SG market).
2. **Refinement:** for each candidate pair, compute title trigram similarity and cosine similarity.
3. **Decision:** cosine ≥ 0.95 and title ≥ 0.5 → auto-link to the same canonical job (state `auto`). Cosine 0.88–0.95 → `pending`, surfaced in triage. Below → no action.
4. Manual confirmation/rejection in triage updates state and, on confirm, merges at the job level.

Same-company reposts with different req IDs are treated as duplicates only above the auto threshold; a company genuinely hiring for two similar roles will typically land in `pending`, which is the correct place for human judgment.

## 11. Security and privacy

- Gmail scope is read-only; the OAuth token is encrypted at rest; the sender allowlist means non-job mail bodies are never sent to an LLM (subject-keyword fallback sends subject + sender only to stage one, body only after a positive classification).
- No platform credentials exist anywhere in the system, v1 or later. This is an architectural invariant, not a config choice.
- Extension permissions are scoped to the three platform domains plus the API origin; no `<all_urls>`.
- Single-tenant v1 still hashes the API bearer token and TLS-terminates properly — the Gmail token makes this box sensitive regardless of user count.
- Multi-user later adds: per-user OAuth, row-level security keyed on `user_id` (the column already exists everywhere), and extension auth via per-user tokens.

## 12. Phasing

**Phase 1 — Email backbone (target: ~1 week of evenings).** Schema, ingest API skeleton, Gmail OAuth + polling, two-stage classification/extraction, matching, 12-month backfill, bare table UI with triage. *Exit criterion: the full application history to date is visible in one place and new status emails appear within 15 minutes.* This alone resolves the acute tracking pain.

**Phase 2 — Extension.** Three adapters, quick-tag popover, manual capture, `needs_enrichment` records get JD text going forward. *Exit: an Easy Apply on LinkedIn produces a fully populated record with zero manual entry.*

**Phase 3 — Intelligence.** JD extraction + verification workflow, embeddings + dedup + triage, cover letter generation, funnel analytics, reminders. *Exit: the funnel view can answer "does focused prep improve my response rate?" with real data.*

**Phase 4 — Productization (optional).** Multi-tenant auth, row-level security, per-user Gmail OAuth flow, extension store listing, JobStreet/SEA positioning (EP/COMPASS awareness is the differentiator against US-centric incumbents Teal/Huntr/Simplify). No architectural change required — that was the point of §5.

Build order rationale: email before extension, even though the extension is more interesting to build — email backfills the past and covers mobile applies; the extension only captures forward.

## 13. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Platform DOM redesign silently breaks an adapter | High (eventually certain) | Thin adapters; capture-failure telemetry surfaced as a dashboard banner + daily count; a broken adapter degrades to the email-only path, losing JD text but not the record |
| Email misclassification (missed rejection, false match) | Medium | Conservative auto-match threshold; triage queue for everything uncertain; corrections harvested as few-shot examples |
| ATS sender not on allowlist → status email never seen | Medium | Subject-keyword fallback net; triage surfacing of "job-like" unmatched mail; allowlist grows over time |
| LLM extraction errors poison analytics | Medium | `verified` flag; funnel views can filter to verified-only; extraction prompt versioning enables re-runs |
| Gmail API quota / token expiry | Low | Refresh-token handling with alerting on auth failure; polling volume is far below quota |
| Scope creep before Phase 1 ships | High (self-inflicted) | Exit criteria per phase; Phase 1 has no LLM-on-JD work at all |

## 14. Open questions

1. ~~Embedding model and dimension~~ — **chosen, not finalized.** `voyage-3.5-lite` / `vector(1024)` (`pipeline/config.py:87-88`), matching the schema column. Never run against the real Voyage API (`VOYAGE_API_KEY` unset, dedup inert) — cost and SEA-English JD quality are both still unmeasured. Revisit once `VOYAGE_API_KEY` is set and `scan` runs against real cross-platform posts.
2. ~~HTMX vs. Next.js~~ — **resolved by sidestepping both.** Plain server-rendered Jinja2 + forms, POST-redirect-GET, no JS build step (`pipeline/web.py`, module docstring). Simpler than either option on the table; revisit only if a dashboard view needs real client-side interactivity Jinja can't express.
3. ~~Whether `recruiter_outreach` deserves its own entity~~ — **resolved.** Neither a separate entity nor unattached events: it's an `applications` row with `origin = 'inbound'` and no `applied` event, deriving status `interested` (invariant #9 in CLAUDE.md, migration `008_application_origin.sql`). Promotion to a real application is just adding the `applied` event later.
4. **Retention policy for raw email bodies — still open, and overdue by its own stated trigger.** `migrations/001_init.sql:86-87`'s comment says "revisit retention before multi-user" — multi-tenancy shipped in migration `003_multi_tenant.sql` and this was never revisited. Tracked in CLAUDE.md's Immediate next tasks.

## 15. What to revisit as it grows

Polling → Pub/Sub push at multi-user; the Postgres queue → a real broker only if worker volume ever warrants it (unlikely below thousands of users); per-site adapters → a maintained community adapter model if the extension is ever distributed; and the matching/dedup thresholds, which should be re-tuned once the backfill corpus exists.
