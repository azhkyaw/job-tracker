# Monetization, Re-examined

**Author:** AZ
**Status:** Draft v1 — research findings, not a commitment. Revisits
`docs/monetization.md` (26 Jul 2026, superseded) with current facts and with
measurements from the author's own install. It does not supersede
`docs/open-source.md`; it confirms that direction and says what would reopen
the question.
**Date:** 30 September 2026
**Scope:** Whether this system can earn money, by which routes, and what each
would take. Covers the author's legal ability to earn in Singapore, the
market, email-ingest compliance for a hosted product, the extension, measured
unit economics, open-source models and the indirect return.

**How to read the evidence.** Every claim carries a label:

- **[P]** primary: the vendor's, regulator's or court's own page, read at
  source in this pass
- **[S]** secondary: press, a law or corporate-services firm, a third-party
  estimate
- **[A]** anecdotal: a forum post or an unverifiable self-report
- **[M]** measured on this install (the dev database, or a figure already
  recorded in `docs/worklog.md`)
- **[E]** our estimate or arithmetic on sourced numbers

**Limits of this pass.** The session's web-search budget ran out part-way, so
later findings came from direct fetches of known pages rather than discovery.
Reddit, several salary sites and Singapore Statutes Online refused fetches.
§16 marks which sources were re-read for this document after the research
agents reported; §14 lists what could not be verified. One reported fact
failed that re-read and is recorded as unverified (§5.2). Nothing here is
legal advice; §3 says where a lawyer or MOM's written answer is needed.

---

## 1. Summary

**Can it be monetized? By someone, in two narrow ways. By its author, not
lawfully until one question about their own status is answered. As a tracker
sold to individuals, not worth doing at any time.**

`docs/monetization.md` shelved the SaaS path over Gmail compliance cost, a
commoditised market and guaranteed churn. Re-tested, the compliance cost
turned out to be the weakest of those reasons, and a stronger one was never
examined. The findings that decide it, in order of weight:

1. **The author may not be allowed to earn from it.** MOM: work pass holders
   "must not take on additional jobs or engage in activities to earn
   additional income in Singapore", and a person on the visit pass that
   follows a cancelled Employment Pass "cannot work" at all. The penalty
   includes a bar on working in Singapore. Donations and overseas customers
   are unresolved grey areas (§3).
2. **Trackers do not earn.** Stripe-verified pure trackers show US$0-25 a
   month. The money in this category is in resume builders and live
   interview assistants. The one tracker with real revenue took eight years
   and six people, and got there by selling to institutions (§4).
3. **The feature this system does best is now free elsewhere.** JobFlow's
   free tier includes inbox sync with automatic status detection and
   unlimited tracking (§5).
4. **Compliance is no longer the blocker.** The Gmail security assessment is
   listed at US$675 a year at its lower level, and forwarding ingest needs
   none (§6).
5. **A user costs more than July assumed.** The author's own search cost
   US$11-19 in model calls over 11 weeks, against a US$33 pass, and cost
   rises with exactly the usage that makes the product valuable (§8).
6. **The SEA visa wedge has no data source.** Every visa-intelligence product
   found runs on government data that Singapore does not publish (§9.3).
7. **Charging changes the extension's position.** LinkedIn's terms forbid
   monetising "the Services or related data" without consent. Every major
   tracker is on LinkedIn's probe list and none was found to have been acted
   against; this extension is not visible to that probe (§7).
8. **Open-source income rounds to zero.** Half of all GitHub Sponsors listings
   have no sponsor; a job tool with 73,000 stars shows one (§10).
9. **The indirect return dwarfs all of it.** A week of median senior pay in
   Singapore is S$2,600-4,500. A verified tracker's lifetime revenue is
   US$170 (§11).

**Recommendation (§12):** do not charge; finish the open-source release;
settle the §3 question privately before any income of any kind, donations
included; write the release narrative around the sponsorship-screen finding;
keep sole copyright so a paid tier stays possible later. If the question is
ever reopened, the order is a cohort edition for career services, then a
hosted pass on forwarding ingest with the user's own model key.

## 2. What changed since July

Each claim `docs/monetization.md` rested on was re-tested, and one it never made was added.

| July's claim | September's finding | Effect |
|---|---|---|
| CASA costs US$540-8,000 a year and lands before revenue | One authorised lab lists AL1 at **US$675** and AL2 at US$5,400 a year [P]. Self-scan is gone, and Google still does not publish which level `gmail.readonly` gets [P] | Compliance is a cost, no longer the blocker (§6) |
| Tracking is free everywhere; the paywall must sit on edge | Still true, and it moved against us: JobFlow's FREE tier now includes inbox sync, auto-detected applications, interviews and rejections, unlimited tracking and ghosting detection [P] | The thing this system does best is a competitor's free tier (§5) |
| LTV is one 90-day pass, so CAC must be near zero | Confirmed by the one tracker with real revenue: its founder reports seekers stay about four months and calls B2B "a much more stable source of income" [P, self-reported] | Unchanged (§4) |
| LLM cost "well under ~US$5" per 90 days | Measured: **US$11-19 over 11 weeks** for the author's own search on the current model mix [M/E] | The heaviest users are the least profitable (§8) |
| "No incumbent touches SEA visa intelligence" | Not refuted, on thin coverage. But the reason is structural: Singapore publishes no per-employer sponsorship data at all [P] | The wedge has no data source except its own users (§9.3) |
| (not examined) | The author's own right to earn from a side business in Singapore | The finding that decides the rest (§3) |

Four things the July doc could not know, because they were built since:

- **IMAP ingest** (28 Jul) removed OAuth for self-hosters. It does not
  transfer to a hosted product (§6.3).
- **Screening-answer capture** (28 Jul) and **ATS capture** (24-30 Sep): no
  competitor was found recording a per-question answer history (§5.3).
- **Outcome analytics on real data** (25 Sep): reply survival curves, and the
  finding that LinkedIn's must-have sponsorship question, not the JD, predicts
  the automatic rejection.
- **The model door** (`pipeline/llm.py`, 8 Sep): any stage can run on an
  open-weight model or a user's own key, which is what lets a hosted tier's
  LLM cost go to zero (§8.3).

## 3. The constraint that comes first: the right to earn in Singapore

Neither July document asked this, and it outranks every market question. The
author is a foreign professional in Singapore who needs an employer-sponsored
pass. Singapore ties the right to earn to the pass.

### 3.1 What the Ministry of Manpower says

> "All work pass holders must only work for their designated employer. They
> must not take on additional jobs or engage in activities to earn additional
> income in Singapore." — MOM FAQ, updated 14 Mar 2024 [P]

| Status | May the holder run a side business or earn from one? | Source |
|---|---|---|
| Employment Pass | **No**, per the sentence above. May own shares. A directorship needs a Letter of Consent, which MOM "will generally grant" only where the second company "is related by shareholding to the Employment Pass holder's employer" and the role is "for purposes related to their primary employment" | MOM [P] |
| S Pass | No. "Cannot be a director or sole-proprietor of a business" | MOM, read by the research pass [P] |
| Short-Term Visit Pass after an EP is cancelled (up to 90 days) | **No work at all**: "Your pass holder cannot work, even while waiting to leave Singapore." | MOM [P] |
| Personalised EP | No. "You are not allowed to start a business or conduct any form of entrepreneurial activity while on a PEP." | MOM [P] |
| Dependant's Pass | Only with a Letter of Consent as a business owner, which carries a local-hiring condition. A separate MOM FAQ says no pass is needed when "working for or providing services to an overseas-based organisation or client" | MOM [P] |
| ONE Pass | **Yes**: "start, operate and work for multiple companies at any one time". Needs a fixed salary of S$30,000 a month | MOM [P] |
| EntrePass | Yes, it is the purpose. The company must be "venture-backed or owns innovative technologies" and meet one of: S$100,000 raised in a single round, a recognised incubator, a prior exit, registered IP, a research collaboration | MOM [P] |
| Permanent resident or citizen | No MOM restriction | |

Penalties: "fined up to $20,000, or jailed up to two years, or both", and
"barred from working in Singapore" [P]. The same 2019 MOM release records a
foreigner who held a clerical job and was fined S$6,000 "for being a
self-employed foreigner without a valid work pass" over a side business [P].
So the rule has been applied to an employed pass holder's sideline, not only
to people with no pass.

### 3.2 What it means here

- **On an Employment Pass**, a hosted subscription, a paid licence or
  consulting is an activity "to earn additional income". Holding shares in a
  company someone else runs is allowed; building, supporting and billing the
  product is not passive.
- **Between jobs on a visit pass**, no work is permitted.
- **A self-sponsored EP through one's own company** needs the company to pay
  its founder at least the qualifying salary: S$5,600 a month now, S$6,000
  from 1 Jan 2027, more with age [P], so S$67,200 a year at the floor before
  a nominee director and secretary (S$2,200-4,200 a year, one source [S]). A
  firm with fewer than 25 PMETs scores "10 points by default" on each of
  COMPASS's two firm-level criteria, leaving 20 to earn from salary and
  qualifications [P]. Against the base rates in §4, that is a business that
  must already be in the top few percent of subscription products before it
  may lawfully employ its founder.
- **Open-sourcing the code earns nothing**, so none of this touches the
  release in `docs/open-source.md`.

### 3.3 What is unresolved

These need MOM's written answer or a Singapore employment lawyer. No source
found settles them.

1. Whether subscription or licence income from customers outside Singapore
   counts as earning "in Singapore" for an EP holder. The overseas-client
   carve-out above sits in a Dependant's Pass FAQ; whether it extends to work
   pass holders is unstated.
2. Whether donations (GitHub Sponsors, Open Collective) are income from an
   activity. No MOM or IRAS text addresses them.
3. Whether a foreign entity (a US LLC, Estonian e-Residency) changes
   anything. The statute's definition reportedly turns on activity in
   Singapore, not on where the entity is registered; the text could not be
   read at source [S]. IRAS separately treats a company as tax-resident where
   it "is controlled and managed" [P].
4. Whether a new employer's contract would claim or restrict a pre-existing
   project. Not researched to a usable standard; see §12.2.

**Which row applies to the author is not recorded in this document, and it
decides everything in §9.** Until it is a row that says yes, the honest
answer to "can this be monetized" is: possibly by someone, not lawfully by
its author, now.

## 4. Who actually makes money in job-search tools

July argued from pricing pages. This pass looked for revenue.

### 4.1 Revenue by sub-category

| Product | What it sells | Revenue | Team | Label |
|---|---|---|---|---|
| Rezi | AI resume builder | US$250,573 MRR, 10,401 subscriptions, US$10.2M all-time | 16-30 | [P] Stripe-verified listing |
| Interview copilot (unnamed marketplace listing) | Live interview assistant, about US$100 a month | US$504,800 trailing twelve months | 1, part-time | [P] seller-reported |
| Huntr | Tracker, then B2B to bootcamps and universities, then an AI resume suite | "Approaching $1 million" ARR, after about eight years | 6 | [P] payment-processor case study |
| JobLogr | Tracker, resume and cover-letter suite | 64 paying of 6,900 registered; asking price US$14,999 | small | [P] seller-reported, figures inconsistent |
| AppTrack | Tracker plus a US$9 AI coach | **US$25 MRR, US$170 all-time**, 3 subscriptions, 15 months old | 1 | [P] Stripe-verified |
| JobTrackfy | Tracker | No active subscriptions | 1 | [P] Stripe-verified |

The pattern is consistent. Money sits in **resume builders** and **live
interview assistants**. The only tracker with meaningful revenue reached it by
adding B2B and a resume builder, with six people over eight years. Verified
pure trackers earn nothing.

### 4.2 Base rates

- Free-to-paid conversion in this category is about **1%**: 0.93% at JobLogr
  [E on P], about 1% at Rezi [E on P]. The same 1.0% holds for a solo
  open-source SaaS outside the category (§10).
- Across subscription apps generally, the median earns about **US$72 a month**
  one year after launch; 17.3% reach US$1,000 MRR within two years and 4.6%
  reach US$10,000. Freemium converts 2.1% of downloads by day 35, and India
  and Southeast Asia convert at **0.7%** against North America's 2.8% [P,
  RevenueCat 2026].
- Monetised Chrome extensions: 0.8% free-to-paid across five extensions [A],
  0.9% in a second report [A]. At US$5-10 a month that needs roughly 10,000
  to 25,000 active users for US$1,000 MRR [E].
- A seeker is active for about four months [P, founder's statement]. A resume
  builder with a cheap trial reports a subscriber LTV of US$67 [P,
  self-reported].

### 4.3 What this says about a hosted pass

A pass at JobFlow's US$32.99 nets about US$30 after payment fees. To clear
US$1,000 a month it must sell about 33 passes a month, and because nobody
renews, that means about 3,300 new sign-ups every month at 1%. None of the
zero-cost channels found delivers that to a newcomer: a Web Store listing
alone produced 23 monthly users in 19 days [A], and the resume builder that
lives on search traffic built its audience for years before it had a product
[P, self-reported].

## 5. Competitive position, re-tested

### 5.1 Pricing, fetched 30 Sep 2026

| Product | Free tier | Paid | What is behind the paywall |
|---|---|---|---|
| **JobFlow AI** | One inbox, auto-detected applications, interviews and rejections, unlimited tracking, hourly sync, ghosting detection | US$12.99 a month, **US$32.99 per 90 days** | AI coach, interview prep, follow-up generation, advanced analytics, more inboxes, 15-minute sync [P] |
| Huntr | 100 tracked jobs | US$40 a month, US$90 a quarter | Unlimited tracking, "Advanced Job Search Metrics", AI resume tools [P] |
| Careerflow | Tracker "Included (Will be limited soon)" | US$23.99 a month; Plus US$44.99 | Resume and LinkedIn tools, mock interviews [P] |
| JobShinobi | None, 7-day trial | US$199.99 a year | Everything. Ingest is by forwarding mail to a unique address [P] |
| Teal | Tracking | about US$29 a month | Resume tools [S, pricing page refused the fetch] |
| Simplify | Autofill and tracking "free forever" | Simplify+, price not found | [S] |
| JobOps (open source, AGPL plus Commons Clause) | Self-host | **GBP 20 a month with your own API keys, GBP 30 with AI included** | The hosted service. It reads job mail and updates applications [P] |

Two readings. The closest competitor now gives away inbox sync, so automatic
status from email cannot be the paid feature. And an open-source tracker with
mail reading and a bring-your-own-key hosted tier, the exact shape this
project would take, is already on sale; its revenue is unpublished.

### 5.2 The platforms

- LinkedIn tracks Easy Apply applications natively with Applied, Viewed,
  Interviewed and Not Selected states; external applications are added by
  hand [S, Feb 2025]. A reported "Job Tracker launched March 2026" did **not**
  survive a re-read of its source and is unverified.
- Indeed announced Career Scout on 10 Sep 2025, which can "organize every
  step in one place" [P].
- OpenAI's Jobs Platform, announced for mid-2026, had no public beta by
  August [S].
- Nothing job-specific was found in Gmail's AI inbox [S].
- Employers are pushing back on automation: Greenhouse added bot and
  mass-application detection in 2025 and identity verification in 2026 [P].
  This system's no-automation invariant is on the right side of that.

### 5.3 What is still not found elsewhere

On the pages read, and absence there is not proof of absence:

1. **A per-question history of screening answers across applications.** The
   nearest thing is autofill memory.
2. **Reply-time distributions from the user's own verified history.**
   Competitors sell "advanced analytics" without showing a survival curve.
   That Huntr puts analytics behind US$40 a month is the one sign that
   analysis is what a tracker can charge for.
3. **Visa signals outside the US.** Only H-1B filters were found; the
   dedicated search for UK, EU and Singapore tools never ran.

All three are real. None is evidence of demand. Each pays off only for a
heavy searcher with enough of their own history to analyse, a small share of
an audience that converts at 1%.

## 6. Email ingest for a hosted product

`docs/monetization.md` §2 called this the load-bearing section. It is now
cheaper than it looked and murkier than it looked.

### 6.1 Gmail OAuth

- `gmail.readonly` is still **restricted**; so is `gmail.metadata`. The
  add-on scopes are only sensitive, but they reach just the message the user
  has open [P]. No non-restricted scope reads mail unattended.
- The cap is "100 new users in total" for an unverified app [P].
- The trigger is broad: "If you store restricted scope data on servers (or
  transmit), then you must go through a security assessment" [P], repeated
  "at least every 12 months" [P].
- **The on-device exemption has gone from the documents.** Neither the scopes
  page nor the restricted-scope page now carries the sentence exempting local
  client applications [P]. Developers asking whether a client that parses
  mail locally and sends only derived results needs the assessment have no
  staff answer [A]. So an extension-side or desktop design is not a
  documented way out.
- Price: one authorised lab lists AL1 at US$675 per application and AL2 at
  US$5,400 a year [P]. Google assigns the level from data sensitivity, user
  numbers and "internal risk indicators" and does not publish which one
  `gmail.readonly` gets [P]. Google itself charges nothing [P].
- Limited Use: the requirements "apply to the raw data obtained from the
  scopes and data aggregated, anonymized, or derived from them" [P].
  Transfers are allowed only for user-facing features with consent, for
  security, by law, or in a merger; sales to "data brokers, or any
  information resellers" are prohibited [P]. The Workspace policy now names
  generative-AI summaries as an approved use, requires prompt-injection
  protection, and bars using the data to train a model beyond the user's own
  [P]. Sending a body to an LLM API for that user's own classification rests
  on the consented-transfer clause; it is not named.

### 6.2 Forwarding

A Gmail filter forwards job mail to a per-user inbound address. No OAuth, no
assessment, no cap.

- Each forwarding address must click a verification link Gmail sends to it
  [P], so the service has to surface or follow that link per user.
- JobShinobi ships exactly this and charges for it (§5.1).
- Inbound infrastructure is cheap: Cloudflare Email Routing is unlimited on
  Workers' free plan, Amazon SES costs US$0.10 per 1,000 messages [P].
- No backfill, which is this system's strongest first-run moment. Whether a
  forwarded message keeps its original sender and date was not verified, and
  the matcher depends on both.

### 6.3 IMAP with an app password

The self-hosted default does not carry over. A hosted service would hold a
credential that reads, deletes and sends from the whole mailbox. App
passwords are "not recommended", unavailable to Workspace and Advanced
Protection accounts, and revoked when the user changes their password [P].
No Google text permits or forbids a third party collecting them [P, an
absence]. The nearest competitor uses OAuth for Gmail and keeps IMAP for
other providers [P].

### 6.4 Microsoft

Delegated `Mail.Read` needs no admin consent for personal accounts, and
publisher verification is free [P]. New work tenants default to a policy that
excludes mail scopes from user consent [P], so Microsoft 365 users need an
administrator. No mandatory paid assessment was found.

### 6.5 Reading

For a hosted tier the order is forwarding first, OAuth once revenue covers
the assessment, IMAP never. That is July's conclusion with a smaller number
attached. Compliance stopped being the reason not to do this.

## 7. The extension: what charging changes

### 7.1 Detection

- LinkedIn probes for extensions by fetching `chrome-extension://<id>/<file>`,
  which succeeds only when an extension exposes files through
  `web_accessible_resources` [P, Chrome docs; S for LinkedIn's code]. Counts
  reported: about 461 IDs in 2024, 6,222 in March 2026 [S].
- **Every large job tracker is on the list.** The published extraction of
  2,953 IDs contains Teal, Huntr, Simplify, Careerflow, Jobscan and
  LazyApply, checked directly [P-derived]. This project's unpacked ID is not
  on it.
- **This extension is not visible to that probe.** `extension/manifest.json`
  declares no `web_accessible_resources` and no `externally_connectable` [M].
  It does add one element to the page when it captures
  (`shared/capture.js`, a closed shadow root for the receipt) [M].
- No warning or restriction tied to a job-tracker extension was found.
  LinkedIn says it uses the data "to determine which extensions violate our
  terms" [S].
- Two US class actions over the scanning were dismissed on 8 Sep 2026 for
  lack of standing; one is on appeal, and a further suit was filed on 14 Sep
  [P, dockets].

### 7.2 The terms

LinkedIn's User Agreement (effective 3 Nov 2025), section 8.2 [P]. Members
agree not to:

- "Develop, support or use software, devices, scripts, robots or any other
  means or processes (such as crawlers, browser plugins and add-ons or any
  other technology) to scrape or copy the Services, including profiles and
  other data from the Services"
- "Rent, lease, loan, trade, sell/re-sell or otherwise monetize the Services
  or related data or access to the same, without LinkedIn's consent"
- "Overlay or otherwise modify the Services or their appearance (such as by
  inserting elements into the Services ...)"

Our reading, not a holding. "Copy" has no volume or automation qualifier, so
capturing a posting's text is inside the first clause on its face, for the
developer and the user alike, whether or not money changes hands. The second
clause is the only one that turns on money, and **a paid product whose value
includes stored LinkedIn posting text falls inside it in a way the free
self-hosted tool does not.** The receipt popover touches the third. The
user's own form answers are the user's content; the posting is not.

SEEK (JobStreet) prohibits "any unauthorised commercial use" and any
"automated process, script, tool" that copies information, and states it uses
device fingerprinting [P]. Indeed prohibits using the site "for your own
commercial gain" [P].

### 7.3 Enforcement

| Target | What it did | Outcome | Label |
|---|---|---|---|
| Proxycurl | Paid scraping API, fake accounts, about US$10M revenue | Judgment Jul 2025, deletion injunction, shut down | [P] |
| ProAPIs | Paid scraping API | Consent judgment 16 Sep 2026 | [P] |
| Kleo | In-page analytics extension, about 70,000 users, reportedly free | Cease-and-desist, extension pulled | [S] |
| Browserflow | Paid automation extension | Cease-and-desist over features "marketed, or intended for automating activity on LinkedIn's website" | [A] |
| Teal, Huntr, Simplify, Careerflow, Jobscan | Freemium trackers and autofill that name or run on LinkedIn, all on the probe list | **Nothing found**, under a capped search | |

No source shows LinkedIn's posture changing because a tool charged. The
stated triggers are scraping at scale, automation, fake accounts and
trademark use. The risk to a read-only tracker looks lower than July's
reading, and the commercial trackers have operated for years with six-figure
installs. Two things still hold: the letter to a solo developer was about
marketing surface, and the author's own account is load-bearing during a live
search. `docs/open-source.md` §3.1 stands, with one addition from §7.2:
charging adds the monetize clause to the two a free tool already touches.

### 7.4 The store

Payment must be sold directly, with the seller identified [P]. Chrome's own
Limited Use policy forbids transferring or selling user data to "data
brokers, or other information resellers" [P]. Windows and macOS users "can
only install self-hosted extensions through enterprise policies" [P], so a
paid consumer extension has to be listed, and a listing mints the stable
public ID.

## 8. Unit economics, measured

July estimated. This is one real user: the author's search from 16 Jul to
30 Sep 2026, read from the dev database on 30 Sep [M].

### 8.1 Volume

| Measure | Count |
|---|---|
| Applications | 338 (309 applied, 29 inbound), 231 employers |
| Emails classified | 1,235, of which 559 job-related (`INGEST_ALL` is on) |
| JDs extracted | 298 |
| Rejection emails through the reason stage | 50 |
| Screening answers captured | 1,632 rows, 362 distinct questions |
| Events by source | 546 email, 260 extension, 78 manual |

About 4.4 applications a day. That is a heavy search, and a hosted product
has to price for it.

### 8.2 Cost per call

| Stage | Model | Per call | Basis |
|---|---|---|---|
| Classify | Sonnet 5, thinking on | US$0.006-0.012 | Lower bound from token arithmetic at today's list price [E]; upper bound is the figure recorded beside `CLASSIFY_MODEL` [M] |
| Classify | Haiku 4.5 | US$0.0035 | Recorded beside `CLASSIFY_MODEL` [M] |
| Extract | Haiku 4.5 | about US$0.002 | [E] |
| Rejection reason | Sonnet 5 | US$0.0027 | US$0.25 for 92 calls, worklog task 35 [M] |
| JD extraction | Sonnet 5, `medium` | US$0.0068 (US$0.0034 batched) | US$0.90 for 264 JDs through the Batch API, worklog task 33 [M] |

List prices on 25 Sep 2026: Sonnet 5 at US$2 and US$10 per million tokens in
and out, Haiku 4.5 at US$1 and US$5, batch at half price.

### 8.3 Cost of one search

| Mix | Over the 11 weeks | Per application |
|---|---|---|
| As configured (Sonnet 5 classify, reason and JD; Haiku extract) | **US$11-19** | US$0.03-0.055 |
| Haiku everywhere | about US$6 | about US$0.02 |
| The user's own key, or an open-weight model | US$0 to the operator | |

Three things follow.

1. **Cost scales with applications and price does not.** Against a US$32.99
   pass netting about US$30, this one search leaves US$11-19. The heaviest
   searchers, who want the product most, are the least profitable.
2. **The cheap mix is measurably worse.** Haiku misfiled an ATS
   account-activation mail three runs in three, and scored 88.8% on the JD
   gold set with ten false visa signals against Sonnet 5's 98%
   (`docs/jd-extraction-models.md`). The models were chosen for one person's
   accuracy. A margin would push the other way.
3. **`INGEST_ALL` doubles the classify bill.** 676 of 1,235 calls were mail
   that is not job-related. A forwarding filter or the keyword pre-filter
   removes most of them, at the cost of missing some job mail.

`pipeline/llm.py` already routes any stage by model name, so a bring-your-own-
key tier is configuration, not new work. JobOps prices exactly that split
(§5.1).

### 8.4 Fixed costs of a hosted tier

Payment: Stripe Singapore takes 3.4% plus S$0.50 on a domestic card, plus
0.5% for an international card and 2% for conversion; a merchant of record
(Polar, Lemon Squeezy) takes 5% plus US$0.50 and handles foreign sales tax
[P]. Assessment: US$675 a year if AL1 (§6.1), about 22 passes. Singapore's
PDPA applies to an individual running a paid service: a Data Protection
Officer, breach notification within three days of assessing a notifiable
breach, and transfer obligations for data hosted abroad or sent to a foreign
LLM API [P, read by the research pass]. GST registration starts at S$1
million of turnover [P].

What is not built: billing and plans, password reset by email, account
deletion and export, rate limits, abuse handling, and a privacy policy that
discloses the LLM processor. Sign-up, sessions, per-user tokens and
row-level security exist.

## 9. The routes, one by one

| Route | Who pays | Evidence that it earns | Verdict |
|---|---|---|---|
| 9.1 Hosted pass for individuals | Job seeker | Verified pure trackers: US$0-25 MRR | Possible, low expected value |
| 9.2 Paid extension | Job seeker | About 1% conversion; needs a store listing | No |
| 9.3 SEA visa intelligence | Seeker or employer | Seeker-paid tools on public data earn US$13 MRR in the UK; employer-paid boards earn well | Not as a product; yes as the story |
| 9.4 Cohort edition for career services | Institution | The one tracker with revenue got it here | Highest ceiling, a different job |
| 9.5 Selling data | Data buyer | Given away free by incumbents; barred by Google's terms | Closed |
| 9.6 Services and referrals | Seeker, or a partner | Works at scale with an audience | Not at one user |
| 9.7 Open-source income | Sponsors, hosted users | Median is zero (§10) | Negligible |
| 9.8 The indirect return | An employer | One week of senior pay exceeds a verified tracker's lifetime revenue (§11) | **The only one that pays now** |

### 9.1 Hosted pass for individuals

The July plan. Its price anchor survives and its premise does not: inbox
sync, the feature the pass was to sell, is free at JobFlow (§5.1). What could
still sit behind a paywall is analysis (reply curves, the answer bank, visa
evidence), which pays off only for heavy searchers, whose LLM cost is highest
(§8.3). At 1% conversion, US$1,000 a month needs about 3,300 new sign-ups a
month (§4.3), in a region that converts at a quarter of the North American
rate (§4.2). The honest expectation is the base rate: the median subscription
app earns about US$72 a month after a year.

### 9.2 Paid extension

About 1% of users pay (§4.2). A paid consumer extension must be listed in the
Web Store (§7.4), which is the step `docs/open-source.md` §3.1 defers until
the search ends, and charging adds the monetize clause (§7.2).

### 9.3 SEA visa intelligence

July called this the moat. The research gives the reason nobody has built it.

- **The products that exist run on public data.** The US publishes employer-
  level visa filings and approvals; the UK publishes its register of licensed
  sponsors as a file, updated several times a week [P]. Australia passed a law
  on 8 Apr 2026 enabling a public register of approved sponsors [S].
- **Singapore publishes nothing per employer.** MOM's statistics are national
  stock by pass type, and it treats firm-level COMPASS data as sensitive:
  "only users who are granted EP eService access can view these information"
  [P]. A Singapore product can only be crowd-sourced, which needs the users
  first.
- **Seeker-paid works only with public data and venture money.** One US
  product claims 10,000 paying subscribers and US$1M ARR in six months [S,
  investor's post; the figures do not square with its price]. A UK tool on
  the sponsor register shows US$13 MRR from about 11,000 users [P].
- **Employer-paid works bootstrapped.** A two-person visa-friendly job board
  in Japan charged a success fee per hire and earned US$62,197 in July 2022
  [P, founder]. That is a recruitment business, not a tracker, and whether it
  needs an employment-agency licence in Singapore was not researched.
- **The market is small and flat.** 204,300 Employment Pass holders in June
  2026, against 205,400 in December 2023 [P]; roughly 41,000 in ICT if the
  2021 share still holds [E]. In Singapore the employer buys visa help (one
  firm charges S$1,000 per EP application [P]); a candidate cannot apply.
- **COMPASS calculators are free** and exist as lead generation for
  immigration firms [P]. MOM's enhanced self-assessment tool is for employers
  and agents [P].

What this system does have is a finding nobody else has published: LinkedIn's
must-have sponsorship question closes an application automatically, and of the 12
such rejections measured on 25 Sep only 4 had a JD that said so. LinkedIn's help page
confirms the mechanism, "automatically archive candidates who don't pass your
screening questions and send an automatic rejection email" [P], and gives no
timing. That is worth a great deal as the release narrative
(`docs/open-source.md` §6.3) and nothing as a subscription.

### 9.4 Cohort edition for career services

The only route where the core design is the product.

- The tracker with revenue sells to "universities, academies, and job
  programs" in more than 30 countries and claims 70 bootcamps [P]. Its
  founder's reason: individuals leave after four months, institutions stay.
- Price points found: about US$8,000 a year for a university's job platform
  [S]; US$13,250 a year for one US university's campus-wide resume tool [P];
  US$1,500-4,000 per displaced employee in outplacement [S].
- The fit is specific. Bootcamps reporting under CIRR publish placement
  outcomes that approved auditors review annually against student records,
  surveys and employer confirmations [P]. An event log built from the
  employer's own emails is that evidence by construction; a self-reported
  kanban board is not.
- Singapore: public career services are delivered by two appointed providers
  [P]; government purchases up to S$6,000 can be made directly and up to
  S$90,000 by quotation [P].

What it takes: organisation accounts, advisor dashboards, cohort reporting,
and a consent model, because Google's terms bar humans from reading
Gmail-derived data without the user's "affirmative agreement to view specific
messages" unless it is aggregated [P]. Then data-processing agreements,
security questionnaires and a sales cycle, against incumbents with
references. It is a company and a sales job, and §3 applies to it in full.

### 9.5 Selling data

Closed, for three independent reasons.

- Candidate-side outcome data is already free. One tracker publishes
  quarterly reports from 1.7 million tracked applications, another from a
  thousand email-tracked seekers, an ATS vendor from 54 million applications
  [P]. All of it is marketing for software.
- Buyers want scale this cannot reach: quant funds look for broad coverage of
  listed companies and five years of history [S]; one labour dataset lists at
  US$85,000 a year [P].
- Gmail-derived data cannot be sold even aggregated or anonymised (§6.1).

### 9.6 Services and referrals

The pattern that works elsewhere is free data plus a paid service at the
moment money is on the table: one salary site sells negotiation coaching at
US$1,250-5,000 with a guaranteed increase [P]. Affiliate rates run 15-45% on
courses and 20% recurring on interview prep [P]; agency referral rewards in
Singapore are vouchers of S$200-400 [P]. All need an audience first, and all
are "activities to earn additional income" (§3).

### 9.7 Open-source income

§10. In short: nothing to plan around.

### 9.8 The indirect return

§11. The project already pays, in the search it was built for.

## 10. Open-source monetization models

`docs/open-source.md` chose profile over revenue without pricing the revenue.
This prices it.

### 10.1 What each model pays a solo maintainer

| Model | Best evidence found | Reading for this project |
|---|---|---|
| Paid hosted cloud | A one-person uptime monitor reports US$24,000 MRR from 1,120 paying accounts among about 113,000, a 1.0% conversion [P, self-reported] | The only model with a real income for one person, and its customers are businesses with a permanent need |
| Donations | Of 41,007 maintainers listed on GitHub Sponsors, half have no sponsor at all, and the top 1% have 19 [S, third-party aggregate]. Median yearly budget across a sample of 1,500 Open Collective projects: US$10 [E on P data]. A personal-finance app with 24,800 stars shows US$156.70 a month [P] | Hobby money at any star count |
| Hosting-marketplace share | One host shares 20% with authors; its largest payout is about US$1,300 a month to one project, the next about US$39 [P] | Needs a one-container app; this is Postgres plus a worker plus an extension |
| Supporter licence, nothing paywalled | One photo app sells US$24.99 and US$99.99 licences; its team is salaried by a funder and publishes no sales [P] | Unproven without a funder |
| Open core or dual licence | Used by team-run projects; no solo B2C figures found | Not applicable at this size |

Three comparables are close enough to be warnings.

- **A personal-finance app** raised money, open-sourced, tried a hosted paid
  version, and ended on 24 Jul 2025 with a pivot to B2B. Its release notes
  name a cost specific to sensitive data: "Many users are not comfortable
  sharing their personal financial situation", so bugs could not be
  reproduced [P]. Job-search mail has the same property.
- **A personal CRM** with 25,400 stars sells hosting at US$90 a year and,
  ten years in, calls itself "still a side project"; both maintainers hold
  full-time jobs [P].
- **Open-source job tools.** JobSync (1,342 stars) has no sponsors and a
  waitlist for an unlaunched hosted version. JobOps (about 4,000 stars) sells
  the hosted tier of §5.1 and publishes no revenue. One local tool that runs
  inside AI coding assistants reached 73,000 stars within months of its April
  2026 release, states it is "NOT a hosted service", and shows a single
  individual sponsor [P]. Stars at that scale did not turn into income. They
  did turn into reach, which is the currency §11 is about.

### 10.2 Licence, with a paid tier as a future option

- A sole author may relicense: "You can add or change to whatever license you
  or your company wants to" [P]. Versions already released stay under the
  licence they shipped with.
- The move from permissive to AGPL has precedent once a hosted business
  exists: one analytics company did it in 2020 after its code was resold, one
  photo app in 2024 [P].
- With outside contributors it takes their agreement. For a proprietary
  version of a copyleft project, "you'll need every contributor to assign
  copyright to you or grant you ... a permissive license" [P].
- One large employer's policy bars any use of AGPL code [P], the clearest
  sign of how a corporate legal team reads it.
- Fair-source and business-source licences are not Free Software and would
  put the project on awesome-selfhosted's non-free page. That list also
  requires a first release more than four months old and a submission
  written by a person: "Machine/LLM-generated contributions are not allowed"
  [P].

So `docs/open-source.md` §7 stands: **Apache-2.0**. The option worth keeping
is sole copyright. If outside contributions arrive, a contributor agreement
is what keeps relicensing possible, and it costs nothing to decide that
before the first pull request.

### 10.3 The cost of hosting

The uptime monitor's own page: "the ops team consists of a single person" and
multi-day outages are possible [P]. The personal CRM's founder describes
side-project burnout [P]. A host takes on updates, backups, monitoring and
security patches for strangers' job-search mail, under the PDPA (§8.4).

## 11. The indirect return, quantified

`docs/open-source.md` asserted profile value. These are the numbers beside it.

- **What a week is worth.** Median total compensation for a senior software
  engineer in Singapore is S$177,623 a year (25th percentile S$133,000, 75th
  S$235,000; 402 self-submitted records that skew to large technology firms)
  [P]. That is S$2,600-4,500 a week across the middle half [E].
- **What a tracker earns.** US$170 all-time for a verified pure tracker after
  fifteen months; US$14,999 asked for a three-year-old tracker suite with
  6,900 registered users (§4.1).
- So a project that shortens the author's search by **one week** returns more
  than the first earns in its life, and by six weeks about what the second
  is asking.

Whether a project shortens a search is less certain than that arithmetic, and
the evidence is mixed:

- A six-year study of one open-source community found merit-based rank
  associated with about an 18% wage increase [P].
- A study of about 7,000 German IT employees found no wage premium for
  open-source engagement [P].
- A 2018 vendor survey of 39,441 developers has hiring managers ranking
  experience first and a portfolio or GitHub second, at 66-80% [P].
- No study was found measuring one project's effect on time-to-hire for a
  senior engineer.

What the category itself shows is reach. An open-source job-search tool
framed as its author's own search reached 73,000 stars in five months (§10.1).
This project has the same frame with harder evidence behind it: 338
applications with verified outcomes, reply curves from real mail, and the
sponsorship-screen finding.

On the demand side, UK contract data puts "AI Engineer" at a median of GBP 600
a day, up 14.3% in a year, with LLM skills named in 2.01% of contract adverts
against 1.06% a year earlier [P]. Singapore day rates were not found, and
contracting is work under §3.

## 12. Recommendation and sequencing

**Do not charge for this now. Finish the open-source release, and keep the
paid options open at no cost.** Three reasons, in order of weight:

1. **It may be unlawful for its author** (§3), and the penalty is a bar on
   working in Singapore, which is the thing the search is for.
2. **The market pays for something else** (§4). Trackers earn nothing; the
   feature this system does best is a competitor's free tier (§5.1).
3. **The indirect return is larger by orders of magnitude** (§11), and it is
   the direction already chosen.

### 12.1 What to do now, in order

1. **Settle §3 for the author's own status**, privately. One written question
   to MOM, or an hour with an employment lawyer, covering subscription income,
   donations and a foreign entity. Until it is answered, do not enable GitHub
   Sponsors or any donation link on the repository: they are the one way
   income could start by accident.
2. **Close the release checklist** (`docs/open-source.md` §11): licence,
   extension split, the CLAUDE.md decision.
3. **Write the narrative around the findings** (`docs/open-source.md` §6.3),
   led by the sponsorship screen. §9.3 says that finding has no substitute,
   and §10.1 says reach is what this category gives.
4. **Decide the contributor-agreement question** before the first outside
   pull request (§10.2).
5. **Keep the extension unlisted** (§7). The reasoning in
   `docs/open-source.md` §3.1 stands, a little weaker on detection and a
   little stronger on terms.

### 12.2 Before signing an employment contract

Not researched to a usable standard, so this is prudence, not a finding.
Singapore copyright gives an employer what is made "during the course of
employment" [S]. A public repository with a licence and a dated history,
published before a contract is signed, is the cleanest record that the
project pre-dates the job. Read the offer's moonlighting and IP-assignment
clauses against it and ask for a written carve-out if they are broad.

### 12.3 What would reopen this

| Trigger | What it unlocks |
|---|---|
| A status that permits it (§3.1: permanent residence, a ONE Pass, leaving Singapore), or MOM's written answer on §3.3 | Any paid route |
| Inbound demand after release: institutions asking, or users asking for hosting | §9.4, then §9.1 |
| Singapore publishing per-employer sponsorship data, as Australia is about to | §9.3 as a product |
| A tracker being found to charge for tracking itself | Re-read §4 |

### 12.4 If it is reopened, the order

1. **Cohort edition for career services** (§9.4), if the author wants to run a
   company. It is the only route where verified events are the product.
2. **Hosted pass on forwarding ingest with the user's own model key**
   (§6.2, §8.3). Zero LLM cost to the operator, no assessment, no cap. Expect
   the base rate (§4.2).
3. **Employer-paid visa-friendly board** (§9.3). Proven elsewhere, a different
   business, licensing unresearched.

Not at any point: selling data (§9.5), a paid extension (§9.2), planning
around donations (§10.1).

## 13. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Income starts before §3 is settled (a sponsor button, a paid pilot, a consulting favour) | Medium, by accident | §12.1 item 1; no donation links until answered |
| §3.3's grey areas are read generously and MOM reads them otherwise | Unknown | Written confirmation, not inference from an FAQ filed under another pass |
| A new employer's contract restricts or claims the project | Medium | Publish first; read the clauses; ask for a carve-out (§12.2) |
| LinkedIn acts on the extension | Low for a read-only, unlisted tool; nothing found against any tracker | Keep it unlisted and out of the pitch; charging is what adds the monetize clause (§7.2) |
| A platform ships this natively | Already partly true for Easy Apply (§5.2) | Differentiate on what platforms will not show: cross-platform history, the answer bank, outcome analysis |
| The competitor with free inbox sync adds the analysis too | Medium | None needed for an open-source release; decisive for §9.1 |
| Google's assurance level for `gmail.readonly` lands at AL2 | Unknown | Forwarding first (§6.5) |
| A hosted tier's best customers cost the most (§8.3) | Certain | The user's own key, or metering |
| Sensitive data makes support impossible (§10.1) | High for any hosted tier | The seeded demo dataset doubles as a reproduction fixture |
| This document goes stale | Certain | §15 |

## 14. Open questions

1. **Which row of §3.1 is the author's, and what does MOM say to §3.3?**
   Everything else waits on it.
2. **Does the author want to run a company?** §9.4 is the only route with a
   ceiling, and it is sales work. If not, the question is closed whatever
   the law says.
3. **Does a forwarded Gmail message keep its original sender and date?** The
   matcher needs both. One filter and one test message would answer it.
4. **Which assurance level does Google assign `gmail.readonly`?** Only the
   verification process answers.
5. **Is the visa claim in §5.3 true?** The search for non-US visa tools never
   ran. One afternoon on the UK, Australian and Gulf markets would say.
6. **Does an employer-paid board need an employment-agency licence in
   Singapore?** Not researched.
7. **Would anyone pay for the analysis?** Untested. The seeded demo is the
   cheapest experiment: show reply curves and the answer bank to ten heavy
   searchers and ask.

Could not be verified in this pass: revenue for Teal, Simplify, Careerflow,
Jobscan, JobOps and the hosted open-source comparables; any institutional
price list for career-services software; Singapore contractor day rates; the
Employment of Foreign Manpower Act's definitions at source; whether a
LinkedIn "Job Tracker" launched in March 2026; community reports of account
action tied to a tracker extension; how common moonlighting and IP clauses
are in Singapore contracts.

## 15. What to revisit

Re-read §3 whenever the author's status changes, and before any income of
any kind. Re-run §4 and §5.1 before building anything in §12.4: JobFlow moved
its core feature into the free tier between July and September, and the
category will move again. Re-check §6.1 annually; the assessment has gone
from five figures to three in a few years and the on-device exemption
disappeared without an announcement. Re-measure §8 after any model or prompt
change, from the database, not from this table.

## 16. Sources

Fetched 30 Sep 2026. **✓** marks a page re-read for this document after the
research agents reported; the rest were read by an agent and not re-read.
Prices and policies move; re-check before acting.

**Singapore: passes, law, data**

- ✓ [Work pass holders and additional income (MOM FAQ)](https://www.mom.gov.sg/faq/work-pass-general/can-a-work-pass-holder-work-in-multiple-jobs)
- ✓ [EP secondary directorship](https://www.mom.gov.sg/passes-and-permits/employment-pass/taking-up-secondary-directorship) · ✓ [Cancelling an EP](https://www.mom.gov.sg/passes-and-permits/employment-pass/cancel-a-pass) · ✓ [EP eligibility](https://www.mom.gov.sg/passes-and-permits/employment-pass/eligibility)
- ✓ [Self-employed or overseas employer (Dependant's Pass FAQ)](https://www.mom.gov.sg/faq/dependants-pass/do-i-need-a-work-pass-if-i-am-self-employed-or-working-for-an-overseas-based-employer)
- ✓ [PEP eligibility](https://www.mom.gov.sg/passes-and-permits/personalised-employment-pass/eligibility) · ✓ [ONE Pass key facts](https://www.mom.gov.sg/passes-and-permits/overseas-networks-expertise-pass/key-facts) · ✓ [EntrePass eligibility](https://www.mom.gov.sg/passes-and-permits/entrepass/eligibility)
- ✓ [MOM press release, 27 Nov 2019 (self-employed foreigner)](https://www.mom.gov.sg/newsroom/press-releases/2019/1127-foreigner-fined-and-jailed-for-working-without-a-valid-work-pass)
- ✓ [Foreign workforce numbers](https://www.mom.gov.sg/documents-and-publications/foreign-workforce-numbers) · ✓ [Self-Assessment Tool](https://www.mom.gov.sg/eservices/services/employment-s-pass-self-assessment-tool)
- [S Pass and business ownership (MOM FAQ)](https://www.mom.gov.sg/faq/s-pass/can-i-start-my-own-business-if-i-am-on-s-pass) · [LOC for Dependant's Pass business owners](https://www.mom.gov.sg/passes-and-permits/loc-for-dependants-pass-business-owners/eligibility)
- [ACRA: local residency requirement](https://www.acra.gov.sg/how-to-guides/foreigners-registering-a-business-in-singapore/requirements-for-local-residency) · [IRAS: company tax residency](https://www.iras.gov.sg/taxes/corporate-income-tax/basics-of-corporate-income-tax/tax-residency-of-a-company) · [IRAS: GST registration](https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/gst-registration-deregistration/do-i-need-to-register-for-gst)
- [Self-sponsored EP costs (one firm)](https://growacross.com/insights/singapore-employment-pass-own-company) · [Copyright and employment](https://singaporelegaladvice.com/law-articles/copyright-law-in-singapore/)
- [GeBIZ procurement thresholds](https://www.gebiz.gov.sg/singapore-government-procurement-regime.html) · [Career-health factsheet, COS 2025](https://www.iac.gov.sg/-/media/mom/documents/budget2025/cos-2025-factsheet-on-career-health.pdf)
- [Australia's approved work sponsor register](https://www.workvisalawyers.com.au/news/all/the-new-approved-work-sponsor-register-2026-what-employers-visa-applicants-must-know.html) ✓ · [UK register of licensed sponsors](https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers) · [USCIS H-1B employer data hub](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub)
- [PDPC advisory guidelines on key concepts, rev. 29 Apr 2026](https://www.pdpc.gov.sg/assets/34058be5-ae13-4c40-89e6-1c945d19f65c)

**Revenue and pricing**

- ✓ [Rezi](https://trustmrr.com/startup/rezi) · ✓ [AppTrack](https://trustmrr.com/startup/apptrack) · [JobTrackfy](https://trustmrr.com/startup/jobtrackfy) · ✓ [SponsoredJobs](https://trustmrr.com/startup/sponsoredjobs) (Stripe-verified listings)
- ✓ [Huntr, payment-processor case study](https://stripe.com/customers/huntr) · ✓ [Huntr pricing](https://huntr.co/pricing) · ✓ [Huntr research reports](https://huntr.co/research) · [Huntr for bootcamps](https://huntr.co/bootcamps)
- [Interview-copilot listing](https://app.acquire.com/public/ey71f43uzv-real-time-ai-interview-copilot-for-job-seekers) · [JobLogr listing](https://flippa.com/12626779) · [StandOut CV interview](https://getlatka.com/interviews/standout-cv-ltd-andrew-fennell-2025)
- ✓ [JobFlow AI pricing](https://jobflow-ai.com/pricing) · ✓ [Careerflow pricing](https://www.careerflow.ai/premium) · ✓ [JobShinobi](https://www.jobshinobi.com/) · ✓ [JobOps](https://jobops.app/) · [Teal pricing, third party](https://toolradar.com/tools/teal-hq/pricing)
- ✓ [RevenueCat, State of Subscription Apps 2026](https://www.revenuecat.com/state-of-subscription-apps/)
- [Extension monetisation after six months](https://dev.to/ktg0215/real-numbers-freemium-chrome-extension-monetization-after-6-months-5hga) · [A second extension report](https://www.indiehackers.com/post/ive-been-working-on-my-first-saas-chrome-extension-for-2-years-and-reached-36-mrr-de06d0000e) · [GhostJob at day 19](https://www.indiehackers.com/post/building-ghostjob-a-chrome-extension-to-detect-ghost-job-postings-day-19-23-mau-0-mrr-b6083b2db2)
- ✓ [Japan Dev, founder's account](https://japan-dev.com/blog/how-and-why-i-built-japan-dev) · ✓ [Migrate Mate, investor's post](https://redbud.vc/latest/why-we-invested-in-migrate-mate)
- ✓ [Levels.fyi, senior software engineer, Singapore](https://www.levels.fyi/t/software-engineer/levels/senior/locations/singapore) · ✓ [Levels.fyi services](https://www.levels.fyi/services/)
- [UK contract rates, AI Engineer](https://www.itjobswatch.co.uk/contracts/uk/ai%20engineer.do) · [UK contract rates, LLM](https://www.itjobswatch.co.uk/contracts/uk/llm.do)
- [Handshake and Symplicity pricing](https://sacra.com/research/handshake) · [One university's resume-tool request, FY24](https://www.uh.edu/sfac/unit-requests/fy24/additional/otr/fy24/ucs.pdf) · [Outplacement cost per employee](https://www.joinleland.com/library/a/outplacement-services-cost) · [CIRR, for schools](https://www.cirr.org/for-schools)
- [Revelio Labs dataset listing](https://aws.amazon.com/marketplace/pp/prodview-m7h5in35nozha) · [Selling data to quant funds](https://www.neudata.co/dp-tidbits/july-tidbits-how-to-sell-data-to-quant-funds) · [Ashby talent trends](https://www.ashbyhq.com/talent-trends-report) · [Careery response-time benchmarks](https://careery.pro/research/job-application-response-time-benchmarks-2025)
- [Coursera affiliates](https://www.coursera.org/about/affiliates) · [Agency referral reward, Singapore](https://www.morganmckinley.com/sg/reward-programme-terms-and-conditions)
- [Open-source rank and wages (2013)](https://doi.org/10.1287/isre.2013.0474) · [GitHub traces in hiring (2013)](https://doi.org/10.1145/2441776.2441794) · [HackerRank 2018 developer skills report](https://www.hackerrank.com/research/developer-skills/2018)

**Platforms**

- ✓ [LinkedIn screening questions (help)](https://www.linkedin.com/help/linkedin/answer/a519651) · ✓ [Tracking applications on LinkedIn, Feb 2025](https://scale.jobs/blog/how-to-track-job-applications-on-linkedin)
- [Indeed Career Scout announcement](https://www.indeed.com/news/releases/indeed-introduces-new-suite-of-hiring-products-career-scout-talent-scout-premium-sponsored-jobs-and-indeed-connect) · [OpenAI Jobs Platform status, Aug 2026](https://www.herohunt.ai/blog/openai-jobs-platform-2026-recruiter-playbook/) · [Greenhouse Real Talent](https://www.greenhouse.com/blog/introducing-greenhouse-real-talent)

**Email compliance**

- ✓ [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) · ✓ [Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) · [OAuth verification FAQ](https://support.google.com/cloud/answer/13463817)
- ✓ [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy) · [Workspace API user data policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)
- ✓ [CASA pricing, one authorised lab](http://casa.tacsecurity.com/site/home) · [CASA tiering](https://appdefensealliance.dev/casa/casa-tiering)
- [Gmail forwarding](https://support.google.com/mail/answer/10957) · [App passwords](https://support.google.com/accounts/answer/185833) · [Add-on scopes](https://developers.google.com/workspace/add-ons/concepts/workspace-scopes)
- [Unanswered: on-device parsing and the assessment](https://discuss.google.dev/t/398372)
- [Microsoft Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference) · [Publisher verification](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview) · [App consent policies](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-app-consent-policies)
- [Cloudflare Email Routing limits](https://developers.cloudflare.com/email-routing/limits) · [Amazon SES pricing](https://aws.amazon.com/ses/pricing)

**The extension**

- ✓ [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement) · [Prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387)
- ✓ [Extracted probe list, 2,953 IDs](https://raw.githubusercontent.com/mdp/linkedin-extension-fingerprinting/main/chrome_extensions_with_names_all.csv) · [BrowserGate: how it works](https://browsergate.eu/how-it-works/) · [SecurityWeek, 13 Apr 2026](https://www.securityweek.com/browsergate-claims-of-linkedin-spying-clash-with-security-research-findings/)
- [Chrome: web-accessible resources](https://developer.chrome.com/docs/extensions/reference/manifest/web-accessible-resources) · [Chrome: distributing outside the store](https://developer.chrome.com/docs/extensions/how-to/distribute) · [Web Store Limited Use](https://developer.chrome.com/docs/webstore/program-policies/limited-use)
- [Ganan v. LinkedIn, docket](https://www.courtlistener.com/docket/73154016/ganan-v-linkedin-corporation/) · [LinkedIn v. Nubela, docket](https://www.courtlistener.com/docket/69575588/linkedin-corporation-v-nubela-pte-ltd/) · [LinkedIn v. ProAPIs, docket](https://www.courtlistener.com/docket/71527603/linkedin-corporation-v-proapis-inc/)
- [Browserflow, developer's post](https://news.ycombinator.com/item?id=34583932) · [Kleo, secondary](https://reepl.io/blog/best-linkedin-chrome-extensions-2026) · [SEEK terms](https://au.seek.com/terms) · [Indeed terms](https://www.indeed.com/legal)

**Open source**

- ✓ [Healthchecks.io](https://healthchecks.io/about/) · ✓ [Firefly III on donations](https://docs.firefly-iii.org/explanation/more-information/donations/) · ✓ [Maybe, final release notes](https://github.com/maybe-finance/maybe/releases/tag/v0.6.0) · [Monica, ten years on](https://monicahq.com/en/blog/we-are-rebuilding-monica/)
- ✓ [career-ops](https://github.com/career-ops-hq/career-ops) · [JobSync](https://github.com/Gsync/jobsync) · [PikaPods payouts](https://opencollective.com/peakford) · [Immich licensing](https://v1.114.0.archive.immich.app/blog/2024/immich-licensing)
- [GitHub Sponsors aggregate](https://sponsors.ecosyste.ms/) · [Open Source Collective](https://opencollective.com/opensource) · [One maintainer's 2023 sponsorship income](https://old.lgug2z.com/articles/github-sponsorship-breakdown-for-2023/)
- ✓ [Open Source Guides: legal](https://opensource.guide/legal/) · [Plausible on its licence change](https://plausible.io/blog/open-source-licenses) · [Google's AGPL policy](https://opensource.google/documentation/reference/using/agpl-policy) · [awesome-selfhosted contributing rules](https://github.com/awesome-selfhosted/awesome-selfhosted-data/blob/master/CONTRIBUTING.md)
