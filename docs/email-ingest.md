# Email Ingest — Research and Plan

**Author:** AZ
**Status:** Draft v1 — research findings and a recommendation, not yet built
**Date:** 27 July 2026
**Scope:** How job-search email reaches this system, reconsidered under the
open-source direction (`docs/open-source.md`). Design doc §6.2 chose the Gmail
API; this asks whether that is still the right default when the operator is a
stranger self-hosting rather than the author.

---

## 1. Summary

The Gmail API path was chosen when this was a personal system and the author
was the only person who would ever run the setup. Under self-hosting it
imposes a Google Cloud project on every operator, and that project comes with
a documented trap that kills sync after seven days (§2).

**Recommendation: add Gmail IMAP with an app password as the documented
default, and keep OAuth as the documented alternative.** IMAP removes the
Cloud project, the consent screen, the verification question and the 7-day
expiry outright, and Gmail's own IMAP extensions make the port unusually
cheap — the existing search query is reused verbatim and the existing
`gmail_message_id` values stay valid.

It is not strictly better. An app password grants broader access than
`gmail.readonly`, several account types cannot use one, and Google labels the
mechanism "not recommended" without committing to a removal date. Those are
real costs, stated in §4.3, and they are why OAuth stays rather than being
replaced.

## 2. Why this is being reconsidered

Three things changed with the OSS decision.

**The setup tax is now paid by strangers.** `README.md` currently asks the
operator to create a Google Cloud project, enable the Gmail API, configure an
OAuth consent screen for an external user type, download `credentials.json`,
and add themselves under Test users. That is a reasonable ask of one person
who wants their own inbox parsed. It is a high-friction first run for someone
evaluating a project, and `docs/open-source.md` §5 already accepts that setup
friction is where this project loses against simpler competitors.

**The 7-day trap makes that path actively broken, not merely tedious.** An
external-user-type app with publishing status `Testing` is issued refresh
tokens that expire in 7 days unless the only scopes requested are a subset of
name, email and profile. `gmail.readonly` is not in that subset, so every
self-hoster following the README loses sync after a week with
`invalid_grant`. Whether flipping to "In production" while unverified stops
the clock is undocumented for restricted scopes — see `open-source.md` §2,
which deliberately refuses to assert either way.

**Multi-tenancy is no longer the constraint it was.** `docs/monetization.md`
§13.1's argument for OSS was precisely that each self-hoster registers their
own Cloud project and so stays under the 100-user cap. That argument still
holds, but it treats the Cloud project as unavoidable. It is not.

## 3. The options

| Option | Cloud project | 7-day trap | Access granted | Verdict |
|---|---|---|---|---|
| Gmail API + OAuth (today) | required | yes, unless production flip works | `gmail.readonly` | keep as alternative |
| **Gmail IMAP + app password** | **none** | **n/a** | full mailbox | **recommend as default** |
| Shared OAuth client in the repo | none for user | n/a | `gmail.readonly` | rejected, §7.1 |
| Forwarding to a mailbox we own | none | n/a | only what is forwarded | deferred, §7.2 |
| `.mbox` / `.eml` import | none | n/a | only what is imported | companion feature, §7.3 |
| Generic IMAP (non-Gmail) | none | n/a | full mailbox | near-free once §5 exists, §7.4 |

## 4. Gmail IMAP with an app password

### 4.1 What it removes

Setup becomes: enable 2-Step Verification, generate a 16-character app
password, paste it into Settings. No Cloud project, no consent screen, no
`credentials.json`, no test-user list, no verification question, no refresh
token to expire. IMAP itself needs no enabling — Google removed the
enable/disable toggle in January 2025 and IMAP is always on.

### 4.2 Why the port is cheap

This is the finding that moved the recommendation from "possible" to "do it".
Gmail's IMAP extensions line up almost exactly with what `gmail_sync.py`
already does.

- **`X-GM-RAW` accepts full Gmail search syntax** inside `UID SEARCH`. That
  means `_backfill_query()` — the `after:` window plus the `ALLOWLIST_DOMAINS`
  `from:` group plus the `SUBJECT_KEYWORDS` group — is reused *verbatim*. This
  was the part expected to need a rewrite into IMAP's clumsier `SEARCH`
  grammar, and it does not.
- **`X-GM-MSGID` is the same 64-bit message ID the API returns**, expressed in
  decimal over IMAP and hex in the API. `format(int(msgid), 'x')` therefore
  reproduces the exact `gmail_message_id` values already stored. Existing rows
  stay valid: no re-ingest, no duplicate emails, no damage to the matcher's
  `source_email_id` links.
- **`UID SEARCH` returns ascending UIDs**, i.e. oldest-first natively. The
  newest-first ordering bug documented in CLAUDE.md — where a rejection was
  processed before the confirmation that would have created its application —
  is structurally impossible on this path rather than fixed by convention.

### 4.3 What it costs

1. **Broader access than `gmail.readonly`.** An app password authenticates the
   whole account for mail: read, modify, delete, and send over SMTP. OAuth's
   read-only scope is genuinely narrower. For a single-user app on the
   operator's own machine this is an acceptable trade, but it must be stated
   plainly in setup docs rather than glossed as "simpler auth".
2. **Not available to every account.** Google excludes work/school/Workspace
   accounts, accounts with Advanced Protection, and accounts whose 2-Step
   Verification is security-key-only. Workspace operators should use OAuth
   with an **Internal** user type, which `open-source.md` §2 already notes is
   exempt from verification and the 100-user cap — so the two paths cover each
   other's gaps rather than competing.
3. **Google calls app passwords "not recommended".** No deprecation date is
   published, and the Workspace guidance says changes will be announced when
   they occur, but the direction of travel is clearly OAuth-only. This is the
   main argument for keeping the OAuth path maintained rather than deleted.
4. **Operational limits.** Gmail documents an IMAP bandwidth ceiling of
   2,500 MB downloaded per day (500 MB uploaded) and roughly 15 simultaneous
   IMAP connections per account. A 15-minute poll of one mailbox is nowhere
   near either, but a large first backfill fetching full bodies is the one
   operation that could approach the bandwidth limit. Note these figures are
   published for Workspace; consumer Gmail limits are not separately
   documented, so treat them as the best available estimate rather than a
   contract.

## 5. Implementation shape

The refactor is a provider boundary, not a rewrite. Both providers emit one
normalised dict — `{id, sender, subject, body_text, received_at}` — and
everything downstream of that is untouched.

| Today (`pipeline/gmail_sync.py`) | IMAP equivalent |
|---|---|
| `get_service()` / `service_for_user()` | `imaplib.IMAP4_SSL` + app password from the same encrypted `users.gmail_credentials` slot |
| `_backfill_query(months)` | reused verbatim as the argument to `UID SEARCH X-GM-RAW` |
| `_list_message_ids()` | `UID SEARCH` — ascending, so the reversal logic is unnecessary |
| `_fetch_and_store()` | `UID FETCH (BODY.PEEK[] INTERNALDATE X-GM-MSGID)` |
| `store_message()` | unchanged, once it takes the normalised dict |
| `is_candidate()` | unchanged |
| `extract_body()` | split: the HTML-strip core is shared; the part-walking differs (API payload dicts vs `email.message.Message.walk()`) |
| `incremental()` via History API | `UIDVALIDITY:last_uid` cursor, `UID SEARCH UID <n>:*` |
| `_save_cursor()` | same table; `gmail_sync_state.history_id` is `text` and holds the composite cursor with **no migration** |

Two further consequences worth noting:

- **Dependencies shrink.** `imaplib` and `email` are stdlib.
  `google-api-python-client`, `google-auth-oauthlib` and `google-auth` become
  optional extras needed only by the OAuth path — a real gain for a repo whose
  stated purpose is being read.
- **Blast radius is small.** `gmail_sync` has exactly four call sites
  (`cli.py` ×3, `gmail_oauth.py` ×1) and no test coverage today, so the
  provider boundary is also the opportunity to give it some.

## 6. Gotchas to design in from the first line

- **`BODY.PEEK[]`, never `BODY[]`, and `EXAMINE`, never `SELECT`.** A plain
  fetch sets `\Seen` and silently marks the operator's job emails as read.
  The API path physically cannot do this, so it is a new failure mode
  introduced by this change — user-visible, and the kind of thing that gets
  noticed only after a backfill has touched a year of mail.
- **`UIDVALIDITY` can change.** If the server returns a different
  `UIDVALIDITY` than the stored cursor, every UID is meaningless and the
  client must re-sync from scratch rather than trusting `last_uid`. This is
  why the cursor is a composite and not a bare integer.
- **CONDSTORE is available; QRESYNC is not.** Gmail supports `CONDSTORE`
  (`HIGHESTMODSEQ`, `CHANGEDSINCE`) but not `QRESYNC`. Not needed for v1 —
  this system only cares about new messages, not flag changes — but it is the
  right tool if reading `\Seen`/label state is ever wanted.
- **Do not hold connections open.** With ~15 simultaneous connections per
  account and the operator's own mail clients competing for them, the poller
  should connect, work, and disconnect rather than idle. `IDLE` is available
  but is not worth the connection budget for a 15-minute cadence.

## 7. Options rejected or deferred

### 7.1 Ship a shared OAuth client in the repo — rejected

Puts a client secret in a public repository, caps the entire user base at 100
unverified users of one project, shows every user an unverified-app warning
on a restricted scope, and lets a single bad actor get the shared client
suspended for everyone. This is the option that looks convenient and is not.

### 7.2 Forwarding to a mailbox we own — deferred

The operator sets a Gmail filter forwarding job mail to an address this system
reads, so no credential for their primary mailbox is ever stored. Genuinely
the strongest privacy story of any option here, and `open-source.md` §12
already lists forwarding ingest as surviving the SaaS-to-OSS transition. It is
deferred because it cannot do backfill at all — the feature that makes first
run feel like magic — and it needs inbound mail delivery, which is its own
operational burden for a self-hoster.

### 7.3 `.mbox` / `.eml` import — small companion feature

Google Takeout exports, or drag-and-drop of individual `.eml` files. Needs no
credentials whatsoever, gives a credential-free path to the backfill
experience, and doubles as the source for both the §6.1 seeded demo dataset
and realistic test fixtures. Recommended as a companion to IMAP, not as a
replacement for polling.

### 7.4 Generic IMAP — near-free, and a real differentiator

Everything in §5 minus `X-GM-RAW` (falling back to standard
`SEARCH SINCE/FROM/SUBJECT` trees) and minus `X-GM-MSGID` (falling back to
`Message-ID` plus `UIDVALIDITY`) works against Fastmail, self-hosted mail and
corporate IMAP. Against the §4 competitors — all Gmail-only — that is a
genuine differentiator for the cost of one fallback branch.

Consumer Outlook.com is **not** included in that claim: Microsoft's basic-auth
deprecation for consumer accounts runs in phases from March 2026, so Outlook
realistically requires its own OAuth implementation. Do not promise it.

## 8. What does not change

- **OAuth stays.** Workspace accounts cannot use app passwords at all, and
  Google's long-term direction is OAuth-only. The recommendation reorders the
  two paths in the documentation; it does not delete one.
- **`invalid_grant` still needs handling.** `open-source.md` §2's second
  bullet is unaffected by any of this. Tokens also die on Google-password
  change, six months of disuse, and user revocation, so the OAuth path needs a
  visible, non-traceback failure regardless of which auth is the default.
- **Invariant #1 is untouched.** IMAP is the user's own mailbox with the
  user's own credential; nothing here scrapes or automates against a job
  platform.

## 9. Open questions

1. **Does the author's own account permit an app password?** Requires 2SV
   enabled and rules out Advanced Protection and security-key-only 2SV. This
   gates testing the whole path and is a two-minute check.
2. **Is the app password stored in the same encrypted slot?**
   `users.gmail_credentials` currently holds an OAuth credentials JSON blob
   and is encrypted with `TRACKER_SECRET_KEY`. Reusing it keeps one secret
   path, but the column then holds two shapes and needs a discriminator.
3. **Does the provider boundary ship with generic IMAP (§7.4) in v1, or
   Gmail-only first?** Doing it later is cheap; doing it at the same time
   avoids a second refactor of the same code.
4. **Does this change the email-retention answer?** CLAUDE.md's open task #7
   asks whether to keep storing full `emails.body_text` or store only an ID
   and re-fetch. IMAP makes "re-fetch on demand" more attractive, because
   `X-GM-MSGID` is stable and searchable, so a body can be recovered without
   depending on a UID that `UIDVALIDITY` may invalidate. Worth deciding
   together rather than separately.

## 10. Sources

- [Gmail IMAP Extensions — `X-GM-RAW`, `X-GM-MSGID`](https://developers.google.com/workspace/gmail/imap/imap-extensions)
- [Google Accounts — Sign in with app passwords](https://support.google.com/accounts/answer/185833)
- [Gmail Help — Add Gmail to another email client (IMAP always on)](https://support.google.com/mail/answer/7126229)
- [Google Workspace Admin — Transition from less secure apps to OAuth](https://support.google.com/a/answer/14114704)
- [Google Workspace Updates — Winding down Google Sync and less secure apps](https://workspaceupdates.googleblog.com/2023/09/winding-down-google-sync-and-less-secure-apps-support.html)
- [Using OAuth 2.0 to Access Google APIs — refresh token expiration](https://developers.google.com/identity/protocols/oauth2)
- [Google Workspace Admin — Gmail bandwidth limits](https://support.google.com/a/answer/1071518)
- [RFC 7162 — CONDSTORE and QRESYNC](https://www.rfc-editor.org/rfc/rfc7162.html)
- [Microsoft modern-authentication enforcement timeline for IMAP/POP/SMTP](https://www.getmailbird.com/microsoft-modern-authentication-enforcement-email-guide/)
