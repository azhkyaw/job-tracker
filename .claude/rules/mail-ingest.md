---
paths:
  - "pipeline/gmail_imap.py"
  - "pipeline/gmail_sync.py"
  - "pipeline/gmail_oauth.py"
  - "pipeline/mailbox.py"
  - "tests/test_email_ingest.py"
  - "docs/email-ingest.md"
---

# Mail ingest: IMAP, Gmail API, OAuth, the pre-filter

Moved here VERBATIM from CLAUDE.md on 9 Sep 2026 so it loads when Claude reads a
matching file instead of in every session (it can also be Read directly).
Dates are the key to each case; the record itself is in the database. The
orchestrator invariant (#10) is still in CLAUDE.md.

## Gotchas learned the hard way

- **Gmail's `messages.list` returns newest-first, but ingest is order-sensitive.**
  `gmail_sync.py`'s `backfill()` and its expired-cursor fallback used to store/enqueue
  `classify_email` jobs in that (newest-first) order, and the worker claims
  `job_queue` FIFO — so during a real backfill a chronologically-later status
  email (e.g. a rejection) could get matched *before* the confirmation email
  that would have created its application. `matcher.find_match()` correctly
  found zero candidates (not a scoring miss — `emails.match_score` stays NULL
  on these, distinguishing them from a real low-confidence match) and the
  email landed in `/triage` for no real reason — reproduced on a real
  application (confirmation + rejection, ~4 days apart) on the first backfill.
  Fixed via `_list_message_ids()`, which collects every page before reversing
  so ingest always processes oldest-first. `incremental()`'s normal path
  (History API) was never affected — Gmail returns history records oldest-first
  already; only its cursor-expired fallback (now inside `incremental_handles`, same
  `messages.list` call) shared the bug.
- **OAuth publishing status "Testing" revokes refresh tokens after 7 days.**
  Google's docs are explicit: an external-user-type consent screen with
  publishing status `Testing` issues refresh tokens expiring in 7 days unless
  the only scopes are name/email/profile — `gmail.readonly` is not exempt.
  `README.md`'s "add yourself under Test users" leaves you exactly there, so
  Gmail sync dies weekly with `invalid_grant: Token has been expired or
  revoked`. Whether flipping to "In production" while *unverified* stops the
  clock is UNDOCUMENTED for restricted scopes (Google ties removal to
  *verified* + published; community reports say the flip works) — test it
  before asserting either way. Handle `invalid_grant` visibly regardless:
  tokens also die on Google-password change (Gmail scopes specifically), six
  months of disuse, and user revocation. See `docs/open-source.md` §2.
  **Handled as of 28 Jul 2026:** `GmailApiProvider.from_stored` catches
  `RefreshError` and raises `mailbox.MailboxAuthError`, the same type an IMAP
  login failure raises — one shared error path surfaces in the CLI, `sync`,
  and Settings for both credential kinds. The empirical production-status
  flip test itself was never run; this handling doesn't depend on its result.
- **The candidate pre-filter drops mail BEFORE it is ever fetched, so a miss
  leaves no row, no log line, and nothing to notice.** `is_candidate` matches
  an allowlisted sender domain OR a `SUBJECT_KEYWORDS` substring **in the
  subject only** — it never reads the body. Three independent ways that lost
  real mail, all found 7 Aug 2026: (1) an employer mails from its OWN domain
  (`humans@tailspin-consulting.com`), which is unbounded and un-allowlistable
  in advance; (2) the subject splits a keyword — "Thank you for your
  **Full-Stack Developer** application to Tailspin Consulting" contains
  neither `your application` nor `thank you for applying`, while the
  `not moving forward` phrasing that WOULD have matched sits in the body one
  line below the only line the filter reads; (3) an allowlisted vendor sends
  from a domain that is not a SUFFIX of the allowlisted one — `workable.com`
  is listed, Workable mails from `candidates.workablemail.com`, and three real
  confirmations were dropped by a list entry that looks correct. Case (1)'s
  email was a REJECTION on an application the app was showing as
  `interview_invite`: a tracker asserting an open thread on a closed role,
  which is worse than a missing row. **Do not fix these by extending the
  lists** — every entry is added retroactively, i.e. only after that case has
  already been lost. `config.INGEST_ALL` (invariant #10) is the fix for a
  job-only mailbox. Cost is not the counterargument (classify is ~3,810 input
  tokens, ~$0.008/email on Sonnet 5) and neither is precision: 225 of 399
  stored emails already classified `not_job_related`, so the filter was buying
  a randomly holed corpus, not a clean one. First `-d 1` run under the flag
  recovered 8 messages, 5 of them real.
- **`filter_query()` returning `""` must make a caller DROP the criterion, not
  send it.** Under `INGEST_ALL` there is no predicate, and
  `gmail_imap.incremental_handles` passes `filter_query()` straight into its
  incremental UID search: `X-GM-RAW ""` is a perfectly VALID quoted string that
  matches nothing, so incremental sync would stall silently while backfill kept
  working — the same invisible-failure shape the flag exists to fix. Pinned by
  a test. Any future caller of `filter_query()` inherits this.
- **`imaplib.IMAP4._command()` does zero quoting.** Every argument is
  concatenated onto the wire verbatim — `select("[Gmail]/All Mail")` sends
  two unquoted atoms and gets `BAD`, and an `X-GM-RAW` query containing
  `subject:"your application"` is a malformed quoted string unless escaped
  first. `gmail_imap.py`'s `_quote()` (backslash- and quote-escaping) wraps
  every mailbox name and search query; nothing reaches `conn.select()`/
  `conn.uid()` unquoted.
- **`imaplib.Internaldate2tuple()` returns `time.localtime(utc)`** — a NAIVE
  struct in the *host's* timezone, not the message's. Using it for
  `received_at` would silently shift every stored email by the host's UTC
  offset. `gmail_imap._parse_internaldate()` parses the `imaplib.InternalDate`
  regex groups directly and builds a tz-aware UTC datetime instead — verified
  against a real `+0800` INTERNALDATE stamp before this was trusted.
- **`UID SEARCH UID n:*` returns the highest existing UID even when `n`
  exceeds it** (RFC 3501 range semantics) — without a client-side
  `uid > last_uid` filter, every incremental sync would re-fetch the newest
  matching message forever. Invisible in practice (the insert is a no-op via
  `ON CONFLICT`) except as a permanent, silent bandwidth leak.
  `gmail_imap.ImapProvider.incremental_handles()` filters this client-side;
  `tests/test_email_ingest.py` asserts the stale UID is never fetched.
- **`BODY.PEEK[]` and `readonly=True` (EXAMINE, not SELECT) are both
  mandatory, together.** Either one missing marks the operator's mail
  `\Seen` — a live, unrecoverable side effect no fake can fully stand in for.
  Verified against a real inbox (28 Jul 2026): noted specific unread emails,
  ran backfill against them twice, confirmed still unread both times.
- **Two more places IMAP diverges silently from the Gmail API if copied
  carelessly:** the API returns headers already MIME-decoded, but raw IMAP
  `FETCH` does not — skip `email.header.decode_header` and a candidate email
  with an RFC 2047-encoded subject (non-ASCII, so Q-encoding replaces spaces
  with `_`) is invisible to `is_candidate`'s keyword match even though a
  human reading the same subject in Gmail would see the keyword plainly.
  Separately, `[Gmail]/All Mail` is a *localised* folder name (different per
  account language) — found via the `\All` SPECIAL-USE flag in `LIST`, never
  hard-coded, and deliberately All Mail rather than `INBOX` since that's the
  parity set with the API's `messages.list` (excludes Spam/Trash, includes
  archived and filter-routed mail — most accounts with job alerts route them
  somewhere other than INBOX).

## Known-untested surfaces (verify on first real contact)

- **Gmail web OAuth end-to-end** (`pipeline/gmail_oauth.py`) — flow code is
  tested for state/storage, not against Google.
- **Gmail API poller against a real inbox** — a 10-day test backfill (Jul 2026)
  already surfaced and fixed two real issues (see Gotchas): the
  `messages.list` newest-first ordering bug, and a same-employer/different-
  branding email that needed the new `refile_email` route rather than
  `merge_jobs`. Still unverified: the full `-m 12` window, `ALLOWLIST_DOMAINS`
  coverage, and match-threshold tuning against a larger real sample.
- **Gmail IMAP poller — verified against a real inbox 28 Jul 2026.**
  `backfill -d 14` against the author's real account: no crash on the real
  `X-GM-RAW` search (the quoting concern in the Gotchas above was the biggest
  unknown going in), 4 new candidates stored, zero collisions against the
  102 rows the prior OAuth backfill had already written (proving the
  hex-identity claim, not just asserting it), cursor came out
  `uidvalidity:uidnext-1`-shaped, two `sync` runs both reported 0 new, a
  deliberately wrong app password surfaced the same friendly message the
  fake predicted (`[AUTHENTICATIONFAILED] Invalid credentials (Failure)` —
  the real server's wire text matched the test fixture exactly), and noted
  unread emails stayed unread across two backfill passes. Still unverified:
  the full `-m 12` window on a real account (the 2,500 MB/day ceiling is not
  a real concern at this account's scale per a rough extrapolation, but
  never measured directly), and the **web** connect form
  (`/settings/gmail/imap`) against a real app password — only the CLI path
  (`cli auth`) was exercised live; the web route has FakeIMAP-suite and
  browser-screenshot coverage but no real-credential run.

