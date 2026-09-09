# Worklog: the dated task register

This is CLAUDE.md's "Immediate next tasks" section, moved here VERBATIM on 9 Sep 2026.
It is the record of what each task measured and decided, by date; the
still-open items are summarised under "Open work" in CLAUDE.md. Gotchas it
cites as "above" now live in `.claude/rules/*.md`; the case names are
Northwind/Contoso-family placeholders (CLAUDE.md, Commands) and the dates are
the key to each real case.

## Immediate next tasks (in order)

1. **Real backfill: done.** Job search started 2026-07-16, so the `-m 12`
   (year-long) default never applied to this user — used `backfill -d 14`
   instead (`-d` overrides `-m` via `months = days/31`, cli.py). Actionable
   triage queue is clear. Concrete finding: an employer's "Welcome to Talent
   Community" autoresponder classifies as `recruiter_outreach` (scored 0.645
   vs. a real application to that same employer, just under
   `AUTO_MATCH_SCORE`) but is really a receipt tied to that application —
   first real candidate for a `email_classify_v2.txt` few-shot example. Still
   open: one inbound lead (a recruitment agency that withheld the client
   name) awaiting a company-name decision in the triage UI.
   **A second instance of that same shape, and it is structural, not a bug**
   (an agency recruiter's InMail, "Software Developer Role - Singapore", 3-4 Aug 2026): a
   LinkedIn **InMail notification** carries the recruiter's message text and
   nothing else — no employer anywhere in the body, and the sender is always
   `hit-reply@linkedin.com`, so the company can't come from the domain either.
   The extraction correctly returns `company: null`, and `find_match` returns
   `pending` on its FIRST line (`if not company`) — before the candidate gate,
   so neither `COMPANY_TRGM_MIN` nor the rescue rules ever run and
   `match_score` stays NULL for a third distinct reason. Expect every recruiter
   InMail to land in triage permanently; that is correct behaviour, since only
   a human knows which employer the recruiter meant (invariant #9: a
   `recruiter_outreach` is never auto-created). Do NOT "fix" this by inventing
   a company from the recruiter's own agency — the two are routinely
   different, and the record would assert something the email never said.
   Remaining from the original plan:
   tune match thresholds against real `emails.match_score` values, and watch
   for more `ALLOWLIST_DOMAINS` gaps as new mail arrives.
   **Threshold tuning has its first real result (4 Aug 2026)** — see the
   `COMPANY_TRGM_MIN` gotcha: the gate, not the score, was the problem, and it
   was fixed with a zero-candidate fallback rather than a new number. The
   scoring thresholds themselves (`AUTO_MATCH_SCORE`, `AUTO_MATCH_MARGIN`)
   remain untuned and still want a larger sample — though `AUTO_MATCH_MARGIN`
   has now demonstrably earned its keep, since it is the only thing that stops
   the rescue fallback auto-matching a same-titled unrelated employer (the
   Fincher Talent near-miss in that gotcha).
   **MEASURED 21 Aug 2026, and the answer is LEAVE THEM.** Not "still untuned" —
   swept, and 0.75/0.15 is a defensible spot, so re-derive nothing before
   reading this. Method: every email with a stored `extraction` and a resolved
   target (296 = 190 auto-matched + 106 human-resolved) replayed through the
   real `_CANDIDATES_SQL` and `_score()`, candidates filtered to applications
   that existed when the email arrived, thresholds swept 0.60-0.85 x 0.00-0.20.
   The human-resolved 106 are the informative half — a human picked those
   targets precisely BECAUSE the current bar sent them to triage, so they say
   what loosening would buy and how often it would land somewhere wrong.
   Relaxing the MARGIN at the current score is a bad trade under this project's
   own asymmetry: 0.15 → 0.10 buys **+3 correct for +1 silent wrong**, and → 0.00
   buys +10 for +3. Relaxing the SCORE is cheaper per error (0.75 → 0.60 at
   margin 0.15 is +9 correct for +1 wrong) but a wrong auto-match is silent and
   needs hand surgery while a triage item costs a click, so 9:1 is not obviously
   a win either. TIGHTENING is expensive and immediate: 0.75 → 0.80 drops 16
   already-correct auto-matches into triage, i.e. there is a real cluster of
   true matches sitting between those two numbers and 0.75 is just under a cliff.
   **Know what the replay cannot tell you**: it disagrees with recorded reality
   on 38 of 296, but 21 of those are `dispatch`'s `create` path (no candidates →
   it mints the application, which the replay does not model), leaving ~6%
   genuine drift — fine for reading the SHAPE of the sweep, not fine for moving
   a threshold on a 3-row difference. And thresholds cannot reach 46 of the 106
   triage items at any setting, because those had no candidate at all: 18
   `recruiter_outreach` (correct, invariant #9), 14 with no usable company (the
   LinkedIn InMail class), 9 whose target did not exist yet, and 5 real gate
   misses — of which 4 are one agency anonymising its client as "American Tech
   Organisation" and therefore unmatchable by any string rule. Tuning the
   thresholds was never going to fix that half.
   **A second finding the same day, from comparing against LinkedIn's own job
   tracker** (`/jobs-tracker/?stage=applied`, read by hand in the browser):
   all 77 of its applied entries were present here, matched by
   `postings.platform_job_id` against the `/jobs/view/<id>` links — zero
   missing, which is the first external confirmation that capture coverage is
   complete. The gaps run the other way and are all explained: LinkedIn has no
   record of an EXTERNAL apply unless you answer its "did you apply?" prompt
   (2 cases), and archiving a job removes it from the applied stage (2 more).
   The one thing LinkedIn genuinely knows that this system does not is
   "Application viewed" — 12 there against 7 `viewed` events here, its set a
   strict superset of ours. That signal only reaches us as email, so the
   remaining 5 are simply mail that never arrived or never classified; reading
   it off the page would mean scraping, which invariant #1 forbids.
   **Re-run 21 Aug 2026 at 167 entries, and this time NOT on the join key
   alone** — that is the upgrade worth keeping, because an id-only match calls a
   record correct when it has the right id and the wrong title, which is a bug
   this project has actually had. Method: page the tracker with a scripted
   `Next` (10/page, ~17 pages, accumulate in `localStorage`), then compare an
   FNV-1a digest of `norm(title + company)` per id — one number that checks both
   fields, and a hash mismatch is loud where a silent field difference is not.
   Verify the two hash implementations agree on one known row FIRST; if they
   don't, EVERY row mismatches, which is at least an obvious failure rather than
   a quiet one. Result: **167 on LinkedIn, 0 missing here** (176 of ours carry a
   LinkedIn job id), and 153 of the 167 agree on title+company exactly. All 14
   disagreements are explained and none is a wrong capture: 9 are company
   BRANDING (`Phone Co` vs `Phone Co, Inc.`, `GDI` vs `Graphic Design Institute`,
   `Fabrikam Group` vs `Fabrikam AG`, `Southridge APAC` vs `SouthridgeKelly`, `Litware
   Singapore` vs `Litware International (Singapore) Pte Ltd` — i.e. the exact
   fuel for the `COMPANY_TRGM_MIN` duplicates, seen from the board side), 2 are
   cards where LinkedIn shows NO company at all and our record is the richer one,
   2 are postings RENAMED after capture, and 1 is a title suffix our record lacks.
   The rename pair is the interesting one and the evidence for it is local, not
   inferred: Margie Group's stored `jd_text` opens with its own heading
   `"Applied AI, Software Engineer (Full-Stack / GenAI Systems)"` — our title —
   while LinkedIn now calls it `Machine Learning Engineer`, so title and JD came
   from the same posting and the posting changed later. Best For You Talent is the same
   shape (`Backend Engineer, AI` → `Python Engineer (AI)`, live page now reads
   "Reposted 2 weeks ago" against the `3 weeks ago`/not-reposted we captured),
   though its JD could not be re-read to prove it — a closed posting will not
   render its description. **Checking the stored JD against both candidate
   titles is the cheap discriminator here**, and it is available offline: a
   wrong-title capture takes the title from a DIFFERENT job, so its JD names the
   other title, whereas a rename leaves title and JD agreeing with each other.
   Going the other way, 9 of ours are absent from LinkedIn's applied stage: 7
   external applies (expected — LinkedIn cannot see one), and 2 Easy Apply
   records that LinkedIn simply does not list. Neither of those two is ours to
   fix: Coho's posting has been DELETED (`/jobs/view/<id>` returns "the job
   posting has been removed"), and Consolidated Messenger's job page still says "Application
   submitted" while the applied list omits it. Do NOT read LinkedIn's
   "Go to company site" link on a submitted application as "you applied
   off-platform" — it says that on the Consolidated Messenger one, which has 8 wizard-captured
   `application_answers` and a `resume_file`, and those exist only for Easy
   Apply. LinkedIn's own "Not seeing some jobs?" disclaimer is the honest
   summary of its list.
   **Note (28 Jul 2026):** switching this account to IMAP and re-running
   `backfill -d 14` (Step 7 of the IMAP rollout, see `docs/email-ingest.md`)
   added 4 more real candidate emails, still `pending` in `job_queue` —
   `work --once` hasn't been run against them yet, so `status` will show a
   nonzero queue depth until it is.
   **Outstanding as of 7 Aug 2026: a catch-up sweep under `INGEST_ALL`.** Only
   `backfill -d 1` has been run since the flag went on, so every employer-domain
   and `candidates.workablemail.com`-style email older than that is still
   missing (the Workable gap has existed for the whole search). `backfill -d 21`
   would recover them, at roughly 200-400 classify calls (~$2-3) and a longer
   triage queue — deliberately deferred, not forgotten. Triage stood at 14
   pending after the `-d 1` run.
2. **OAuth token expiry — largely resolved.** See the 7-day gotcha above; IMAP
   + app password is now the default (`docs/email-ingest.md`, shipped and
   verified against a real inbox 28 Jul 2026), which has no refresh token to
   expire, and `invalid_grant`/`RefreshError` now surfaces as the same
   `mailbox.MailboxAuthError` an IMAP login failure does — handled visibly in
   the CLI, `sync`, and Settings, not a traceback. What's still genuinely
   open: the empirical test of whether flipping Google Cloud publishing
   status to "In production" while unverified stops the 7-day clock — never
   run, and this account no longer depends on the answer since it's on IMAP.
3. **Release blockers** (`docs/open-source.md` §11, ordered there): LICENSE
   (Apache-2.0 recommended), extension split into its own repo, decide
   whether CLAUDE.md ships. Fixture names and git history are both done
   (26 Jul 2026) — the history rewrite was needed for personal data, not
   secrets; §11 records the method and the three false-positive traps.
   **Both had regressed by 2 Sep 2026** — 37 commits of ordinary
   comment-writing put ~35 real names back, two of them people — and both
   were redone that day, CLAUDE.md included. "Does CLAUDE.md ship" is now only
   a question about publishing the author's own search narrative, and
   `scripts/audit_names.py --history` is the pre-publish check (CLAUDE.md, Commands).
   **The resume-profile blocker is CLEARED** (21 Aug 2026): a clean checkout
   used to fail cover-letter generation with `resume profile not found at
   profile.md`, a path nothing in the product ever wrote to, while the route
   that works (Settings -> Resume profile) went unmentioned. The file fallback
   is gone entirely — see CLAUDE.md's Environment section for why it was also a tenancy
   hole. Remaining blockers are the three named above.
3b. **`applications.focused` — CUT, 21 Aug 2026.** Migration 015 drops the
   column; `analytics.by_focus` and its artifact-existence COALESCE fallback,
   the detail-page toggle, the `/applications/{id}/focused` route, the
   manual-entry and edit selects, the analytics panel, and the receipt's
   Tailored/Generic buttons all went with it. The final tally is why: **175
   `false`, 22 unset, zero `true`** across every real application, so the
   dimension had exactly one value and `_rate`'s own `len(rows) > 1` guard meant
   the panel had never rendered once. **That evidence is the whole case and it
   stands alone** — a constant is not a dimension.
   **Correction, same day: `by_resume` is NOT the successor, and saying so was
   wrong.** The claim "resume_file is what focused was meant to measure"
   originates in `migrations/014`'s header (3 Aug) and was repeated into
   task 3b, `analytics.py` and the analytics template before anyone checked it
   against the column's contents. They measure different things.
   `focused` asked about EFFORT — was this application customised for this role.
   `resume_file` records POSITIONING — which of two STANDING resumes was sent
   (`…-resume-AI-engineer.pdf` 67, `…-resume-dotnet-engineer.pdf` 21), neither
   of them written per employer. So `by_resume` answers "which framing gets
   replies", not "does effort pay off". The narrow true version: this author
   never wrote a bespoke resume, so the only thing `focused` could ever have
   registered on their real behaviour is "did I send the resume matching this
   role" — which `resume_file` does capture, better and without asking. That is
   a much weaker claim than the one that was recorded.
   **Consequence: "does tailoring pay off" is now UNMEASURED, not answered.**
   If it is wanted back, the lesson from `focused` holds — it has to be read off
   something observable at apply time (a per-application cover letter existing, a
   resume filename that varies by EMPLOYER rather than by track), never asked for
   afterwards. A third framing of the same question, asked the same way, fails
   the same way.
   `migrations/014` and `015` still carry the superseded claim in their header
   comments and were deliberately left alone — invariant #8, never edit an
   applied migration, and a comment is not worth making an exception for. This
   entry is the correction of record.
   Two knock-on decisions worth knowing: `/captures/{id}/tag` survives carrying
   only a note (still its own route, because the capture must not wait on a
   human), and the external-apply confirm popover collapses from three tagging
   buttons to one "Yes, capture it" — the tag used to BE the confirmation there,
   so that box needed a button of its own or the ask-first path would have had
   no way to say yes. `tests/test_phase3.py` now asserts the `by_resume` panel
   in place of the old `by_focus` one, and seeds two resume files so the
   dimension actually splits rather than asserting on an empty state.

4. **First feature: follow-up drafting** (`docs/features.md` §3.1) — best
   evidence-to-effort ratio in the backlog, and `REMINDER_DAYS = 10` already
   matches the researched 7–10 business-day window. **Half-built as of 28 Jul:**
   the `/follow-ups` queue (UI rule 9) already surfaces the right set and logs
   a one-click `follow_up_sent`. What's missing is the draft itself — that page
   is where it belongs, next to the button that currently just marks it done,
   and it has more room for one since the queue stopped sharing the list.
   **First check whether the SET is still the right one.** `REMINDER_DAYS = 10`
   was chosen against the researched 7-10 business-day window and validated at
   18 rows out of 48 applications. At 195 applications the same rule returns
   **98**, which is not a day's task list by any reading — and a queue nobody
   can finish stops being worked at all, which would waste the drafting feature
   rather than justify it. Measure before building on top of it: how many of
   the 98 are old enough that a follow-up is pointless, and does a second
   threshold (or an age cap) cut it to something workable? The number moved
   because the denominator did, not because the rule broke.
   **MEASURED 2 Sep 2026, and an age cap is the fix.** 133 of 210
   applications qualify, and not one `follow_up_sent` event has ever been
   filed — the queue has never been worked, which is what an unworkable list
   looks like. Reply latency over the 58 applications that got a first reply:
   median 3 days, 90th percentile 8, slowest 22. So beyond three weeks no
   application in this dataset has ever heard back, yet 93 of the 133 are
   older than 21 days (58 at 22-30, 21 at 31-45, 14 beyond 45). A cap near
   21 days leaves ~40, which is a day's list; the 10-day ENTRY threshold
   itself agrees with the data. Build the drafting feature on the capped set,
   not on the 133.
5. **One real apply via the extension** on each platform; fix whichever
   adapter selectors have drifted. **LinkedIn: done** (27 Jul, VANARSDEL — three
   defects found and fixed). **JobStreet: two real applies, still not
   clean.** 28 Jul (Baldwin Recruitment) surfaced the navigating-apply receipt
   loss and the opening-click timestamp; 29 Jul (First Up Consultants)
   surfaced an MV3 service-worker race that lost the stashed company name
   entirely (`unknown company` on the record — corrected by hand). All three
   fixed same-day; salary/work-type capture added alongside. **One more real
   apply needed** to confirm the race fix holds — see known-untested.
   **LinkedIn is now the well-exercised path** (3 Aug 2026): Easy Apply and
   external both verified end to end on real applies, plus the screening-answer
   labels, the recruiter card, the resume field and the external-apply popover.
   JobStreet's race fix is still the one waiting on a real submit.
   **Two real LinkedIn losses since, both refiled by hand** (4 Aug 2026):
   wideworld.ai (external — root-caused to the preload-frame gotcha above, fixed
   by `relayApply`) and Lamna · Senior AI Engineer (Easy Apply — **cause
   never established**; it fell in the window right after an extension reload,
   so the orphaned-context case is the leading candidate but was not confirmed,
   and the popup's ring buffers were not read in time). Both records exist with
   `captured_via='manual'`; the Easy Apply's screening answers are gone for
   good. **Next Easy Apply is the one to watch** — with `tell()` in place a
   dead context now announces itself instead of vanishing, so a repeat that is
   STILL silent means a real detection miss on that form, not a stale tab.
   **A third loss, 21 Aug 2026, and it was the last one of its family**: Woodgrove
   Finance (external apply) saved NOTHING at all — the popover said so, which
   is the one thing that went right. Root-caused for real this time, off the
   extension's own LevelDB rather than a live session, and the answer retired
   two earlier misdiagnoses: `getJob()` was walking UP to `window.top` from a
   frame that HAD the job. Fixed in extension **0.8.0** along with the
   immediate path's tab-URL fallback and a failure record that can tell the
   remaining shapes apart. The record was recreated via `/applications/new`,
   and the blind Lucerne Consultants capture from 20 Aug repaired via `/edit`.
   Also settled by that dump: **one older loss, 7 Aug 12:30 SG, is
   unidentifiable** — nothing was applied to that day and the failure record of
   that era holds no job id and no tab URL.
   **What to read on the next apply**: `doc_source` in the provenance line.
   `"self"` is positive proof the new fallback rescued a capture the old code
   would have lost, and nothing else demonstrates it — `tests/test_extension.js`
   proves the LOGIC, not that it fires in a real frame. Verify 0.9.0 is live
   and the tab was opened after the reload first. The same apply also
   answers whether the `<dialog>` sweep fix works (`.claude/rules/extension.md`, Known-untested).
   **Indeed: never exercised.** Keep this **load-unpacked only** — an
   unpacked extension has a random per-install ID, while a Chrome Web Store
   listing mints a stable public one that LinkedIn's extension-fingerprinting
   script enumerates (`docs/open-source.md` §3). Do not publish to the store
   during the author's active job search.
6. If enabling dedup: set `VOYAGE_API_KEY`, run `scan`, review duplicate
   bands in `/triage` on real cross-platform posts.
   **Never enabled as of 2 Sep 2026**: 0 embeddings, 0 candidate pairs, and
   every one of the 210 jobs has exactly one posting, so `merge_jobs` has only
   ever run in tests. Extraction verification is similarly unused (0 of 192
   rows verified) and 4 cover letters exist in total. For a release these are
   surfaces the README presents as complete that no real data has exercised —
   either exercise them once or label them experimental.
7. **Decide the email body retention policy** (design doc §14 open
   question #4, `migrations/001_init.sql:86-87`) — overdue by its own stated
   trigger: the comment's "revisit before multi-user" passed when
   `003_multi_tenant.sql` shipped RLS. Raw `emails.body_text` (recruiter
   names, salary figures, personal details) sits unencrypted-at-rest per-row
   with no retention policy. Decide: keep storing full bodies for
   reprocessing (current behavior), or store only `gmail_message_id` and
   re-fetch on demand. **Under the open-source direction this is ordinary
   hygiene, not a compliance gate** — it is the self-hoster's own data on
   their own disk. It was a hard gate only on the (superseded) hosted path,
   where Google's restricted-scope limited-use policy applies
   (`docs/monetization.md` §2.2). IMAP shipping makes "re-fetch on demand"
   more attractive than it was: `X-GM-MSGID` is a stable, searchable id, so a
   body can be recovered without depending on a UID that `UIDVALIDITY` can
   invalidate — see `docs/email-ingest.md` §9 q4. The IMAP work deliberately
   did not decide this; it remains open.
   **Half-decided as of 7 Aug 2026, for the `not_job_related` half only.**
   `INGEST_ALL` (invariant #10) had to answer this to ship at all — removing
   the pre-filter means personal mail reaches the DB — so
   `worker.handle_classify_email` now purges `body_text` once the classifier
   rules a message out, keeping the row for ingest idempotency. That is the
   easy half: nothing re-reads a `not_job_related` body. **Still open: bodies
   of mail that IS job-related**, which is the recruiter names, salary figures
   and personal details the original question was actually about, and which
   `extract_email` and any future re-run genuinely need. Note the purge only
   fires under `INGEST_ALL`, and only on newly-classified rows — pre-existing
   `not_job_related` bodies were left in place rather than mass-deleted.
   Counted 2 Sep 2026: 225 such rows still hold a body, 2.5 MB of the 3.85 MB
   of bodies stored, and nothing reads them. One UPDATE clears them —
   deliberately not run, since it is a deletion and the author's call. The
   other half, bodies of job-related mail, is still the open question.
8. **The vLLM lab** (`docs/vllm-lab.md`, started 8 Sep 2026): five stages on
   one L4 in GCP — first serve, a replay of the ~300 stored classifications
   through an open-weight model (`scripts/replay_classify.py`), serving
   internals, Cloud Run / GKE / tensor parallel, and embeddings served by
   vLLM to switch dedup on for the first time. Blocked at its §2 until the
   author clears the billing-account project limit and the L4 quota by hand
   (their call, deliberately); `pipeline/llm.py` stays unverified against a
   real server until stage 1 runs. The replay is the measurement that decides
   whether an 8B model can take classification over — never reason about it.
9. **Rejection reasons on every rejected event** (9 Sep 2026). Asked whether a
   visa non-proceed deserved a different indication than `rejected`. Measured
   first: 51 rejected events, 40 by email with an empty payload and 11 by hand
   (6 visa, 3 role_closed, 2 other); 5 of the 6 visa ones were INBOUND leads
   (a recruiter approached, then dropped the thread when visa came up), and
   only 1 of the 40 rejection emails mentions a visa at all — the reason
   arrives out of band, which is why the manual form got the field on 28 Jul.
   22 postings were extracted `local_only`; the user applied to all of them,
   6 are rejected, 11 still wait. Decided: keep `rejected` as the status (a
   `visa_blocked` type was costed and refused — `.claude/rules/web-ui.md`
   rule 12) and make the REASON first-class instead: `set_rejection_reason`
   tags any rejected event whatever its source, the list wears the reason in
   grey and filters on it (`unrecorded` is the tagging queue: 38 applications
   on the day it shipped), and `/analytics` counts it per reason with an
   inbound split. Found while verifying on real data: the list's search box
   had read "None" since the stall band shipped on 8 Sep (`base.html`'s
   top-level `{% set q %}` shadowing the child context — gotcha in the web-ui
   rule); fixed and pinned by a test. Still open, and the honest use of the
   tally: nothing yet draws the line from `extractions.visa_signal =
   local_only` to the application's outcome — that join is the
   COMPASS-adjacent panel `docs/features.md` §3.4 wants.
