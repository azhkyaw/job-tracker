# Employer Career Sites — Analysis and Plan

**Author:** AZ
**Status:** Accepted; being built. The four decisions in §14 were taken on
24 Sep 2026, all as recommended. Built the same day: the redaction in §11,
phase A and phase B (§12). Phase C is next.
**Date:** 24 September 2026
**Scope:** What it takes for the extension to capture a job, and the
application made for it, on an employer's own career site or its applicant
tracking system (ATS). Today it captures only on LinkedIn, JobStreet and
Indeed. The trigger was one Singapore employer's SAP SuccessFactors career
site (§2). Read this with `.claude/rules/extension.md`, whose history of
carrying job identity through a capture this builds on.

---

## 1. Summary

Supporting employer sites takes three separate pieces of work:

1. **Reading the job.** A generic reader for schema.org `JobPosting`, which
   most vendors emit because Google's job search requires it, plus three
   small vendor readers for the sites that emit none (§5).
2. **Knowing that the user applied.** Per-vendor submit hooks on the ATS
   forms, with a popup "Capture this job as applied" button as the floor on
   any page (§6).
3. **Carrying the job's identity from the listing to the form.** The form is
   often on a different site, behind a candidate sign-in, and on a page that
   names no job at all (§7).

The biggest measured payoff is not the case that prompted this. 51 of 283
applications went from LinkedIn to an employer's site. All 51 have a job
description, taken from LinkedIn's listing, but **none has a single screening
answer**, against 148 of 200 applications made on the platform. Their applied
time is the LinkedIn click, not the submit (§3). Hooks on the ATS forms that
*complete* those LinkedIn records fix both. The server already supports that
without changes (§8).

Two findings constrain the design:

- **A capture on an employer's site would create a duplicate** of the same
  job captured on LinkedIn. `ENRICH_JOB_SQL` never attaches a new posting to a
  job that already has a real one, so the two need an explicit link. Loosening
  that heuristic is the wrong fix (invariant #3; §10).
- **Singapore employer forms ask for NRIC, date of birth and race.** The
  answer sweep stored such values until 24 Sep 2026, when withholding them
  was built ahead of everything else (§11).

## 2. The case that prompted this

Verified live on 24 Sep 2026 in the browser. I did not sign in and submitted
nothing.

- **The site is SAP SuccessFactors under the employer's own domain.** The job
  page is `careers.<employer>.com/job/<Location-Title-slug>/<id>/`, a
  SuccessFactors Career Site Builder site. Its resources come from
  `rmkcdn.successfactors.com` and `performancemanager10.successfactors.com`,
  and elements carry `data-careersite-propertyid` attributes (`title`,
  `location`, `description`, `customfield1`, …). The hostname itself says
  nothing. `capture.js:detectAts()` matches on hostname suffixes, so it would
  return null here.
- **The job data is schema.org `JobPosting` as microdata, with no JSON-LD.**
  Four quirks:
  - `hiringOrganization` is a bare string,
    `<meta itemprop="hiringOrganization" content="<short brand>">`, not a
    nested Organization.
  - `datePosted` reads `"Thu Sep 24 00:00:00 UTC 2026"`, which is Java's
    `Date.toString()` format.
  - `validThrough` reads `"Tue Dec 01 16:00:00 UTC 2026"`: Singapore midnight
    on 2 Dec, stored as UTC.
  - The only address field is `streetAddress: "Singapore, SG"`.

  The description is about 6,500 characters, and `<link rel=canonical>` is
  present.
- **"Apply now" leaves for another site, in the same tab.** The button is
  `a.apply.dialogApplyBtn`, with href `/talentcommunity/apply/<id>/`.
  Clicking it navigates the tab to
  `career10.successfactors.com/careers?company=<tenant>`, a candidate sign-in
  page (username, password, "Create an account").
  - That page's URL carries only `company`, and neither its URL nor its body
    contains the listing's id.
  - A direct GET of the apply URL, without the listing page's session,
    redirects to the site root.
- **Not seen:** the application form after sign-in. Signing in is out of
  bounds, so whether that form shows the title or a requisition id is unknown.

This one site shows all three hard properties at once: an employer-branded
domain, a listing and a form on different sites, and the job's identity lost
at the handoff. §4 shows that none of them is unusual.

## 3. What the tracker does with these sites today

**Nothing, on the site itself.** The manifest injects content scripts only on
LinkedIn, JobStreet/SEEK and Indeed. Anywhere else, the popup's capture button
reports "This page isn't a supported job site."

Measured on the dev DB, 24 Sep 2026, over 283 applications:

| Population | Applications | Job description | Screening answers | Applied time recorded |
|---|---|---|---|---|
| LinkedIn "Apply on company website" (`payload.external = true`) | 51 | 51, all LinkedIn's copy | **0** | the LinkedIn click |
| Applied on the platform itself (Easy Apply, SEEK Quick Apply) | 200 | not measured | 148 | the submit |
| Made from an email, `platform = 'other'` | 11 | 0, and no URL | 0 | the email |

**The ATS is known for 35 of the 51 external applications:** Workday 8,
SuccessFactors 7, Greenhouse 6, Ashby 6, Workable 4, iCIMS 2, JazzHR 1,
Breezy 1.

**Job-related mail received, by vendor:** Ashby 20, Workable 18, Workday 12,
SuccessFactors 11, SmartRecruiters 6, Greenhouse 6, JazzHR 5, iCIMS 2. The
SuccessFactors count is low, because that vendor often sends from the
employer's own domain.

So no capture on a career site has ever happened. An application made
directly on an employer's site is known only from its mail, with no job
description to extract from. And the external path, the one the extension
does see, loses every screening answer.

## 4. How career sites expose a job: an 18-vendor survey

**Method.** Public job pages were fetched with curl on 24 Sep 2026 (plain
GETs, with no sign-in and no submission). Where the page was an empty
single-page-app shell, the vendor's public JavaScript bundle was read for the
code that builds its JSON-LD.

**Evidence tags:** **L** = seen in the live server HTML, **B** = read in the
bundle's code, **A** = the vendor's public JSON endpoint. The page URLs are
not reproduced here, because every one names an employer (the real-names
rule in CLAUDE.md).

### 4.1 Structured data on the job page

| Vendor | `JobPosting` in the server HTML | Shapes that matter |
|---|---|---|
| Greenhouse | **None** (L) | The data sits in the Remix loader state, `window.__remixContext`: title, `company_name`, HTML `content`, ISO `published_at`. Pay ranges are strings like `"$165,000"` even when the currency is SGD |
| Lever | JSON-LD on the job page, **none on `/apply`** (L) | Nested Organization. Address fields are null. `employmentType:"Full-time"` is not a schema.org value. No identifier, no salary |
| Ashby | JSON-LD in an empty shell, also on `/application` (L) | `identifier.value` is the posting's UUID. It has the only numeric `baseSalary` seen, with `unitText:"YEAR"` |
| Workday | JSON-LD in an empty shell, **only while the posting is live** (L) | `hiringOrganization.name` is the legal entity ("… Pte. Ltd."). `identifier.name` holds the job TITLE, and `value` is the requisition id. The description is plain text, still containing `&amp;` |
| SmartRecruiters | Microdata, no JSON-LD (L) | Dates and address are `<meta>` tags, one with no `content` attribute. The description is split across four itemprops |
| Workable | None in the server HTML. The bundle adds it on the client, and **deliberately skips it on `/apply`** (B) | `identifier.value` is the shortcode. Salary `minValue` is a string (made with `toFixed(2)`) |
| iCIMS | Not verified: blocked by an AWS WAF CAPTCHA | — |
| Taleo | Not verified: no live posting reached | — |
| Oracle Recruiting Cloud | None in the server HTML. The bundle adds it only if the tenant turned a setting on and the job is not confidential (B) | Dates are full ISO. `identifier.value` is the requisition id |
| SuccessFactors Career Site Builder | Microdata only (L) | See §2 |
| BambooHR | None in the server HTML (L) | The public `/careers/{id}/detail` JSON (A) has a date-only `datePosted` |
| Teamtailor | JSON-LD (L) | **The description is entity-encoded HTML, so it needs decoding twice.** `jobLocation` is an array |
| Recruitee | **None**, and none in the main bundle (L+B) | The data is entity-encoded JSON in `<div data-component="PublicApp" data-props>`. **No publish date anywhere** |
| Personio | JSON-LD on the job page, **none on `/apply`** (L) | The organization name has a trailing space. `employmentType` is an array |
| JazzHR | Two JSON-LD blocks, an Organization then the JobPosting (L) | Every address field is `""`. It uses a non-standard `uniqueJobCode` instead of `identifier` |
| Avature | **None** (L) | Server-rendered label/value pairs |
| Eightfold | None in the server HTML. Its config says `publishToGoogle:true`, so JSON-LD is probably added on the client (not verified) | — |
| Phenom | Three JSON-LD blocks, with the JobPosting between two WebPages (L) | Entity-encoded HTML description. Three different dates on one job |
| MyCareersFuture | None in the server HTML. The bundle adds it on the client (B) | `datePosted` is the REPOST date. The currency is hard-coded to SGD. The organization may be the agency that posted the job |
| Careers@Gov | JSON-LD (L) | `datePosted` is the last-activity time. The organization is the government agency |

### 4.2 Where the job id and the apply form live

| Vendor | Job id in the URL | Where the apply form is | Stable hooks seen |
|---|---|---|---|
| Greenhouse | `/{board}/jobs/{numeric}`; employer sites use `?gh_jid={id}` | Inline, `form#application-form`. Employer sites embed it via `/embed/job_app` in an iframe (the iframe is prior knowledge, not observed) | Input ids `question_{id}`, `#resume` |
| Lever | `/{co}/{UUID}` | Same host, `/{UUID}/apply` | `button#btn-submit[data-qa=btn-submit]` |
| Ashby | `/{org}/{UUID}` | Same host, `/{UUID}/application` (an SPA) | None in the server HTML |
| Workday | `…/{title-slug}_{REQID}`; the canonical URL differs in case and segments | Same host, `…/apply`, behind a **candidate account** (prior knowledge) | `data-automation-id` values in the bundle (`adventureButton` = Apply) |
| SmartRecruiters | `/{Co}/{numeric}-{slug}` | Same host, `oneclick-ui/…/publication/{UUID}`. **That UUID is not the job-URL id** | `a#st-apply`; on the apply page, `[data-test=topbar-job-title]` |
| Workable | `/{acct}/j/{shortcode}/` | Same host, `/j/{code}/apply/` | None |
| Oracle Recruiting Cloud | `…/job/{numeric}` | Same host, email verification, no password (B) | `data-qa=applyFlowPaginationNextButton` (B) |
| SuccessFactors Career Site Builder | `/job/{slug}/{numeric}/` | **Another host** (`career{N}.successfactors.com`), behind a sign-in (§2) | `a.apply.dialogApplyBtn` |
| Teamtailor | `/jobs/{numeric}-{slug}` | An overlay on the same page | `data-careersite--jobs--form-overlay-job-id-value` |
| Recruitee | `/o/{slug}`: **no id in the URL** | A tab on the same page, `form#offer-application-form` | `[data-testid=submit-application-form-button]` |
| Personio | `/job/{numeric}` | Same host, `/job/{id}/apply` | `button.career-submit-application-btn` |
| JazzHR | `/apply/{10-char}/{slug}` | Inline on the same page | `#resumator-submit-resume` |
| Avature | `/careers/JobDetail/{slug}/{numeric}` | Same host, `/careers/Login?jobId={id}`: a **sign-in** | Found only by `href` |
| Phenom | `/…/job/{REQID}/{slug}` | **Another vendor's host**: the page's state names a Workday `applyUrl` | None in the server HTML |
| MyCareersFuture | `…/{slug}-{32-hex}` | Same host, behind a **Singpass** login, or an external URL | `#job-details-apply-button` (B) |
| Careers@Gov | `/jobs/{source}/{id}` | **Another host**: the agency's own ATS, via a `<button>` with no href | None |

### 4.3 What a generic reader must tolerate

1. **Apply pages usually drop the data the listing had.** Lever, Personio and
   SmartRecruiters apply pages carry no JobPosting, and Workable suppresses
   it on `/apply` on purpose. Read the job on the LISTING and carry it
   forward: JobStreet's stash pattern.
2. **Both JSON-LD and microdata occur.** Microdata values live in
   `<meta content>`, in element text and in nested itemscopes, and some
   `<meta itemprop>` tags have no `content` at all.
3. **Descriptions come in three forms:** HTML; entity-encoded HTML (decode
   twice); and flattened plain text with entities still in it.
4. **Dates come in every format:** date-only strings; ISO with `Z`,
   milliseconds or an offset; `+0000` with no colon; and Java's
   `Date.toString()`, whose `validThrough` is local midnight stored as UTC,
   so a naive date reads one day early. Google defines `datePosted` as the
   ORIGINAL posting date, yet three of the sampled sites send a repost date
   or a last-activity time.
5. **`hiringOrganization` can be a nested object, a bare `<meta>` string, or
   a nested microdata scope.** Its name may be a legal entity, may carry a
   trailing space, or may be the agency rather than the employer.
6. **Pages carry several ld+json blocks, and `jobLocation` can be an
   array.** No sampled page used `@graph`, and `@context` came in four
   spellings; walk `@graph` anyway.
7. **`identifier` cannot be trusted.** Workday puts the title in it, JazzHR
   uses a non-standard key, and Lever has none. **The URL is the job id.**
8. **Location and employment type are unreliable.** Values seen include
   nulls, empty strings, a country name where an ISO code belongs,
   `addressRegion:"Asia"`, and a remote flag contradicting the vendor's own
   API.
9. **Salary is rare and loosely typed.** Numbers arrive as strings, and
   `"$"` appears beside `currency:"SGD"`.
10. **The site that shows the job is often not the one that takes the
    application** (SuccessFactors, Phenom, Careers@Gov). Four vendors put the
    form behind a sign-in.

## 5. Reading the job

- **One pure function, `readJobPosting(doc, loc)`, returning the shape
  `getJob()` already returns.**
  - JSON-LD first: every `ld+json` block, arrays, `@graph`, and all four
    `@context` spellings. Then microdata.
  - It takes its document as a parameter, as `linkedin.js:readJob(doc, loc)`
    does, so `tests/test_extension.js` can drive it from fixtures with no
    browser.
- **How the fields map:**
  - `title`.
  - `company` from `hiringOrganization.name`, or from the bare string.
  - `jd_text` from the decoded description with tags stripped.
  - `location` from the first `jobLocation`, dropping empty parts.
  - `salary_raw` as a display string rendered from `baseSalary`.
    `pipeline/salary.py` owns the parsing, and the period must survive the
    rendering: MONTH against YEAR is load-bearing in Southeast Asia
    (migration 011).
  - `platform_job_id` from the URL (§10), falling back to `identifier` only
    when the URL has no id.
- **Read at the click, not at injection.** JSON-LD that a client-rendered
  site adds (Workable, Oracle Recruiting Cloud, MyCareersFuture) may not
  exist yet at `document_idle`. It will by the time a person clicks Apply.
  This is the existing rule about snapshotting at the trigger event, applied
  to a new source.
- **Vendor readers for the three sites with no JobPosting:** Greenhouse
  (Remix loader state), Recruitee (`data-props`) and Avature (label/value
  pairs).
  - Content scripts run in an ISOLATED world and cannot read page globals
    such as `window.__remixContext`, `__appData` or `phApp`.
  - So parse the inline `<script>` text that assigns them, or register the
    reader with `world: "MAIN"` (Chrome 102+) and hand the result back
    through a DOM event.
  - Parsing the text keeps everything in one world. Use MAIN only if a vendor
    stops inlining its state.
- **Last resort:** `document.title`, `og:title`, `<h1>`. Record which source
  answered (`_prov.title_source`), which LinkedIn's history showed is the
  field that explains a bad title weeks later.
- **Fixtures.** A saved real page names its employer, so scrub it to the
  Northwind/Contoso family before it is tracked. `audit_names.py` cannot
  flag an employer that never reached the database, which is exactly the
  case for a page saved only as a fixture.

## 6. Knowing the user applied

Reading a job is solved by the web's own SEO. "Applied" is declared nowhere,
which makes this the hard part.

- **Per-vendor submit hooks exist and look stable where seen** (§4.2):
  - Lever: `button#btn-submit[data-qa=btn-submit]`
  - JazzHR: `#resumator-submit-resume`
  - Recruitee: `[data-testid=submit-application-form-button]`
  - Personio: `.career-submit-application-btn`
  - Greenhouse: `form#application-form`
  - Oracle Recruiting Cloud and Workday: `data-qa` and `data-automation-id`
    values

  **This is a list, but a bounded one.** The objection to the mail
  pre-filter's allowlists (`.claude/rules/mail-ingest.md`) was that
  employers are unbounded and each entry is added only after a loss. A
  vendor list is bounded, about 15 entries in practice, and changes on the
  vendor's release cycle, not the job market's.
- **Generic backstop: the form's `submit` event**, listened for in the
  capture phase on any form that contains a resume file input. For a native
  form, the event fires only once the browser's own validation has passed.
  A React form with `noValidate` weakens that guarantee, which is why the
  vendor hooks come first.
- **Write at submit, not on the "thank you" page.**
  - A submit that fails validation leaves a visible record that can be
    deleted.
  - A missed "thank you" page loses the application silently.
  - This project takes the recoverable failure every time (invariant #3; the
    popover history in `.claude/rules/extension.md`).
  - Retrying is already idempotent: `/captures` files one `applied` event
    per application, and `completed` only moves its time forward.
- **The floor, on any page:** the popup's "Capture this job as applied"
  button. It was offered with worklog task 20 and never built. It would use
  the existing `activeTab` permission plus a new `scripting` permission,
  with no standing access to any site.
- **Multi-step wizards (Workday) are already handled.** `shared/answers.js`
  accumulates each step in `sessionStorage`, keyed by `answerFormKey()`.
  Each vendor adapter must supply that key.

## 7. Carrying the job's identity from the listing to the form

| Shape | Vendors | Mechanism |
|---|---|---|
| Apply page on the same site, same tab | Lever, Ashby, Workable, Personio, Workday | The existing keyed stash (`background.js:stashPendingJob`), keyed on the id in the URL. **SmartRecruiters is the exception**: its apply-page UUID is not the job URL's id, so its key must come from the listing, which contains both |
| Different site, same tab | SuccessFactors → career{N}, Phenom → Workday, Careers@Gov → an agency's ATS | A **declared handoff** (below) |
| New tab | LinkedIn "Apply on company website"; any listing link with `target=_blank` | **`sender.tab.openerTabId`** (below) |

**The declared handoff.**

- **Why the current fallback is not enough.** The form page names no job
  (verified on SuccessFactors), so the keyed stash misses. The only fallback
  today is `takePendingJob`'s 30-minute same-tab *guess*.
  `.claude/rules/extension.md` says never to lengthen that window, and a
  sign-in plus account creation can outlast it.
- **So make it a fact.** When Apply is clicked on the listing, stash the job
  together with the host the apply control leads to (its `href`, or the
  vendor detected from the page's resources). The form page claims only a
  stash from its own tab that named its own host.
- **Where the form page shows a title, compare it with the stash**, the way
  `linkedin.js`'s stale-pane guard compares the pane against its card.
- **A handoff still counts as a keyed hit** in the merge order
  (`capture.js:withStashedJob`). The same-tab guess stays last and fills
  gaps only.

**The opener tab.**

- **A tab opened from another carries `openerTabId`.** Chrome restricts only
  a tab's `url`, `pendingUrl`, `title` and `favIconUrl` to the `tabs`
  permission, so the id is readable from `sender.tab` with the permissions
  already held.
- **The background can map the new tab back to the tab that opened it**,
  and to whatever that tab stashed or saved.
- **Unverified:** whether LinkedIn's external button (which resolves its
  destination on the server) actually sets `openerTabId`. Check it on the
  first real click.

## 8. The LinkedIn → employer path

This is the §3 gap, and the server can already close it:

1. On LinkedIn, the external click works as today, and the confirm popover
   stays. Additionally, stash the LinkedIn identity (platform, job id) under
   the LinkedIn tab.
2. In the new tab, the ATS adapter's submit reads the opener's stash and
   posts `/captures` with `platform='linkedin'`, LinkedIn's job id, the
   answers it swept, and `completed=true`.
3. `upsert_record` finds the posting by `(platform, platform_job_id)`, the
   answers attach, and `completed` moves the applied time to the real
   submit (`web.py:captures`).

**Order doesn't matter.** If the user never answers LinkedIn's popover, the
ATS submit creates the record. If they answer it later, `/captures` sees an
`applied` event and changes nothing.

**The popover stays because it is still the only copy on that path** whenever
the destination is a site with no adapter. The ATS side only upgrades a
capture; it never replaces the popover as the thing that saves.

A posting for the employer's own ad could hang off the same job as a second
posting (postings ≠ jobs, invariant #3). That needs one server change: an
explicit "attach to this job" parameter (§10), never a similarity match.

## 9. Permissions

- **Static content-script matches for the ATS hosts where forms live**
  (`greenhouse.io`, `lever.co`, `ashbyhq.com`, `myworkdayjobs.com`,
  `successfactors.com` / `.eu`, `smartrecruiters.com`, `workable.com`,
  `personio.com`/`.de`, `applytojob.com`, `recruitee.com`, `teamtailor.com`,
  …). These make the hooks automatic.
  - Adding hosts is a permission increase: a store-installed extension is
    disabled until the user re-approves it.
  - Unpacked, it is a reload, plus the existing rule to refresh tabs opened
    before the reload.
- **Employer-branded domains get per-site opt-in.**
  - The manifest already declares `optional_host_permissions: https://*/*`,
    and nothing uses it for this.
  - A popup "Enable on this site" button calls `chrome.permissions.request`
    for that origin (from the click, which counts as the required user
    gesture), then `chrome.scripting.registerContentScripts`. Registrations
    persist across sessions by default.
  - The list grows by the user's consent, with no code change per employer.
  - The vendor is identified by fingerprint, not hostname:
    `rmkcdn.successfactors.com`, `cdn.phenompeople.com`, `avacdn.net`,
    `staticfe.bamboohr.com`, `assets.cdn.personio.de`,
    `teamtailor-cdn.com`, and so on (§4).
- **Capture on any page** needs only `activeTab` plus `scripting` (§6).
- **Not recommended: running on every site.** A content script on
  `<all_urls>` contradicts `extension/PRIVACY.md` ("content scripts on the
  three job sites") and the account-risk posture of `docs/open-source.md`
  §3. That section's other advice, "describe capability, not platforms", is
  actually served by generic career-site support.
- **Invariant #1 is unchanged.** Capture happens on the user's click, on a
  page the user opened, with no automation, no background visits and no
  stored credentials. MyCareersFuture is a job board behind a Singpass
  login and falls under the same rule as the three platforms.

## 10. Server and data model

- **Two employers' job ids can collide.** `postings_platform_job_uidx` is
  `(user_id, platform, platform_job_id)`, and every career site would share
  `platform = 'other'`. Two employers whose ids are both `12345` would
  collide. `upsert_record` would find the other employer's posting and
  `COALESCE` the new capture's fields into it: silent, and wrong.
  - **Option (a):** namespace the id by host (`careers.<employer>.com/<id>`).
    No migration.
  - **Option (b):** add a platform value. That means a migration, the
    four-places rule in `.claude/rules/database.md`, and the platform list
    hand-copied in `web.py` at three sites (`/captures`,
    `/applications/new`, `/applications/{id}/edit`).
  - Either way, `postings.ats` records the vendor.
- **A capture on the employer's site duplicates a LinkedIn capture.**
  `ENRICH_JOB_SQL` attaches a new posting only to a job whose postings are
  all `email_only`, so a career-site capture of a job already captured on
  LinkedIn mints a second job. Embedding dedup has never run. The fix is the
  explicit link from §7–8, never a looser enrich rule: a wrong merge has no
  inverse (invariant #3).
- **Smaller follow-ons:**
  - `joburl.parse` must learn the ATS URL shapes in §4.2 so manual entry
    derives the same id the adapters do. It mirrors the adapters, which stay
    the source of truth.
  - `analytics.by_platform` would put every employer site under `other`. A
    split by ATS becomes possible once `ats` is set reliably.
  - Company names, which feed email matching:
    - A legal entity ("… Pte. Ltd.") is handled by `norm_company`.
    - A short brand inside a longer name is handled by the containment rule
      (`.claude/rules/matching.md`).
    - A short brand against a legal name sharing no word lands in triage.
      That is visible, not silent.

## 11. Screening answers and sensitive fields

- **`shared/answers.js` already skips password and file inputs**
  (`answers.js`, the type check in its control walk).
- **Nothing filtered sensitive fields, and it had already cost something.**
  Singapore employer forms, SuccessFactors ones especially, ask for NRIC/FIN,
  date of birth, race, religion and marital status. US forms ask
  equal-opportunity questions (gender, ethnicity, veteran status,
  disability). Measured the day this doc was written: 5 of the stored
  answers were equal-opportunity ones, from ordinary Easy Apply forms. So
  this was built first, ahead of phase A, rather than with phase B.
- **Built (24 Sep 2026, decision 3):**
  - The question is kept and the value replaced by `(withheld)`, IN THE
    EXTENSION (`shared/answers.js`, at `record()`), so the value never leaves
    the browser, not even into the tab's `sessionStorage`.
  - "This employer asked for my NRIC" is a fact `/answers` can usefully show
    across employers. The number itself never helps.
  - `answers.is_sensitive()` withholds the same answers again on the server,
    inside `clean()`. `tests/sensitive_questions.json` holds the two sides to
    one list.
  - Nationality and work authorisation are deliberately not withheld: the
    visa analysis reads them.
  - `cli redact-answers [--apply]` withholds rows stored before the rule, or
    before a pattern was added to it. It has no undo, by design.
- **`answerFormRoot()` must exclude the ATS's own sign-in and sign-up
  forms.** The sweep would otherwise file a username as an answer.
- **Vendor widgets need wrapper-aware reading** (Workday's button-driven
  dropdowns, Greenhouse's react-select comboboxes). It is the same work
  LinkedIn's `<dialog>` rebuild needed (`.claude/rules/extension.md`), and
  the `sweeps` ring buffer is what will show where it falls short.
- **`extension/PRIVACY.md` must be rewritten:** which sites, which fields,
  and what is redacted.

## 12. Plan

**A. Reader, popup capture, id namespacing.**

- `readJobPosting` and its tests.
- A popup "Capture this job as applied / as interested" on any page, via
  `activeTab` + `scripting`.
- Namespaced ids (§10).
- `ats` from the page's fingerprint.

The employer-site listing becomes capturable with one click, with no standing
permissions. A job description reaches the next direct application.

**Built 24 Sep 2026, extension 0.11.0:**

- `extension/shared/jobposting.js` is the reader (`read`, `idFrom`,
  `atsOfUrl`, `vendorOf`), and now also owns the ATS vendor table that
  `capture.js` used to keep.
- `extension/adapters/generic.js` is what the popup injects, and
  `joburl.generic_id` is `idFrom` in Python.
- **Two changes beyond this plan:**
  - The popup's "applied" capture writes at once even for an employer site.
    The click is the confirmation the ask-first popover would request.
  - `/captures` accepts `external: null`: "applied" on a platform page cannot
    tell Easy Apply from the employer's site, so it records neither.
- **Manual entry now refuses an employer's link left on the form's default,
  LinkedIn.** Stored with no id, such a record could never converge with a
  capture of the page.
- **Verified:**
  - 33 URL shapes against both implementations.
  - Every reader shape in §4.3 against a fake DOM, with two rules
    mutation-tested (each mutation turns its tests red).
  - The reader run inside the real page of §2, which yielded everything
    §2 describes and the full 6,506-character job description.
- **Not yet run live:** the popup's injection path. It needs the unpacked
  extension reloaded first.

**B. ATS hooks that complete the LinkedIn external record** (§8).

- Vendors in the order the author's own data ranks them: Workday,
  SuccessFactors, Greenhouse, Ashby, Workable.
- Static host matches, the opener-tab link, the sensitive-field redaction,
  and per-vendor `answerFormRoot` and submit hooks.

This closes the 0-of-51 answer gap and fixes the applied time.

**Built 24 Sep 2026, extension 0.12.0:**

- **Measured first, read-only, on four vendors' live apply pages** (Lever,
  Greenhouse, Ashby, Workable): the form root, the submit control, and
  whether the job's title appears on the form page. Workday and
  SuccessFactors put the form behind a candidate sign-in, so they were not
  read.
- **The rule is structural, not per vendor** (`adapters/generic.js`):
  - The application is the `<form>` that takes the resume. Otherwise it is
    the nearest container of every visible control, and only on a page that
    has a file input or an apply-flow address.
  - Never a container that also holds a password field.
  - The submit is a control inside it whose words send an application.
  - The one vendor list left is two language-independent hooks (Lever's
    `#btn-submit`, Workable's `data-ui="apply-button"`).
- **Ashby has no `<form>` at all**, which is why the root is found by what it
  holds. On Ashby's live page the rule first chose `<body>`: reCAPTCHA's
  hidden response field sits outside the form, and the common container
  then was the whole page. Now only visible controls place the root.
- **The same field would have been stored as an answer**, with its token as
  the value, since the sweep names an unlabelled control by its `name`.
  Lever's hCaptcha writes one INSIDE its form. `answers.js` now skips a
  control that is both unrendered and named only by its own attribute.
  Both conditions are required: Easy Apply hides its native radios behind
  labelled wrappers. The server drops the three captchas' response keys as
  a second line.
- **Run in each of the three pages that load without sign-in** (Lever's
  stalled in the browser tab, so it was read as fetched HTML), the rule
  picked the right root, and exactly one button out of 5, 10 and 22 as the
  submit.
- **The link is `sender.tab.openerTabId` plus a title check.**
  - The board's click stashes its job under its own tab
    (`background.js:stashExternal`).
  - The submit takes the entry whose title matches
    (`jobposting.js:sameJob`), or the only entry when the page shows no
    title. Anything else links nothing.
  - A board tab's box still asking "Capture this application?" becomes the
    receipt when the submit saves the application.
- **Changed from the plan:**
  - An ATS listing stashes its job as the page loads, not on an "Apply"
    click: no per-vendor opening selectors, and the apply page gets the
    listing's clean title and JD.
  - A field read by fallback (a tab title) is marked weak, and the listing's
    stash replaces it.
- **NOT yet run live through the extension:**
  - a real submit;
  - the opener link, including whether LinkedIn's external button sets
    `openerTabId` at all;
  - the receipt handoff.
  Read the popup's provenance line on the next external apply
  (`.claude/rules/extension.md`).

**C. Per-site opt-in plus the declared handoff** (§7, §9).

- The case in §2 becomes automatic, from the listing through to the
  career{N} submit.
- The employer's own posting attaches to the job by explicit link.

**What to read on first real contact:**

- **A:** the provenance line's `title_source`.
- **B:** whether `openerTabId` was present, and the `sweeps` line.
- **C:** whether the form page after sign-in shows the title (§2 "Not
  seen").

## 13. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| An ATS submit links to the WRONG job (wrong tab, stale stash) | Medium | Link only on keyed facts (the URL id, the declared host, the opener tab). The same-tab guess stays last, fills gaps only, and its window is never lengthened. Record the source in provenance |
| A false application from a submit that failed validation | Low | Visible and deletable. Vendor hooks come before the generic `submit` backstop |
| Vendor DOM drift silently stops hooks from firing | High, over time | The failure and provenance ring buffers. The popup button stays as the floor. The confirmation email still creates the record |
| Sensitive values stored in the tracker | High on SG forms without a filter | Redact in the extension (§11) |
| Duplicate records, LinkedIn against the employer site | Certain without §8 | An explicit link. Never a looser enrich rule |
| Id collision under `platform='other'` | Low per capture, silent when it happens | Namespace or a new platform value (§10), before the first capture |
| JSON-LD not yet present when read | Low | Read at the click. Vendor readers and title fallbacks, with the source recorded |
| Permission creep or a store listing that reads as surveillance | Medium for a release | Opt-in per site, and a `PRIVACY.md` rewrite. No `<all_urls>` |

## 14. Decisions

All four were taken by the author on 24 Sep 2026, each as recommended.

1. **Automatic hooks:** a static list of ATS hosts in the manifest, or opt-in
   per site for everything? **Decided: the static list for the hosts where
   forms live, opt-in for vanity domains.**
2. **Id identity:** namespace under `other`, or add a new platform value?
   **Decided: namespace.** It needs no migration and is reversible. Revisit
   when analytics wants a split.
3. **Sensitive fields:** redact the value in the extension and keep the
   question? **Decided: yes. Built (§11).**
4. **When to write:** at submit, or on a confirmation page? **Decided: at
   submit (§6).**

## 15. Not verified

- The SuccessFactors form after sign-in: title, requisition id, whether the
  submit navigates.
- Whether LinkedIn's external button sets `openerTabId`.
- iCIMS (WAF CAPTCHA) and Taleo (no live posting reached).
- Client-rendered JSON-LD on Workable, Oracle Recruiting Cloud,
  MyCareersFuture, BambooHR and Eightfold: known from bundle code only, never
  seen in a rendered page.
- That employer sites iframe Greenhouse's embed form: prior knowledge, not
  observed. If so, frame 0 needs a content script (per-site opt-in) before
  `tracker-relay-getjob` can ask it who the job is.

## 16. Sources

- Google Search Central, [Job posting (JobPosting) structured
  data](https://developers.google.com/search/docs/appearance/structured-data/job-posting):
  the required properties, and `datePosted` as the original posting date in
  ISO 8601.
- schema.org, [JobPosting](https://schema.org/JobPosting).
- Chrome for Developers, [chrome.tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs):
  only `url`, `pendingUrl`, `title` and `favIconUrl` need the `tabs`
  permission.
- Chrome for Developers, [chrome.permissions](https://developer.chrome.com/docs/extensions/reference/api/permissions):
  `optional_host_permissions` with `https://*/*` for hosts discovered at
  runtime, requested from a user gesture.
- Chrome for Developers, [chrome.scripting](https://developer.chrome.com/docs/extensions/reference/api/scripting):
  `registerContentScripts` (Chrome 96+, `persistAcrossSessions` defaults to
  true, `world` from Chrome 102), and `ExecutionWorld` ISOLATED against MAIN.
- Live evidence, 24 Sep 2026: the §2 walk in the browser, the §3 queries
  against the dev DB, and the §4 survey (URLs withheld under the real-names
  rule).
