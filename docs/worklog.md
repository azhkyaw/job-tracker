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
10. **The stored email body is the HTML alternative, not the `text/plain`
   part** (23 Sep 2026). Reported as two triage previews of a LinkedIn
   "Your application was viewed by <agency>" email showing only the footer.
   The raw message's plain part IS the footer; everything (role, "Applied
   on", the poster) is HTML-only, and both extractors preferred plain. Sized
   by re-fetching all 477 job-related rows read-only: 436 have both parts,
   41 HTML only, 68 stored bodies were stubs (LinkedIn's "viewed" template
   40/40 and its rejection template 27/27, one agency mail stored empty), and
   four senders in total ship a plain part that is not the mail (`.claude/
   rules/mail-ingest.md` has each). Every stub scored 0.75 — company only,
   title neutral — so the two at an agency with three applications could
   not be resolved by the machine. Fixed by one shared choice
   (`mailbox.body_from_parts`, HTML first per RFC 2046 §5.1.4) and a
   parser-based `html_to_text`; replayed 30 real emails through classify +
   extract on the new bodies (29/30 agree, every role_title change a stub
   gaining its title) before touching data. Backfill: 477 bodies rewritten
   from the raw messages with the old text as the UPDATE guard and in a
   snapshot; 18 pending rows re-extracted and re-dispatched — the three
   "viewed" ones auto-matched at 1.0 to three different applications, the
   15 referral rows (inbound, by invariant #9 never auto-matched) now name
   their roles; triage 27 → 24. Cohort check of the 67 already-filed stubs:
   65 name the role they were filed on, 2 are one employer's "viewed" mails
   carrying a renamed posting title (same applied day, one application), so
   no refile. Note for `scripts/replay_classify.py`: the stored bodies are
   now the new extractor's output, so a replay compares a candidate model on
   the corrected text against labels made from the old text — a better
   corpus, not an apples-to-apples one, for the 68 stub rows.
11. **The exact-title rescue needs a shared company word** (23 Sep 2026).
   Asked why an employer's interview thread had filed onto an agency's
   application. Replayed the matcher's candidate queries for the email: the
   employer's mail name missed the trigram gate against the board's longer
   employer name (0.417), rule 2 admitted the real record, rule 1 admitted
   the agency because its posting title matched the mail's wording byte for
   byte, and the title term outscored the real record 0.875 to 0.662 — a
   silent wrong auto-match on a live thread, the failure invariant #3 ranks
   worst. Every real rule-1 rescue on record shared a company word with the
   mail; the stranger shared none, so that is now the condition
   (`config.RESCUE_SHARED_WORD_MIN`). Three variants replayed over all 427
   stored emails before choosing (`.claude/rules/matching.md`): the chosen
   one changes 11 decisions — 7 silent wrong matches become triage items, 2
   hand-resolved emails become correct auto-matches, 2 correct auto-matches
   become triage items. Data: the four emails re-filed through `refile_email`
   (snapshot first), the stray contact removed; the employer's application
   now reads `interview_invite`, the agency's `viewed`. Path 3d's fixture,
   which had modelled a company sharing nothing, was reshaped to the real
   pattern and path 3f added for the stranger.
12. **How the rejections ended, at a glance** (23 Sep 2026). Asked to see
   the 55 rejections on the list page as three kinds: after at least one
   round (a screening call or a coding test), on visa, or just a platform's
   auto-reply. Measured first: 7 visa (6 with no round), 5 after a round
   (2 role filled, 1 other, 2 untagged), 43 without a round — 29 LinkedIn
   form letters, 11 ATS form letters, 3 filed by hand; the closing email's
   extracted `platform` agrees with its sender on all 40. The stage is
   derivable from the timeline, so unlike the reason chips (42 of 55
   untagged) the split is complete on day one, which is what justifies
   showing it at rest beside the funnel's rejected entry rather than only
   on click (`.claude/rules/web-ui.md` rule 13). One bucket expression
   (`analytics.rejected_how_sql`) feeds the list's `how` filter, the chips
   and a sibling "How it ended" table on /analytics with the channel
   columns. Not done, deliberately: a per-row badge (the trace draws the
   rounds already) and a LinkedIn-specific bucket (the channel lives in the
   chip's hover text and the analytics table; a fourth chip is one line if
   wanted).
13. **The UI, redrawn** (23 Sep 2026). Asked to envision a new UI, then to
   implement it. Looked first: rendered all 277 real rows and found the
   list's axis labels physically colliding at the capped trace width (the
   boxes of "2 Jul" and "16 Jul" 1px apart, "10 Sep" against "today"), the
   trace column 90% empty gridline under newest-first because the axis is
   12 weeks and most rows are under three, and Dark Reader active in the dev
   profile (8 injected styles), so the 28 Jul palette had still never been
   seen rendered. Kept every information-design rule that had been verified
   against real data (`.claude/rules/web-ui.md` rules 1, 4-13: colour = the
   state of the wait, one axis, funnel-as-filter, follow-ups as a page,
   submission-date sort, no JS); replaced the visual language wholesale
   (rules 3, 14-16): Newsreader + IBM Plex Sans Condensed for Archivo + DM
   Mono, warm stone for blue-slate, no uppercase labels, no monospace outside
   code, no middle-dot meta strings, sentences for event types
   (`web.EVENT_LABELS`), facts as label/value lists for the nine-column
   postings table, per-email re-file collapsed into a `<details>`, ink
   buttons and links. The one new mechanism is `trace.heat()`: amber graded
   from the follow-up threshold to eight weeks, one `--heat` per row driving
   the rail numeral, the tail and the queue's count through one color-mix.
   Axis ticks became month starts on spans of eight weeks and up. Contrast
   verified numerically for both palettes (light amber darkened from AD6207
   to 9A5705 after failing AA at 4.25:1). Four test anchors changed with the
   copy ("Inbound, awaiting your call", "Why? Not recorded", "Same job,
   merge them"); nothing behavioural moved. The mockup it was built from is
   a private artifact linked from `project_ui_redesign_vision_2026-09-23.md`
   in the memory directory. Still owed: eyes on the app itself without Dark
   Reader, and the heat curve's 56-day ceiling is a judgement that a replay
   over the 150 waiting rows could settle.
14. **Inbound moved to its own page** (24 Sep 2026). Asked whether inbounds
   should leave the Applications list; the answer was in the list's own
   code. Its default-sort comment says the page should read as "what you
   sent, most recent first", and the leads-first pin, the `.tl-sep`
   dividers, the `lead` flag, the `started_at` sort and trace.py's blue
   start all existed to make a population that is not that fit a page built
   for the other one. Real data on the day: 255 applied, 22 inbound (11
   awaiting a decision, 1 interviewing, 10 rejected), 1 saved; the pin was
   designed for 4 leads and 11 now sat above the first application — the
   follow-ups move (task 9's rule) at nearly three times the size. Built
   `/inbound` as a sibling of `/` off one `_list()` builder, one template,
   one `_SORTS`; membership by origin strictly (2 of the 22 have a later
   `applied` event and stay put), `?origin=` and the All/Applied/Inbound/
   Saved tabs gone, a grey `saved` tag in their place, a page count where
   the tabs stood, an inbound lede (approaches / awaiting / reached an
   interview), a triage-lane nudge instead of the follow-up one, a nav pill
   for the leads awaiting a decision, delete redirects and detail-page nav
   following `_list_path()`. `.claude/rules/web-ui.md` rule 17 has the
   design. One inherited claim fell on the way: the rules file said a Jinja
   macro cannot see the render context; it can (same-template macros, the
   shape `list_url()` has), and the macro now reads `page` from it. Tests:
   the ordering block moved to `/inbound`, the `_SORTS` loop crosses both
   pages, and the pill, the detail-page highlight and the delete redirect
   are asserted.
15. **A recruiter's approach, filed by hand** (24 Sep 2026). Asked whether a
   record that began with a recruiter's WhatsApp message, followed by the
   user emailing a resume, counts as inbound. It does: origin is who started
   the thread, and the resume is the lead being taken up, the shape of the 2
   promoted leads already on `/inbound`. Left alone it was 1 of the 5
   interviewing records on the record page. The data held no trace of the
   approach; the record began at the resume email, created by triage's
   "Create application", the actionable lane's only button. A search for
   records whose apply was an email the user SENT found one more of the
   same shape, a 27 Aug agency thread whose first email opens "Great
   speaking with you" and was classified a status update. Nothing in the app
   could fix either: no form edits origin, and `recruiter_outreach` was
   excluded from the timeline form. Built "A recruiter approached me first"
   on the timeline and on manual entry (rule 17 of the web-ui rules has the
   rules), invariant #9 reworded around it. Not applied to the two real
   records: the WhatsApp date is not in the data (the resume email says only
   "As discussed"), and the agency thread's direction needs the user's word.
   Known gap, unchanged: `merge_jobs` never reconciles origin.
   **Then applied by the user, the same night** (found 24 Sep 2026 while
   syncing context): both records carry a hand-filed `recruiter_outreach`
   event, channel WhatsApp, `origin_was: applied`, created at 01:31 and
   01:32 SGT, five minutes before this entry was committed, so its "Not
   applied" was out of date on arrival. The 3 Sep thread still reads
   interviewing, the 27 Aug agency thread `applied`. Counts after: 24 on
   `/inbound`, 253 on `/`, 1 saved (task 14's day was 22 and 255).
16. **Mail the user sent, filed as what they did** (24 Sep 2026). A
   read-only data audit, asked for as "anything odd about the data",
   ranked this first: grouping events by the email's SENDER showed 27
   stored emails from the user's own addresses producing 28 events as if
   an employer had sent them — replies in scheduling threads filed as
   `interview_invite` (4 of one application's 19), follow-ups as notes, one
   reply dated three weeks ahead off the interview day it quoted, and two
   resume emails ruled `not_job_related` with bodies purged. Nothing in the
   code or docs recorded that ingest reads All Mail and so reads both
   directions. Probed the real mailbox read-only before designing: Gmail's
   `\Sent` label is on 26 of the 27 own-address messages, and the 27th is a
   confirmation the user FORWARDED in from another address — received, and
   rightly a confirmation — so the label, not the From address, is the fact
   (`.claude/rules/mail-ingest.md` has the wire form, which is not the one
   Google's docs print). Built: migration 016 (`emails.sent_by_user`, four
   `sent_*` classifications), both providers reading the label, a separate
   `email_classify_sent_v1` so received mail is byte-identical, and the
   matcher rules in `.claude/rules/matching.md` — including a latent bug
   the tests exposed (`dispatch` never passed the classification to
   `_create_application`). Suites: all eight green, with new checks in
   test_llm, test_email_ingest and test_integration path 3g. Repair,
   dry-run first and snapshotted (outside the repo, in
   `job-tracker-snapshots/2026-09-24-sent-mail.json` beside it: 99 events,
   10 applications): the 26 re-classified with the new prompt (16 reply, 7
   application, 3 follow_up); each filed one had ONLY its own events
   replaced, on the same application, dated to its send time; where the old
   events included an `applied` fabricated by triage's "Create
   application" (the email is what made the record), that start was kept.
   Result: 0 `interview_invite` from the user's own mail (was 11), 3
   `follow_up_sent` recovered, one inbound lead now `applied` (the user had
   answered the recruiter with a resume), the two recovered resume emails
   in triage (no company named in either), every audit invariant still 0.
   Left for the author: one 23 Sep follow-up is filed on a different
   employer's application, a hand-link slip made in triage three links in
   22 seconds; the repair kept the human's match and corrected only the
   event, so re-filing it from the detail page moves it cleanly.
17. **An event happens when its email arrives** (24 Sep 2026). The open
   `_event_time` bug of 2 Sep, picked up as problem 2 of the same day's data
   audit. Measured first, read-only: 14 of 445 received-mail events sat off
   their email's arrival, not the audit's 8 (that count took only offsets
   over 3 days) — 10 invites 1-21 days ahead (3 events in the future, all on
   one live interview thread), a rejection a month back on the apply day it
   quoted, and 3
   notes, two of them a misread "Applied on 29 Jul" (as 2 Jul, confirmed
   against both bodies). No reading of a stated date survives all of them,
   since nothing says which event it is FOR, so it went into the payload
   (`stated_date`) and every event onto arrival. Replayed the matcher first,
   because the same timestamp is its date signal: 2 of 14 decisions moved,
   one each way, neither wrong. The page shows a stated date only when it is
   ahead of arrival ("for 14 Oct 2026" on a live interview thread), which
   the two misread digests would otherwise have contradicted. Suites: all
   eight green; `test_integration` path 2 had pinned the old rule and now
   pins the new one. Repair, dry-run first and snapshotted
   (`job-tracker-snapshots/2026-09-24-stated-dates.json`): 14 events moved,
   199 annotated, 0 skipped, 0 left in the future. Detail and numbers:
   `.claude/rules/matching.md`. The other machine keeps the old rule until
   it pulls.
18. **C# and C++ are two questions** (24 Sep 2026). Problem 3 of the same
   day's data audit: the answer key kept `[a-z0-9]` only, on the server and
   in the extension alike, so both keyed as "…with c". Measured the
   candidate rule over all 888 stored answers before writing it: 6 keys
   change, 1 group splits, none merge — and the sixth was a second case of
   the same defect nobody had listed, a question in Chinese keyed as the
   bare `c`. So the rule became general (any script's letters, marks and
   digits, NFKC, a letter-glued `#`/`+` spelled) rather than a `#`/`+`
   exception. Validated the new extension test against the old key first:
   with the two questions on different wizard steps, the old key LOSES the
   first answer before capture — a worse bug than the one reported, which
   the real form escaped by asking both on one step. Built `answers.renorm()`
   and `cli renorm-answers` for the stored rows, since the key may only be
   derived in Python and every future change to it strands rows the same
   way; dry-run, snapshot (`job-tracker-snapshots/2026-09-24-answer-keys.json`),
   applied: 6 re-keyed, a second run finds 0, 888 rows, no occurrence gaps.
   Suites: all eight green; `tests/question_norms.json` is read by both the
   Python and the Node suite. Extension 0.10.1 — reload it and refresh open
   tabs. Detail: `.claude/rules/extension.md`.
19. **An earlier application to a role applied to again** (24 Sep 2026).
   Problem 4 of the same day's audit, parked as the author's decision.
   Re-measured first: 15 of the 150 follow-up rows, not the audit's 12.
   Measuring also overturned the audit's guess that one studio's repeated
   ads (Adventure Works, `.claude/rules/matching.md`) were distinct roles —
   their job descriptions are word-for-word identical — and separated the
   one pair known to be two roles (applied 5 Aug and 17 Sep: identical
   title, 36% word overlap, each answered on its own). Put three options to
   the author: rule suggests and you confirm, fully automatic, or by hand.
   They chose the first and left the studio's four undecided. Built:
   `analytics.reapplications`, the row's second line and "Same role, close"
   button on /follow-ups, `web.mark_reapplied` (a `withdrawn` with
   `payload.superseded_by`, worded "You applied again"), and
   `ingest.UNKNOWN_COMPANY`/`UNKNOWN_TITLE` so the placeholders never
   match. On the dev DB the real function flags the same 15, one role's
   three attempts (17 Jul, 5 Aug, 23 Sep) chaining 1 -> 2 -> 3; the live page was measured, not just
   rendered, which caught the column jog. Suites: all eight green, 20 new
   checks in test_web. Nothing was filed on the author's behalf: the 15
   wait on /follow-ups for their clicks. Detail: `.claude/rules/web-ui.md`
   rule 9c.
20. **A "saved" tag on an application that was sent** (24 Sep 2026).
   Problem 5 of the audit, framed there as "should applying move origin
   saved -> applied?" Measured first, and the record's history said
   otherwise: the confirmation email (12:33) predates the capture (12:34),
   so it was never saved. The job was APPLIED to, the extension missed the
   capture, and the popup's only button is "Capture this job as
   interested", so recording it through the extension meant filing it as
   saved and marking it applied by hand. The list's tag claims "never
   applied to" but tested `origin == 'saved'`; it now tests that too, and
   no applied event. Invariant #9 is untouched (origin is still never
   derived), and the author chose to correct this one record's
   demonstrably false origin by hand, snapshot first
   (`job-tracker-snapshots/2026-09-24-saved-origin.json`): 257 applied, 24
   inbound, 0 saved. Not built, offered: a popup button to capture a job as
   applied. Suites: all eight green; the tag had no test before.
21. **The audit's minor items** (24 Sep 2026). Re-measured all ten first;
   two of the "minor" ones hid real mechanisms. Duplicate extraction rows (7)
   had two causes: queue duplicates (a `scan` re-run during the outage) and
   the edit form re-queuing an UNCHANGED JD, because browsers submit
   textareas with CRLF and the comparison was raw — which had also written
   CRs into 50 of 245 stored JDs. Fixed both (`worker.handle_extract_jd`'s
   time guard, `web._form_text` on every textarea), normalised the 50,
   deleted the 7 older rows; the guard was then seen working live. The JD
   never extracted was a 3 Aug hand-transferred JD (a raw UPDATE skips the
   pipeline): `scan` + `work --once` drained it and the 3 jobs pending since
   02:46 (4 Haiku calls). Expired sessions (18) now clear on every login.
   Email-made postings get `company_norm` like captured ones (29
   backfilled). The truncated title is JobStreet's own cut — its own email
   subject carries the same text — so nothing to fix. The author's calls:
   the 11 keystroke snapshots in three "City" answers deleted (the prefix
   rule found 9; the whole lists showed 2 typo'd ones of the same kind), one
   employer's 15 referral emails left in triage for them, duplicate rejections left
   (cosmetic), one-company-two-names deferred. Left for the author: the 3
   blind captures, fixable by re-capturing each posting from the popup
   (it fills the JD, queues extraction and adds no event to an applied
   record). Snapshots for every repair in `job-tracker-snapshots/`. Suites:
   all eight green, 6 new checks.
22. **Every email since 16 Jul 2026, reconciled** (24 Sep 2026). Asked to
   make sure nothing was missed and everything was filed right. Read-only
   first: the mailbox's headers through the app's own IMAP provider
   (All Mail, Spam and Trash selected read-only, `BODY.PEEK` headers — no
   bodies, nothing marked read) against the `emails` table. 1,055 messages
   since 16 Jul; every one up to 01:45 that day was stored, none stored had
   vanished; the only 11 missing had arrived since, because sync had not
   run — `sync` + `work --once` filed them. Spam held one marketing mail.
   LinkedIn's "your application was sent" mail only starts reaching this
   mailbox on 23 Jul, so the 20 on-platform applications of 16-22 Jul never
   had one to file. Filing: every email processed, every filed email's
   events on exactly its own application; the platform subject templates
   ("sent to X", "viewed by X", "Thanks for applying to X") agreed with the
   filed record on 227 of 227; no event before its application; nothing
   about an application wrongly set aside (every portal mail — account
   verifications, "complete your application" — belongs to a recorded
   application or to one never submitted). One misfile, the known 23 Sep
   follow-up, re-filed through `refile_email` (snapshot first). Two title
   mismatches looked like the stale-pane capture bug and were NOT: each
   job's own page read "Application submitted" for the stored job id —
   `.claude/rules/matching.md` has the lesson. The author's calls: a 16 Jul
   acknowledgement hand-linked to a differently named record is right as
   filed; two confirmations the margin held back (one studio's repeated
   title) and a sent reply wait in triage for them.
   **The set-aside mail was first checked by SUBJECT only, and the author
   asked whether it had been checked at all** — rightly: the 7 Aug 2026
   pre-filter miss (`.claude/rules/mail-ingest.md`) was a rejection whose
   giveaway sat in the body, one line below the subject. So every ignored email since
   16 Jul (570) was read by BODY — 155 from the DB, 415 read back from the
   mailbox read-only, since `not_job_related` bodies are purged under
   `INGEST_ALL` and the mailbox is the only place left to audit them. No
   rejection, receipt or "viewed" language in any; 377 were the three alert
   streams, the rest marketing, recruiter connection invites, portal
   verification/OTP mail and personal mail, and every portal mail belongs to
   a recorded application or to one started and never submitted. Lesson:
   an audit of what was set aside has to read what the classifier read.
23. **Can an employer's mail still reach its application?** (24 Sep 2026).
   The author pointed at one correctly filed ATS confirmation; behind it,
   the company check was hiding two other applications to the same employer
   stored under a second name, and the item task 21 had deferred as "one company, two
   names" turned out not to be cosmetic. Asked for a comprehensive check:
   every filed email replayed against today's records through the matcher's
   own SQL and scoring, and every application tried under every name its
   employer had used. Found the suppression shape three times — one of them
   a live interview thread whose next email would have auto-filed onto a
   rejected lead of the same title — and a separate class of names sharing
   no word (parent brands, agency/client names, a spelling). Measured
   moving word containment from the rescue into the gate (5 decisions: 4
   better, 1 to triage, 0 wrong; a wash on 21 Aug) and, on the author's
   go-ahead, shipped it with path 3i, validated against the old matcher.
   The no-shared-word class stays in triage by the author's choice. Also
   linked that employer's referral email onto the application it led to (triage
   route, snapshot first). Detail: `.claude/rules/matching.md`.
24. **Screening answers that should never be stored** (24 Sep 2026). Found
   while planning career-site capture (task 25): 5 stored answers were
   equal-opportunity questions (gender twice, race/ethnicity, veteran
   status, disability) from ordinary Easy Apply forms, and Singapore
   employer forms ask for NRIC and date of birth. The author chose to keep
   the question and withhold the value. `answers.is_sensitive()` and the
   extension's `isSensitive` apply one rule, whole words of the normalised
   question: identity numbers, date of birth or age, race, religion, marital
   status, gender, veteran status, disability — not nationality or work
   authorisation, which the visa analysis reads. The answer becomes
   `(withheld)` in the extension at `record()`, so the value never reaches
   the tab's `sessionStorage`, and again in `clean()`.
   `tests/sensitive_questions.json` holds the two to one list, and the
   extension's sweep test goes red against the old code with the raw value
   in the store AND in `sessionStorage`. `cli redact-answers [--apply]`
   withholds rows stored before the rule (the dry run prints questions,
   never values; no undo). Extension 0.10.2.
25. **Employer career sites, phase A** (24 Sep 2026). Asked to support an
   employer's own career site (a SuccessFactors Career Site Builder page on
   its own domain), analysed first in `docs/career-sites.md`: 51 of 283
   applications went LinkedIn → employer site with 0 screening answers,
   direct career-site applications existed only as 11 mail-made shells, and
   an 18-vendor survey measured how job pages expose a job. The author took
   all four of its §14 decisions as recommended. Built: the toolbar popup
   captures ANY page as applied or interested (activeTab + scripting, the
   generic reader injected on the click); `extension/shared/jobposting.js`
   reads schema.org JobPosting as JSON-LD or microdata in every shape the
   survey found; a posting's id is `<host>/<token>`, with
   `joburl.generic_id` its Python twin and `tests/job_urls.json` (33 shapes)
   holding them together. Run inside the real page that prompted it, the
   reader returned the title, company, location, posted date, vendor and the
   full 6,506-character JD. Not yet run live: the popup's injection path,
   which needs the extension reloaded. Next: phase B.
26. **Employer career sites, phase B** (24 Sep 2026). A submit on an ATS's
   own application form is captured with its answers, and when a job
   board's "Apply on company website" opened that tab, it completes the
   board's record instead of starting a second one. The forms were read
   first, live and read-only, on Lever, Greenhouse, Ashby and Workable. What
   they share became the rule: the application is where the resume goes,
   it never asks for a password, and a control inside it says it sends it.
   Ashby has no `<form>`, so the root is found by what it holds. Running the
   rule in Ashby's real page found a defect first: reCAPTCHA's hidden field,
   outside the form, dragged the root to `<body>`, and the sweep would have
   stored the captcha's token as an answer. The fix is `answers.js:machinery()`
   (unrendered AND named only by its own attribute — visibility alone would
   re-lose Easy Apply's hidden radios), plus a server drop. After it, each
   of the three pages gave one root and one submit button (out of 5, 10 and
   22). The link is the browser's `openerTabId` plus a title check
   (`jobposting.js:sameJob`). Anything ambiguous files its own record,
   visible and mergeable, rather than risk the wrong one. Content scripts
   run on 11 ATS host patterns, SuccessFactors limited to `/career*`.
   Extension 0.12.0. Unverified: LinkedIn setting `openerTabId`, a real
   submit, and Workday and SuccessFactors behind their sign-ins.
27. **The first real SuccessFactors apply was missed** (24 Sep 2026).
   Reported as "it didn't trigger". Nothing had reached the tracker, and the
   extension's buffers held nothing about the apply, because no content
   script had run. Chrome's History database (paths and parameter names
   only) showed the application form at `/portalcareer…`, outside phase B's
   `/career*` pattern, and, after the register postback, at an address with
   no `career_ns`. `Secure Preferences` proved 0.12.0 had been loaded.
   (While checking, the one record the popup had made that evening was
   missing; the author had deleted it.) With permission, the signed-in form
   was read in the author's own browser, read-only: `form#careerform`, 59
   fields in collapsed sections, no file input, and "Apply" as a `<span
   role=button>`. Fixed: the `/portalcareer*` pattern; a root rule for a
   form of 5+ fields that holds a submit-worded control; and the missed
   submit now records why (`nearMiss`). Re-run in the live form, one root
   and only "Apply" of 74 buttons, at both addresses. Extension 0.12.1.
   The form page names no company and suffixes the title with its
   requisition number; phase C's listing opt-in is what supplies both.
28. **Employer career sites, phase C** (24 Sep 2026). "Always capture on
   <site>" in the popup asks Chrome for that one host; the worker registers
   the generic scripts there and injects them into the open tab. A site runs
   them only while it is in `enabledSites` AND its permission stands, so a
   remote tracker server's granted origin never gets them. Registrations
   are re-derived on install, startup and every permission change, and a
   grant is completed from `permissions.onAdded` in case the prompt closed
   the popup. The handoff from listing to form is the TAB: an employer's
   "Apply now" first goes to its own domain, so the host its link names is
   not the host the form is on. Each listing remembers its job under its
   tab, and a submit takes the opener's list first, then its own tab's,
   picking the one title that is the same job (`pickListed`). Run on the
   real pair from task 27, each form title chose its own listing over the
   sibling role. The second-posting link for board records is deferred.
   Extension 0.13.0. Not yet run live: enabling a site, then a real apply.
29. **A toolbar icon that says whether this page is captured** (24 Sep 2026).
   The extension had no icon, only Chrome's default letter. Designed with
   `scripts/make_icons.py`: a capture frame around a trace, the list page's
   own mark. Following the colour rule, it is grey at rest and blue when
   capturing, where the trace ends in an amber dot, the wait an application
   starts. Hollow against filled keeps the two states apart without colour.
   Concepts were rendered at real toolbar sizes on light and dark toolbars,
   then judged. A bare trace read as a key, a rising one as a wrench, a
   list as any menu, a bare viewfinder as every screenshot tool. At 16 px the
   inner trace blurs, so that size draws frame and dot on the pixel grid,
   and heavier strokes read as a die's five. The switch is a
   `declarativeContent` rule over the same patterns the capture scripts use,
   because a tab's own icon survives navigation (Chrome resets it only on
   close). Extension 0.14.0. Not yet seen in a real toolbar.
30. **"Unknown company" from an ATS form** (24 Sep 2026). The first live
   SuccessFactors submit (23:44) was captured whole: 60 answers including a
   four-entry work history, the formal-name answer withheld, the real submit
   time, and vendor successfactors. It still filed as "unknown company ·
   AVP, Software Engineer (1234)": the form names no employer, and the site
   had not been turned on (the extension's store held no enabled sites and
   no listing stash). It was the only nameless record of 285. Repaired
   through `/edit` from the listing (company, clean title, 6,506-character
   JD, the listing's own id), with the applied instant restored to the
   microsecond and all 60 answers kept; the snapshot is
   `2026-09-24-sf-avp-identity.json`. Fixed for next time without relying on
   the user remembering the site switch:
   - `stripRequisition` drops a title suffix only when it equals the
     address's own requisition id;
   - a nameless capture's receipt now ASKS for the company, pre-filled from
     the tenant or the career site the tab came from, and names the site to
     turn on. It stays open until answered.
   `/captures/{id}/tag` gained `company`, which only ever replaces the
   placeholder. Not built: an email-side rescue that names a nameless record
   from its confirmation. It waits for that email to reach the database to
   be replayed against. Extension 0.15.0.
31. **`/analytics` redrawn as a report** (25 Sep 2026). The old page was a
   funnel of four bars, two weekly sparklines and three SQL tables whose
   rates were computed over EVERY application, so the last weeks' sends
   (with no time yet to be answered) dragged each row down, and LinkedIn's
   "viewed" notice counted as a response. Rebuilt on one fetch
   (`analytics.facts`) and two pure modules, `insights.py` (statistics) and
   `charts.py` (flow and curve geometry), tested without a database in
   `tests/test_insights.py` and held to the list's own SQL in
   `tests/test_web.py`. Measured on the day, over 277 sent applications
   and 25 approaches:
   - **The reply clock stops.** Half of all replies came within 3 days and
     nine in ten within 10. By Kaplan-Meier, 34% of applications ever hear
     anything. Of applications silent at day 14, 4% heard later; at day 28,
     under 1%. None has heard after day 34. The page calls that the reply
     window, and 128 waiting records are past it. `trace.FULL_HEAT_DAYS`
     (56, task 13) now has a measurement to replay against. It was not
     changed.
   - **Viewed is not an answer.** It is LinkedIn's alone, and it reversed a
     comparison: by heard-back, on-platform beat the employer's site 33%
     to 29%; by answered (a rejection or a round), the employer's site won
     29% to 19%. Comparisons use answered, over applications at least 14
     days old (by then 93% of all replies and 80% of rejections had come).
   - **Reposted listings were answered more, not less:** 12 of 33 (36%)
     against 18 of 127 (14%), intervals not overlapping. `docs/features.md`
     §3.3 lists a repost as a ghost-job signal; on this record it points
     the other way.
   - **Fresh listings do better,** unevenly: 1 to 6 days old 30% (14 of 46),
     a month or more 10% (2 of 20). No single row clears the baseline.
   - **An approach reached a round one time in 5; an application one time
     in 35** (8 rounds from 277 applications, 5 from 25 approaches).
   - **LinkedIn's rejection letter is a timer:** 27 letters, median 3.0
     days after applying, none sooner than 2.9.
   - **58 employers were applied to more than once; 32 of them never
     answered anything** (79 records): per-employer silence, the other half
     of features.md's ghost-job signal.
   - Of 67 comparison rows (59 rated), three cleared the baseline: an
     applicant-tracking vendor at 4 of 8, reposts, and afternoon submissions.
     About three would by chance alone, and the page says so beside them.
   Salary is captured on 0 of 277 applications, so the page has no pay
   analysis and its coverage panel says why. `by_platform`, `by_resume`,
   `by_technology` and `weekly` are gone. They are now rows in
   `insights.DIMENSIONS`, a registry the pure suite loops. Also in this
   change: `trace.live()` is the one rule for a blue wait. It was
   duplicated in two templates, and the page's squares need the same colour
   as the list rows.
32. **A rejection LinkedIn made by itself is a screen, not a visa reason**
   (25 Sep 2026). The author asked for a "likely visa" mark on rejections
   whose form or job description mentioned sponsorship, then asked that it
   stay apart from rejections a person said were visa. Measured first:
   - A form that recorded "you need sponsorship" was rejected 36% of the
     time (12 of 33 settled). A form with no visa question: 13% (12 of 89).
     The intervals do not overlap.
   - All 12 were LinkedIn letters. LinkedIn documents the mechanism: a
     failed must-have screening question is auto-archived, and "Auto-archived
     applicants will receive the rejection message three days after they
     apply" (Recruiter Help a412523). 21 of the 27 LinkedIn letters answering
     the author's own applications arrived 72.01-72.02 hours after the
     submission; the next nearest was 69.7 hours, then 114 and later.
   - The job description is weak evidence and was left out: rejected 29%
     where it wants locals only (21), 18% where it says nothing.
   So "How it ended" gained two derived buckets and needs no tagging:
   `sponsorship_screen` (12, the timer plus a declared need) and
   `form_screen` (9, the timer alone). They take precedence over a recorded
   reason, so a screened application tagged `visa` stays a screen, and
   `visa` (8) counts only what a person said. "Without a round" fell from
   42 to 22, so the tagging queue is now the rejections a person or a
   system actually decided. `sponsorship` states what the form learned,
   not a proven cause: some of those forms also asked must-have-style
   experience questions answered low. The declared need is one rule in
   Python and SQL (`answers.declares_sponsorship`), held to every real
   wording in `tests/sponsorship_answers.json`. Authorisation questions are
   matched first and on legal wording only, since "willing and able to work
   full time" is not a visa question. Rows wear the screen as a grey tag
   beside the reason, and the rail's status line now wraps: "sponsorship
   screen" beside "rejected" did not fit in 7rem and was being clipped.
33. **Which model runs the JD extractor under `jd_extract_v2`** (25 Sep 2026;
   `docs/jd-extraction-models.md`). v2 splits v1's `local_only` into
   citizens/PR only, no sponsorship, in-country (the author's distinction: a
   pass may be sponsored for someone already here, just no hire from abroad)
   and locals preferred, records only what the JD says, and checks
   `visa_notes` against the JD verbatim ("no quote, no signal"). Replaying
   it, Haiku kept inferring. The sweep ran on 54 hand-labelled postings
   through the real `extract`, batched at half price, and cost about $1.95
   against a $5 budget:
   - Haiku 4.5: 88.8% correct, 10 false signals, all reproducible.
   - Sonnet 5 at thinking-off, `low`, `medium` and `high`: 98.0-98.1%,
     no false signals.
   - Opus 5.5 `low`: 100% on one run, at twice Sonnet's cost.
   Effort barely moves Sonnet here. It writes about 150 output tokens at
   `medium`, and `high` doubles that with no gain. Recommended: Sonnet 5 at
   `medium`, the only level with identical labels on both runs. The sweep
   also found our own verbatim check failing a correct quote on a JD captured
   with spaces inside words ("Singapor e"), on every Sonnet run;
   `quoted_in` now ignores whitespace. The API credit ran out mid-way through
   an earlier full replay (38 of 264 left undone), and the worker's outage
   handling held the queue untouched as designed.
   Applied the same day on the author's word:
   - `JD_MODEL` is Sonnet 5 with `JD_EFFORT = "medium"`. `effort` is opt-in
     in `llm.py`, so the other stages are unchanged.
   - v2 is live, and migration 017 widened the CHECK constraint.
   - All 264 JDs were backfilled through the Batch API for about $0.90, and
     65 of the 66 gold labels agree.
   - The list wears the signal as a grey tag, with the JD's own sentence as
     its title (UI rule 9d).
   Of the 12 rejections LinkedIn's sponsorship screen closed, only 4 had a JD
   that said `no_sponsorship`. The knockout lives in the form's question,
   not in the JD.
34. **A bird's-eye view of visa evidence** (25 Sep 2026). Asked for status,
   the JD's visa hint and the form's answer in one view, and offered three
   homes (a matrix on `/analytics`, tags and filters on the list, or both,
   linked), the author chose both, linked. The data shaped it: of 278
   records the user started, 42 forms recorded the need for sponsorship, 19
   JDs restricted who may apply, and 9 had both, 5 of those rejected. 205
   had neither kind of evidence. The design:
   - A form bucket per application (`answers.FORM_VISA`: needs, asked,
     no visa question, no form) and a JD group (`jd_extraction.VISA_GROUPS`:
     restricts, sponsors, nothing), each ONE SQL expression.
   - "Visa, at a glance" on `/analytics`: rows are form buckets, columns
     JD groups, cells hold the status squares.
   - `?visa=` / `?form=` on the list, formatted from those same expressions,
     so all 11 non-empty cells matched their rows on real data (tested
     over every cell).
   - A second grey tag on the row quoting the form's question and answer.
   Found on the way: `trace._month_ticks` let a month label sit 7% into the
   axis, over the start label, on a filtered list's short span. It now keeps
   12% clear. And a Windows `uvicorn --reload` that logged "Reloading..."
   without restarting, followed by Chrome serving the page from cache, made
   the fixed note look broken twice. Fetch with `cache: 'no-store'` before
   believing a screenshot after a server restart.
35. **The reason a rejection email states is filed from the email** (25 Sep
   2026). The author asked why two rejected recruiter threads did not read
   as visa. Both recruiters' LinkedIn replies said so outright ("the team
   can not sponsor your EP"; "a specific mandatory requirement for
   candidates who are Singapore Permanent Residents or Citizens"), and the
   extractor had written both into `notes`, a free-text field nothing
   reads. The reason was only ever set by hand, by design (invariant #2,
   9 Sep). Neither the screen buckets (LinkedIn's 72-hour timer on a form)
   nor the JD's tag could reach an inbound thread with no form and no JD.
   Of the 46 emailed rejections, 3 stated a visa reason, all recruiter
   replies; one had been tagged by hand on 5 Aug, and these were the other
   two. A keyword rule would have been wrong: a fourth "visa" hit was a
   company of that name in a job-digest footer.
   Offered three options, the author chose to read the reason from the email:
   - Stage 3, `email_classifier.rejection_reason` (prompt
     `rejection_reason_v1`), runs on `rejection` mail only. It returns a
     reason from `STATED_REASONS` (the page's vocabulary without `other`
     and `unstated`) with the sentence that states it, checked verbatim by
     `pipeline/quotes.py`, the JD stage's check moved to a shared module.
     A failed quote gets one repair, then the answer is none, not an
     error, so the rejection still files. It is a separate prompt rather
     than extraction v2 because extraction runs on every job email, and
     the screen buckets read its `platform`.
   - The worker stores the answer inside the extraction, so the triage and
     refile paths carry it (`matcher.extraction_from_raw`).
     `_append_event` files it on the rejected event with
     `reason_source: email` and `reason_quote`.
   - The timeline says "the email says “…”", and the row's tag title
     quotes it. The why-select overrides: the same reason keeps it the
     email's, another or a clear makes it the user's and drops the quote.
   Replayed with Sonnet 5, two trials, $0.25 (the author declined the
   Haiku comparison). It gave identical answers on all 46 emails, and 5
   stated reasons (visa x3, a filled role, a LinkedIn letter naming the
   failed screening question), all correct on reading. None was missed
   among the 41 form letters, and the footer "visa" was ignored. The two
   rejections already tagged by hand agreed exactly. Backfilled on the
   author's word, snapshot first: the answer onto all 46 emails and a
   reason onto the 3 events that had none. Every inbound rejection now
   carries a reason. On `/`, 39 of 57 rejections still have none: 21
   screens, 2 after a round, and 16 without a round, the ones worth
   tagging.
   Found on the way: a memory written earlier the same day said
   `analytics.py`'s two response-type lists had been unified. They have
   not: `_REMINDER_WHERE` keeps its own list, as `.claude/rules/web-ui.md`
   says. The memory was corrected.
36. **The listing → ATS handoff, rebuilt around the job's ATS id** (28 Sep
   2026; `docs/career-sites.md` §16). Asked before a real apply whether the
   extension would record it: an employer's Career Site Builder listing
   handing over to SuccessFactors' form on another data centre. Checked
   read-only, and the answer was yes for the submit, no for the rest:
   - the tracker server was not running, and the worker does not retry a
     failed POST, so the submit would have been lost; it was started;
   - the form rules found `form#careerform` and one submit of 136 buttons;
   - the record would have filed as "unknown company", with no JD and the
     session crumb as its id. The listing was never read (the site was not
     enabled, and this site's JobPosting scope holds only the description,
     so even an enabled site would not have stashed it), and a sibling role
     with the same title rules a title-only link out.
   The form's session then timed out 13 minutes idle. Measured next: 55
   applications went LinkedIn → an employer's site and none has its
   answers; phase B has not fired once. The one clue was a Workday submit on
   25 Sep turned down as "no application form found", its record saved only
   by the popover, answerless, and later invited to interview. Only the
   requisition is printed on every surface of the flow (the SuccessFactors
   form, its first addresses, every confirmation email), and emails carry an
   ATS id for SuccessFactors and Workday only (3 of 11 and 4 of 19; 0 of 74
   for Greenhouse, Workable, Ashby and SmartRecruiters, after a first
   pattern that miscounted meeting links and dates was corrected).
   Designed: the join key is the job's id on the ATS, bound at the handoff,
   stored on the job and upserted on by the server, and looked up in email.
   Built the same day, on a branch while the application was still open:
   P0, a review step's "Submit" (0.15.1), and P1's extension half, the id
   read off the page when the address lost it (0.16.0). The application was
   then sent and captured (22 answers, 6 withheld, the first textarea ever
   stored), and repaired from the listing through `/edit`, snapshot first
   (`2026-09-28-sf-sc-identity.json`). P1's server half followed, with the
   column's home on `jobs` by the author's choice: migration 018
   (`jobs.ats_job_id`), the upsert on it in `upsert_record`, `merge_jobs`
   carrying it, the email lookup `matcher.match_by_ats_id` ahead of name
   matching, and 0.17.0 sending it. Found on the way: a worktree's suite
   cannot see a NEW migration, because the container mounts the main tree's
   `migrations/` (CLAUDE.md). P2 the same day (0.18.0): the handoff bound
   when the hiring system's first page loads, to the last listing the tab
   (or its opener) showed, instead of watching the Apply click; a listing
   is a page that publishes a JobPosting; SuccessFactors ids carry their
   tenant. P3 the same day (0.20.0). Enabling a site had never worked: the
   stuck 25 Sep `pendingSite` note and the missing grant read like a
   dismissed prompt, but Chromium's source showed every request refused
   before any prompt, since the popup asked for `*://host/*` and no ONE
   declared pattern contained both schemes. Also SuccessFactors quick apply
   (a note from the job page's Apply, filed by the landing) and the icon's
   third state (a job page that is not captured). P4 the same day (0.21.0):
   a thin record completed from its listing afterwards, by a join-only
   candidate id where the listing's number is the requisition and by the
   popup's explicit attach everywhere else, and a tenant's known name offered
   on the next nameless receipt. Open: the first real apply through each.

37. **The first LinkedIn → ATS apply to keep its answers, filed twice**
   (28 Sep 2026, extension 0.21.1). An employer on Oracle Recruiting Cloud:
   the form captured 29 answers, the first of §16.2's 55-strong path to
   keep any, but as its own record ("Work Summary" at "<employer> Career
   Site"), beside the LinkedIn record the popover filed 10 s later. Read
   from the extension's LevelDB rather than guessed: the handoff had bound
   the tab `via: "opener"` with the job's id `…/2087` (which also settles
   that LinkedIn's external apply sets `openerTabId`), and the submit read
   its own id off `…/job/2087/apply/section/1` as `…/1`. `handoffFits`
   refused the binding on `2087 ≠ 1`, and the title fallback failed on the
   section heading. One cause: `idFrom` took the LAST id-shaped segment,
   and an apply flow's tail can hold a number. Fixed as a rule on both
   sides (`jobposting.js:idFrom`, `joburl.generic_id`): the id is looked
   for before the first `apply`/`application` segment, the tail only when
   nothing precedes it (JazzHR). Red on the old code with the real
   signature (`/1`, and `/3` for a third section, which shows the answers
   would also have keyed per section); 305 extension checks, the 38 shared
   URL cases and all nine suites green; an in-place `reverse()` mutation,
   written and then caught before it ran, turns five fallback cases red.
   Of the 19 postings on platform `other`, this was the only one with a
   segment after `/apply`. Repaired with the author's go-ahead, snapshot
   first (`2026-09-28-oracle-handoff-twins.json`): `/edit` on the form's
   record, the ATS id set, `merge_jobs` keeping the LinkedIn record (JD,
   board id) and gaining the answers, the popover's duplicate `applied`
   removed, the submit's instant restored to the microsecond. Offered, not
   built: the form page's weaker reads (a section heading as the title, a
   tab title's "Career Site" as the company) yielding to an exact stash,
   and a server-side detector putting a board record and an ATS capture of
   the same vendor, minutes apart, in triage's duplicate band with the
   BOARD record as the winner (triage's merge keeps the older record, the
   wrong one here).

38. **Inbound approaches: whose move it is, and closing one yourself**
   (28 Sep 2026). Asked how to handle a recruiter who goes quiet after a
   reply, and an approach that is not a fit. Measured first, over the 29
   inbound records: all 9 open leads (6 to 66 days old) held one event, the
   approach, because a reply on LinkedIn or WhatsApp reaches no ingest path
   and an emailed reply files a status-less `note`; so every lead sat under
   "Awaiting your call" and in the nav pill, and none could reach
   `/follow-ups`, which required an `applied` event. The only close was "I
   withdrew", worded for an application never made. And, found on the way,
   9 of the 15 rejected inbound records drew an OPEN tail, 4 of them blue,
   since the trace capped a row only when its last event was terminal (a
   recruiter writing again after dropping you; a rejection filed by hand for
   today, anchored at noon, sorting before that afternoon's approach; two
   hand-filed events tying on one noon). Built, in three parts, each with
   its own full suite run:
   - **The trace** (`trace.closing`): a thread ends at its latest close,
     wherever it sits, for the list and `/analytics` alike; equal to "the
     status is closed" by migration 013's precedence. The old rule, run on
     the three real shapes, reproduced the blue tails. On the day: 0 of 15
     drawn open.
   - **Close this approach** (`web.close_approach`): "Not for me" (optional
     why, `_DECLINE_WHY`) and "They went quiet", both `withdrawn` +
     `payload.closed`, worded "You declined" / "They went quiet" with a grey
     tag on the row; inbound only, one close per thread, dated so it never
     precedes the approach or a same-day event (`_on_the_thread`); undo by
     deleting it.
   - **I replied** (`web.mark_replied`): a `note` with `payload.reply`.
     `analytics.reply_sql` / `theirs_sql` / `awaiting_you_sql` define whose
     move it is once, for the pin, the pill, the lede, `/analytics` and a new
     kind of `/follow-ups` row: a lead you answered, silent REMINDER_DAYS
     since, with "They went quiet" beside "Followed up" (a follow-up resets
     its clock rather than retiring it). Emailed replies count through
     `emails.sent_by_user`, and both read "You replied".
   40 new checks (7 in `test_insights`, 33 in `test_web`), and the old
   "closed is closed" check now asserts no silence on a closed row. Nothing moved on the
   author's records by itself: the 9 leads stay awaiting until their replies
   or closes are recorded, which is the author's to do from each lead's page.
   Not built: placing a same-day event from the general timeline form after
   the day's last event (the trace no longer depends on it).

39. **A SuccessFactors submit the extension could not see** (29 Sep 2026,
   extension 0.22.0). A LinkedIn → SuccessFactors apply reached the tracker
   only through LinkedIn's popover: no answers, no ATS id, the popover's
   time. Every buffer was read before anything was guessed. The handoff had
   bound the tab (`via: "opener"`, with the ATS id), and then nothing: no
   capture, no sweep, and no near miss after 17:53. Chrome's history put
   the form open for 15 minutes and the send at ~18:03, and the tab's
   sessionStorage, read OFF DISK (its older entries sit in snappy-compressed
   tables, where a grep finds nothing), still held `__tracker_form_answers`:
   13 answers, never taken. Then the live form was read, read-only, in a
   fresh tab. It is SuccessFactors' newer candidate experience, built from
   UI5 web components. `form#careerform`'s light DOM held 26 controls, all
   `type=hidden`; the 22 real fields are `<ui5-input>`s with their input in
   an open shadow root; "Submit" is a `<ui5-button>` whose inner `<button>`
   holds only a `<slot>`. Running `generic.js`'s own rules against that DOM:
   zero controls, so no root, so no sweep (the 13 were the TYPED fields,
   through answers.js's edit backstop; every field prefilled from the
   candidate's profile was lost), and a Submit whose label read as "", so
   not even a near miss. The requisition was printed nowhere; it sits in
   `<meta name="jobRequisitionId">` and a hidden `career_job_req_id`, so the
   store was keyed by the session crumb. Built, as rules rather than a
   SuccessFactors entry:
   - `generic.js` descends into open shadow roots for every query
     (`deepAll`) and steps out of them to the host on every walk up (`up`,
     `closestDeep`), as answers.js already did; a control's label is its
     text, else its SLOTTED text, else its value, else its `aria-label`.
     Rule 2 then roots the form by its Submit, whatever the address says.
   - `jobposting.js:pageId` reads a requisition from a field NAMED as one
     (`meta`/`input` matching `REQ_FIELD`) before the printed "(N)"; two
     that disagree give nothing, a bare `id` field is never read.
   - The silent case now leaves a line: `answers.js:leftover()` finds an
     earlier page's store that no capture took, and `capture.js` reports
     it once from the next page (hiring systems only, never with a sign-in
     in view), leaving the answers in place for a repair.
   15 new checks (320 extension checks in all, all nine suites green); the
   fixture reproduces the measured structure, and on the old code 5 of its
   10 go red with the real signatures (root `null`, the Submit not a
   submit, the key the crumb), while the scope guards stay green either
   way. Repaired with the author's go-ahead, snapshot first
   (`job-tracker-snapshots/2026-09-29-sf-candidate-experience-repair.json`):
   the 13 answers, `ats`, and the ATS id through `POST /captures` without
   `completed`, the applied time moved to 18:03 through `/edit` (the edit
   form read back first and asserted equal to the record). The profile-
   prefilled answers are lost for good. The disk-reading procedure is
   `job-tracker-snapshots/tools/session_answers.py`.

40. **Interview threads that go quiet** (29 Sep 2026). Asked how the list
   handles an interview "ignored after chasing them for status", or one that
   went badly and was never answered. It did not: the row read
   "interviewing" forever (its tail warming through the heat), `/follow-ups`
   never asked about it (any response kept a record out of the queue), and
   nothing closed it truthfully ("They went quiet" was inbound-only; "I
   withdrew" and "They rejected me" both say what did not happen, and a
   rejection would count as an answer on `/analytics`). Measured first: 15
   records ever had a round, 7 rejected, 8 open, 2 of those silent 19 and 20
   days; the 6 rounds that were answered took 0, 5, 12, 13, 18 and 22 days
   after the last round, which is why nothing closes by itself. Built, the
   28 Sep lead design extended rather than a new one:
   - `/follow-ups` gains a third kind of row, a ROUND gone quiet: status
     `interview_invite` / `engaged` (`analytics.OPEN_ROUND`) and the latest
     MOVE by either side (`move_sql`: anything but a note to self)
     REMINDER_DAYS old. The row names what moved last (`web._MOVED_AS`),
     carries "They went quiet" beside "Followed up", and a follow-up resets
     its clock. `reapplications` skips such rows.
   - `close_approach` accepts your own application for "They went quiet"
     after a round (never "Not for me", never before a round), with an
     optional `payload.note`; the detail page asks "Heard nothing since?".
     Same `withdrawn` + `closed: went_quiet`, so the tag, the timeline and
     `/analytics` needed nothing.
   20 new checks in `test_web`, dates relative to today; on the old code the
   first goes red with the real signature (the silent interview not in the
   queue, the badge and rows still agreeing). Rendered read-only against the
   dev DB: the 2 threads appear as the first round rows, 5 of 5 open-round
   applications offer the close, and the inbound round offers its own.

41. **A Greenhouse job-board capture that read almost nothing** (29 Sep
   2026, extension 0.23.0). A direct apply on `job-boards.greenhouse.io`
   filed "unknown company" with no JD, no location, 5 answers and no resume.
   The capture itself was right: the provenance showed the submit on the
   page, and the sweep found the form (8 controls, 5 kept, 2 "no value").
   The live page (read-only, nothing typed) showed why, five ways: no
   JobPosting and no `og:site_name`, the employer only in the tab title
   ("Job Application for <title> at <company>"), the JD and location in the
   vendor's own blocks, the country a react-select whose input is emptied
   after a pick, and the resume a file input (ignored by design) whose only
   label is its "Attach" button, its question on the enclosing
   `role="group"`. The form has no custom questions, so those two were the
   only answers lost. Fixed as rules: `titleEmployer` (anchored on the job's
   own title, weak), `LISTING_DOM` (per-vendor selectors, only where no
   JobPosting filled the field), `canonicalUrl` keeping https,
   `answers.js:shownChoice` for a combobox (skipping what its
   `aria-describedby` names, never climbing into the question), a file
   field's name under its group's name, and `answers.py` promoting a
   "Resume…" upload to `resume_file`. 9 new extension checks (329) and 2 in
   `test_captures`; on the old code 6 go red with the record's own
   signature, the guards green either way. Repaired the same day, snapshot
   first (`2026-09-29-greenhouse-listing-repair.json`): the employer, JD
   (through the extension's own `htmlToText`) and location from the page,
   through `/edit`, the URL to https (same id), the applied instant
   restored after `/edit` truncated it. The country and the resume file
   were not guessed, and the author chose not to add them.
   That evening the same job was filed twice: the Greenhouse page had been
   opened with no opener (not by LinkedIn's Apply; nothing recorded a
   LinkedIn click) and submitted at 20:20, and at 21:04 LinkedIn's Apply for
   the same job opened it again, its handoff binding WITH the ATS id, and
   the popover answered 3 s later filed a LinkedIn record. The reverse of
   the order the handoff was built for (§16): the popover's capture does not
   send the ATS id its tab's handoff had just learned, which the server
   would have joined on (migration 018). Merged at the author's request
   through `merge_jobs`, the board record kept as in task 37, the popover's
   duplicate `applied` removed so the record keeps the real submit's time;
   snapshot `2026-09-29-greenhouse-linkedin-twins.json`. Offered and
   declined by the author: the popover capture sending that ATS id.

42. **Every LinkedIn radio saved its question as its answer** (found 30 Sep
   2026 from one record the author pointed at, extension 0.23.1). The record
   read "Are you comfortable working in an onsite setting?*" answered "Are
   you comfortable working in an onsite setting?". The dev DB showed the
   extent: all 33 LinkedIn radio rows captured 25-29 Sep, across 16
   applications, and none of the 114 before. The radio code had not changed
   since 0.9.0 (2 Sep), so the cause was on LinkedIn's side. A live Easy
   Apply (opened from the job's own page, read with `javascript_tool`,
   closed with Discard) showed it. The `role="radio"` wrapper is gone and the
   native input carries the `aria-label` itself: the QUESTION on every
   Yes/No option, the FILENAME on each resume card. `radioOption()` fell
   through to the input's own label. The classic search page still opens
   the textbook modal, which reads correctly. Fixed as a rule over the whole
   group (`answers.js:radioGroup()`): a name every member shares is the
   question, and the answer is the checked member's row. Four new extension
   checks (333 in all); on the old code the two Yes/No checks fail with the
   record's exact signature. Eight of the 33 rows are sponsorship or
   work-authorisation questions (7 applications), so those sit in
   `/analytics`' "asked" bucket; the author's earlier answer to "require
   sponsorship" was Yes 27 of 27 times. The artefact was seen on 25 Sep,
   when the sponsorship rule was written to ignore it, and not traced.
   LinkedIn's applied-job page shows only the resume, not the answers, so
   the real answers cannot be read back from anywhere. Repaired by the
   author's choice, snapshot first (`2026-09-30-radio-question-as-answer.json`,
   all 33 rows): the 7 "require sponsorship" rows set to the Yes the author
   stated, each UPDATE guarded on its old value. Those 7 applications moved
   from "asked" to "needs", and the one LinkedIn rejected by its 72-hour
   letter moved from a form screen to a sponsorship screen (14 and 10 on the
   day, against 12 and 9 on 25 Sep). The other 26, including one "legally
   authorized to work in Singapore", keep the question with the broken
   answer.

43. **An ATS wizard's later step that nothing recognised** (30 Sep 2026,
   extension 0.24.0). Asked, before submitting a LinkedIn → SmartRecruiters
   apply, to make sure capture worked. Checked live with the extension's own
   rules pasted into the page, nothing typed or sent. Step 1 had been swept
   (26 answers in the tab's store, keyed by the publication id), but on
   step 2, the screening questions, `applicationRoot()` found no root (no
   `<form>`, no file input, an address ending `/screening`) and turned the
   Submit down. Two more gaps sat behind it: the Yes/No questions (work
   authorisation, sponsorship) are `role="radio"` custom elements with no
   `<input>`, and every question's label is slotted, so it read as `*`. The
   author chose to hold the submit for a fix. Three rules
   (`.claude/rules/extension.md`): a later step of a form an earlier sweep
   found (the store's new `rooted` mark), drawn ARIA controls, and
   flat-tree labels. 16 new extension checks (349), red on the old code
   with the live signature (the step swept nothing), and all nine suites
   green. The submit came from a second tab that LinkedIn's Apply opened at
   12:04, so the new code swept step 1 afresh. It stored 37 answers, the
   visa radios and the slotted questions among them.

44. **The same apply, filed twice** (30 Sep 2026, extension 0.24.1). The
   submit captured all 37 answers but filed under the SmartRecruiters
   listing's identity, and the LinkedIn popover, answered 15 s later, filed
   the board's record. The provenance buffer said why. The handoff, bound
   through the opener at the tab's first page, held the listing's number,
   while the form sent its publication UUID, and `handoffFits` refuses two
   differing ids as another job's form. The fallback then saw no opener tab
   (`linked: "tab+title"`, `candidates: 1`), so the title matched only the
   tab's own listing. Why Chrome gave no `openerTabId` at the submit is not
   established. A prediction made before the submit, that the title
   fallback would still link the record, rested on that unchecked opener
   id. Repaired with the author's go-ahead, snapshot first
   (`2026-09-30-smartrecruiters-linkedin-twins.json`), a dry run and every
   precondition asserted: `merge_jobs` keeping the LinkedIn record (now two
   postings, 37 answers, the form's ATS id), and the popover's duplicate
   `applied` removed so the record keeps the submit's instant. The author
   chose the mechanism fix, `learnsAlias`: a page reached, by its referrer,
   from one the binding knows, and not a listing, is the same job under a
   second id; an alias fits only where the submit's title agrees. The
   referrer and the form's lack of a JobPosting were measured live before
   building. 10 new extension checks (359), the live submit's refusal among
   them, and all nine suites green. Offered and not chosen: task 37's
   triage twin detector. This was the third board-plus-ATS twin in three
   days (tasks 37, 41, 44). Seen, not acted on: step 1's upload is labelled
   "Choose a file or drop it here", so `resume_file` stayed empty.

45. **A passwordless sign-in filed an application** (1-2 Oct 2026,
   extension 0.24.2). The author asked why one record id existed. It was
   "MyGreenhouse", "unknown company", one `applied` event and one answer
   (the author's email), filed at 17:07:33 UTC on 1 Oct from
   `my.greenhouse.io/users/sign_in?…source=quick_apply…`, the sign-in a
   Greenhouse job board offers for autofill. The extension's buffers (read
   from the LevelDB) gave the sequence. A LinkedIn listing at 17:07:03, the
   handoff bound to the job board at 17:07:07. The sign-in's Submit then
   captured at 17:07:33: rule 2 of `applicationRoot()` (a form, a "Submit",
   five or more fields, no password) on 8 controls the sweep read as all
   filled and none labelled, most likely the emailed code's boxes. The real
   submit followed at 17:08:10. It took the handoff
   (`linked: "opener+handoff"`) into ONE record with 8 answers and the ATS
   id, the first time the submit was seen taking the binding (0.18.0's open
   item). No mail, contact or other row pointed at the false record. It was
   deleted with the author's go-ahead, through
   `POST /applications/{id}/delete` (TestClient), snapshot first
   (`2026-10-02-mygreenhouse-signin-capture.json`). The fix is a rule: rule 2
   counts only fields that ask something, by the sweep's own `labelFor`
   (answers.js exposes it), so the root rule and the sweep's `noLabel`
   count are one predicate. The popup's injection carries no answers.js and
   keeps the old count. 5 new extension checks (364): the three negatives
   are red on the old code with the real signature, and a labelled control
   and the fallback stay green on both. The UI5 fixture's inputs got
   labels, since the real page's had them (its typed answers resolved),
   modelled as `aria-label`. Expect a near-miss line in the popup at the
   next MyGreenhouse sign-in: the refusal, not a loss.

46. **Eightfold under an employer's own domain** (2 Oct 2026, extension
   0.25.0). Asked to support an apply address on `careers.<employer>`
   that looked like Eightfold. Read live first, signed in, nothing typed or
   sent (`.claude/rules/extension.md` has the shape). The generic rules
   already fit the form: a root, the submit, the JobPosting (in the server
   HTML, against `docs/career-sites.md` §4.1's guess), the `?pid=` id and
   the vendor. What did not fit was the domain. Off a vendor's own host
   there was no handoff and no ATS id, so a LinkedIn → employer-domain apply
   leaned on the opener check at the submit, which filed a twin on 30 Sep
   (task 44). Four rules: `generic.js:hiringSystem()` (a vendor's app with
   its form on the page); `claimHandoff` everywhere, sending the page's own
   id, a `site` binding from the opener only, and a title check where ids
   cannot decide; the worker's re-bind as a pure `jobposting.js:rebind()`
   that keeps an id to its host; and `answers.py` promoting the resume
   combobox ("Upload your resume", a text field's bare file name). 16 new
   extension checks (380) and 3 Python ones. A mutation run undid each rule
   alone and turned exactly its own check red. One stored row would also
   match the wider resume rule, an "Upload resume" file field, and its
   application already has its `resume_file`. Enabling the site is the
   author's click in the popup; it has never completed live.
   Found on the way, not acted on: the 1 Oct apply whose verification codes
   came from an Eightfold sender went through WORKDAY. Its only trace is a
   near miss on `…/consentCollection/…` ("Submit" was not captured: no
   application form found on this page), and its record came from email
   alone: "unknown role", no answers.

47. **A confirmation by abbreviation made its own record** (2 Oct 2026,
   made 29 Sep). The author asked why one record existed beside the
   extension's. A bank's Workday confirmation named the employer by its
   initials, with no title and no job id, against a record named
   "1011 <legal name>" (Workday's company code in front of its JobPosting's
   organisation). Zero candidates, and a confirmation with none is created,
   not triaged: `.claude/rules/matching.md` has the case, and corrects the
   24 Sep belief that this class lands in triage. Re-filed through
   `refile_email` with the author's go-ahead, snapshot first
   (`2026-10-02-workday-abbreviation-refile.json`); the route deleted
   the empty duplicate. Three fixes measured, none built: an initials rule
   (3 pairs in 269 names, 1 right), Workday's sender as the tenant (decides
   this mail only), and offering the capture the mail confirms (1 decision
   in 526 mails, to triage; wider windows admit strangers).

48. **The 30 Sep stale pane: three applications under another job's name**
   (2 Oct 2026, extension 0.25.1). Found while sizing task 47: three Easy
   Apply captures in three minutes, all "AI Engineer" at the employer of a
   16 Sep application, under three different job ids, each beside a
   LinkedIn confirmation for a different employer that had made its own
   record. Each id's own page named the real job and said "Application
   submitted". The case, the guards that could not fire and the fix are in
   `.claude/rules/extension.md`. With the author's go-ahead: the three
   records repaired through `/edit` (company, title and location from each
   job's page; the form read back first; the applied instants restored, one
   to its real submit 16 s before the retry that wrote it), the three
   confirmations re-filed and their duplicates deleted (snapshot
   `2026-10-02-stale-pane-captures-repair.json`). The JDs stay empty (the
   hidden tab never loaded them). The extension fix: the pane's own id
   (`JobDetails_AboutTheJob_<id>`, `/jobs/view/<id>` links) as a second
   check, both checks on the preload frame's read against the id it
   borrows, and the Easy Apply modal before a subframe's first form. That
   last change ends a junk answer, "Filter results by: Date posted", stored
   by all three captures and two on 8 Sep. `answers.py` drops it as
   LinkedIn chrome, and the 5 rows plus one resume upload the 46 rule now
   promotes were removed (snapshot `2026-10-02-answer-chrome-cleanup.json`).
   12 new extension checks (392) and one Python check; a mutation run
   turned each part's own check red. Disproved and recorded: that the
   structural title search anchors on the results list without a JD.

49. **A six-step apply kept only its last step** (2 Oct 2026, extension
   0.25.2). The author applied on an employer's own career site (Phenom,
   enabled the day before) through Phenom's OWN apply, not a Workday
   handoff as `docs/career-sites.md` had assumed: `…/apply?jobSeqNo=<job>
   &step=N&stepname=<name>`, "Next" between steps, submit on step 6. The
   record's identity, JD and time were right (linked by tab and title to
   the listing's stash); its answers were one, the review step's file
   upload. Cause, read from the extension's buffers and the tab's session
   storage: no id rule matched the address, so its id was path plus query,
   the STEP included, and that id keys the answers store, which answers.js
   empties when the key changes. The step-6 store on disk was `rooted`, so
   the earlier steps had been found and swept, then dropped. Fixed as a
   rule (`jobposting.js:idFrom` and `joburl.generic_id`): on an apply
   address, the fallback leaves out a parameter naming the step, and keeps
   the rest of the query. No stored posting id changes. 3 new extension
   checks plus 4 fixture cases; on the old rule they give the real
   record's signature (only the last step's answers). The lost answers are
   unrecoverable: each step's store overwrote the last. Found on the way,
   not built: the submit click's two delayed re-sweeps run after `take()`
   and refill the store from the review page (the store on disk was saved
   100 ms after the capture), which can report a false "form left holding
   answers"; and a Workday requisition shaped `_PT-JR012345` misses the
   segment rule, so a same-day Workday record's id is its whole address.

50. **A capture's own click refilled the store it had just taken** (2 Oct
   2026, extension 0.25.3; found in task 49). `take()` empties the answers
   store, and then the submit click's 0 and 300 ms re-sweeps, and any later
   click on that page, saved the review step back into it, so the next full
   page load could report a sent form as "left holding answers".
   `answers.js:save()` now skips the address a capture took the store on;
   memory still holds the re-sweep, for a second submit there. 4 new
   extension checks; without the guard the first goes red with the exact
   store found on disk. Whether the 1 Oct Eightfold report (19 answers left,
   44 s after an 18-answer capture) was this is not established: which page
   saved that store is not on record.

51. **A Workday requisition with two letter groups gave a fallback id** (2 Oct
   2026, extension 0.25.4; found in task 49). The segment rule took
   `_<up to 5 letters><digits>`, so a bank's `_PT-JR012345` matched nothing
   and the posting's id became its whole apply address, `?src=` included
   (the form still kept its 60 answers: the address held still). The rule
   is now short letter groups, hyphens allowed, then digits (`PT-JR012345`,
   `R-07654321`, `REQ-2024-001`), each group still capped at five letters so
   a slug word (`_engineer-2024`) is not an id; both implementations, 4
   fixture cases, red on the old rule. Replayed over the 18 stored
   non-platform postings: 1 changes, that record. Repaired with `/edit`
   (the form read back and asserted equal to the record first), the applied
   instant restored to the microsecond and `jobs.ats_job_id` set as a
   capture would have (snapshot `2026-10-02-workday-req-id-repair.json`).
   Not known: whether the employer's mail prints `PT-JR012345` or
   `JR012345`; `match_by_ats_id` needs the whole token, and no stored mail
   names it yet.

52. **Lever's custom questions were named by an option, an id or a
   placeholder** (2 Oct 2026, extension 0.25.5). Checked live BEFORE the
   first real Lever submit (a LinkedIn → Lever apply, held for the fix):
   the handoff had bound (`via: "opener"`, the Lever id), the form rooted
   on its resume input and `button#btn-submit` sat inside it, but every
   custom question would have saved under the wrong name. Lever writes the
   question in a `<div class="application-label">` beside the field's own
   `<div>`: no `<label>`, `<fieldset>` or role. So a Yes/No question was
   named by its first option ("Yes"/"No"), the location-and-right-to-work
   select by its `name` (`opportunityLocationId`), and both text questions
   by their placeholder, "Type your response". `answers.js` now takes the
   block before a nameless control's field (above the placeholder, on-screen
   controls only, so a captcha field stays machinery) and, for a radio group
   with no group element, the block before the smallest box holding its
   options. `precedingText()` refuses a neighbour that is itself a control,
   for `asking()`'s code-box rows. 3 new checks; on the old code the fixture
   gives exactly the names predicted from the live page. **Verified on the
   real submit**: one record (`opener+handoff`), 14 answers under their real
   questions, `resume_file` set. The record's four "No" answers were
   suspected, "corrected" and restored from the snapshot
   (`2026-10-02-lever-answers.json`): the author had clicked No. Current
   location read as empty at every sweep; whether it was filled is not
   established.

53. **Other LLM APIs weighed; classify moved to Sonnet 5.5** (3 Oct 2026).
   Asked for research on APIs comparable to the Claude models in use.
   - **How it was done:** four research agents read vendor pages, and the
     claims the conclusions rest on were re-read at source. The record is
     `docs/llm-alternatives.md`.
   - **Cost is no reason to switch:** the whole bill is a few dollars a
     month.
   - **What `pipeline/llm.py`'s OpenAI-compatible backend can and cannot
     do:** it reaches Mistral, Groq, DeepInfra, Fireworks and OpenRouter
     unchanged. But it files a 402 or 408 as the job's fault, and it cannot
     drop `temperature: 0`. The fixes are its §8; none is built.
   - **The replay, on the author's go-ahead:** Sonnet 5.5 ran on all three
     Sonnet stages for about US$1.25. Classify was better: right on five ATS
     verification mails where Sonnet 5 stored `other`. JD extraction and
     the rejection reason each showed one reproducible miss, so only
     `CLASSIFY_MODEL` moved.
   - **A correction to an earlier figure:** classify's measured input on job
     mail is 1,470 tokens a call, not the 3,810 recorded on 4 Aug.
   - **Still open:** §8's backend changes, whether an outage-fallback
     provider is worth a second key, and Haiku 5.5 once it has a price.

54. **A LinkedIn → Phenom apply filed twice: Chrome had forgotten the
   opener** (3 Oct 2026, extension 0.26.0). Reported by the author as "the
   same application", by the two record ids.
   - **Repair:** `dedup.merge_jobs` onto the LinkedIn record, which has the
     name, the JD and the board id, with the form's 21 answers and posting
     moved across. The popover's `applied`, 11 s after the submit's, was
     removed. Snapshot first (`2026-10-03-phenom-linkedin-twins.json`).
     Done before sync, with no employer mail yet arrived.
   - **Cause:** the site was enabled mid-visit, so the first handoff claim
     came 37 s after the click, by which time Chrome had dropped the tab's
     `openerTabId`. And the Phenom form's id was the whole-query fallback,
     so even a bound handoff could not have checked the submit against
     it. The evidence, and a first wrong guess (LinkedIn's `safety/go`):
     `.claude/rules/extension.md`.
   - **Built:** the kept opener, trusted only on the host the Apply left
     for, and `idFrom`'s named job parameter (Python mirror
     `joburl.generic_id`, `tests/job_urls.json` +7 cases, 3 changed). Both
     were replayed against real addresses before they were written. All
     nine suites pass.
   - **Not done:** the merged record's Phenom posting still holds the old
     fallback id; the new rule would give the job's token. Cosmetic unless
     that page is captured again.
   - **Next real check:** an external apply on an enabled site where the
     tab was switched away and back. The provenance line should read
     `opener-kept+handoff`.

55. **A SuccessFactors candidate-experience submit was refused as "outside
   the application form"** (3 Oct 2026, extension 0.26.1). Reported by the
   author as "details were not captured". The apply was LinkedIn →
   SuccessFactors, and the handoff bound through the opener.
   - **Repair:** the LinkedIn popover's record (JD, board id, 0 answers)
     got the form's 40 answers from the tab's session storage, plus `ats`
     and `ats_job_id`, through `POST /captures` without `completed`, so the
     applied time stayed. Snapshot first
     (`2026-10-03-sf-outside-form-repair.json`). No questions were about
     visas. The form held profile, work-history and education fields.
   - **Cause:** UI5's file uploader keeps its `<input type=file>` in a
     one-control `<form>` inside its shadow root. Rule 1 of
     `applicationRoot` took the nearest form around a file input, so that
     form became the root and the real Submit sat outside it. 0.22.0's
     shadow reading is what made the input visible. Counted on the live
     form, reopened by address in a fresh tab with nothing typed or
     clicked: `.claude/rules/extension.md`.
   - **Built:** `generic.js:formAround`, which passes over a form holding
     nothing but uploads. `test_extension.js` adds the uploader to the
     candidate-experience page and fails on the old code with the real
     near-miss text. The full suite passes.
   - **Next real check:** a candidate-experience submit with a resume
     upload. It should capture at the Submit, with no "left holding N
     answers" line after it.
   - **A second miss the same day, repaired, cause NOT investigated:** a
     LinkedIn → SuccessFactors QUICK apply (`career5`, 15:20), reaching
     the `isRedirectToAppSent=true` landing. The extension logged
     `"Apply" was not captured: no application form found on this page`
     on the quick-apply page (sign-in, contact fields, upload). It never
     wrote a quick-apply note, so the landing filed nothing. Its 9 answers
     came from a superseded Session Storage row after the tab was closed
     (`.claude/rules/extension.md` → Procedures). They went onto the
     LinkedIn popover's record the same way as above (snapshot
     `2026-10-03-sf-quick-apply-repair.json`). Applied time left at the
     popover's, about a minute after the send.

56. **A SuccessFactors form on SAP's newer data centre ran no script**
   (3 Oct 2026, extension 0.26.2). The author asked mid-form whether
   `career44.sapsf.com/portalcareer?_s.crb=…` was a supported ATS. It is
   SuccessFactors' classic form, but on `sapsf.com`, which neither
   `jobposting.js`'s vendor table nor the manifest knew: no content script,
   so a submit would have filed only LinkedIn's answerless popover record.
   - **Built before the submit:** `sapsf.com` / `sapsf.eu` in `VENDORS`,
     the manifest's `/career*` and `/portalcareer*` patterns,
     `joburl._TENANT_PARAM` and the mail allowlist; a `tests/job_urls.json`
     case; and a `test_extension.js` check that loops `VENDORS` against the
     manifest (red on the old manifest).
   - **Real submit, after a Save, an extension reload and a tab reload:**
     one record on the LinkedIn posting, 60 answers, `ats_job_id`
     `career44.sapsf.com/<tenant>/<req>`. The handoff still bound, though
     the form's first load ran no script. `resume_file` empty, as on the
     other classic-form records (an attachment widget).
   - Committed `cb7d351` and pushed. The other machine needs a pull and an
     extension reload.

57. **Listings that ask for the CV by email** (4 Oct 2026, extension
   0.27.0). The author asked whether the tracker could catch listings that
   ask for an emailed application, since they might be missing them.
   - **Measured first, read-only:** of 333 stored JDs, 22 had one sentence
     holding an email address and a CV word, and 15 asked for the email
     alone; 7 also offered the platform's button. None of the 22
     applications had any mail of the author's filed on it. Almost all
     were recruitment agencies on LinkedIn.
   - **The rule** (`pipeline/email_apply.py`): one sentence with an
     address and cv/resume/résumé, not also saying "apply
     online/now/here/through/via". The 19 address sentences it leaves out
     were licence lines, privacy and accessibility contacts and "for a
     confidential discussion". "application" admitted only an
     accessibility contact, so it is not a CV word. One rule in Python and
     SQL, held together by `tests/email_apply.json`; on the dev DB the two
     agree on all 381 postings.
   - **Owed** (`analytics.email_owed_sql`): your own application, applied,
     the JD asks, and no response, withdrawal, sent mail filed on it or
     answer by hand. An emailed CV clears it by itself (it files as a note
     from a sent email). "I emailed it" / "Not needed"
     (`web.mark_emailed`) file a note with `payload.emailed`.
   - **Shown:** the application's page (the sentence, a mailto naming the
     role, both answers), `/follow-ups` first under "Asked for by email",
     in grey, the list's aside, and the extension's receipt, which stays
     open while it carries the ask.
   - **On the day:** 8 applications owed the email, two of them applied
     to in the previous two days. The receipt has never been seen on a
     real apply: on the next apply to a listing that asks, check that the
     receipt quotes the sentence and its mailto opens the mail client.

58. **The first iCIMS apply, checked after the submit** (4 Oct 2026,
   extension 0.27.1-0.27.2). The record itself was right: one record on the
   LinkedIn posting through the handoff, the iCIMS job id, the JD, the
   submit's time. Its answers showed four faults, each a mechanism:
   - **Resume uploads were never promoted** (`7e85458`): the rule wanted
     "resume" in a label's first words, and upload widgets name the input
     with their own chrome ("My Computer (Opens new window) Upload your
     resume/CV…", "Upload options"). All 5 file fields ever stored were
     resumes, none promoted. Now the label or the file's own name need only
     say resume/CV. The 5 records repaired (snapshot
     `2026-10-04-resume-uploads.json`).
   - **The server kept the first 60 answers** (`eba400a`): a 28 Jul bound
     against a runaway scrape, which cut the END of every long ATS form, the
     screening step. 14 records, 24 Sep - 3 Oct (Workday, SuccessFactors,
     iCIMS). Now 500. The iCIMS record's full 83-answer store survived in Session Storage
     (copied at once to `2026-10-04-session-storage/`) and went in through
     `POST /captures`: 59 -> 82 answers, its 23 the whole screening step,
     "Do you require a work pass (visa)?" among them. **The other 13 are
     lost**: Chrome compacted the store minutes after the submit.
   - **The profile step had no root** (0.27.1): iCIMS creates the account
     INSIDE the application form ("Password" / "Password (Re-enter)" among
     108 labelled questions), and `generic.js` refused any container holding
     a password. Found by running answers.js's own `labelFor`/`valueOf`
     against the live profile page (read-only), which read every field
     right, then the form's shape and History's step addresses. A password
     now marks a sign-in only where fewer than 15 other questions are asked.
   - **The dropdowns stored the typed search** (0.27.2): iCIMS keeps the
     choice on a `visibility:hidden` `<select>` with its own label, and a
     `role=combobox` search box beside it holds what was typed ("singa").
     `answers.js:filterBox` treats a combobox in the same field as a
     `<select>` as machinery, in the sweep and the edit backstop alike.
   - **Next real check:** an iCIMS apply after reloading the extension:
     Last Name, Country, School and Degree on the record as chosen, no
     "— Type to Search —" rows.

59. **/analytics, read against the real page** (4 Oct 2026). The author
   asked whether "Every application, week by week" should start its weeks
   on a Saturday, then what else the page should change. Measured on the
   dev DB first (350 sent applications, Singapore time) and rendered live.
   - **Weeks stay on Monday.** The author's apply-days run in 15 bursts of
     consecutive days. A Monday boundary cuts none of them; a Saturday one
     cuts 5, holding 65 applications. Bursts begin on a Monday 5 times and
     never on a weekend, and weekends carry 36 sends, 21 of them on
     3-4 Oct. Reopen if weekends keep looking like that one, as a per-user
     setting read by the week rows AND the Rhythm calendar, whose Mon-Sun
     labels are literals in the template.
   - **The findings list every row that clears the rule** (`b373203`,
     `insights.findings`). The six-row cap was ranked by distance from the
     base rate, which a small sample wins: 8 rows cleared, and the list led
     with a hiring system at 4 of 7 and cut first postings at 19 of 147
     and Thursday at 9 of 84. Now one line per comparison, biggest sample
     first, a two-valued comparison with both sides.
   - **The rows of squares count answered** (`356d3d2`): the week rows
     print sent, answered and rounds, an employer row "N answered". 39 of
     the 99 heard back had only LinkedIn's "viewed" notice, 10 of 14 in
     the week of 14 Sep. "Never answered" went from 35 employers to 48,
     and the 35 that sent nothing at all are said beside it.
   - **The month you applied** (`2f2514a`), the first comparison and the
     page's one trend: July 22% answered (11 of 51), August 22% (32 of
     144), September 14% (8 of 59, interval 7-25%).
   - **The reply window moved from 34 days to 55** (task 31's
     measurement): one rejection on 28 Sep, to an application whose 4 Aug
     date was typed by hand. The next longest wait is a "viewed" notice at
     48 days. The window splits the waiting applications 162 inside and 92
     past, and the hint comparing it with `FULL_HEAT_DAYS` (56) no longer
     shows. Not changed: the window is the maximum by design. Defining it
     on answers rather than any signal is the alternative if it should
     move less.
   - **Offered, not built:** each recruiter approach placed in its own week
     rather than one row (a structural change). Seen and left: the resume
     comparison shows the one resume named with nothing after the shared
     stem as its full file name, clipped (its title has it); salary is on
     0 of 350, the LinkedIn adapter's known gap.
60. **An offer is a round, not a close** (7 Oct 2026, migration 019). The
   search's first offer, and the model had no word for how it went. An
   agency's recruiter rang on 21 Sep (WhatsApp) with the client's offer of
   the seat, moved to another city; the author said they would consider
   it, was chased on the 22nd, declined to relocate and asked for the
   original city to be reconsidered, and heard nothing more. The record (an
   inbound lead, interview invitation 9 Sep) held none of it and sat on
   `/follow-ups` as a round gone quiet since the invitation, where "They
   went quiet" would have closed it with the offer never recorded.
   - **The gap.** `offer` ranked above both closes (70 against 60), and
     since migration 013 the highest precedence ever recorded wins, so a
     filed offer was the status for good; `trace.TERMINAL`,
     `insights.CLOSED` and `analytics.OPEN_ROUND` ("without `offer`, which
     is terminal") each said the same thing in its own list, and
     `close_approach` refused any thread holding a terminal event. A
     declined, rescinded or quiet offer was a green, closed success that
     could not be closed. 0 of 386 records held an offer event, which is
     why nobody had noticed.
   - **The fix** (`migrations/019_offer_is_a_round.sql`): `offer` ranks
     55, between `interview_invite` and the closes; `TERMINAL` drops it and
     `CLOSED = frozenset(trace.TERMINAL)`, `OPEN_ROUND = ROUND_EVENTS` —
     one definition each. An offer in hand is an open thread, still green
     (`insights` keeps the tone), queued as a round after `REMINDER_DAYS`
     ("They made an offer on …" when the offer was the latest move), and
     closed by "Not for me" with a why (now allowed on the author's own
     application once an offer is on the thread — "Offer in hand:" on the
     detail page), "They went quiet", or "They rejected me" with a reason
     for a rescinded one. `analytics.summary`'s `offered` is unchanged, so
     an offer that was made counts whatever came after. Twelve new checks
     (`tests/test_web.py`, `tests/test_insights.py`): a declined offer
     reads withdrawn and still counts; a same-day rejection outranks the
     offer; an inbound offer keeps its own close; the pure suite's
     `closing()`, tone and headline.
   - **Filed on the record** through the real timeline route, as the page
     would: the offer (21 Sep, WhatsApp, the move to the other city in its
     note), the author's "I'll consider it" (a note), the chase (22 Sep,
     `engaged`) and the author's answer (a note). Status now `offer`; the
     queue row reads "They got in touch on 22 Sep, nothing since", 14 days,
     one of 5 round rows that day. **The close is the author's click**: "Not
     for me — location or work mode" (the cause, countable) or "They went
     quiet" (the literal state), on the page or on `/follow-ups`.
   - **Not built: "I replied" after a round.** `web.mark_replied` is for
     open leads, so a hand-filed answer of yours on a thread that has moved
     is a plain note, which `analytics.move_sql` does not count as a move:
     the queue's wait runs from THEIR latest move (right for the queue) and
     the timeline reads "Note", not "You replied" (weaker). Also not built:
     accepting an offer — the search ends there, and `offer` with nothing
     after it says so for now.
61. **/follow-ups is "Your move"** (7 Oct 2026). Asked to analyse the page
   and propose boldly. Measured first, on the dev DB:
   - **197 rows, 192 of them unanswered applications oldest first.** By the
     page's own Kaplan-Meier curve (n=350), 150 had under a 5% chance of
     ever hearing back; 99 were past 56 days at 100% heat (rule 14's flat
     amber, back). 126 of the 192 had **nobody to write to** — a LinkedIn
     Easy Apply with no recruiter card — and every one wore "Followed up".
     56 had a captured recruiter (53 with a profile URL), 3 a thread the
     author wrote in. Age × reach: only **7** were both inside the odds and
     reachable. 33 employers held 85 rows (one agency 7, another 6). Five
     `follow_up_sent` in the whole search; the one that drew an answer was
     a chase after an interview (filed 4 Sep, rejected 15 Sep), and no cold
     nudge ever has. First-answer lag: median 3 days, p90 18.
   - **The reframe.** A WAIT is the list's (drawn as heat); this page is for
     MOVES. `analytics.queue` sections every row by the move it offers:
     asked for by email; after a round; worth a nudge (inside the odds,
     someone to write to, best odds first, the move and the odds on the
     row); applied to again (the suggestions as a band, any age); then two
     counts — gone quiet, past `insights.quiet_after`, and recent with
     nobody to write to. The cut is derived: the first day from
     `REMINDER_DAYS` on at which under `QUIET_CHANCE` (5%) of those still
     silent ever heard back — **23 on the day**, beside task 4's hand-measured
     21, and it moves as replies arrive. `analytics.reply_odds` builds the
     same curve `/analytics` draws from one 23 ms query (facts() is 120 ms
     and the pill reads it on every page); `tests/test_web.py` holds the two
     curves equal. Below `MIN_TIMING_N` replies nothing is quiet.
   - **One confirmed close for the dead weight** (`/follow-ups/quiet`): the
     page lists exactly what it will close, each with its fate — "They went
     quiet" today, or "You applied again" linked to the later application
     (mark_reapplied's shape) — and the POST re-checks every id. The first
     many-events-in-one-click action in the app; the author chose "in, with
     a confirmation". `/analytics`' "still waiting" gets honest with it.
   - **The pill counts moves** (`queue_count`, one statement: emails owed +
     rounds + leads + nudges): **19 against 197** on the day — 8 emails
     owed, 5 after a round, 6 worth a nudge (one of the 7 reachable young
     rows carries an applied-again suggestion instead) — beside 23 applied
     to again, 129 gone quiet (150 past the odds, 21 of them in the
     applied-again band) and 34 recent with nobody to write to; the
     confirmation page listed 152. The applied-again band is outside the
     pill (its rule is Python over JD text, 89 ms) and the lede says both.
     The list's aside reads "N moves to make". Rendered through TestClient
     against the dev DB: every section present, the six nudge rows each
     naming a recruiter with a profile link and "about 6-7% of applications
     this old still hear back".
   - **Not built, next on this page:** the draft beside each nudge (a
     mailto body for a thread, a copyable two-liner for a LinkedIn contact;
     template first, a model only if the template gets used), and one card
     per agency filing N follow-ups in one click. Left out on purpose: a
     "reply to a human sender" reach class — 124 of 580 inbound senders
     looked human by domain, but 44 were LinkedIn's relay and 28 Workday's,
     too noisy to name a person. A nudge still has no odds of WORKING: the
     curve says who still hears back unprompted, and the five follow-ups
     cannot say whether a nudge changes that.
62. **The two lists, read on a real screen** (7 Oct 2026). Asked for UI
   changes to `/` and `/inbound`; looked first, in the dev Chrome with no
   Dark Reader, so this was also the first sight of the app's own dark
   palette (it holds; web-ui.md's Known-untested has the half that is
   answered). Measured on the day: 351 rows on `/`, 20,417px, 29 screens;
   a first screen of six rows under 320px of chrome; 111 open rows at 100%
   heat; 492 grey tags, 242 of them `on-platform`; 188 traces with one dot;
   on `/inbound`, 33 of 35 approaches with a recruiter on record whom the
   row never named. Six proposals; the author took five (A, quiet rows
   going cold past the odds cut, was left out — the heat scale is
   unchanged).
   - **B, landmarks** (web-ui rule 10b): a divider at each month's first
     row on the record under the default sort, and the table's head
     carrying an index of months that jumps to them. The head is sticky
     (`.register`, `overflow:clip` on the card), so the axis, count and
     index stay in view down all 29 screens. Built with a divider for the
     newest month too; seen on the page, it repeated the index right above
     it and spent the row F had won, so the newest month has none.
   - **C, who approached**: the recruiter on record beside the company on
     `/inbound` rows, in the role's grey; any contact's name finds the
     thread from the search box, on both pages.
   - **D, only the exception wears a tag**: `on-platform` left the row;
     `employer site` stays. The three states stay three on the detail page
     and in /analytics' comparison.
   - **E, the lede says the week** (`analytics.week`, a rolling 7 days):
     "In the last 7 days: 44 sent, 12 heard back, 2 interview invitations.
     Since 16 Jul 2026: 103 of 351 heard back (29%)." The inbound lede leads
     with "7 awaiting your call, 6 new in the last 7 days". Found on the way
     and fixed: the lede's "since" read the trace axis, which is the
     visible rows', so a search moved the date ("Since 23 Sep") and not the
     counts beside it; it now comes from the counts' own query.
   - **F, one toolbar row**: search, sort and the page's links share a
     row; the count moved into the table's head, where "Company and
     role" stood.
   - Rendered and checked in the browser after the suite passed: the head
     pins at the top with an opaque background, and a jump to August lands
     its divider 6px below the head. Thirteen new checks, three adapted
     (the on-platform tag, the record's "no divider" check now naming the
     pin's two, the count's new place).

63. **An external apply filed twice: a kept opener cannot follow a career
   site's handoff** (7 Oct 2026). Asked what happened to the two newest
   records: one application to an investment firm, filed as the
   employer's form (no company, 75 answers, the requisition) and as the
   LinkedIn popover's record (the name, the JD, no answers), created 41 s
   apart.
   - **Read from the extension's storage, not guessed:** two applies to the
     same employer from one LinkedIn tab, two minutes apart, both Apply
     links leaving for the employer's own career site, which hands over to
     SuccessFactors on another host. The first bound `opener+handoff`,
     Chrome's live opener still standing 34 s after the click. The
     second's tab never got a handoff, and its submit's provenance reads
     two candidates and no link: Chrome had dropped the opener, and
     0.26.0's kept opener is trusted only on the host the Apply left for,
     which was the career site, not the hiring system. So the kept opener
     cannot cover an employer whose Apply passes through its own site
     first. What made Chrome drop it is not recorded.
   - **Repair**, at the author's word: `merge_jobs` onto the LinkedIn
     record, the popover's `applied` removed and the form's submit time
     kept, the 3 Oct shape. Snapshot first
     (`2026-10-07-sf-linkedin-twins.json`); a dry run in a transaction with
     every count asserted, then the commit.
   - **Not built:** a kept opener that also links when the hiring system's
     page names, as its referrer, the host the Apply left for. Measure
     that career site's referrer first.
