---
paths:
  - "pipeline/matcher.py"
  - "pipeline/dedup.py"
  - "pipeline/ingest.py"
  - "pipeline/email_classifier.py"
  - "scripts/replay_thresholds.py"
---

# Matching: candidates, thresholds, event dates

Moved here VERBATIM from CLAUDE.md on 9 Sep 2026 so it loads when Claude reads a
matching file instead of in every session (it can also be Read directly).
Dates are the key to each case; the record itself is in the database. The
invariants that govern this code (#3, #4, #9) are still in CLAUDE.md.

## `refile_email` in practice (moved from CLAUDE.md invariant #3, 25 Sep 2026)

CLAUDE.md invariant #3 says what `web.py:refile_email` is for (one misfiled
email; never `merge_jobs`). Its uses and the procedure, as they stood there:

Used in anger on three at once (Contoso, Fabrikam, Litware, 4 Aug 2026),
where the email hadn't been *misfiled* so much as it had created its own
record after the `COMPANY_TRGM_MIN` gate hid the real one — same remedy,
since that record contained nothing but the email's own events and
`_job_is_empty` therefore cleared it. 98 → 95 applications. Used a fourth
time the same way (Wingtip Talent Group, 4 Aug 2026, 96 → 95) after the gate
hid the real record from LinkedIn's own confirmation — same shape, same
remedy.

**Driving `refile_email` without the UI:** it is a route, not a library
function, so reach it through the real code path rather than hand-writing
the UPDATEs —
`starlette.testclient.TestClient(web.app, cookies={'session': sid})`
with `sid` from `auth.create_session(conn, user_id)` runs the actual
route against the dev DB. Snapshot the affected rows to JSON first; the
route deletes events and may delete an application.

## Gotchas learned the hard way

- Matching/dedup thresholds in `pipeline/config.py`
  (`AUTO_MATCH_SCORE`, `COMPANY_TRGM_MIN`, `DEDUP_*`) are FIRST DRAFTS,
  deliberately untuned — tune against real backfill data, not intuition.
  **`COMPANY_TRGM_MIN` has now been measured, and it was silently creating
  duplicate applications** (4 Aug 2026). It gates the CANDIDATE LOOKUP, so an
  employer that brands itself differently in mail than on the job board is
  never even considered and `_create_application` mints a second record:
  `contoso` ← "Contoso Markets" (0.467), `fabrikam group` ← "Fabrikam" (0.538),
  `litware singapore` ← "Litware International (Singapore) Pte Ltd"
  (0.314 — LinkedIn's OWN confirmation, four seconds after the extension
  captured the same job), all against 0.6. Every one had a byte-identical
  title and timestamps within a minute, so each would have scored ~0.8 and
  matched correctly had the candidate been visible. Note company similarity
  is ONLY a gate — the score is title 0.5 + date 0.3 + platform 0.2, so
  company agreement contributes nothing once past it (invariant #3's "company
  carries almost no information", same fact from the other side).
  Fixed by a fallback, NOT by moving the threshold: `_CANDIDATES_RESCUE_SQL`
  runs only when the gate yields zero candidates, and admits a job on either of
  two rules — an EXACT title (ignoring company entirely), or one company name's
  WORDS containing the other's. Widening `COMPANY_TRGM_MIN` itself loosens the
  net for every email, and one title routinely spans several employers here
  ("Senior AI Engineer" covers five), so it risks a silent WRONG auto-match;
  the fallback can only ADD candidates where there were none, and
  `AUTO_MATCH_SCORE` + `AUTO_MATCH_MARGIN` still both apply, so several
  same-titled applications fail the margin and land in triage. A visible
  triage item beats a silent duplicate — the same preference invariant #3
  states for merges.
  **The word-containment rule is rule 2, added for a FOURTH case that walked
  past rule 1 as well** (Wingtip Talent Group, 4 Aug 2026): LinkedIn's own
  confirmation said "Wingtip Talent Group" while the extension had captured
  "Wingtip Talent Group | Specialist Technology Recruitment" (0.375), and the
  titles differed by a platform-added suffix — "Senior Full Stack .NET Engineer"
  against "Senior Full Stack .NET Engineer- Hybrid - Singapore" — so the
  byte-identical test missed it too. A tagline appended to an employer's name is
  a strict word SUPERSET, which trigram similarity actively punishes: the extra
  words dilute it toward zero however exact the shared prefix is. Measured
  before it was written — across all 81 distinct `company_norm` values in the
  real DB it admits exactly ONE new pair, the Wingtip one, so no false positives on
  real data. It independently covers `contoso` ⊂ `contoso markets` and `fabrikam` ⊂
  `fabrikam group`; Litware stays rule 1's, since `norm_company` drops its
  parenthetical and leaves `litware singapore` against `litware
  international`, neither a subset. Neither rule covers all four alone.
  **The two rules are OR'd into ONE query rather than tried in sequence, and
  that is load-bearing, not tidiness.** `find_match` computes
  `margin_ok = len(scored) == 1 or …`, so a LONE rescued candidate auto-matches
  with no margin check at all. Sequencing would have handed the Wingtip confirmation
  exactly one candidate — "Fincher Talent", an unrelated agency holding the
  byte-identical title `Senior Full Stack .NET Engineer`, captured three minutes
  after the real Wingtip record — which scores 0.920 and would have auto-matched
  onto the WRONG employer: silent, and worse than the duplicate being fixed.
  OR'd, the true Wingtip rows compete, the margin falls to 0.094 and it lands in
  triage instead. Any future rescue rule joins the same OR, for the same reason.
  **The RESCUE ITSELF is gated on "the main gate returned zero rows", and that
  gate decays as the DB grows** (measured 21 Aug 2026). One unrelated company
  clearing `COMPANY_TRGM_MIN` is enough to suppress the whole fallback — and
  "unrelated" is easy at 0.6: replaying the Wingtip confirmation against today's
  195 applications, the gate returns `datum consulting group` (0.667) and
  `liberty consulting group` (0.607), neither of them the employer, while the
  real record sits at 0.375 and the rescue that would find it never runs. On
  4 Aug the same email got zero candidates and the rescue fired; nothing about
  the email changed, only the number of unrelated companies in the DB.
  **This also breaks the `match_score` diagnostic below**: the suppressed shape
  scores the WRONG companies, so it comes out non-NULL and reads as a
  low-confidence miss rather than "no candidate was ever seen".
  **Do NOT fix it by OR-ing the rescue into the main gate — that was measured
  and it is much worse.** Replayed over all 296 stored, scored emails: always
  OR'ing turns **29 correct auto-matches into triage items and prevents zero
  wrong ones**, because rule 1 (exact title, any company) drags in the same
  same-titled strangers the gate exists to keep out ("Senior AI Engineer" spans
  five employers here) and they break the margin. Promoting ONLY rule 2 (word
  containment, the precise one) into the gate is a wash: +1 correct
  (`VANARSDEL AI TECHNOLOGIES` → the `vanarsdel` record, a real branding case) and −1
  (a `title: null` email whose extra candidate breaks the margin), 177/1/118
  either way. So it stands as-is. The number to watch is the suppression count:
  **1 of 296 today**. Re-measure before touching it — the fix only becomes worth
  its cost if that grows.
  **It grew, and rule 2 moved into the gate on 24 Sep 2026.** Found from one
  email the author pointed at (placeholder names, real measurements): an
  employer's own ATS mail filed under `wide world` beside two
  LinkedIn-captured `wide world group` applications (similarity 0.40), after
  which that employer's short-name mail could not reach the August pair at
  all. A reachability replay of all 432 filed emails against the live
  records found three shapes of suppression — that one; a recruiter's
  "margie consulting group" admitted to two UNRELATED consulting groups
  (>= 0.6) and never to its own record, stored under the name plus a
  tagline; and the worst, an agency's `trey talent` mail auto-matching a
  REJECTED same-titled lead at exactly 0.75 while the live interview thread,
  `trey talent x woodgrove bank`, was no candidate: a silent wrong match on
  a live thread. With containment in the gate, 5
  decisions change — 2 wrong -> right, 2 triage -> right, 1 right -> triage
  (the two name forms of one agency then compete on the margin) — and none
  go wrong; replayed again on the shipped code, 294 auto onto their record,
  0 onto another, 0 suppressed. Rule 1 stays a rescue (as a gate term it was
  the 21 Aug disaster). `tests/test_integration.py` path 3i pins both
  shapes, and fails on the old matcher with the first shape's signature (0.423,
  triage). What is left is a class no string rule reaches — a parent brand
  (a telco group writing for its subsidiary), an agency or its client under
  another name or an abbreviation, a spelling (`<name>.com` against
  `<name> Company`): 20 of 432 emails, 8 (application, name) pairs. They land in triage, visibly, never on a wrong record; the
  author chose to leave them there rather than learn aliases from triage
  links (a slip-link would teach a wrong alias).
- **Rule 1 made the silent wrong match the paragraph above only feared, and
  now requires a shared company word** (23 Sep 2026). A recruiter's mail
  from "Woodgrove" (company_sim 0.417 against the extension's "Woodgrove
  Southeast Asia", under the gate) carried the title "Full Stack AI Engineer"
  — byte for byte the title of an unrelated agency's application
  (company_sim 0.077). Rule 2 admitted the real record, rule 1 admitted the
  agency, and since company similarity is only a gate the title decided it:
  1.0 against 0.575 (the real posting reads "Full Stack Engineer – Generative
  AI & Agentic AI"), 0.875 against 0.662, margin 0.213 over the 0.15 bar. Four
  emails of a LIVE interview thread filed onto the agency, which then read
  `interview_invite` for an interview it never gave while the real
  application read `confirmation` — the tracker asserting the wrong state on
  two real applications at once. The margin is no defence when the true
  record's title is worded differently from the mail's. Found by the user
  ("why were the Woodgrove emails mixed there"), diagnosed by replaying
  `find_match`'s two candidate queries for one of the emails read-only.
  **What every real rule-1 rescue had in common, and the stranger did not:
  the two company names shared a word** (contoso/"Contoso Markets",
  fabrikam/"Fabrikam", litware/"Litware International"). Rule 1 now also
  requires one shared word of `config.RESCUE_SHARED_WORD_MIN` (4) letters —
  the tokens `norm_company` leaves behind ("x", "&", "pte", "ltd", "the",
  "and", "ai", "it", "sg", "co") are all shorter, so no stop-list. Measured
  before it was written, three variants replayed over all 427 stored,
  extracted emails against that day's DB (198 distinct `company_norm`
  values, 6 word-containment pairs, all genuine): **(A)** drop rule-1
  candidates whenever rule 2 admits anyone — 7 decisions change, and it
  leaves the fourth Woodgrove mail (extraction "Woodgrove Singapore", neither
  subset nor superset of "woodgrove southeast asia") on the agency; **(W)**
  the shared-word condition — 11 change: 7 silent wrong auto-matches become
  triage items (all four Woodgrove mails, plus three older mails a human had
  filed by hand that the old rule would TODAY send to an agency holding the
  same title), 2 hand-resolved emails become correct auto-matches (their 15
  and 16 same-titled strangers no longer break the margin), and 2 correct
  auto-matches become triage items (a subsidiary's coding-test mails, sent
  by the parent company whose name shares no word with the subsidiary's);
  **(A+W)** identical to W. W was implemented; the two visible costs are the
  trade invariant #3 already states. The four mails were re-filed the same
  day through `refile_email` (rows snapshotted first), and the recruiter
  contact the mis-match had copied onto the agency's job was deleted.
  `tests/test_integration.py` path 3f pins the stranger case, and path 3d's
  fixture — which until then modelled rule 1 with a company sharing NOTHING,
  a rule broader than any real case — now shares a word, as every real case
  did. The exact-title rescue stays load-bearing: it is still the only rule
  that reaches the Litware shape (neither name a subset of the other).
- **A fifth duplicate of the 4 Aug shape, with a new reason rule 1 missed it**
  (found 23 Sep 2026, made 7 Sep). An extension capture of a LinkedIn posting
  that redirected to the employer's site ("Contoso Group Singapore", "Senior
  Software Engineer"), and 66 minutes later the employer's iCIMS autoreply,
  branded with the hiring subsidiary ("Contoso Media") and titled "Senior
  Software Engineer (2026-170938)" — the ATS appends its requisition id to
  every title. Company similarity 0.28 against the 0.6 gate; rule 2 fails
  because neither word set contains the other ({contoso, media} vs {contoso,
  group, singapore}); rule 1 fails on the SUFFIX alone — the shared word
  "contoso" would have passed. Zero candidates, so a confirmation minted a
  second record, `auto_matched` with a NULL score (the bullet below). The
  classify job had also sat four days in the credit-balance outage, so the
  duplicate appeared long after the capture. Re-filed through `refile_email`
  (snapshot first); the empty record and its job were removed by the route,
  277 → 276. Only ONE stored title carries a parenthesised requisition id, so
  the matcher was not changed for it — if a second ATS mail does the same,
  the general fix is to strip a trailing `(…id…)` before rule 1's equality,
  not to add a sender to a list.
- **LinkedIn's confirmation email can name a job by a title its posting does
  not show** (24 Sep 2026 email audit). Of 236 confirmations, two stated a
  title unlike the record they were filed on — "AI Fullstack Engineer" for
  a record reading "Fullstack Software Engineer", "Senior AI/ML Engineer"
  for "Data Scientist – Platform AI Squad" — each within a minute of its
  capture, which reads exactly like the stale-pane capture bug
  (`.claude/rules/extension.md`). It was not: each stored job id's own page
  said "Application submitted" (one also "Application viewed", matching a
  viewed email already on the record), and one posting was marked
  "Reposted". So the email's title is LinkedIn's name for the requisition,
  not proof of what was captured. The fact that settles which job an
  application is for is the job page's application status for the stored
  id; check it before editing a record on a title mismatch. The
  company-in-subject check had no such false positive (227 of 227).
- `emails.match_score` stores the best candidate score even for `pending`
  rows — that's the tuning dataset. **`NULL` means something different and
  more specific: ZERO candidates were found, not a low-confidence miss.** That
  is how the four duplicates above were identified after the fact; on a
  duplicate the email reads `triage_state = 'auto_matched'` with a NULL score,
  which looks like success and is not.
- **A boilerplate suffix shared by two titles INFLATES their similarity, and
  `AUTO_MATCH_MARGIN` is what feels it.** Mirror image of the Wingtip case above:
  there a tagline appended to a COMPANY name deflated similarity and hid a real
  match; here boilerplate appended to a TITLE inflates it and blurs two real
  ones. Adventure Works tags every posting `(Adventure Works Portfolio Company)`, which
  lifts `Senior AI Engineer` vs `Agentic AI Engineer` from 0.462 to **0.714**.
  Applying to both roles a day apart then produced best 0.900 / runner-up
  0.752 — margin **0.148 against the required 0.15**, so a correct match landed
  in triage, missed by 0.002. Note the platform term cancels out of a margin
  whenever both candidates share a platform, so title does all the work.
  **This is the margin working, not failing** (guessing would have filed the
  Senior AI Engineer confirmation onto the Agentic AI Engineer application —
  silent and wrong), and the same email's twin auto-matched a day earlier at
  the identical 0.9 purely because it was then the only candidate and
  `len(scored) == 1` waives the margin.
  **A fix was measured and deliberately NOT implemented** (7 Aug 2026), so
  re-measure before reopening rather than re-deriving: stripping a trailing
  parenthetical ONLY when both titles carry the identical one fires once across
  all 37 same-company title pairs, and replayed over 143 real scored emails it
  touched 40 comparisons, changed 2 decisions, both correct (one matching an
  application a human had already resolved to by hand), with zero regressions.
  Stripping parentheticals ALWAYS is disqualified on real data — it collapses
  `Senior Software Engineer (Backend)` vs `(Web)` and `AI Engineer (GenAI)` vs
  `(Generative AI / AI-ML / Microsoft Copilot)` to similarity **1.000**, where
  the parenthetical is the only thing distinguishing two live applications.
  Note this rule pushes toward MORE auto-matching, i.e. the silent direction,
  so the replay is the evidence that clears it, not the argument.
- **Mail the user SENT files what the user did, never an employer's outcome**
  (24 Sep 2026, migration 016; the ingest side is in
  `.claude/rules/mail-ingest.md`). `EVENT_TYPE` maps the four `sent_*`
  classifications: an application to `applied`, a follow-up to
  `follow_up_sent` (which is what takes a record off `/follow-ups`), a reply
  to a note (no status moves), a withdrawal to `withdrawn`. Three rules sit
  beside it. `_event_time` returned the SEND time for sent mail, whatever
  `event_date` the extractor found — in the user's own message that date is
  the other side's event (the interview being arranged), never theirs. (Hours
  later the same rule was extended to ALL mail — the stated-date bullet at
  the end — so the function no longer reads `sent_by_user` at all.)
  `_append_event` turns an `applied` into a note when
  the record already has one: a resume emailed for a role already applied
  to on the platform is part of that application, and two `applied` events
  on one record read as two starts. And `_CREATES` lets the user's own
  emailed application mint a record when nothing matches, as a confirmation
  always could — a reply or follow-up never does. **Writing that exposed a
  latent bug**: `dispatch` called `_create_application` WITHOUT the
  classification, so it always took the default `"confirmation"`. Harmless
  while only confirmations could create; for an emailed resume it would have
  filed a confirmation the employer never sent beside a fabricated
  `applied`. `tests/test_integration.py` path 3g pins each rule and loops
  `SENT_TYPES` against both `EVENT_TYPE` and the DB CHECK. The extractor
  needed no change: on all 26 real sent emails it named the counterpart's
  company and recruiter off the quoted thread, never the user.
- **A bare date has no time-of-day — never default it to midnight.** Midnight
  UTC displays as the previous day for non-UTC users, and local midnight sits
  on the date boundary so it shifts if the user later changes timezone. The
  two ingest paths differ by what they legitimately know: an email HAS a real
  instant, and since 24 Sep 2026 `matcher._event_time` simply uses it (until
  then it put a stated date on `received_at`'s time-of-day — the next
  bullet); a typed form date does not, so `ingest.local_date_to_utc` anchors
  it at local noon (`ingest.DEFAULT_TIME_OF_DAY`) rather than inventing the
  submission time — fabricated precision the user can't see or correct.
- **An event happens when its email arrives; a date the email STATES rides
  in the payload** (fixed 24 Sep 2026, worklog task 17 — the history of how
  the rule was found follows). `_event_time(email_row)` is `received_at` and
  nothing else, for every classification and both directions; the extractor's
  `event_date` goes into the email's OWN event as `payload.stated_date`
  (`_append_event`), never into the start `_create_application` fabricates
  beside it. The detail page shows it as "for 14 Oct 2026" only when it falls
  AFTER the day the event is filed on (`web._stated_ahead`): forward, a
  stated date is what the email looks forward to — the interview, the slot a
  cancellation freed, a test deadline; at or before arrival it is either
  history the timeline already draws or the extractor's misreading, and both
  real digests below would have printed a date their emails never said.
  **The date is also `find_match`'s date signal**, so the change was replayed
  before it shipped: of the 14 emails it could move, 2 decisions changed, one
  each way and neither wrong — a hand-linked invite (stated 20 days ahead)
  now auto-matches its own application, and the rejection quoting its
  submission date now scores 0.735 against the 0.75 bar and would wait in
  triage, i.e. it is scored like every other rejection that arrives a month
  after the apply. Repair, dry-run first and snapshotted
  (`job-tracker-snapshots/2026-09-24-stated-dates.json`, outside the repo):
  14 events moved back to arrival — 10 invites, the rejection, 3 notes; 0
  left in the future (was 3) and 0 email events off their email's arrival —
  and 199 correctly dated ones gained `stated_date`, so every event from an
  email that stated a date carries it, exactly as new mail does. UPDATE in
  place, guarded on each row's old values, rather than refile's delete and
  re-insert, which would drop a `reason` a human had set on a rejection.
  The OTHER machine keeps the old rule until it pulls — the shared DB cannot
  tell it apart — so new invites filed there still land on the interview
  day. `email_extract_v1`'s "the caller falls back to the received timestamp
  when this is null" is now only half the story; it is deliberately not
  re-versioned (invariant #5), since what it asks the model to extract is
  unchanged. Tests: `test_integration` path 1 (a rejection quoting the apply
  day sorts after it), path 2 (asserted the OPPOSITE until this change), 3g,
  and 3h (a future-dated invite); `test_web` the display rule both ways.
  **The history.** A stated event date is right for a confirmation and wrong
  for an interview invite, and `matcher._event_time` applied it to both (found
  2 Sep 2026). `email_extract_v1` rule 4 defines `event_date`
  to include "an interview scheduled for a specific date", so an invite's
  timeline event lands on the INTERVIEW day rather than the day the invite
  arrived: 4 of 37 invite events sit 1-14 days after their own email (mean 8),
  and one was dated in the future when measured. Three consequences:
  `avg_days_to_resp` is inflated on those rows; the list's trace axis
  stretches past today while its right-edge label still reads "today"
  (`trace.build` takes `t1 = max(now, last stamp)`); and `silent_days` reads
  0 until the date passes. Fix shape: let a stated date move confirmations
  and rejections only, and keep an interview date in the event payload.
  **That shape is wrong for rejections, found by the 24 Sep 2026 data
  audit.** A rejection that arrived 7 Sep quotes the date the application was
  submitted ("...on 04/08/2026"), so it filed on the apply day and sorts
  BEFORE the `applied` event — a same-day rejection that never happened. And
  two LinkedIn "new activity in jobs you applied for" digests whose body
  reads "Applied on 29 Jul" were extracted with `event_date` 2 Jul, filing
  notes before the search began. The rule that covers every case: an event
  happens when its email arrives; a stated date belongs in the payload.
  Sent mail already follows it (the bullet on sent mail, above).
