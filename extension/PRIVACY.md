# Job Tracker Capture — Privacy

This extension exists for one purpose: when YOU apply to (or manually capture)
a job on LinkedIn, JobStreet/SEEK, or Indeed, it reads that job page's
description, title, and company from the page you are viewing and sends it —
with the tags you enter — to the tracker server YOU configured, authenticated
with YOUR token.

**Any other job page, only when you ask (since 24 Sep 2026).** On an employer's
career site or its hiring system, nothing runs until you click "Capture this
job as applied" or "as interested" in the toolbar popup. That click lets the
extension read the ONE page in front of you, once (Chrome's `activeTab`): the
job details the page publishes for search engines, its title, and which hiring
system it runs on. It keeps no access to that site afterwards.

**Hiring systems' own application pages.** The extension also runs on the
sites of the hiring systems employers use to take applications (Greenhouse,
Lever, Ashby, Workable, Workday, SuccessFactors' career pages, SmartRecruiters,
iCIMS, JazzHR, Breezy). There it does what it does on LinkedIn's Easy Apply:
when YOU click the form's own submit button, it saves the job and the form's
questions and answers to your tracker. It never reads a sign-in form (a
password field anywhere in the form rules it out), and it skips a page's
hidden machinery, such as a captcha's response field. When that page was
opened by a job board's "Apply on company website", the application is filed
onto the record the job board started. That link comes from the browser's own
note of which tab opened which, not from reading your tabs' addresses.

**Employers' own career sites, only the ones you turn on.** On a site like
careers.<employer>.com, the popup offers "Always capture on <that site>".
Clicking it makes Chrome ask you to grant the extension that ONE site. From
then on, on that site:
- each job page you open is remembered for its tab, locally, for up to two
  hours;
- when that tab moves on to the employer's hiring system and you submit the
  application, the application is filed onto that job.
"Stop capturing on <that site>" in the popup, or removing the site in
chrome://extensions, ends it.

When a hiring system's form doesn't name the employer, the receipt asks you
for it instead. Its suggestion comes from the page's own address and the
site you arrived from. Both are read in your browser, and the company goes
to your tracker only if you press Save.

On an in-page apply form (LinkedIn Easy Apply today) it also reads the
questions that form asks and the answers you gave, so your own tracker can
show what you told that employer. Two consequences worth stating plainly:

- These are the values in the form's own fields — if a step asks for your
  phone number or work-authorisation status, that is what gets read and
  stored. It goes to your tracker and nowhere else.
- **Except identity and protected-characteristic answers, which are
  withheld in the browser.** For a question about an identity number (NRIC,
  FIN, passport, national ID), date of birth or age, race or ethnicity,
  religion, marital status, gender or sex, veteran status or disability, the
  extension keeps the question and replaces your answer with "(withheld)"
  before it is stored anywhere, including the tab's own working copy. The
  server withholds the same answers again if one ever arrives.
- Fields are read at the moment you submit (and as you fill each step, since
  the form discards earlier steps). Nothing is read from forms you never
  submit beyond that in-tab working copy, which is discarded after the
  capture or after two hours.

**When the send happens (changed 28 Jul 2026).** Where the apply action is
unambiguous — LinkedIn Easy Apply's final "Submit application", or an explicit
capture from the extension popup — the record is now sent to your tracker
**immediately on that click**, without waiting for you to confirm in the
popover. The popover that follows is a receipt; tagging it or ignoring it
changes nothing about what was already sent. This exists because the old
behaviour held the only copy of the capture in a popover that vanished after
45 seconds or when you closed the modal, silently losing real applications.
Where the apply action is *ambiguous* — "Apply on company website", where you
may still walk away without applying — nothing is sent until you confirm.

- No background browsing, scraping, or automated page visits. Capture happens
  only on your click.
- No data is sent anywhere except the API base URL you set in Options.
- No analytics, no third-party requests, no ads.
- Stored locally: your API base URL and token (chrome.storage.sync), a ring
  buffer of the last 10 capture failures (chrome.storage.local), and the
  in-progress apply form's answers for the tab you're applying in
  (sessionStorage, cleared on capture), and, for up to two hours, the job
  an "Apply on company website" click left for, so the employer's form can
  be filed onto it (chrome.storage.local).
- Permissions: content scripts on the three job sites and on the hiring
  systems' application pages listed above; storage; activeTab and scripting,
  which together let a popup click read the page in front of you and nothing
  else; and, only for sites you turn on yourself, access to that one site
  (an optional permission Chrome asks you for, site by site).
  declarativeContent lets the toolbar icon turn blue on a page the extension
  captures on. Chrome itself checks each page's address against the
  extension's own list of sites. The extension reads nothing to do it.
  Host access to your own server (localhost by default; remote origins only
  when you grant them in Options), plus linkedin.com — needed only so the
  receipt can be drawn in the page's top frame instead of inside the Easy
  Apply modal, which Chrome destroys when the modal closes. It grants nothing
  the content script on that site doesn't already have.

Store-listing checklist (when publishing): single-purpose description matching
the above, this policy hosted at a public URL, and a justification per
permission — all three follow directly from this file.
