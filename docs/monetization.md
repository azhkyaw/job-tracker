# Monetization and Product Strategy

**Author:** AZ
**Status:** Superseded by `docs/open-source.md` (26 Jul 2026) — kept as
reasoning history, not a plan. §2's compliance analysis is what made the
open-source argument concrete and remains valid if the direction ever reverses.
**Date:** 26 July 2026
**Scope:** What it would take to charge for this system, and what to build if
we do. Supersedes design doc §12 Phase 4 ("Productization"), which assumed
productization was purely an engineering exercise. It is not.

---

## 1. Summary

The design doc's Phase 4 says multi-tenancy needs "no architectural change —
that was the point of §5." That is true of the *code* and false of the
*product*. Three findings change the plan:

1. **`gmail.readonly` is a Google *restricted* scope.** Unverified apps are
   capped at 100 users. Going past that requires annual third-party security
   assessment (CASA) costing roughly $540–$8,000/year depending on tier. This
   is a recurring cost floor that must be cleared before user 101, and it
   makes the retention question (design doc §14 #4) a compliance gate rather
   than a cleanup task.
2. **The design doc's central bet is no longer differentiated.** "Email is the
   source of truth for status" now ships commercially (JobFlow AI: Gmail /
   Outlook / iCloud / IMAP OAuth, auto-detected confirmations, rejections and
   interview invites, free unlimited tracking). Tracking itself is
   commoditized to free across the whole category.
3. **The product succeeds by becoming unnecessary.** A job search is a 3–6
   month event, so monthly subscription LTV is capped at a few months and
   churn is guaranteed, not a leak to be fixed. Pricing must match that shape.

What remains defensible is **SEA visa intelligence** (§6, wedge 1), which no
incumbent touches, and **candidate-side verified outcome data** (§6, wedge 2),
which nobody else can produce. The recommendation (§12) is to ship a 90-day
pass on the visa wedge, put the free tier on a forwarding-ingest path that
avoids OAuth entirely, and fund the CASA assessment out of revenue rather than
before it.

## 2. The Gmail restricted-scope constraint

This is the load-bearing section. Everything downstream is shaped by it.

`gmail.readonly` — the scope `pipeline/gmail_oauth.py` requests, chosen in
design doc §6.2 specifically because it is read-only and therefore assumed
low-friction — is classified **restricted**, the highest tier, alongside
`gmail.modify` and `https://mail.google.com/`. Only `gmail.send` is merely
*sensitive*. Read-only does not mean low-privilege in Google's taxonomy;
breadth of data access does.

| Requirement | Detail |
|---|---|
| Unverified user cap | **100 users worldwide.** Below that, exempt from verification. Above it, unverified apps accessing Gmail data are restricted. |
| Brand verification | 2–3 business days |
| Data-access verification | Required for restricted scopes; "potentially several weeks" |
| Security assessment (CASA) | Required, **annually re-certified**, if data is accessed from or through a third-party server — which describes this system exactly |
| Reported cost | Tier 2 ≈ $540–$1,500/yr; Tier 3 (full penetration test) ≈ $4,500–$8,000. Free Tier 2 self-scan has been withdrawn. |

**Tier assignment is unconfirmed for our scope set.** Sources conflict: one
reports `mail.google.com` as the Tier 3 trigger with `gmail.readonly` landing
at Tier 2; another reports any restricted scope triggering Tier 3. Confirm
with Google during verification and budget for the range. Historical context
for scale: the legacy manually-driven assessment for this scope class cost
$15,000–$75,000, so the current path is cheap by comparison — but it is
recurring, and it lands before the product has revenue.

### 2.1 The escape hatch: forwarding ingest

A unique per-user inbound address, plus a Gmail filter the user creates
themselves to auto-forward job mail to it, delivers the same emails into the
same pipeline with **no OAuth, no CASA, no user cap** — and it works for
Outlook, iCloud and corporate mail, which the OAuth path does not. A
competitor (JobShinobi) already ships this as its primary ingest.

Architecturally this is a third source alongside the extension and Gmail sync,
and it changes nothing downstream: it terminates in the same
`matcher.dispatch` call `gmail_sync.py` already makes. Trade-offs against
OAuth: no historical backfill (forwarding is prospective only, so the
strongest first-run moment — §9.6 — is lost on this tier), user-visible setup
friction, and forwarded mail arrives with envelope headers rewritten, which
needs handling in the classifier.

That backfill asymmetry is a feature for pricing, not a defect: it puts the
single most compelling capability on the paid side for a reason a user can
understand without being told about compliance tiers.

### 2.2 Consequences for the plan

- **Free tier = forwarding ingest. Paid tier = OAuth auto-sync.** Compliance
  cost then falls only on revenue-generating users, and the 100-user OAuth
  ceiling becomes a *paid-user* ceiling — which is a milestone worth reaching,
  not a growth cap.
- **Design doc §14 open question #4 (email body retention) is now a gate.**
  Google's restricted-scope limited-use policy constrains retention, onward
  transfer, and mandates deletion on request. Raw `emails.body_text` stored
  indefinitely and unencrypted (`migrations/001_init.sql:86-87`) is a
  verification finding waiting to happen. Decide before any billing work.
- **LLM subprocessing must be documented.** Sending email bodies to Anthropic
  is onward transfer of restricted-scope data. Defensible as service-provider
  processing, but it must be disclosed in the privacy policy and stated
  accurately in the verification submission.

## 3. Competitive landscape

| Product | Free tier | Paid | Notes |
|---|---|---|---|
| **JobFlow AI** | unlimited tracking, 1 inbox, hourly sync | $12.99/mo · **$32.99 / 90 days** | Closest competitor. Gmail/Outlook/iCloud/IMAP OAuth, AES-256-GCM token storage, UK GDPR framing |
| Teal | unlimited tracking | $9/wk · $29/mo · $179/yr | $19M raised; repositioned from "job search" to "career growth" |
| Huntr | 40–100 jobs | ~$10–40/mo | Positions on lower friction than Teal |
| Careerflow | 10 tracked jobs | $23.99/mo · $172.99/yr; Plus $44.99/mo | Resume/ATS/LinkedIn optimization bundle |
| Simplify | autofill + tracking free | limits/analytics paid | Autofill across 100+ portals is the wedge |
| OSS / Apps Script parsers | free | — | CareerSync and others parse Gmail directly; a Chrome extension does it locally |

Reading of this table: **nobody charges for tracking, and the one company with
$19M in funding cannot either.** Free tiers are generous precisely because
tracking has no defensible margin. Any plan that prices the dashboard loses.

Also relevant: job seekers reportedly will not pay for job *access* (viewed as
a free commodity) but will pay for a demonstrable edge with a clear ROI. The
paywall must sit on edge, not on storage.

## 4. The churn shape

The honest framing is that this product's job ends when it works. Retention is
not a fixable leak; it is the success condition. Two observed industry
responses:

- **Match the shape.** JobFlow's $32.99 / 90-day pass. Priced as an event, not
  a subscription. No cancellation moment, no resentment, and it aligns with how
  users actually experience the need.
- **Change the product.** Teal's move to "career growth" positioning. This
  works only if the post-hire product is real; otherwise it is retention
  theatre that inflates churn metrics without inflating revenue.

**Take the pass.** Proposed pricing — a proposal, not research, and untuned in
the same spirit as the thresholds in `pipeline/config.py`:

| Plan | Price | Contents |
|---|---|---|
| Free | — | Forwarding ingest, manual entry, extension capture, ~25 applications, full event log and dashboard |
| Job Search Pass | **SGD 39 / 90 days** (≈ US$29) | Gmail OAuth + backfill, unlimited applications, visa scoring, prep packs, benchmarks |
| Monthly | SGD 15/mo | Same as pass, for users who prefer to drip |

Anchored just under JobFlow's $32.99. The critical structural consequence:
**LTV is capped at roughly one pass, so CAC must be near zero.** That makes
distribution the binding constraint, not features — which is the entire
argument for wedge 2 below.

## 5. What not to build

- **Resume builder / ATS scoring.** Jobscan and Careerflow own this; it is
  fully commoditized and we would be the fifth-best option.
- **Application autofill.** Simplify gives it away free, and it would add a
  large adapter-maintenance burden on top of three adapters we already cannot
  verify (CLAUDE.md, "Known-untested surfaces"). Note it does *not* violate
  invariant #1 — autofill is user-initiated and writes only to forms the user
  opened — but it is a red ocean.
- **Job discovery / recommendations.** Already a non-goal (design doc §3), and
  it would put us in direct competition with the platforms whose ToS we are
  deliberately staying clear of.

## 6. Differentiation: three wedges, ranked

### Wedge 1 — SEA visa intelligence (the actual moat)

Every incumbent is US-centric. None model COMPASS. `jd_extraction.py` already
extracts `visa_signal`, and 2026 is an unusually good year to arrive:

- From 1 Jan 2026 MOM reset the 65th-percentile salary benchmarks across every
  sector (average **+5.1%**) and updated the recognised-qualifications lists.
- The Shortage Occupation List changed: **Cyber Risk Specialist,
  Cybersecurity Operations Specialist and Product Manager (Digital) were
  removed**; two healthcare occupations added.
- Renewals fall under the new rules from 1 Jul 2026.
- Malaysia doubled Employment Pass salary requirements from 1 Jun 2026, with a
  1:3 local-internship requirement for some firms.

The unbuilt product: **a per-application COMPASS estimate.** C1 (salary vs.
sector benchmark) and C2 (qualifications) are computable from data we already
capture. C3 and C4 — employer nationality diversity and support for locals —
are the two an individual applicant *cannot* self-assess, and are exactly what
aggregate data across users could infer. "Which employers actually clear
C3/C4" has no substitute on the market.

Audience: SG/MY expat professionals — high willingness to pay, acute problem,
and a decision (invest prep time or not) that the answer directly changes.
This is also the audience the author belongs to, which is worth something.

Cost: requires real domain research and a maintained benchmark dataset that
changes at least annually. That is the price of a moat.

### Wedge 2 — verified outcome data as the distribution engine

Every existing employer-responsiveness dataset is either survey-based (The
Interview Guys' Ghosting Index synthesizes ~50 studies) or recruiter-side
(Pin's index, derived from ATS-side conversations). Neither observes what
actually happened to a candidate.

This system does: append-only events with real-world `occurred_at`, derived
from employer emails rather than self-report. That yields true
`applied → first_response` intervals per employer and per ATS — the only
trustworthy answer to "who actually replies, and how fast."

Two uses:

- **Externally:** a free SEA employer response-rate index. This is the
  near-zero-CAC distribution channel §4 requires.
- **Internally:** benchmark comparison in `/analytics` ("12% response vs. 19%
  median for SG backend roles"), which is the one retention hook that survives
  the user getting hired, because it is interesting before they start too.

**Hard constraint:** requires explicit opt-in aggregation and k-anonymity
thresholds, and Google's restricted-scope terms limit what may be done with
Gmail-derived data. Safest construction is to build the aggregate only from
forwarding-tier and explicitly opted-in users, and to get the construction
reviewed before publishing anything. Do not treat this as a data-mining
opportunity on the paid tier's inbox access.

### Wedge 3 — the JD archive

Postings vanish within weeks (design doc §2); we keep the text. Quiet, but it
is the precondition for prep packs and comp negotiation, and no competitor
relying on live posting URLs can match it retroactively.

## 7. The monetization trigger nobody else has

The event log already knows the exact moment willingness to pay spikes.

An `interview_invite` event is the highest-intent second in the entire customer
lifecycle, and `gmail_sync.py` detects it within 15 minutes (NFR2). A
`rejected` event is the *lowest*-intent moment and should trigger nothing at
all. No competitor built on manual entry can time an offer this precisely,
because they do not know the interview happened until the user tells them.

Monetize the invite, not the dashboard. This is the single highest-leverage
consequence of invariant #2 (status as an append-only event log), and it was
not a design goal — it falls out for free.

## 8. Unit economics

Design doc NFR3 set an LLM ceiling of "a few dollars/month at single-user
volume," and classification is Haiku-class on allowlist-filtered mail, so the
existing pipeline is comfortably cheap. Two new cost drivers:

- **Prep packs and cover letters** are Sonnet-class and user-initiated. At a
  SGD 39 / 90-day price point with Stripe SG fees (~3.4% + SGD 0.50) and
  hosting, per-user LLM spend needs to stay well under ~US$5 across the whole
  90 days. Rate-limit generation per pass; do not offer unlimited.
- **Backfill** is a burst of classification at signup, bounded by the window.
  The `-d N` finding (`docs/worklog.md`, task #1) matters here: defaulting to
  12 months when the user started searching two weeks ago burns tokens for
  nothing. Ask for the start date during onboarding.

**Measure both against a real pass before launch.** These are estimates, and
this project's track record with untuned first-draft numbers is explicit.

## 9. Feature roadmap

Ordered by leverage, with dependencies respected.

1. **Retention/deletion policy** (design doc §14 #4). Compliance gate for
   everything else, and it interacts with whether wedge 2 is legally clean.
2. **Billing plumbing.** Stripe, plus `plans`/`subscriptions` tables following
   the invariant-#6 checklist (`user_id`, GRANT, RLS, `tenant_isolation`) and
   plan gating in `web.py`. Nothing in the current schema knows what a plan
   is. Note invariant #8: new numbered migrations, and the filename must be
   added in all four hardcoded places.
3. **Forwarding ingest** (§2.1). Unlocks the free tier, non-Gmail users, and
   the compliance dodge. Terminates in the existing `matcher.dispatch`.
4. **Interview prep pack**, triggered on `interview_invite` (§7). Likely
   questions derived from the archived JD, company/role brief, and the user's
   own application timeline as recall. This is the paywall.
5. **COMPASS score per application** + employer visa track record (wedge 1).
6. **Onboarding = the backfill.** The strongest moment this system has is
   "here are the 40 applications you forgot about, reconstructed from your
   inbox," and it currently sits behind a CLI command. It should be the
   first-run experience, with the search start date asked up front (§8).
7. **Actionable follow-ups.** `analytics.reminders()` already finds the
   applications; have it draft the email rather than just listing them.
8. **Benchmark comparison** in `/analytics`, once wedge 2 has consented data.

## 10. Legal and compliance

- **Google restricted-scope limited use:** retention limits, no advertising
  use, constrained human review, deletion on request, annual re-certification.
  Drives §9.1.
- **Singapore PDPA:** applies once there are users other than the author.
  Requires a named DPO, breach notification, and access/correction handling.
- **LLM subprocessing:** disclose Anthropic as a processor; state it
  accurately in the OAuth verification submission (§2.2).
- **Extension store listing:** `extension/PRIVACY.md` already contains the
  store-listing checklist and needs public hosting. The no-scrape invariant
  (#1) is a genuine asset in review — the permission set is narrow and every
  permission has a one-line justification.
- **Invariant #1 is a marketing asset, not just a constraint.** "No stored
  platform credentials, ever" is a credible differentiator against anything
  that automates a logged-in session, and it is why this can be listed in a
  store at all.

## 11. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| CASA lands at Tier 3 (~$5–8k/yr) | Medium | Confirm tier before committing; forwarding tier (§2.1) serves free users without OAuth, so the assessment is deferrable until paid users justify it |
| Competitor (JobFlow et al.) adds SEA visa features | Low–Medium | Their audience is US/UK; visa modelling is unglamorous, jurisdiction-specific, and needs annual maintenance — a genuine moat because it is tedious |
| Pass model produces too little revenue to fund compliance | Medium | Gate OAuth behind the pass so cost tracks revenue; 100 paying users at SGD 39 covers Tier 2 with margin |
| Wedge 2 aggregate is not legally defensible | Medium | Build from opted-in/forwarding users only; get review before publishing; the internal benchmark works with less data than the public index needs |
| Adapter drift breaks capture for paying users | High (eventually certain) | Already mitigated by design (§13 of design doc): loud failure, degrade to email-only path. Becomes a support burden, not a data-loss event |
| Onboarding fails to reach the backfill "wow" | High | §9.6 makes it the first-run flow; treat time-to-first-reconstructed-application as the activation metric |
| MOM changes COMPASS rules and the scoring goes stale | Certain (annual) | Benchmarks in versioned config, not code; the Jan 2026 reset is the template for what maintenance looks like |

## 12. Recommendation and sequencing

Ship the SEA visa wedge on a 90-day pass. Put the free tier on forwarding
ingest only. Treat the verified-outcomes index as the marketing budget rather
than a product line. Stay under 100 OAuth users deliberately until the pass
converts, then fund CASA Tier 2 out of revenue.

Sequence: §9.1 and §9.2 (retention, billing) → §9.3 (forwarding) → §9.4 (prep
packs, the first thing worth paying for) → §9.5–6 (visa scoring, onboarding) →
§9.7–8 (follow-ups, benchmarks).

Wedge 1 before wedge 2. They are sequential, not parallel: visa intelligence
differentiates immediately and needs no user base, while the outcomes index
needs consented data that only accumulates after there are users.

## 13. Open questions

1. **SaaS or open source?** Still undecided, and this document assumes SaaS
   throughout. An OSS release inverts §2 entirely: self-hosters register their
   own Google Cloud project, so each deployment is its own sub-100-user app
   and CASA never applies. That is a genuinely attractive way out of the
   compliance problem — at the cost of every revenue model here. A hybrid
   (OSS core, hosted paid tier) keeps both but doubles the support surface.
2. **Email body retention** (design doc §14 #4). Drop bodies after extraction
   and re-fetch on demand, or keep them encrypted with a TTL? Now blocking
   (§2.2, §9.1).
3. **Is the author the target user, or a sample of one?** Wedge 1 assumes the
   SG/MY expat professional segment is large enough and reachable. Untested.
   The response-rate data from the author's own search is the first evidence
   either way.
4. **Does the free tier need the extension?** Giving away capture strengthens
   the funnel and the wedge-2 dataset, but the JD archive is arguably the
   thing worth charging for.
5. **Pricing currency and rails.** SGD works for SG/MY; Indonesia and the
   Philippines need local payment methods, which Stripe covers unevenly.
   Defer until there is demand outside SG/MY.

## 14. What to revisit

Re-check the CASA tier and price annually — this landscape moved from $15k–75k
to sub-$1k in a few years and will move again. Re-check whether tracking is
still commoditized; if incumbents start charging for it, the calculus in §3
changes. Re-run the competitive scan before building anything in §9.4–9.8,
since JobFlow shipped the core thesis of this project between the design doc
and this document.

## 15. Sources

Verified July 2026. Competitor pricing and third-party assessment costs change
frequently; re-check before acting on §4.

- [Gmail API scopes (scope classification)](https://developers.google.com/gmail/api/auth/scopes)
- [Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [When verification is not needed](https://support.google.com/cloud/answer/13464323)
- [Google CASA tiers and costs](https://deepstrike.io/blog/google-casa-security-assessment-2025)
- [CASA Tier 2/Tier 3 provider pricing](https://www.switchlabs.dev/post/casa-tier-2-tier-3-security-review-providers-pricing-and-the-cheapest-option)
- [JobFlow AI](https://jobflow-ai.com/)
- [Huntr vs Teal](https://huntr.co/blog/huntr-vs-teal) · [Careerflow review/pricing](https://www.remotejobassistant.com/blog/careerflow-review) · [Teal Series A](https://www.abc27.com/business/press-releases/cision/20250122LA01288/teal-announces-series-a-funding-to-expand-its-ai-powered-careers-platform-bringing-total-financing-raised-to-19-million/)
- [Why job seekers pay](https://www.jobboardsecrets.com/2025/12/22/the-price-of-an-edge-why-job-seekers-are-starting-to-pay-and-when-its-worth-it/)
- [COMPASS 2026 changes (KPMG)](https://kpmg.com/xx/en/our-insights/gms-flash-alert/flash-alert-2025-282.html) · [Updated qualification lists and SOL](https://www.humanresourcesonline.net/compass-updates-updated-qualification-lists-and-revised-shortage-occupation-list-to-take-effect-from-1-january-2026)
- [Pin Employer Ghosting Index](https://www.pin.com/blog/employer-ghosting-index/) · [The Interview Guys Ghosting Index](https://blog.theinterviewguys.com/the-2025-ghosting-index/)
