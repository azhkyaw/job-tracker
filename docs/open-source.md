# Open-Source Release — Research and Plan

**Author:** AZ
**Status:** Draft v1 — research findings, not a commitment
**Date:** 26 July 2026
**Scope:** Releasing this system as a self-hosted open-source project, with
profile/outreach value as the stated goal rather than revenue. Companion to
`docs/monetization.md`, which analysed the SaaS path; §12 here says what to do
with that document.

---

## 1. Summary

Open source is the better direction, and `docs/monetization.md` §13.1 already
contained the core argument: self-hosters register their own Google Cloud
project, so each deployment is its own sub-100-user app and the CASA compliance
problem disappears entirely. That still holds.

But this research surfaced three things that change execution:

1. **The extension is the liability, not the asset.** LinkedIn actively
   fingerprints for 6,236 specific Chrome extension IDs on page load, and has
   sent cease-and-desist letters to solo developers over *marketing surface*
   rather than technical behaviour. Publishing to the Chrome Web Store mints
   the stable public ID that script probes for — on the author's own account,
   during an active job search. That is precisely the harm design doc §2 built
   the whole architecture to avoid. See §3.
2. **The OSS niche is already crowded**, including at least one project that is
   architecturally near-identical (FastAPI + plain HTML + LLM email
   classification + self-hosted). "Self-hosted job tracker" is not a blue
   ocean either. See §4.
3. **Profile value and adoption value point in opposite directions here.** The
   sophistication that makes this a good portfolio piece — Postgres, pgvector,
   RLS multi-tenancy, event sourcing — is pure setup friction for a
   self-hoster who wants one container. Optimising for both produces a worse
   version of each. Since the stated goal is profile, §5 recommends choosing
   profile explicitly and *not* rebuilding on SQLite.

Recommended shape: publish the server as the flagship repo optimised for
reading, keep the extension in a separate repo distributed as load-unpacked
only (no store listing until the job search ends), lead with a written
engineering narrative, and ship a seeded public demo.

## 2. The self-hoster OAuth token problem

The most immediate practical issue, affecting the author's live install before
it affects anyone else.

**Confirmed:** Google's OAuth documentation states that "a Google Cloud
Platform project with an OAuth consent screen configured for an external user
type and a publishing status of 'Testing' is issued a refresh token expiring in
7 days, unless the only OAuth scopes requested are a subset of name, email
address, and user profile." We request `gmail.readonly`, so the exception does
not apply.

`README.md:81-83` instructs the operator to add themselves under **Test
users**, which leaves publishing status at Testing. Every self-hoster following
the README loses Gmail sync after 7 days with `invalid_grant: Token has been
expired or revoked`, and files the same issue.

**Not confirmed — do not state as fact:** whether switching publishing status
to "In production" while *unverified* removes the 7-day expiry. Google's docs
only tie removal of the clock to an app being verified and published, and the
support page says production-status projects "should complete the verification
process" if they meet the criteria. Community reports say flipping to
production works. This is undocumented behaviour for restricted scopes and
could change without notice.

**Therefore the engineering answer, which does not depend on resolving the
ambiguity:**

- Test the production-status flip empirically on the author's own install and
  document the observed result with the date observed, flagged as undocumented.
- Handle `invalid_grant` explicitly in `pipeline/gmail_sync.py` — surface
  "Gmail authorization expired, run `cli auth` again" rather than a traceback,
  and set a visible banner in the dashboard. This is required regardless of
  which way the flip goes, because tokens also die on password change (Gmail
  scopes specifically), six months of disuse, and user revocation.
- Document the 7-day reality honestly in setup, including the worst case, so
  self-hosters aren't surprised.
- Note the alternative for the subset who have Google Workspace: an
  **Internal** user-type app is exempt from verification *and* the 100-user cap
  entirely. Useless for personal Gmail accounts, which is most self-hosters.

## 3. Legal and account risk: the extension

This is the finding that most changes the plan, and it deserves to be
uncomfortable reading.

**LinkedIn fingerprints installed extensions.** Its script probes for **6,236
extension IDs** by testing whether static resources belonging to each ID are
reachable, and separately collects CPU core count, memory, screen resolution,
timezone, language, battery and audio characteristics. LinkedIn says it uses
this to identify extensions violating its terms and to detect accounts fetching
excessive data. The detected-extension count has grown steadily — ~2,000 in
2025, ~3,000 shortly after, 6,236 now.

**The mechanism matters for us specifically.** That probe works by extension
ID. A locally loaded unpacked extension has a random per-install ID; a
Chrome-Web-Store-published extension has a **stable, public, permanent ID**.
Publishing to the store therefore converts "something LinkedIn cannot
enumerate" into "an entry that can be added to a list," and the store listing
itself is publicly searchable by anyone at LinkedIn looking for tools that
mention LinkedIn.

**Enforcement precedent exists against solo developers.** Browserflow, a
general-purpose browser automation extension, received a LinkedIn C&D demanding
it stop developing tools marketed for LinkedIn automation, remove all LinkedIn
references and logos from marketing, and affirm future compliance in writing.
The developer's defence — that the tool wasn't LinkedIn-specific and all
automation was user-initiated on the user's own account — did not prevent the
letter. Separately, LinkedIn restricted the developer account behind
"Teamfluence." Legal commentary on the Browserflow case was consistent: hiQ v.
LinkedIn is narrower than popularly believed (LinkedIn prevailed on User
Agreement violations), C&Ds are cheap intimidation, and the risk is
asymmetric — a solo developer versus a well-funded legal team.

**Where this project actually stands.** Technically we are in a much stronger
position than Browserflow: read-only, no automation, no background activity, no
scheduled visits, no stored platform credentials, capture only on a user's own
click on a page they already opened. Invariant #1 is a genuine and unusually
clean defence, and `extension/PRIVACY.md` documents it well.

But the Browserflow lesson is that the letter targets **marketing surface, not
technical merit**. And our surface is saturated: the README, design doc, and
adapter filenames all foreground LinkedIn, and the project is positioned around
the three platforms by name.

**The personal stake is the real issue.** Design doc §2 chose this entire
architecture because "the realistic failure mode is account restriction during
an active job search." An extension published under a stable ID, listed
publicly, running on the author's own LinkedIn account *during that same job
search*, reintroduces the exact risk the design was built to avoid — by a
different route.

### 3.1 Mitigations, in order of value

1. **Do not publish the extension to the Chrome Web Store until the job search
   ends.** Load-unpacked only. Costs adoption; removes the stable-ID exposure
   and the searchable listing. Revisit when the author's account is no longer
   load-bearing.
2. **Split the extension into its own repository.** The server, Gmail ingest,
   manual entry and JD archive are fully functional with no extension at all.
   Keeping them in one repo means a C&D aimed at the extension threatens the
   flagship project; separating them contains the blast radius and lets the
   main repo be the portfolio piece without the risk.
3. **Never use platform logos or trademarks** anywhere — README, docs, demo,
   store listing. This was an explicit Browserflow demand.
4. **Describe capability, not platforms**, in the project name and tagline.
   Platforms get named factually in adapter documentation, which is
   unavoidable and fine; they should not be the pitch.
5. **Foreground invariant #1 in the README.** It is the actual defence and it
   is genuinely differentiating against anything automating a logged-in
   session.
6. If a letter ever arrives: retain a lawyer, do not respond unrepresented,
   and do not sign written affirmations about future conduct without review.

## 4. The OSS competitive landscape

`docs/monetization.md` §3 found the SaaS market commoditized. The OSS side is
crowded too:

| Project | Stack | Notes |
|---|---|---|
| **JobSync** | Next.js, Shadcn | Self-hosted, AI resume review, job matching, analytics; privacy-first positioning |
| **CareerSync** | Gmail parsing | Stateless by design, no server-side storage, self-hostable |
| Unnamed FastAPI tracker | **FastAPI + plain HTML/JS, SQLite, LLM (Ollama/Anthropic/OpenAI)** | Applications, documents and recruiter emails in one local app; LLM parses postings and classifies email. Architecturally near-identical to this project |
| Job Application Manager | React + TS | Dashboard, Markdown resume editor, interview journal |
| Apps Script / notebook trackers | various | Several Gmail-parsing scripts, low polish |

That third row should be read carefully before writing any positioning: same
framework, same no-build-step philosophy, same LLM email classification, and a
*simpler* deployment story.

**What still differentiates this codebase** — and these are real, not
consolation prizes:

- **Gmail History-API incremental sync with a stored cursor plus windowed
  fallback**, and a genuine multi-month backfill. Most alternatives parse on
  demand or are stateless. The ordering bug fixed in `_list_message_ids()` is
  the kind of thing only a project that actually ran a real backfill would
  have found.
- **Status as an append-only event log** with real-world `occurred_at` and a
  derived-status view — not a mutable status column. Nothing else in the list
  does this, and it is what makes the funnel analytics correct for free.
- **postings ≠ jobs ≠ applications** with pgvector dedup and a single audited
  merge path.
- **Versioned prompts with per-row version recording**, enabling selective
  re-runs.
- **Five test suites that stub every LLM call** — zero-cost, deterministic, and
  they have caught every regression in the project.
- **SEA/visa awareness** (`visa_signal`), which no OSS alternative touches.

## 5. The central tension: profile vs. adoption

Worth stating plainly because it determines a lot of downstream work.

The things that make this repo *impressive to read* — Postgres with pgvector,
RLS-based multi-tenancy with `SET ROLE` and per-request GUCs, event sourcing,
a worker with `FOR UPDATE SKIP LOCKED` and savepoint-per-job — are exactly the
things that make it *harder to adopt* than a single-container SQLite app. A
self-hoster wanting a personal tracker does not want tenant isolation; they are
the only tenant. The competitor in §4 row 3 wins on setup friction precisely
because it is less sophisticated.

Optimising for both yields a worse portfolio piece and a worse self-hosted app.

**Recommendation: choose profile, explicitly.** The stated goal is profile and
outreach, so:

- **Do not rebuild on SQLite** and do not strip multi-tenancy. That is weeks of
  work that makes the repo less interesting for the stated goal.
- Reduce friction only where it is free: a working `docker compose up`, a
  migration runner, a seeded demo dataset. Do not reduce it by removing
  architecture.
- Accept that adoption will be modest and that this is fine. Stars are not the
  goal; being *readable evidence of judgement* is.

## 6. What actually creates profile value

The research is fairly blunt about this. Recruiters spend on the order of
**eleven seconds** on a GitHub profile, and engage substantially more with
projects that have runnable code or a live demo. What reportedly matters for
career switchers is evidence of having shipped something real end-to-end, not
volume of code. The AI hiring market is competitive — postings up, applications
up far more — so differentiation has to be legible fast.

Concrete implications, ordered:

1. **A seeded public demo** beats any additional feature. Fabricated data, one
   URL, no signup. This is the single highest-leverage item and it does not
   exist.
2. **A README that shows the product working within one screen** — screenshots
   of the funnel and triage views above any prose.
3. **A written narrative**, published somewhere linkable. The genuinely
   differentiated artifact is the *engineering record*: a real design doc with
   a rationale trail, versioned prompts, cost-controlled two-stage
   classification, and CLAUDE.md's list of things that broke and why. Very few
   portfolio repos have any of that. The Gmail newest-first ordering bug, the
   shadow-DOM `composedPath()` fix, and the midnight-date reasoning are better
   evidence of seniority than any feature.
4. **Frame it as an LLM pipeline, not a job tracker.** "A production LLM
   ingestion pipeline that happens to track job applications" is accurate and
   avoids competing in the commoditized category from §4.
5. Only then, features.

## 7. License

**Recommendation: Apache-2.0.** Permissive enough to maximise adoption, with
an explicit patent grant that makes it safe for a prospective employer's legal
team to look at, which is the relevant audience given the goal. It also
qualifies as Free Software under the FSF definition, which matters for
awesome-selfhosted inclusion (§9).

Alternatives considered: MIT is simpler but lacks the patent grant. AGPL-3.0
would preserve a future hosted-tier option by requiring commercial operators to
publish modifications — worth it only if `docs/monetization.md` might be
revived, and it costs some adoption. BSL and other source-available licenses
solve a cloud-commoditization problem this project does not have, and would
disqualify it from Free Software listings.

Note that no license choice affects §3 in any way. Licensing governs copying,
not platform terms of service.

## 8. Chrome Web Store, if it is ever listed

Facts for the decision, which §3.1 recommends deferring:

- **$5 one-time** developer registration, covering up to 20 extensions.
- **Review time:** 7–14 business days for a first submission from a new
  account, 2–5 for established accounts, 24–48 hours for updates. As of April
  2026 a submission surge has extended these.
- **Privacy policy** must be publicly hosted and enumerate data categories
  collected, purpose per category, sharing, storage and security, and a
  deletion contact. `extension/PRIVACY.md` already contains a store-listing
  checklist and mostly satisfies this.
- **Remote-hosted-code rejections are the common failure** under MV3. Our
  extension POSTs to a user-configured server, which is data transmission, not
  remote code execution — but reviewers conflate the two, so the justification
  needs to be explicit and the configurable base URL clearly explained.
- **Permission justification per permission** is required. Our set is narrow
  (content scripts on three sites, storage, activeTab, optional host access) and
  every one already has a one-line rationale in `PRIVACY.md`.

## 9. Distribution

- **awesome-selfhosted** — the realistic highest-value listing. Submissions go
  to `awesome-selfhosted-data` as structured data; entries must be Free
  Software per the FSF definition (Apache-2.0 qualifies), and automated
  workflows flag dead links and unmaintained projects. Requires the project to
  be genuinely documented and maintained.
- **Show HN** — one shot, best spent on the written narrative from §6.3 rather
  than on a bare repo link.
- **GitHub topics** — `job-application-tracker`, `application-tracker`,
  `job-tracker` are live topic pages where the §4 competitors are found.
- **r/selfhosted and job-search subreddits** — check each subreddit's
  self-promotion rules before posting; several require prior participation or
  restrict launch posts to specific threads.
- Avoid any channel where the pitch has to name LinkedIn to make sense (§3.1.4).

## 10. Maintenance posture

Worth being honest about, because the failure mode is well documented:
self-hosted project abandonment is accelerating, with commentary attributing it
to tooling that lets one person ship far more code than one person can
maintain, alongside ~54% burnout rates among solo founders.

This project has three specific liabilities: adapter breakage is "eventually
certain" (design doc §13), Gmail API drift, and LLM provider changes. All three
generate issues from strangers.

Mitigations that cost little:

- **State the maintenance posture in the README** — best-effort, personal
  project, PRs welcome for adapters. Setting expectations is free and prevents
  most resentment.
- **CI on every PR** (GitHub Actions with a pgvector service container running
  `scripts/test.sh`). Without it, adapter PRs from strangers are unreviewable
  and become a burden rather than help. This is the single highest-leverage
  maintenance investment.
- **A CONTRIBUTING.md focused on the adapter model** — thin adapters were
  designed for exactly this (design doc §15), and adapter PRs are the
  contributions most likely to arrive.
- Issue templates that require the extension's failure ring buffer contents,
  so adapter reports are actionable on arrival.

## 11. Pre-release checklist

Audit results, run 26 July 2026:

- **Git history is clean.** No secret was ever committed across all 23 commits
  (`.env`, `credentials*.json`, `.gmail_token.json`, `profile.md` all
  gitignored from the start). Publishable with no history rewrite — this is
  the usual thing that sinks a release and it is already fine.
- Single author, no remote configured yet.
- No LICENSE file exists.
- Personal identifiers in tracked files: real company names appear in
  `tests/test_web.py:285,299,320,323` (Beacon Search) and `:1012`
  (an ATS-branding mismatch), and in CLAUDE.md's gotchas (real company names). All are
  the author's own information; genericise the fixtures and make CLAUDE.md a
  conscious decision rather than an oversight.
- `.claude/settings.local.json` contains the author's Windows username but is
  gitignored; the tracked `.claude/settings.json` is clean.

Ordered work:

1. LICENSE (Apache-2.0), and a README maintenance-posture statement.
2. `invalid_grant` handling + honest token documentation (§2).
3. Split the extension into its own repo (§3.1.2); scrub platform framing from
   the main repo's pitch (§3.1.3–4).
4. Genericise test fixtures; decide on CLAUDE.md.
5. `docker compose up` path + a migration runner. The runner also retires the
   "migration filenames hardcoded in FOUR places" trap, which is a genuine
   contributor hazard.
6. Seeded demo dataset + public demo deployment (§6.1).
7. README rewrite for strangers, screenshots first (§6.2).
8. CI with a pgvector service container (§10).
9. CONTRIBUTING.md + issue templates (§10).
10. `/security-review` pass before the repo goes public — auth, token
    surfaces, RLS boundary.
11. The written narrative (§6.3), then launch (§9).

## 12. What dies, and what happens to monetization.md

Dead: billing, pricing, the 90-day pass, COMPASS scoring as a paid wedge, and
the verified-outcomes benchmark index — that last one needed centralized data
this direction deliberately will not have.

Survives, re-motivated: **prep packs** (still the best feature, now free),
**forwarding ingest** (now motivated by self-hosters who don't want to create a
Google Cloud project at all — a bigger deal for OSS than it was for SaaS, since
it removes the §2 problem entirely for those users), and **onboarding as
backfill** (now the thing that decides whether a stranger sticks). Email
retention drops from compliance gate to ordinary hygiene, since it is the
self-hoster's own data on their own disk.

`docs/monetization.md` should be kept, not deleted — it is the record of why
this direction was chosen, and §2's compliance analysis is what makes the OSS
argument concrete. Mark it superseded at the top and leave it as reasoning
history. If the direction ever reverses, that analysis is still valid.

## 13. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| LinkedIn C&D over the extension | Low–Medium, rising with visibility | §3.1: separate repo, no store listing during the job search, no logos, capability-first framing |
| Author's own LinkedIn account restricted mid-search | Low, but the highest-consequence outcome | Load-unpacked only (random per-install ID); defer store listing entirely |
| Self-hosters hit the 7-day token expiry and churn | **High if undocumented** | §2: test, document honestly, handle `invalid_grant` visibly |
| Repo gets no traction | High | Expected; §5 accepts it. Profile value comes from readability and the narrative, not stars |
| Support burden exceeds appetite | Medium | §10: stated posture, CI, issue templates. Archiving later is acceptable and normal |
| A §4 competitor is simply better for self-hosters | Likely already true on setup friction | Don't compete there (§5); differentiate on the pipeline and the engineering record |
| Adapter breakage generates unanswerable issues | High (certain over time) | Community adapter model, ring-buffer issue template, degradation to email-only path already designed |

## 14. Open questions

1. **Does CLAUDE.md ship?** It is arguably the most differentiating artifact in
   the repo — a genuine record of what broke and why — and it also documents
   the author's real job search, including named companies. Publishing it is a
   privacy call only the author can make.
2. **One repo or two?** §3.1.2 recommends splitting the extension for blast
   radius. The cost is a worse first-run story, since capture and server are
   documented as one system.
3. **Test the production-status flip or document the 7-day reality?** Doing
   both is best; if only one, document the reality, since it is true regardless.
4. **Is the demo deployment worth ongoing cost and exposure?** §6.1 argues it
   is the highest-leverage item; it also means running a public instance with a
   real Anthropic key and an attack surface. Seeded read-only data, generation
   features disabled, and a hard spend cap.
5. **Does the SEA/visa angle survive?** It was a monetization wedge, but it is
   also the one thing no OSS alternative does. Possibly the strongest
   positioning available — unclear whether it is worth building without revenue
   attached.

## 15. Sources

Verified July 2026.

- [OAuth 2.0: refresh token expiration](https://developers.google.com/identity/protocols/oauth2) · [When verification is not needed](https://support.google.com/cloud/answer/13464323) · [Refresh token 7-day limit explained](https://www.unipile.com/google-oauth-refresh-token/) · [`invalid_grant` causes](https://nango.dev/blog/google-oauth-invalid-grant-token-has-been-expired-or-revoked)
- [LinkedIn scans for 6,000+ Chrome extensions](https://www.bleepingcomputer.com/news/security/linkedin-secretly-scans-for-6-000-plus-chrome-extensions-collects-data/) · [Ask HN: LinkedIn C&D for a Chrome extension (Browserflow)](https://news.ycombinator.com/item?id=34583932) · [LinkedIn C&D precedent, email-scraping plug-in](https://threatpost.com/linkedin-goes-after-email-scraping-browser-plug-in/105185/)
- [Chrome Web Store review times 2026](https://extensionbooster.com/blog/chrome-web-store-extension-review-time-2026-how-long-guide/) · [Developer registration fee](https://www.extensionradar.com/blog/chrome-web-store-developer-fee-2026) · [CWS policy updates 2026](https://developer.chrome.com/blog/cws-policy-updates-2026) · [Remote-hosted-code violations](https://developer.chrome.com/docs/extensions/develop/migrate/remote-hosted-code) · [Privacy fields](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy)
- [OSS license comparison 2026](https://ossalt.com/guides/oss-licensing-guide-mit-apache-agpl-2026) · [License guide](https://www.opensourcealternatives.to/blog/open-source-license-guide)
- [awesome-selfhosted](https://awesome-selfhosted.net/) · [awesome-selfhosted guide](https://www.solosoft.dev/post/awesome-selfhosted-guide-2026/)
- [JobSync (self-hosted OSS tracker)](https://github.com/Gsync/jobsync) · [GitHub topic: job-application-tracker](https://github.com/topics/job-application-tracker)
- [Self-hosted app abandonment is accelerating](https://www.xda-developers.com/self-hosted-app-abandonment-speeding-up-reaching-projects-trust/) · [Solo founder burnout 2026](https://www.foundra.ai/key-reads/solo-founder-burnout-top-reason-quit-2026)
- [AI engineering portfolios](https://www.dataexpert.io/blog/ultimate-guide-ai-engineering-portfolios) · [AI engineering hiring practices 2026](https://github.com/alexeygrigorev/ai-engineering-field-guide)
