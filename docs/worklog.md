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
