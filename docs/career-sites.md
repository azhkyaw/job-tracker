# Employer Career Sites — Analysis and Plan

**Author:** AZ
**Status:** Accepted; being built. The four decisions in §14 were taken on
24 Sep 2026, all as recommended. Built the same day: the redaction in §11
and phases A, B and C (§12). Awaiting a real apply through each to verify.
**Revised 28 Sep 2026 (§16):** the link from a listing to its ATS form moves
from the tab to the job's own id on the ATS; P0 and P1 are built.
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

**Built 24 Sep 2026, extension 0.13.0** (the first bullet above; the second
was deferred):

- **"Always capture on <site>" in the popup** asks Chrome for that one host
  (`*://host/*`, from `optional_host_permissions`).
  - The worker registers the generic scripts for it
    (`chrome.scripting.registerContentScripts`) and injects them into the
    open tab at once.
  - A site runs them only while it is in `enabledSites` AND its permission
    stands. Permission alone would also enable a remote tracker server that
    `options.js` asks for.
  - `syncSites()` re-derives the registrations on install, on startup and on
    every permission change.
  - The permission prompt can close the popup before its own code resumes,
    so the grant is completed from `chrome.permissions.onAdded`, matched to
    a `pendingSite` note the popup wrote.
- **The declared handoff became the tab itself.** It is not a destination
  host: the listing's "Apply now" goes to its OWN domain first
  (`/talentcommunity/apply/…`), so the host its link names is not the host
  the form is on.
  - Every listing that publishes a JobPosting remembers its job under its
    tab, in the store phase B built for the opener.
  - A submit asks for the opener's list first (a job board's record wins),
    then its own tab's, and takes the one entry whose title is the same job
    (`jobposting.js:pickListed`). The measured SuccessFactors form titles
    the job "VP - … AI Engineer (1234)": the listing's title with its
    requisition number appended, which the containment rule accepts, while
    rejecting a sibling role that shares three of its four words.
  - The record takes the listing's identity: company, clean title, JD, and
    its `<host>/<token>` id.
- **Not built:** the second posting for a job board record, which needs a
  server change and waits on phase B's opener link being seen to work.
- **NOT yet run live through the extension:** enabling a site (a
  chrome-extension:// page no automation may click), and a real submit
  after it.

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
- ~~Whether LinkedIn's external button sets `openerTabId`.~~ It does: seen
  on a real LinkedIn → Oracle apply, 28 Sep 2026 (§16.6).
- iCIMS (WAF CAPTCHA) and Taleo (no live posting reached).
- Client-rendered JSON-LD on Workable, Oracle Recruiting Cloud,
  MyCareersFuture, BambooHR and Eightfold: known from bundle code only, never
  seen in a rendered page.
- That employer sites iframe Greenhouse's embed form: prior knowledge, not
  observed. If so, frame 0 needs a content script (per-site opt-in) before
  `tracker-relay-getjob` can ask it who the job is.

## 16. The handoff, rebuilt (28 Sep 2026)

The phases in §12 link an ATS submit to its listing by the TAB (the opener's
list, then the tab's own) plus a title check. A real application on 28 Sep
2026 showed every link in that chain failing at once, and the author's data
showed the same path losing far more elsewhere. This section is the design
that replaces it, measured first.

### 16.1 The case

An employer's career site (SuccessFactors Career Site Builder, placeholder
`jobs.litwarebank.com`) hands over to SuccessFactors' hosted form on another
data centre, in the same tab:

```
jobs.litwarebank.com/job/Principal-AI-Engineer/51234-en_GB   listing: company, title, JD
  "Apply now" = <a href="/talentcommunity/apply/51234/">
career2.successfactors.eu/careers?company=litwarebk          sign-in: tenant only
career2.successfactors.eu/career?company=litwarebk&career_job_req_id=51234   create account
career2.successfactors.eu/portalcareer?_s.crb=…              form: <h1> "Principal AI Engineer (51234)"
  … 13 minutes idle: the session timed out, back to sign-in, the form again
```

What broke, link by link:

1. **No script ran on the listing:** the site was not enabled (§12 phase
   C). Enabling is itself broken: the one attempt, on the 24 Sep employer's
   site, never completed (Chrome holds no grant, and the popup's
   `pendingSite` note has waited since 25 Sep 00:22).
2. **Had it run, the listing would not have been stashed.** This site
   writes `itemprop="title"` OUTSIDE its JobPosting scope, which holds only
   the description, and there is no `hiringOrganization`. The title falls
   back to `og:title`, and `stashListing()` stashes only a read whose
   `title_source` is `jsonld` or `microdata`.
3. **The form names no employer, and its address is a session crumb.** The
   posting id became `career2.successfactors.eu/portalcareer?_s.crb=…`,
   which changes at every sign-in, and `stripRequisition` could not remove
   "(51234)" because the id held no 51234.
4. **Titles cannot pick the job.** The same site lists a sibling
   "Principal AI Engineer", 51232, in the same city: `pickListed` would find
   two entries of one title and link neither.

A submit there would still have been captured: the rules found
`form#careerform` (30 fields) and exactly one submit of 136 buttons, read
live and read-only. It would have filed as "unknown company" with no JD.

### 16.2 Measured on the author's data (28 Sep 2026)

| How the application was sent | Applications | With form answers |
|---|---|---|
| On the platform (Easy Apply, SEEK Quick Apply) | 218 | 164 |
| **LinkedIn → the employer's site** | **55** | **0** |
| Directly on an employer's site, captured | 1 | 1 |
| Manual entry or email only | 9 | 0 |

The 55 by vendor: Workday 10, SuccessFactors 7, Ashby 6, Greenhouse 6,
Workable 4, iCIMS 2, Breezy, JazzHR and Rippling 1 each, and 17 whose vendor
was never detected (LinkedIn resolves the destination on its server).
**Phase B has not captured one of them.** The one clue: on 25 Sep 2026 at
10:52:13, "Submit" on a Workday form (placeholder Alpine Ski House,
`…/Senior-AI-Engineer_R200001/apply/autofillWithResume`) was turned down with
"no application form found on this page". The record exists only because the
LinkedIn popover was answered 18 seconds later, with 0 answers, and that
application went on to an interview invitation.

**Which surfaces carry the job's own id on the ATS** (the requisition):

| Surface | Tenant | Requisition |
|---|---|---|
| Career Site Builder listing | inline `companyId`, `ssoUrl` | `j2w.Apply.init({jobID})`, URL, "Requisition Number" — **but** on the 24 Sep employer's site the listing id (ten digits) is not the requisition (four) |
| First SuccessFactors pages | `?company=` | `career_job_req_id=` on some tenants |
| SuccessFactors form, job page | lost after a postback | `<h1>` and tab title end "(51234)": 3 tenants of 3 |
| Workday, every step | host | URL `_R200001`, kept through `/apply/…` |

**Emails that carry that id**, per vendor, over every email filed to an
application whose vendor is known: Workday 4 of 19 (R-numbers: "Reference
Role: R0012345"), SuccessFactors 3 of 11 (every confirmation: "your interest
in AVP, Software Engineer (1234)"), Greenhouse 0 of 21, Workable 0 of 29,
Ashby 0 of 15, SmartRecruiters 0 of 9. A first pattern counted Ashby's
interview-meeting links and SuccessFactors' dates as ids; the counts above
are from the corrected patterns, each hit read by eye.

### 16.3 The design

1. **The join key is the job's id on the ATS: the ATS page's own `idFrom`,
   and where its address has lost the id, the requisition number the page
   prints.** That is one exception, not a vendor table: every other vendor
   in §4.2 keeps the id in the URL through the whole flow. Pages whose
   address fell back to path-plus-query (§5) and whose `<h1>` or tab title
   ends in "(digits)" take `host/<digits>`. `stripRequisition` then works,
   and the answer store and the stash share the key, so both survive a
   postback, a sign-in and a timeout.
2. **Bind at the handoff, not at the submit.** On a page that publishes a
   JobPosting, an apply-worded link or button outside any application form
   is a DEPARTURE: the job is leaving for its ATS (today's `nearMiss` case,
   reread; SuccessFactors' "Apply now" is an `<a>`). The first ATS page to
   load in that tab, or in a tab it opened, claims the departure within
   seconds, and the worker records `ATS id → listing job` for days, not the
   same-tab guess's 30 minutes (which stays as it is). The ATS page also
   writes `{tenant, requisition}` into its own origin's `sessionStorage`,
   which outlives the postbacks that strip them from the URL. Decided at
   the handoff, the binding is made seconds after the click; decided at
   the submit, it is made after the sign-in, the account and, on 28 Sep,
   a session timeout.
3. **The server stores the ATS id on the JOB and upserts on it** (a
   migration; a requisition is the employer's role, not an ad, so
   `dedup.merge_jobs` carries it). Order stops mattering:
   - listing first (an enabled site, or the popup on the listing): the
     submit completes that record;
   - submit first (a thin record): a later capture of the listing, even a
     toolbar click after applying, fills in its company, title and JD.

   An exact key is the recoverable kind of link invariant #3 asks for, and
   it holds across the author's two machines.
4. **Email joins on the key, by lookup, not by parsing.** A job-related
   email whose body contains the ATS id of an application still open is
   that application's, before any name matching. Parsing "a number in the
   body" would take any figure in a letter for an id. It fixes the
   sibling case (two records, one title) and gives the open "email-side
   rescue" (§12, task 30) a key instead of a guessed name. Measured above:
   SuccessFactors and Workday only.
5. **Reading an employer-branded listing:**
   - fix the enable flow (link 1);
   - stash any page where a JobPosting was FOUND, whatever the title's
     source (link 2);
   - read the Career Site Builder's inline config (`companyId`, `ssoUrl`,
     `jobID`);
   - take the company from the tab-title suffix ("… | Litware Bank") as a
     weak field;
   - remember tenant → company once the receipt's question is answered, so
     each tenant is asked once;
   - a third toolbar-icon state, "job page, capture off", from
     `declarativeContent`'s `PageStateMatcher({css: ['[itemtype$="JobPosting"]']})`.
     It needs no host permission, but CSS conditions match only DISPLAYED
     elements, so it sees microdata (Career Site Builder) and never JSON-LD,
     which lives in a `<script>`.
6. **SuccessFactors quick apply.** Signed in with a complete profile, the
   job page's own "Apply" sends the application at once (Relecloud, 25 Sep
   2026: `isQuickApplyPostLoginRedirect`, then
   `isRedirectToAppSent`, and the confirmation email the same minute). The
   click is a departure; landing on `isRedirectToAppSent=true` is the
   completion. There is no form, so no answers.

**Not in the design:** fetching the listing from the background (invariant
#1, §9: no background visits); a content script on every site; lengthening
the same-tab guess; linking on a title alone when two candidates share it.
**What stays unavoidable:** the JD of an employer-branded listing needs one
click on that domain — enabling it once, or the toolbar on the listing,
before or after applying.

### 16.4 How general

| Flow | Vendors | Listing readable | Id on the form page | Email carries it |
|---|---|---|---|---|
| Listing and form on one ATS host | Workday, Ashby, Greenhouse, Workable, Lever, Personio, JazzHR, iCIMS (unverified) | yes, static hosts | the URL, throughout | Workday only |
| One host, the apply page drops the id | SmartRecruiters (apply UUID), Recruitee (no id) | yes | no: only the binding carries it | no |
| Employer front end → ATS elsewhere | Career Site Builder → SuccessFactors, Phenom → Workday, Careers@Gov | after one click on the domain | SuccessFactors: the printed "(N)"; Workday: URL | SuccessFactors |
| Front end with the form in an iframe | Greenhouse embed (`?gh_jid=`) | the same | the iframe's token | no |

The mechanism (key, binding, upsert, email lookup) is the same for every
vendor. What varies is where the id sits, which is `idFrom` everywhere but
one place, and whether the listing is on a host the extension can read.
Weighted by §16.2, the employer-branded case is 2 applications and the
LinkedIn → ATS case is 55; the binding serves both, through the opener tab
for the second.

### 16.5 Plan

- **P0. Make the ATS submit fire on a wizard's last step.** The Workday miss
  above: the reason logged means `applicationRoot` found no root on an apply
  address, which the code allows only when fewer than two answerable
  controls exist or a password sits in the container. A review step shows
  the answers as text. So on an apply-flow address with no root and no
  visible password field, a submit-worded control is the submit. The
  existing wizard test put "Submit" beside inputs, which a real review step
  does not. **Built 28 Sep 2026** (§16.6).
- **P1. The job's ATS id, on both sides.** Extension: §16.3 item 1. Server:
  the column on `jobs` (the author's choice over `postings`), the upsert and
  the email lookup (items 3 and 4). **Built 28 Sep 2026.**
- **P2.** The handoff binding, the stash gate, the Career Site Builder
  config reader, and the tenant inside SuccessFactors ids. **Built 28 Sep
  2026** (§16.6). Changed from the plan: no click is watched for the
  departure. The tab already remembers each listing it shows, and a
  listing's Apply navigates FROM it, so the departure is the last listing
  the tab showed on another site (`pickDeparture`). A listing's own Apply
  is never an application's submit: a page that publishes a JobPosting
  gets no form root by rule 3 unless its address is an apply flow.
- **P3.** Fix enabling, the icon's third state, quick apply. **Built 28 Sep
  2026** (§16.6). Enabling's cause was not the prompt at all: Chrome refused
  every request before showing one.
- **P4.** Completing a thin record from its listing afterwards; tenant →
  company names. **Built 28 Sep 2026** (§16.6).

**Open decisions and risks:**

- **The tenant was not in a SuccessFactors id** (`career10.successfactors.com/12345`,
  pinned in `tests/job_urls.json` until P2). Requisitions are per-tenant
  sequences on a shared host, so two employers on one data centre could
  share a number and collide under `postings_platform_job_uidx` (the §10
  failure) and, since P1, under `jobs.ats_job_id`. P2 puts it in:
  `<host>/<tenant>/<id>`. No posting holds a SuccessFactors-host id; the
  five SuccessFactors `ats_job_id`s backfilled on 28 Sep were rewritten the
  same day, with the author's go-ahead and a snapshot.
- **The listing must still be read.** On an employer-branded domain that
  means the site is enabled. Since P3 enabling reaches Chrome's prompt; it
  has still never completed on a real site.
- **Unverified:** that a Workday review step has fewer than two controls
  (deduced from the logged reason, not seen); the "(N)" suffix beyond three
  tenants; how often a Career Site Builder listing's id differs from the
  requisition (seen once of two).

### 16.6 Built

- **P0, extension 0.15.1** (`adapters/generic.js`): `applicationRoot`
  unchanged; a submit-worded control on an apply-flow address counts when
  the page has no application root and no visible password field. The
  sign-in and job-alert cases stay refused. Red against the old code on the
  three review-step checks; without the password guard, both sign-in tests
  go red.
- **P1, extension 0.16.0** (`shared/jobposting.js:pageId`): the page-aware
  id, used by `read()` and by `generic.js`'s `answerFormKey()`. `idFrom`
  now says when it fell back (`by: "path"`); the URL-only rule and its
  Python mirror are unchanged. A page-derived id stores the address without
  its query, since a crumb is session state and not the job's address. Each
  guard (the ATS host, the fallback) mutated out turns exactly one test red.
- **P1, server, migration 018 and extension 0.17.0:**
  - `jobs.ats_job_id`, unique per user. `generic.js:atsJobId()` is the
    page's own id on the vendor's host, never a crumb; `capture.js` reads it
    in the submit's own tick and sends it BESIDE the identity, because a
    link to a job board's record replaces the identity.
  - `ingest.upsert_record`: a new ad whose ATS id a job holds joins that
    job, whichever arrived first, and replaces only an "unknown company"
    placeholder with its name. Otherwise the id lands on the capture's job,
    unless another job holds it: then nothing moves and nothing merges.
  - `dedup.merge_jobs` carries the loser's id to a winner without one.
  - `matcher.match_by_ats_id`, before `find_match`: the one application
    whose ATS id the mail names, behind the same company gate (the gate is
    now one constant, `_COMPANY_GATE`), or a nameless record by its id
    alone. Two ids named decide nothing.
  - Tested in `tests/test_captures.py` (both orders, a linked board record,
    an id already held, merge) and `tests/test_integration.py` path 3k (two
    same-titled records at one employer, a nameless record, a stranger's
    mail with the same number).
  Backfilled with the author's go-ahead on the six real records whose id is
  proven (a form, an address, or a confirmation's "(N)"); replayed over the
  469 stored job-related emails, the lookup fires on 5, every one already
  filed on that record, and claims none wrongly.
- **P2, extension 0.18.0** (0.17.1 for the tenant alone):
  - SuccessFactors ids carry the tenant: `jobposting.js:TENANT_PARAM` and
    `tenantOf`, mirrored in `joburl.generic_id`. The postback form gets it
    as `pageId`'s hint from `generic.js`, which keeps the tenant an earlier
    page's address named in the hiring system's own `sessionStorage`.
    `stripRequisition` reads an id's last segment.
  - A page is a listing when it publishes a JobPosting (`_prov.structured`),
    which is what `stashListing` now asks; an employer's own site gives a
    weak company from its tab title's owner (`siteOwner`); a Career Site
    Builder listing adds where it hands over (`atsHandoff`: data centre and
    tenant from its inline config).
  - The handoff: each hiring-system page in a top frame sends
    `tracker-claim-handoff`; the worker binds the tab (`handoffs`, three
    days, cleared at browser start) to `pickDeparture`'s listing: the
    opener's last entry when fresh (15 minutes), else the tab's own last one
    on another host, never an older one, and never one that names another
    data centre, tenant or vendor. A later page of the same visit adds the
    job's id; a page showing ANOTHER job's id under the same listing does
    not rebind. At the submit, `takeExternal` tries the binding first,
    checked by `handoffFits` (same host; the same job id where both know
    it); the provenance line says "the handoff".
  - The pure rules are tested, and each guard mutated out turns its test red
    (the listing root, the tenant, most-recent-only, the same-host
    exclusion, the job id). The worker's store and the messages are not.
- **P3, extension 0.18.1 to 0.20.0:**
  - **Enabling (0.18.1).** The popup asked for `*://host/*` while the
    manifest declared `https://*/*` and `http://*/*` apart. Chromium needs
    ONE declared pattern to contain every scheme of a request
    (`URLPatternSet::ContainsPattern` → `URLPattern::Contains`), so every
    request was unlisted and refused ("Only permissions specified in the
    manifest may be requested") before any prompt, and the click handler,
    with no catch, showed nothing. The manifest declares `*://*/*`; the
    popup reports a refusal; a test checks every requested origin against
    the manifest with Chromium's rule.
  - **Quick apply (0.19.0).** A SuccessFactors job page's own "Apply"
    leaves a note in the hiring system's sessionStorage (a job page with a
    real id, no application form, no sign-in in view); the landing
    `isRedirectToAppSent=true`, seen once, files it through `capture.js`
    as a completed external apply, linked like any ATS submit. The landing
    alone files nothing; a note waits ten minutes and is used once.
  - **The icon's third state (0.20.0).** Grey with a filled dot on a page
    that publishes a JobPosting as microdata and is not captured: a
    `declarativeContent` CSS rule at priority 100 under the capturing
    rule's 200, the order Chromium's `GetDeclarativeIcon` resolves by.
    JSON-LD pages stay hollow: CSS conditions see only displayed elements.
- **P4, extension 0.20.1 and 0.21.0:**
  - **A tenant's name (0.20.1).** A nameless capture whose hiring-system id
    carries a tenant (`joburl.ats_tenant`) is offered the name another
    record gave that tenant, as `company_suggestion` in the `/captures`
    response, which the receipt pre-fills over the page's own guess. Offered,
    not stored: a tenant is one employer's instance, but the user confirms.
    SuccessFactors only, the one vendor measured whose form names no
    employer.
  - **Completing a thin record (0.21.0), two ways, both exact:**
    - a Career Site Builder listing proposes `<data centre>/<tenant>/<its
      own number>` as `ats_job_candidates` (`jobposting.js:atsCandidates`);
      `upsert_record` joins the ONE job holding it and never stores it, so
      on a site whose number is not the requisition it matches nothing;
    - the popup's "Attach this page to an application…" lists the last 30
      days' records, thin first (`GET /captures/recent`), and the user's
      pick makes the page a posting of that job
      (`POST /captures/{id}/listing`, `upsert_record(attach_to_job=…)`). A
      page that is already another record's posting is refused (409):
      combining two records stays `merge_jobs`'.
  - Either way the job takes the listing's company and title only where it
    holds the placeholders, and the listing's JD is extracted as its own
    posting's.
- **The first real run, and the fix it needed (0.21.1, 28 Sep 2026).** A
  LinkedIn → Oracle Recruiting Cloud apply, the first of the §16.2 path to
  keep its answers (29). The handoff bound as designed, through the
  opener, with the job's id (`…/job/2087` → `<host>/2087`). The submit then
  read its own id from `…/job/2087/apply/section/1` as `<host>/1`, so
  `handoffFits` refused the binding and the form filed a second record
  beside the LinkedIn one. `idFrom` and `joburl.generic_id` now look for
  the id before the first `apply`/`application` segment, and in the flow's
  tail only when nothing before it is id-shaped (JazzHR's `/apply/<id>`).
  §16.3 item 1 assumed every vendor keeps the id in the URL "through the
  whole flow"; Oracle keeps it, followed by a number that is not. The pair
  was merged onto the LinkedIn record (`.claude/rules/extension.md` has the
  repair). Still unseen: a submit that takes the binding.
- Nothing else has run on a real apply; `.claude/rules/extension.md` says
  what to read on the next one.

## 17. Sources

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
- Chrome for Developers, [chrome.declarativeContent](https://developer.chrome.com/docs/extensions/reference/api/declarativeContent):
  `PageStateMatcher.css` takes compound selectors only, "CSS conditions only
  match displayed elements", and the API "can be used without host
  permissions".
- Live evidence, 28 Sep 2026: the §16.1 form read live and read-only in the
  author's own tab; Chrome's History database for the navigation chain
  (paths and parameter names only); the §16.2 queries against the dev DB.
