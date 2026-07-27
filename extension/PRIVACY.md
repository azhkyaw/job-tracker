# Job Tracker Capture — Privacy

This extension exists for one purpose: when YOU apply to (or manually capture)
a job on LinkedIn, JobStreet/SEEK, or Indeed, it reads that job page's
description, title, and company from the page you are viewing and sends it —
with the tags you enter — to the tracker server YOU configured, authenticated
with YOUR token.

On an in-page apply form (LinkedIn Easy Apply today) it also reads the
questions that form asks and the answers you gave, so your own tracker can
show what you told that employer. Two consequences worth stating plainly:

- These are the values in the form's own fields, whatever they are — if a
  step asks for your phone number or work-authorisation status, that is what
  gets read and stored. It goes to your tracker and nowhere else.
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
  (sessionStorage, cleared on capture).
- Permissions: content scripts on the three job sites; storage; activeTab.
  Host access to your own server (localhost by default; remote origins only
  when you grant them in Options), plus linkedin.com — needed only so the
  receipt can be drawn in the page's top frame instead of inside the Easy
  Apply modal, which Chrome destroys when the modal closes. It grants nothing
  the content script on that site doesn't already have.

Store-listing checklist (when publishing): single-purpose description matching
the above, this policy hosted at a public URL, and a justification per
permission — all three follow directly from this file.
