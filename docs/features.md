# Feature Roadmap — Research and Prioritisation

**Author:** AZ
**Status:** Draft v1 — research findings and a recommended order
**Date:** 26 July 2026
**Scope:** What to build next, given the open-source direction
(`docs/open-source.md`). Supersedes the roadmap in `docs/monetization.md` §9,
which assumed features existed to justify a paywall.

---

## 1. How these were prioritised

Three lenses, applied in order:

1. **Does it help the author's own live job search?** This is the only user
   with verified needs, and the search is active now (started 2026-07-16).
2. **Does it earn profile value?** Per `docs/open-source.md` §6, that means
   readable evidence of judgement and a demo someone grasps in seconds — not
   feature count.
3. **What does it cost to maintain?** Every feature is a future issue from a
   stranger (`docs/open-source.md` §10).

A feature that scores on all three goes to Tier 1. Anything that only scores on
lens 2 is a portfolio ornament and lands in Tier 3.

## 2. The 2026 market context that should drive selection

The job search this system was designed for (design doc, 21 Jul 2026) is
already a harder one than the doc assumes, and the numbers argue for specific
features rather than general polish:

- **Application volume has exploded.** The average posting now receives **242
  applications**, roughly 3× its 2017 level; LinkedIn has reported peaks of
  **11,000 applications per minute**. Auto-apply tools are a direct cause.
- **A large share of postings are not real.** Estimates range from **18–22%**
  (Greenhouse, per quarter) to **27.4%** (a LinkedIn listing analysis) to
  **47%** of seekers encountering them; **81% of surveyed recruiters admit
  their employer posts ghost jobs**. The US reported 6.9M openings against 4.8M
  hires in Feb 2026. The average ghost-job cycle **wastes 9 hours**.
- **Generic AI output is now actively penalised.** **49% of US hiring managers
  auto-dismiss résumés they suspect are AI-generated**, and **62% reject AI
  résumés that lack personalisation.**
- **Following up measurably works and almost nobody does it.** Following up 1–2
  weeks after applying correlates with a **~20% lift in callbacks** (Resume
  Worded, 1,200 job seekers), with other research claiming up to 30%; the ideal
  first window is **7–10 business days**; cap at **three** total; personalised
  messages get **30–50%** better response than generic. Yet **57% of job
  seekers never follow up at all** (Glassdoor).
- **Interviews are increasingly machine-mediated.** 81% of recruiters use video
  interviews and 70%+ of enterprises are adopting video interview platforms;
  asynchronous one-way formats mean no interviewer to read, no clarifying
  questions, and AI scoring against a competency rubric.

Read together: the scarce resource is no longer *tracking* applications, it is
**deciding which applications are worth the effort, and extracting maximum
value from the few that respond.** That is what Tier 1 targets.

## 3. Tier 1 — build these

### 3.1 Follow-up drafting

**Best evidence-to-effort ratio in the entire backlog.** ~20% callback lift,
57% of people don't do it, and personalisation is the difference between a lift
and nothing.

What already exists: `analytics.reminders()` finds applications with an
`applied` event older than `REMINDER_DAYS` and no response or follow-up. The
`follow_up_sent` event type exists in the schema. The JD text, contacts, and
timeline are all on hand.

What's missing: it lists them and stops. Have it **draft the message** — using
the archived JD and the recorded contact so the draft is specific, not generic,
because the research says generic follow-ups perform the same as silence. Then
record `follow_up_sent` on send-confirmation.

Note `REMINDER_DAYS` defaults to **10**, which lands inside the researched
7–10 business-day window. That default was a guess and the research validates
it — worth a comment in `config.py` saying so, and a cap at three follow-ups
per application to match the guidance.

Effort: small. Highest priority.

### 3.2 Interview prep pack

The moment with the most value at stake, and the system detects it within 15
minutes of the invite email arriving.

The schema is already waiting: `artifacts.kind` permits **`prep_note`**, and
nothing in the codebase ever generates one — `covers.py` only does cover
letters. This is a designed-for affordance left unused.

Contents, per the research on what actually helps:

- **Likely questions derived from the archived JD** — the JD is the source of
  the competency rubric an async AI interviewer scores against.
- **A STAR story bank mapped to JD requirements.** Research recommends 8–10
  situations with measurable outcomes, drawn from the user's own history
  (`users.resume_profile`), each mapped to a specific requirement rather than
  generic.
- **Async/AI-interview specific guidance when the invite indicates one** —
  structured responses against the competency rubric, no rapport to build, no
  clarifying questions available. Given 81% video-interview adoption this is
  the common case, not the exotic one, and no competitor's prep feature
  distinguishes the format.
- **The user's own application timeline as recall** — what they applied to,
  when, and what they said.

**Schema gap to resolve:** `interview_invite`'s `occurred_at` is when the
*invite arrived*, not when the interview *is*. Prep needs the latter. Put the
scheduled datetime in the event's `payload` JSONB rather than adding a column —
consistent with invariant #2, and `payload` exists for exactly this.

Effort: medium.

### 3.3 Ghost-job and non-responsive-employer scoring

The sleeper feature, and the one where this system has an unfair advantage.

Published ghost-job signals map almost exactly onto fields already captured:

| Signal | Field |
|---|---|
| Listing open 60+ days / keeps reappearing | `postings.posted_label`, `postings.reposted` (migration 004) |
| No salary range | `extractions.salary_min/max` |
| Vague boilerplate JD, no named team or scope | JD text — LLM-assessable during existing `extract_jd` |
| No response after applying | **the event log** |
| Company perpetually hiring the same role | `jobs` + dedup across postings |

The last two are the differentiator. Every published ghost-job guide tells the
reader to watch for non-response — but only a system holding a verified,
timestamped outcome history can compute it. A per-employer "you have applied 4
times, zero responses in 90 days" signal is not something a tool without email
ingest can produce.

`posted_label` is deliberately unparsed (design doc §7: "granularity is too
fuzzy" for a date). That's fine — a ghost score needs coarse buckets
("<1 week", "1–4 weeks", ">1 month"), which the label supports without
pretending to precision.

Value to the author directly: at 242 applications per posting and 9 hours
wasted per ghost cycle, a badge that says "deprioritise this" is worth more
than any generative feature.

Effort: small-to-medium — a scoring function, an LLM prompt addition to the
existing JD extraction, and a badge in the list/detail views.

### 3.4 COMPASS estimator (Singapore)

The one feature no competitor has, SaaS or OSS, and now fully specifiable:

| Criterion | Points | Applicant can know? |
|---|---|---|
| C1 Salary vs sector median PMET wage | 0/10/20 | **Yes** — `extractions.salary_min/max` + a maintained benchmark table |
| C2 Qualifications (degree / recognised / top-100) | 0/10/20 | **Yes** — static per user, from profile |
| C3 Nationality diversity of employer's PMETs | 0/10/20 | **No** — employer-internal |
| C4 Employer's local PMET share | 0/10/20 | **No** — employer-internal |
| C5 Skills bonus (Shortage Occupation List) | 0 or +20 | **Yes** — role title vs the SOL |
| C6 Strategic bonus (MOM/EDB programme) | 0 or +10 | Rarely |

Pass threshold is **40 points**. The decisive insight: **C1 + C2 alone can
reach 40**, so a well-paid applicant with a recognised degree passes on
applicant-side criteria only, regardless of C3/C4. That makes an applicant-side
estimator genuinely actionable rather than a partial guess — and where C1+C2
falls short of 40, the honest output is "depends on the employer's C3/C4, which
you cannot see," which is itself useful information nobody else surfaces.

**Do not fabricate C3/C4.** Report them as unknown. The feature's credibility
depends on it.

Maintenance cost is real and recurring: sector benchmarks reset annually (Jan
2026 average **+5.1%**) and the SOL changes (Jan 2026 **removed** Cyber Risk
Specialist, Cybersecurity Operations Specialist, and Product Manager (Digital)).
Keep both in a versioned config file with an explicit "valid as of" date and a
staleness warning in the UI — never in code.

Effort: medium. Also the strongest available positioning for the OSS release
(`docs/open-source.md` §14.5).

### 3.5 Seeded demo dataset

Not a user feature; the highest-leverage *release* item. `docs/open-source.md`
§6.1 argues a runnable demo beats any additional feature for profile value, and
it cannot exist without fabricated data — the author's real dashboard contains
actual recruiter names and salary figures.

Doubles as fixtures for screenshots and for the existing suites. Effort: small.

### 3.6 Onboarding as backfill

Currently the system's strongest moment — "here are the 40 applications you
forgot about, reconstructed from your inbox" — sits behind a CLI command. For a
stranger evaluating the project, this is the make-or-break minute.

Include the search start-date question: defaulting to 12 months when someone
started two weeks ago burns tokens for nothing (the `-d 14` finding in
`docs/worklog.md`, task 1). Effort: medium.

## 4. Correction to an existing feature: cover letters

This is the research finding that changes something already built rather than
adding to the list.

`covers.py` generates a cover letter from the JD plus the user's resume profile
(`users.resume_profile`, edited at Settings → Resume profile). In a market
where **49% of hiring managers auto-dismiss résumés they suspect are
AI-generated** and **62% reject AI output that lacks personalisation**, a
one-click "generate cover letter" button is a liability if it yields anything
generic. The feature as designed can actively harm the user.

Changes worth making:

- **Force specificity in the prompt** — require concrete hooks from the JD and
  specific stories from the user's own history; refuse to emit unfalsifiable
  boilerplate. This is a new prompt version, so `cover_letter_v2.txt` plus a
  constant bump (invariant #5), never an in-place edit.
- **Label output as a draft requiring edit**, in the UI, not just implicitly.
- **Surface the research in the UI** where the button lives. A one-line note
  that unedited AI cover letters are commonly auto-rejected is more useful than
  a better model.

This also raises the value of measuring tailored vs generic on the author's
own data: it is precisely the variable the research says matters. **There is
currently no such measurement.** `applications.focused` and `analytics.by_focus`
were built for it and cut on 21 Aug 2026 — across every real application the
column came out 175 `false`, 22 unset and zero `true`, so it never split and
its panel never rendered. `analytics.by_resume` is NOT a replacement: it
records which of two standing resumes was sent (positioning), not whether an
application was customised (effort). Anything built here needs an instrument
read off something observable at apply time — a per-application cover letter
existing, a resume filename that varies by employer rather than by track —
because asking the question after the fact is exactly what failed.

## 5. Tier 2 — worth building, after Tier 1

- **Local model support (Ollama).** For a self-hosted audience this is close to
  table stakes — the nearest OSS competitor (`docs/open-source.md` §4) already
  supports Ollama/Anthropic/OpenAI, and it removes the API-key barrier plus the
  "my email goes to a third party" objection entirely. `config.py` already
  isolates model choice; the client code is Anthropic-specific. Strong adoption
  lever, moderate effort.
- **Requirement-gap view (JD vs profile).** Teal charges for keyword gap
  analysis; here JD extraction and the resume profile already exist, so the marginal
  cost is low. Frame as "decide whether to apply" and as input to the prep
  pack — *not* as a résumé rewriter (§6).
- **Interview date + upcoming view.** Falls out of §3.2's payload change; a
  small "what's coming" surface is disproportionately useful mid-search.
- **Export / data portability** (JSON + CSV). Cheap, and for a self-hosted
  privacy-positioned tool it is a trust signal as much as a feature.
- **Personal response-time benchmarks.** The crowd-sourced index died with the
  SaaS direction, but "your median time-to-first-response by platform and by
  ATS, from your own verified history" needs no other users and extends
  `analytics.py` directly.
- **Offer/comp tracking through the funnel.** Salary fields exist on
  extractions; carrying stated vs offered through to the offer event closes the
  loop and is the one thing that stays useful after a search ends.

## 6. Tier 3 — do not build

- **Résumé builder / ATS scoring** — commoditized (`docs/monetization.md` §5).
- **Application autofill** — Simplify gives it away, and it compounds the
  extension risk in `docs/open-source.md` §3.
- **Auto-apply / mass apply** — actively harmful. It is a named cause of the
  242-applications-per-posting problem, and 49% AI-suspicion auto-dismissal
  means volume now reduces per-application odds. It also violates the spirit of
  invariant #1.
- **Job discovery / recommendations** — an explicit non-goal (design doc §3),
  and it would put the project in direct competition with the platforms it
  deliberately stays clear of.
- **Crowd-sourced employer benchmark index** — needs centralised data the OSS
  direction won't have.
- **Mobile app** — the email path already covers mobile applies (design doc
  §6.2).

## 7. Unused affordances already in the schema

Worth knowing before designing anything new — the original design left room
that was never filled:

- `artifacts.kind` permits **`prep_note`**; nothing generates one (§3.2).
- `events.payload` JSONB is unused by most paths — the natural home for
  interview datetimes, follow-up metadata, and ghost-score provenance without
  new columns. **Partly filled 28 Jul 2026:** manual timeline events now carry
  `reason` and `channel` there (`web.py:_EVENT_REASONS` / `_EVENT_CHANNELS`),
  prompted by a visa rejection delivered over WhatsApp — news with no ingest
  path, which the event log previously had no truthful way to record.
  **Filled further 9 Sep 2026:** `reason` can be set on any `rejected` event
  whatever its source (`web.py:set_rejection_reason`), the list wears and
  filters on it, and `/analytics` counts it per reason — the question the
  vocabulary was written to answer. Visa stays a reason on `rejected`, not a
  status: colour is the state of the wait, and closed is closed.
- `postings.reposted` and `posted_label` (migration 004) are captured but read
  by nothing — both are ghost-job signals (§3.3).
- `events.type` includes `follow_up_sent` and `interested`, both barely used.
- `extractions.visa_signal` / `visa_notes` are extracted and displayed but
  drive no logic (§3.4).

Every Tier 1 feature except the demo dataset is activating something already
designed, which is why the effort estimates are as low as they are.

## 8. Recommended order

Interleaved with the release checklist in `docs/open-source.md` §11, since two
items are both features and release assets:

1. **Follow-up drafting** (§3.1) — smallest, best-evidenced, immediately useful
   to the author's live search.
2. **Seeded demo dataset** (§3.5) — unblocks the demo, screenshots, and the
   release narrative.
3. **Ghost-job scoring** (§3.3) — highest practical value per hour for the
   author, and novel enough to anchor the launch writeup.
4. **Cover-letter correction** (§4) — small, and it stops an existing feature
   from doing harm.
5. **Interview prep pack** (§3.2) — after the payload/date change.
6. **COMPASS estimator** (§3.4) — the differentiator, but it needs the
   benchmark research and carries annual maintenance, so not first.
7. **Onboarding as backfill** (§3.6).
8. Tier 2, in the order listed.

Everything here should follow the release blockers in `docs/open-source.md`
§11.1–11.3 (license, token handling, extension split) — those are cheap and
gate publication.

## 9. Open questions

1. **Does the COMPASS benchmark table get maintained, honestly?** A stale
   estimator is worse than none, because it produces confident wrong answers
   about someone's visa eligibility. If the answer is "probably not annually,"
   ship it with a hard staleness cutoff that disables the feature rather than
   degrading silently.
2. **Where does the ghost score's LLM assessment live?** Folding it into the
   existing `extract_jd` prompt means one new prompt version and no new job
   type, but couples two concerns and re-runs everything on either change. A
   separate job type is cleaner and costs another queue path.
3. **Is Ollama support worth the abstraction?** It widens adoption
   substantially, but a provider abstraction is exactly the kind of layer that
   makes a codebase less pleasant to read — which is the stated profile goal.
   Possibly a contributor's job rather than the author's.
4. **Should the ghost score ever be shown pre-apply?** It is most valuable
   before applying, but the extension captures at apply time, so the data
   arrives too late for the decision it should inform. A manual "check this
   posting" path would fix that and is a genuinely new capability — but it is
   also the beginning of job discovery (§6).
5. **How much of the resume profile should the prep pack assume?** A STAR story bank
   needs richer input than a résumé profile. Either the prep pack extracts
   stories from a thin profile (lower quality) or the user maintains a fuller
   one (friction). Untested.

## 10. Sources

Verified July 2026.

- [Ghost jobs 2026 survey vs BLS data](https://enhancv.com/blog/ghost-jobs-survey-2026-bls-data-comparison/) · [67% of seekers affected](https://www.cpapracticeadvisor.com/2026/04/30/ghost-jobs-still-haunting-67-of-job-seekers-report-finds/182536/) · [How to spot ghost jobs](https://www.metaintro.com/blog/how-to-spot-ghost-job) · [Ghost job signals](https://jobstrack.io/blog/ghost-jobs-2026)
- [AI résumé statistics 2026](https://jobcannon.io/blog/ai-resume-statistics-2026)
- [Job application response rates 2026](https://www.loopcv.pro/guides/job-application-response-rate/) · [Follow-up timing and templates](https://resumeoptimizerpro.com/blog/how-to-follow-up-on-a-job-application) · [Follow-up email effectiveness](https://careerbldr.com/blog/follow-up-email-after-application/)
- [COMPASS points calculator and criteria](https://rafflescorporateservices.com/compass-points-calculator-singapore-employment-pass-2026/) · [COMPASS 40-point framework](https://singaporeemploymentagency.com/compass-framework-40-points-singapore-ep-2026/) · [MOM COMPASS booklet (PDF)](https://www.mom.gov.sg/-/media/mom/documents/work-passes-and-permits/compass/compass-booklet.pdf) · [COMPASS scoring for employers](https://www.corestaff.com.sg/compass-scoring-explained-singapore-employers/)
- [Asynchronous video interview platforms 2026](https://www.jobma.com/blog/best-asynchronous-video-interview-platforms/) · [HireVue candidate experience](https://www.willo.video/blog/hirevue-candidate-experience-review) · [HireVue interview guide](https://www.careertestprep.com/knowledge/hirevue-interview)
- [STAR method guide](https://blog.theinterviewguys.com/the-star-method/) · [STAR interview method and story banks](https://resources.biginterview.com/behavioral-interviews/star-interview-method/)
