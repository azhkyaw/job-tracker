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
- **A bare date has no time-of-day — never default it to midnight.** Midnight
  UTC displays as the previous day for non-UTC users, and local midnight sits
  on the date boundary so it shifts if the user later changes timezone. The
  two ingest paths differ by what they legitimately know: an email HAS a real
  instant, so `matcher._event_time` borrows `received_at`'s time-of-day; a
  typed form date does not, so `ingest.local_date_to_utc` anchors it at local
  noon (`ingest.DEFAULT_TIME_OF_DAY`) rather than inventing the submission
  time — fabricated precision the user can't see or correct.
- **A stated event date is right for a confirmation and wrong for an
  interview invite, and `matcher._event_time` applies it to both** (found
  2 Sep 2026, NOT yet fixed). `email_extract_v1` rule 4 defines `event_date`
  to include "an interview scheduled for a specific date", so an invite's
  timeline event lands on the INTERVIEW day rather than the day the invite
  arrived: 4 of 37 invite events sit 1-14 days after their own email (mean 8),
  and one was dated in the future when measured. Three consequences:
  `avg_days_to_resp` is inflated on those rows; the list's trace axis
  stretches past today while its right-edge label still reads "today"
  (`trace.build` takes `t1 = max(now, last stamp)`); and `silent_days` reads
  0 until the date passes. Fix shape: let a stated date move confirmations
  and rejections only, and keep an interview date in the event payload.
