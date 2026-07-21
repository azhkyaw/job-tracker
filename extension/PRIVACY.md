# Job Tracker Capture — Privacy

This extension exists for one purpose: when YOU apply to (or manually capture)
a job on LinkedIn, JobStreet/SEEK, or Indeed, it reads that job page's
description, title, and company from the page you are viewing and sends it —
with the tags you enter — to the tracker server YOU configured, authenticated
with YOUR token.

- No background browsing, scraping, or automated page visits. Capture happens
  only on your click.
- No data is sent anywhere except the API base URL you set in Options.
- No analytics, no third-party requests, no ads.
- Stored locally: your API base URL and token (chrome.storage.sync) and a
  ring buffer of the last 10 capture failures (chrome.storage.local).
- Permissions: content scripts on the three job sites; storage; activeTab.
  Host access to your own server (localhost by default; remote origins only
  when you grant them in Options).

Store-listing checklist (when publishing): single-purpose description matching
the above, this policy hosted at a public URL, and a justification per
permission — all three follow directly from this file.
