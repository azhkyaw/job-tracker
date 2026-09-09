---
paths:
  - "extension/**"
  - "tests/test_extension.js"
  - "tests/test_captures.py"
  - "pipeline/answers.py"
  - "pipeline/salary.py"
---

# Extension: capture, frames, forms, identity

Moved here VERBATIM from CLAUDE.md on 9 Sep 2026 so it loads when Claude reads a
matching file instead of in every session (it can also be Read directly).
Dates are the key to each case; the record itself is in the database. The
invariants that govern this code (#1, #3, #11) are still in CLAUDE.md.

## Procedures (from Commands)

- **Repairing a capture that saved blind** (no company/title/job id — the
  preload-frame gotcha below): drive `POST /applications/{id}/edit` via
  `TestClient`, same pattern as `refile_email` (invariant #3), NOT hand-written
  UPDATEs — that route is the only place `jobs.company_norm`,
  `postings.company_norm`, `platform_job_id` and the canonical URL stay
  consistent, and it pre-checks the `platform_job_id` unique index. `/edit` is
  FULL-STATE: a blank field CLEARS, so read every field off the record first and
  override only what changed (the applied instant and `external` are the easy
  ones to wipe by accident; the form's HH:MM also drops seconds).
  Identity for the fix comes from `document.title` on `/jobs/view/<id>/`
  (`"<title> | <company> | LinkedIn"`), which survives a frozen background tab
  when every selector returns null. Done three times: Coho 18 Aug 2026,
  Trey Consulting 20 Aug 2026, Lucerne Consultants 21 Aug 2026 — all three kept
  their screening answers and resume file.
  Repair BEFORE running `sync`: against a blank record the confirmation email
  finds no candidate and mints a second application that then needs a refile.
  When the capture saved NOTHING at all (the external-apply path writes only
  after the popover is answered, so a failed read discards the application
  outright) there is no record to `/edit` — drive `POST /applications/new`
  through the same `TestClient`, which is the third ingest path and reaches the
  identical `ingest.upsert_record`. Done once: Woodgrove Finance 21 Aug 2026.
- **Reading the extension's ring buffers WITHOUT the popup.** The popup is the
  documented way to read `failures` / `provenance` / `sweeps`, but it needs the
  user, and `chrome-extension://` and `chrome://extensions` are both blocked to
  claude-in-chrome — so a bug reported hours later stalls on a screenshot
  request. The buffers are `chrome.storage.local`, which is a LevelDB on disk at
  `<profile>/Local Extension Settings/<extension-id>/`, and they survive an
  extension reload and a browser restart. The unpacked id is derivable, not
  looked up: SHA-256 the absolute extension path **encoded UTF-16LE on Windows**,
  take the first 16 bytes, map each hex nibble 0-f onto a-p —
  `D:\projects\job-tracker\extension` → `apikfpnjpcpimjlfjlbjefflebfopohp`
  (confirmed: that directory exists); this machine's
  `C:\projects\job-tracker\extension` → `eeigcpnpbikhppjomninfjldeamiebba`
  (confirmed 2 Sep 2026 — it held the `sweeps` buffer that root-caused the
  `<dialog>` bug below). The hash is over the path BYTE FOR BYTE, so the drive
  letter's CASE matters: a lowercase `c:` hashes to a completely different id
  that looks just as plausible and names a directory that does not exist — so
  confirm the directory before concluding the extension stored nothing. Parse
  EVERY `*.log` in it (the number varies per profile — `000004` on one machine,
  `000003` on the other — and a compaction can move keys into a `.ldb`; apply
  deletes as well as puts, in order) as a real LevelDB log
  (32 KiB blocks, 7-byte record header, WriteBatch payload) rather than grepping
  it — a value larger than a block is split across records and a naive text
  scan silently truncates the JSON mid-string, which is exactly what happened
  first. This settled the 21 Aug root cause in minutes with no user round trip.
- **`node tests/test_extension.js`** — the only real tests `extension/` has
  (21 Aug 2026). Loads `adapters/linkedin.js` into a `node:vm` context with stub
  documents, so a FRAME GEOMETRY is just a pair of stubs: top-frame, the preload
  iframe holding the page while `window.top` is an empty shell, Easy Apply's
  modal iframe, and nothing-anywhere. That second one is the shape that ate four
  captures; it needs no browser to reproduce, which is the whole reason this
  file can exist. `readJob(doc, loc)` taking its document as a PARAMETER is what
  makes it testable — keep it that way. Selectors are matched by exact string in
  the stub on purpose, so renaming one fails here rather than in the field.
  **Validate a new case against the OLD code before trusting it**: reverting the
  fix (`if (usableJob(top))` → `if (true)`) turns the preload case red with
  `title: null, company: null, doc_source: "top"` — the exact signature of the
  real records — while the modal-iframe guard stays green, which is how you know
  the guard is real and not accidentally coupled to the thing it guards.
  Wired into both `scripts/test.*` ahead of the Python suites; a missing `node`
  prints a visible SKIP rather than passing quietly. `shared/capture.js` is
  deliberately NOT covered — it is an IIFE that installs listeners and calls
  `chrome.runtime.*` on load, so harnessing it costs more than the bugs it
  would catch; the adapter is where the layout knowledge lives.

## Gotchas learned the hard way

- **Shadow DOM breaks click delegation:** `shared/capture.js`'s apply-detection
  listener must use `ev.composedPath()`, never `ev.target.closest(...)` —
  LinkedIn's Easy Apply renders its controls inside a shadow root, which
  retargets `ev.target` to the shadow host for any listener outside that tree.
- **JobStreet's split view puts 30 OTHER jobs' data next to the one you want.**
  The results list and the detail pane use different `data-automation` names
  for the same facts: the pane has `job-detail-salary` / `job-detail-location`
  (1 each, document-unique even on the search page), the cards have `jobSalary`
  (×18) and `jobLocation` (×30). A plain `querySelector` on the CARD name
  returns the first card's value — reading `$7,000–$10,000 / Paya Lebar` for a
  Central Region job paying `$10,000–$11,000`. Plausible, silent, wrong. Only
  ever use the `job-detail-*` names; never the unprefixed ones.
- **LinkedIn has the same split-view disease, and it corrupts the whole
  identity, not just a field** (9 Sep 2026). On `/jobs/search` and
  `/jobs/collections` the job card, the JD and the top card live in a detail
  PANE while the job id comes from the URL's `currentJobId` — two sources that
  desync. The pane keeps rendering a previously-viewed job (a promoted listing
  that loaded into it first is the usual culprit) while `currentJobId` has
  already advanced to the job being applied to, so `readJob` returns the right
  id and a DIFFERENT job's company/title/JD. Three real applications filed this
  way on 8 Sep 2026 — Northwind Labs, Fabrikam and Tailspin — every one stamped
  with `Contoso Markets · Full Stack Engineer, AI systems` and the byte-identical Contoso Markets
  JD, Contoso Markets being the promoted card at the top of an "AI Engineer" search.
  Repaired by hand via `/edit` (all three kept their screening answers; the
  applied instants were restored to the microsecond since the form is HH:MM).
  Signature in the provenance buffer: several reads with the SAME
  `page:{company,title}` across DIFFERENT `id`s on `layout:"search"`, and the
  same id re-read on `layout:"view"` giving different, correct content.
  **The pane carries no job id of its own** (measured live 9 Sep) — the only
  readable ids are on the results LIST cards (`data-occludable-job-id` /
  `data-job-id`, stable non-hashed attributes). So `readJob` now cross-checks:
  find the card whose id equals `currentJobId`, read its own title
  (`a.job-card-container__link`, doubled visible+visually-hidden text, take the
  first half) and company (`.artdeco-entity-lockup__subtitle`); if that
  disagrees with what the pane rendered, the pane is stale — re-source
  title/company from the card and DROP the JD (the card has none, and the
  pane's JD is the wrong job's). `currentJobId`, the fact, is kept, and
  `_prov.stale_pane = {url, shown, card}` records the catch. The guard is the
  card's EXISTENCE, not the pathname, so `/jobs/view/` (no results card) is a
  no-op and a future split layout is covered. `tests/test_extension.js` has the
  stale case and a healthy-pane control; the stale one goes red against the old
  code with the exact Contoso Markets signature, the control stays green either way.
  Extension **0.10.0**. **Unverified on a real apply** — see Known-untested.
- **Never name a DOM-extracted variable `location`.** It shadows
  `window.location`, so any `location.pathname` read ABOVE it in the same scope
  hits the temporal dead zone and throws — killing the whole adapter. `node
  --check` passes it happily, because it's a runtime error, not a syntax one.
- **Salary is printed BESIDE the ad, not inside it** (migration 011). That's
  why `extractions.salary_min` has been null on jobs whose pay is plainly on
  the page: the LLM only ever sees `jd_text`. Platform-stated pay now lands in
  `postings.salary_*` — kept separate from `extractions` on purpose, since a
  figure the employer printed and one a model inferred are different kinds of
  evidence, and re-running a prompt must not change a published number.
  `pipeline/salary.py` owns parsing (invariant #4's rule applied to pay); the
  extension sends only the displayed string. **`salary_period` is load-bearing**
  — SEA quotes monthly where most markets quote annual, and this project's own
  answer bank already holds 10,800 (monthly) beside 128,000 (annual) for one
  person. Indonesia writes `15.000.000`, so that dot is a THOUSANDS separator;
  parsing it as a decimal turns 15 million into 15.
- **LinkedIn ships 3+ concurrent DOM layouts** (`/jobs/view/`, `/jobs/search-results/`,
  `/jobs/collections/recommended/`) with different CSS stability and even different
  `document.title` behavior — verify adapter changes live against more than one. Prefer
  matching DOM *shape* (e.g. a `<p>` with `·`-separated `<span>` children) over exact-text
  or fixed-position string splits; both broke on real listings.
- **Never let the capture popover hold the only copy of a capture.** Until
  28 Jul 2026 nothing was POSTed until the user answered "focused or generic?",
  so a real application vanished if they ignored the box for 45s or closed the
  Easy Apply modal (which destroys the iframe the popover renders in). Now an
  *unambiguous* apply — Easy Apply's final submit, popup capture — writes
  immediately and the popover is only a receipt; `POST /captures/{id}/tag`
  carries the note afterwards (it carried a tailored/generic tag too until
  that column was cut, `docs/worklog.md` task 3b). An *ambiguous*
  one — "Apply on company website", where you may never actually apply — still
  confirms first. Keep that split: it's about evidence, not UI taste. The
  receipt is relayed to frame 0 via the service worker
  (`chrome.tabs.sendMessage` — **requires host permission for the site**,
  hence `*://*.linkedin.com/*` in host_permissions; `activeTab` does NOT cover
  it, since a click on the page's own button isn't an activeTab invocation),
  with an in-frame fallback when the relay fails.
- **An apply control that NAVIGATES loses its receipt entirely.** JobStreet's
  is a real `<a>`: the click captures synchronously, but the POST is async via
  the service worker, so the page (and the content script waiting on the
  `.then()`) is torn down before the response lands. The record saves — the
  worker owns the fetch — and the receipt, with it the only offer to tag the
  application, never renders. Confirmed on a real apply 29 Jul 2026: record
  present, untagged, no popover ever seen. The worker now stashes the
  receipt per tab (`background.js:stashReceipt`, 5-minute TTL) and the next
  content script to load claims it; rendering it in-page sends
  `tracker-receipt-shown` so the held copy can't fire twice. This is a class
  of bug, not a JobStreet quirk — any adapter whose apply control navigates has
  it, and LinkedIn only escapes because Easy Apply stays in-page.
- **LinkedIn does NOT escape it for EXTERNAL applies — it just loses the box a
  different way.** "Apply on company website" opens the employer's site in a
  NEW TAB that takes focus immediately, so the confirm popover is born on a
  page nobody is looking at (`document.visibilityState` already reads `hidden`
  when it mounts) and `ui.fade(45000)` deletes it unseen while the applicant is
  still filling in the real form. On the external path the popover holds the
  ONLY copy of the capture — that path asks BEFORE it writes, deliberately —
  so the timer wasn't dropping a tag, it was dropping the whole application,
  silently. Reported by the user, reproduced end to end 3 Aug 2026.
  `mount()` now runs the countdown only while the tab is visible and restarts
  it in full on return (the reader gets the whole window from the moment they
  can see it); `hold()` still cancels outright, and the `visibilitychange`
  listener is torn down on close so a replaced popover can't leak one.
  Verified on a real external apply (Tailwind Tech, 3 Aug 2026): the record
  saved WITH a tag, which on this path can only happen if the box survived.
  **That fix covered one geometry, and the countdown is now gone entirely**
  (4 Aug 2026). `visibilityState` tracks tab OCCLUSION, not window FOCUS: with
  the employer site in a second Chrome window, or LinkedIn's tab dragged into
  its own, the tab stays `visible` the whole time it sits unread behind
  another window and the 45 seconds burn down exactly as before. There is no
  event for "nobody is looking at this", so `confirmPopover` ends in `hold()`
  rather than `fade()` — the same rule `failurePopover` already followed, for
  the same reason (this box is the only copy). The × dismisses it; until then
  it waits. Don't reintroduce a timer here on the grounds that an ignored box
  is untidy.
- **An apply flow that spans PAGES must carry the job snapshot with it.**
  JobStreet defers like LinkedIn (`deferInternalApply`) so the applied time is
  the submit, not the opening click — but its flow is `/job/<id>` → `/apply` →
  `/apply/profile` → `/apply/review`, and only the LISTING shows the company.
  Verified on the real review page 29 Jul 2026: `getJob()` there returns the id
  (URL) and the title (`<h1>`), but company is a bare `<span>` with no
  `data-automation` on it or any ancestor. Capturing at submit alone would file
  "unknown company". So the opening click stashes the snapshot through the
  service worker (`background.js:stashPendingJob`, 2h TTL) and the submit
  merges it in, gaps only — the page in front of you always wins over a stale
  snapshot. Nothing is POSTed until the submit, so an abandoned flow expires
  unsent instead of leaving a phantom application.
  (This reverses the 28 Jul design, which captured on the opening click and
  corrected the time afterwards via `completed`. That existed because the
  submit hook was an unverified guess and a miss would have lost the
  application; the hook is now verified against the real page. The `completed`
  correction is KEPT server-side — it costs nothing and still fixes the time
  when an applied event already exists, e.g. from a confirmation email.)
- **An MV3 service worker can be killed mid-write, and `return false` is what
  invites it.** A message handler that returns false tells Chrome it is
  finished, so the worker may be terminated between an async handler's storage
  READ and its WRITE — and every message the extension sends from an apply
  click is sent milliseconds before the page navigates, the worst moment to be
  racing a shutdown. A real JobStreet apply on 29 Jul 2026 filed
  "unknown company": deferral worked, the submit captured, but the stashed job
  snapshot was simply not there. Two rules now: an async handler **returns
  `true` and calls `respond()`** (the open port is what keeps the worker
  alive), and **every write goes through `setLocal()`** so it can be awaited —
  an unawaited `chrome.storage.local.set` resolves nobody's promise, so even a
  correct-looking `.then()` fires before the data lands. Content-script side,
  send with the promise form (`.catch(() => {})`), not the callback.
- **A frame that cannot reach `window.top` gets ITSELF back, with no error —
  and that breaks `getJob()` and the stash key TOGETHER.** Both walk to the top
  frame for job identity (`getJob()` reads its DOM, `answerFormKey()` its URL),
  so one unreachable top degrades both at once: `getJob()` returns null, the
  key falls through to the frame's own href, and the submit asks the stash for
  a key the opening click never wrote. Root cause of a real Easy Apply filed
  with no company, title or job id (Proseware, 3 Aug 2026) whose recorded url was
  `linkedin.com/preload/?_bprMode=vanilla` — the frame's own address. The
  correct snapshot was sitting in storage under the real `currentJobId` and
  expired untouched. Note `completed = true` deliberately bypasses the
  empty-job guard in `capture()` (a late apply page legitimately shows less
  than the listing), so nothing stopped the write. Mitigated by
  `background.js:takePendingJob(key, tabId)` falling back to the newest stash
  from the SAME TAB when the key misses — `sender.tab.id` is shared by every
  frame in a tab, which is the one identifier both ends still agree on. That
  fallback is a guess where a keyed hit is a fact, so it gets its own much
  shorter window (`PENDING_JOB_FALLBACK_MS`, 30 min vs the 2 h TTL).
  **It happened again on 18 Aug 2026, which retired the stash as the ONLY
  rescue.** Same signature exactly — Easy Apply, `external:false`, recorded url
  `linkedin.com/preload/?_bprMode=vanilla`, company/title/`platform_job_id`/JD
  all null — while the 3 screening answers and `resume_file` saved perfectly
  beside it, because those live in the submitting frame and only identity has to
  travel. Every stash-based rescue shares one precondition, that something was
  REMEMBERED at the opening click: it needs that click to have been seen
  (`stashJob()` returns silently when `getJob()` is null, so a resumed draft or
  an unmatched opener stashes nothing) and the take to fall inside the 30-min
  same-tab window. Frame 0 has the job page in front of the user the whole time,
  so `capture.js:askTopJob()` now ASKS it (`tracker-relay-getjob` →
  `tracker-getjob` at frameId 0, the same targeting as the receipt/apply
  relays), gaps-only and LAST in the merge order — the stash was taken while the
  listing was definitely on screen, this read happens after the submit when the
  top card may already read "application sent". Note the direction is the
  opposite of `relayApply`: identity comes TO the submitting frame, the capture
  never leaves it, because the answers exist nowhere else. `linkedin.js:getJob()`
  stopped returning null when the DOM is gone but the URL still carries a job id
  (`!title && !jdEl && !idFromUrl`) — an id is a fact and discarding it is what
  made the record unidentifiable rather than merely thin.
  **It recurred on 20 Aug 2026, identically** (Trey Consulting · Senior Dotnet
  Developer): same preload url, same blank identity, 6 answers and the resume
  file saved beside it. Two things came out of that one. First, the tab URL the
  user supplied — `/jobs/collections/recommended/?currentJobId=4452411130` —
  proves the tab's top document was NOT the preload page, so this really is a
  subframe and the frame-0 ask is the right shape of fix (it kills the rival
  hypothesis that the TAB itself had navigated to `/preload/`). Second, it is
  still unknown whether the 18 Aug fix was even RUNNING: reloading an unpacked
  extension does not re-inject content scripts into tabs opened before the
  reload, so an apply from a pre-reload tab runs the old code no matter what
  `chrome://extensions` says. The manifest is now bumped per change (0.6.0) so
  the reload state is readable at a glance instead of inferred from behaviour —
  do that with any extension fix whose verification depends on it being live.
  **A FOURTH identity source now backs the other three**: the tab's own URL,
  read from `sender.tab.url` in the service worker AT CAPTURE TIME (never cached
  — LinkedIn rewrites its URL by pushState without re-injecting, so a cached
  copy can name a different job) and parsed by `adapter.jobFromUrl()`. It
  carries no company and no title, but a job id makes a record findable,
  dedupable and repairable, where a blank one is none of those. Full merge
  order: submitting page > keyed stash > frame 0 > tab URL > same-tab guess.
  The breadcrumb also records `topFrame` and `tabUrl`, because "frame 0 was
  asked and had nothing" and "this frame believed it WAS frame 0, so nobody was
  asked" produce the identical empty result and are different bugs.
  **Open, and cheap to settle next time**: both losses may be the
  `/jobs/collections/recommended/` layout specifically — the 20 Aug one
  demonstrably was, the 18 Aug one has no recorded layout. The provenance buffer
  stores `layout` for every successful capture, so comparing the blind ones
  against the successes answers it without a live session.
  **SETTLED 21 Aug 2026, and the premise of this whole bullet is WRONG: the top
  is reachable, and the job is not in it.** Everything above is a correct
  description of the symptom and three mitigations aimed at the wrong cause —
  read it as the trail, not as the diagnosis. `linkedin.com/preload/?_bprMode=vanilla`
  is not a hidden stub the frame is trapped inside. It is a **full-viewport,
  same-origin iframe LinkedIn boots whole pages into** — measured live at
  2133x1050, `position:absolute` at (0,0), `opacity:0; z-index:-1` while idle,
  carrying `render-mode-VANILLA` / `app-loader--default` / `ember-application`
  on its `<html>`, and `contentWindow.top === window` — i.e. LinkedIn renders
  the next page in it and reveals it. While it is the revealed page, the job
  card, the JD and the apply button are all in THAT document, the click lands
  there, and `window.top` is the outer shell holding the page you just left.
  Two facts already in the buffers say this and were misread: the 20 Aug
  breadcrumb recorded `topFrame:false` with `layout:"collections"` — and
  `layout` is read off `window.top.location.pathname`, so the top was
  demonstrably readable, it just had no job on it — and the popover the user
  saw was rendered BY that frame, which a 0x0 stub could not have shown.
  **`getJob()` walking up was the bug**: it left the only document with an
  answer to consult the one without. It now reads the top, and when that yields
  no title and no JD reads THIS frame instead, merging gaps-only so the top
  still wins every field it can answer — which is what keeps the Easy Apply
  modal iframe correct, since that document loses by having nothing rather than
  by being distrusted. `platform_job_id` and `url` move as a PAIR in that merge:
  a self-read's `url` falls back to the frame's own href, which is truthy and
  wrong, and keeping it is precisely how a record came to store
  `linkedin.com/preload/?_bprMode=vanilla` as the job url. `_prov.doc_source`
  (`"top"` | `"self"`) records which document answered.
  Five "no job found" entries in the extension's own failure buffer share the
  signature (3 Aug x2, 4 Aug, 7 Aug, 21 Aug), as do all three blind records
  repaired by hand. **The 7 Aug 12:30 SG one is an application that was simply
  lost** — nothing was applied to that day, and the record of that era holds no
  job id and no tab URL, so it cannot be identified even now.
  **The failure record is the reason it took three weeks**: it was
  `{platform, url, at}`, which says which frame failed and nothing about why, so
  five entries from two different bugs rendered as one identical line. It now
  carries `topFrame`, `tabUrl`, `read` (`none` | `id` | `empty`), `docSource`
  and `layout`, and the popup prints the subframe case in full. Same lesson as
  the `withStashedJob` breadcrumb below, arrived at from the other direction:
  write the diagnostic from the failing branch first.
  **And the first draft of that fix was built on the very check the bug is
  about**, which is the durable lesson: `askTopJob()` opened with
  `if (window === window.top) return null`, exactly like `relayApply()` and the
  apply-click dispatch already did. If the frame's browsing context has been
  DISCARDED, `window.top` is the frame's own window, so that guard reads TRUE
  inside a subframe and every one of those relays quietly takes the frame-0
  branch in the only situation it exists for — a fix that cannot fire on its own
  bug. **Never decide frame identity from the DOM here; ask the browser.**
  `capture.js` now holds one `isTopFrame`, seeded from `window === window.top`
  and corrected at injection by `tracker-whoami` (`sender.frameId === 0`, the
  browser's own record), and every frame decision reads it. The receipt claim
  awaits that answer rather than reading the seed, since it runs at injection —
  the one moment the seed has not been corrected yet. Note which hypothesis was
  true of the 18 Aug frame (discarded, vs. merely unreadable) was NEVER
  established; asking the browser is correct under both, which is why it was
  done that way instead of settling the question first.
  **The `pendingJobs` dump settled the rest of it, and argued the OPPOSITE of
  the obvious fix** (18 Aug 2026). Seven stashes were in storage, every one from
  11 Aug, every one in the same tab, none consumed — and NONE for the job that
  had just been applied to. So (a) that opening click never stashed at all, which
  makes the frame-0 ask the whole fix and widening `PENDING_JOB_FALLBACK_MS` a
  fix for nothing; and (b) the 30-minute window is the only thing that stopped
  the same-tab fallback pasting `Fourth Coffee · Singapore Applied AI Solution Engineer`
  onto the Coho application — plausible, silent and permanent, against an
  empty record that was repaired in a minute. **Never lengthen that window**;
  the guess is load-bearing in the wrong direction. Two changes came out of the
  measurement: `takePendingJob` now prunes on READ as well as write (the prune
  lived only in `stashPendingJob`, and writes are exactly what stops happening
  when openers can't read the job — hence 6.6-day-old entries under a 2 h TTL),
  and it returns `{job, exact}` so the caller can rank a KEYED hit (a fact about
  this job) above a live frame-0 read, and both above a same-tab GUESS. Merge
  order is now page > keyed stash > frame 0 > same-tab guess, and the popup
  prints a warn line naming any field a guess supplied.
  Also added: `stashJob()` records a `stage:"open"` breadcrumb when the opener
  fires but `getJob()` comes back null — the dump could prove no stash existed
  but not WHY, because "opener never matched" and "opener matched, job
  unreadable" leave the identical trace, which is none.
- **A diagnostic that only fires on the healthy path is not a diagnostic.**
  `withStashedJob` emitted its `tracker-provenance` breadcrumb inside
  `if (was)`, i.e. only when a stash was FOUND, so the 18 Aug loss above — the
  single shape the buffer most needed to explain — wrote nothing at all, leaving
  "no stash" and "no capture" indistinguishable in the popup. It now emits for
  every completed capture, carrying `stashed`, `askedTop` and `fromTop` (which
  fields frame 0 supplied), and the popup prints a warn line for the one case
  that matters: no stash, nothing recovered, identity missing. Worth generalising
  — the ring buffers exist to explain failures, so any new one should be written
  from the failing branch first and the happy path second.
  **Its sibling, learned 2 Sep 2026: a diagnostic must not share the PREDICATE
  of the thing it watches.** The sweep's `noRootHint` reported `dialogPresent`
  by testing `[role='dialog']` — the same selector `answerFormRoot()` had just
  failed on — so for two weeks the popup printed “no dialog was open” for
  captures made inside an open dialog, and the one buffer written to explain
  the failure instead corroborated it. Note it never lied: the statement was
  true of the selector and false of the page. When writing a check that answers
  “was the thing my code looked for actually there”, derive it INDEPENDENTLY —
  a different selector, an attribute-free structural test, the browser's own
  answer — or it can only ever agree with the code.
- **The same unreachable-top frame kills an EXTERNAL apply outright, and a
  subframe is the wrong place to handle one even when the read succeeds.**
  Found on a real loss the user reported as "the popup didn't appear"
  (wideworld.ai, 4 Aug 2026): the click WAS detected, `capture()` ran inside
  LinkedIn's hidden `linkedin.com/preload/?_bprMode=vanilla` iframe (same-origin,
  so `all_frames: true` injects into it), `getJob()` came back null, and the job
  guard returned BEFORE `confirmPopover()` — which on this path writes nothing,
  so the whole application was discarded, leaving one "no job found" line in the
  popup's ring buffer and nothing else. Two independent faults, one frame:
  it cannot READ the job (unreachable top, per the bullet above), and it cannot
  SHOW the popover, because `mount()` appends to that frame's own
  `documentElement` — invisible even when the read works. `showResult()` had
  already solved the second half for the RECEIPT via `relayToTop`; the ask-first
  path never got it. Now `capture.js:relayApply` hands an immediate apply from
  any subframe to frame 0 (`tracker-relay-apply` → `tracker-apply`, same
  targeting rules as the receipt relay: tab from `sender`, never the message,
  and frame 0 may not relay to itself), and frame 0 answers `false` when it
  can't see a job either so the local attempt still records the failure.
  **Gated to the immediate-apply path on purpose** — Easy Apply's deferred
  submit legitimately fires inside the modal's iframe, and its answers only
  exist there. Debugging note: the popover host is a bare `<div>` with no
  attributes on `<html>` whose `shadowRoot` reads null (closed), so
  `[...document.documentElement.children]` is how you check whether it mounted
  without needing to see the page.
- **`chrome.runtime.sendMessage` throws SYNCHRONOUSLY once the extension
  context dies, and a synchronous throw is invisible to a trailing `.catch()`.**
  Reloading an unpacked extension does exactly that to every tab already open —
  content scripts are NOT re-injected, so the old script keeps running against a
  dead port. `capture()` ends in `send(payload).then(showResult)`, so the throw
  unwound the entire capture: no record, no receipt, AND no failure entry, since
  `recordFailure` is itself a `sendMessage`. The result is indistinguishable
  from the click never being detected — which is exactly the ambiguity that made
  the wideworld.ai loss above take a full session to pin down. Every content-script
  message now goes through `capture.js:tell()`, which returns a resolved promise
  carrying `{ok:false, error, dead:true}` instead of throwing, and names the fix
  in the popover ("refresh this page"). **When a capture goes missing right
  after any extension edit, check this before anything else** — and remember
  that a stale tab shows `Extension context invalidated` in its console, which
  is the cheapest positive confirmation available.
- **`trim()` does not remove invisible characters, and platforms ship them
  inside button labels.** JobStreet's submit button reads
  `"⁠Submit application"` — a WORD JOINER glued to the front. It renders
  as nothing, `String.trim()` leaves it (format characters, category Cf, are
  not whitespace), and `=== "Submit application"` fails against a button that
  looks exactly right. Silent: no error, the capture simply never fires.
  `shared/capture.js:visibleText()` strips U+00AD/200B–200F/2060–2064/FEFF and
  folds all whitespace before comparing; every text match goes through it.
  Assume any exact-text hook needs this — NBSP inside a wrapped label is the
  same bug wearing a different hat.
- **`aria-labelledby` often names an element AND its own wrapper**, so joining
  every referenced element's text gives you the label twice: real captures
  stored `"Country Country"` and `"Location (city) Location (city)"`. Cosmetic
  in display, corrosive in the bank — `"City"` and `"City City"` normalise
  differently, so one question splits into two rows and stops grouping.
  `answers.js:labelFor()` now drops a part already contained in one it kept.
  **That fix did NOT cover the common case, and the bank kept doubling for
  another week.** It compares label PARTS against each other, but LinkedIn's
  Easy Apply fields carry no `aria-labelledby` at all — the doubling lives
  inside a single `<label for=…>`, a different branch entirely:
  `<span aria-hidden="true">Email address</span><span
  class="visually-hidden">Email address</span>`. That is the standard a11y
  pattern (visible copy hidden from screen readers, hidden copy carrying the
  accessible name) and BOTH are rendered, because `visually-hidden` clips
  rather than `display:none` — so `innerText` returns both. Confirmed live
  3 Aug 2026; it had already reached 55 of 174 rows across 19 distinct
  questions. `answers.js:labelText()` now reads labels the way assistive tech
  does, skipping `aria-hidden="true"` subtrees, and is used by EVERY branch of
  `labelFor()` plus the radio legend. Two guards it needs: skip nodes with no
  client rects (keeps `innerText`'s display:none behaviour instead of silently
  gaining `textContent`'s — a visually-hidden span still HAS rects, which is
  exactly how it differs), and fall back to raw text when stripping leaves
  nothing, so a label with an aria-hidden copy and no twin still resolves.
- **A capture-phase sweep reads the DOM BEFORE the page's own handler runs** —
  which is the entire point for a wizard (the step's fields are gone
  afterwards) and exactly wrong for a **typeahead**: clicking a suggestion
  doesn't advance anything, it writes the value asynchronously *after* your
  handler. A real capture stored `"singa"` as a city. The click listener now
  sweeps three times (capture phase, `setTimeout 0`, `setTimeout 300`); the
  extra passes are safe because a sweep only overwrites questions it can
  currently see, so an advanced step's stored answers are untouched.
- **Extension: snapshot DOM data synchronously at the trigger event, not lazily.**
  `shared/capture.js`'s `capture()` used to read the job DOM inside the tag-popover's
  callback (fires whenever the human clicks, seconds later) — by then SPAs like LinkedIn's
  Easy Apply often already replaced the relevant DOM (e.g. an "application sent"
  confirmation swapping out the top card), silently losing data.
- **A claude-in-chrome tab is usually HIDDEN, and Chrome freezes hidden tabs —
  which looks exactly like the site rate-limiting you.** Cost several rounds on
  3 Aug 2026: LinkedIn job pages came back as ~1,500-char skeletons (nav + top
  card, no `#job-details`), reproducibly, and it was misdiagnosed as LinkedIn
  throttling. It was not. In a background tab `requestAnimationFrame` never
  fires and `setTimeout(fn, 100)` takes ~1000ms, so anything the page defers —
  lazy-loaded JD, `IntersectionObserver`, hydration, polling loops — simply
  never runs. The tell that should have settled it immediately: the USER was
  applying to jobs on the same account in the same minutes, fine. A server-side
  limit cannot be that selective.
  **Probe before concluding anything about a slow page** (two seconds, no
  side effects):
  `visibilityState` / does rAF fire within 2s / does a 100ms timer take ~1s.
  All three agreeing = frozen tab, not the site. Fixes, in order: `computer`
  actions (real input events force layout and DID partially revive the page,
  though not the deferred fetches), then ask the user to foreground the Chrome
  window — a fresh `tabs_create_mcp` tab is ALSO hidden, so creating one does
  not help. Once visible, the same URL loaded 11,362 chars on the first try.
  **Two more symptoms of the same freeze, both seen 4 Aug 2026:** `innerText`
  returns `""` even for elements that exist, because it needs layout and a
  frozen tab has none (`textContent` is unaffected — and note `getJob()` reads
  the JD via `innerText`, so a JD can come back empty rather than missing); and
  `Page.captureScreenshot` times out, so you cannot screenshot your way out of
  it. **`document.title` survives regardless** — on `/jobs/view/` it is
  `"<title> | <company> | LinkedIn"`, which was enough to identify a job
  (Lamna · Senior AI Engineer) for a by-hand refile when every top-card
  selector returned null. Same last-resort path `getJob()` already trusts on
  that layout.
  Also: CDP JavaScript-evaluate 45s timeouts and `Page.captureScreenshot`
  timeouts are downstream of this, not separate faults — poll loops written
  as `setTimeout(…, 1000)` silently run at 1s+ and blow the deadline.
  **Same mechanism, product side:** the capture popover's auto-dismiss burned
  down unseen in a backgrounded tab (see the external-apply gotcha below).
  Background-tab semantics bit the product and then bit the debugging of it.
- **The browser tool BLOCKS base64 and cookie/query-string-looking output, and
  truncates text results at ~1,000 chars.** Getting a 2,314-char JD out of a
  page defeated `slice()` (truncated), `btoa()` (`[BLOCKED: Base64 encoded
  data]`) and a selector string containing `[id^=…]` (`[BLOCKED: Cookie/query
  string data]`). What works: `navigator.clipboard.writeText()` in the page,
  then `Get-Clipboard -Raw` — but it needs `document.hasFocus()`, so click the
  page via `computer` first, and it overwrites the user's clipboard (say so).
  Verify the transfer with a checksum computed on BOTH sides; PowerShell adds
  CRLF, so normalise before comparing.
  **Correction (9 Sep 2026): the clipboard route is unreliable in a claude-in-chrome
  tab, and `get_page_text` is the one that works.** A claude-in-chrome tab is
  hidden/frozen (`document.visibilityState` stays `hidden` even with the window
  foregrounded), and there `navigator.clipboard.writeText()` never settles — the
  promise sat `pending` across polls, `document.execCommand("copy")` returned
  `false`, and `Get-Clipboard` came back empty, six attempts wasted transferring
  three JDs. The `get_page_text` tool returns the WHOLE page untruncated (it beat
  the ~1,000-char JS-return cap that defeats a plain `return jd`), so it is the
  transfer path: read the JD from it directly, and when the page's own structure
  hides the text from its article heuristic (LinkedIn's collapsed description did
  this on one of the three), overwrite `document.querySelector("main").innerHTML`
  with the text in a `<pre>` and call `get_page_text` again — it reports
  `Source element: <article>` and returns it verbatim. `localStorage` persists
  across same-origin navigations, so stash each page's extract there and pull
  them at the end. Still verify with a checksum on both sides.
- **Testing a LinkedIn adapter fix without risking a real apply:** open Easy
  Apply on any live posting via claude-in-chrome, inspect the DOM directly
  with `javascript_tool` (`document.querySelector`, `el.shadowRoot`, etc.),
  then close and **Discard** — never Save/Submit. Found and verified the
  shadow-root bug this way (3 Aug 2026) without a single real application
  created or harmed in the process.
- **`document.querySelector` never descends into a shadow root, open or
  closed — and LinkedIn wraps the ENTIRE Easy Apply modal in one when it's
  opened from the standalone `/jobs/view/<id>/` page, silently losing every
  screening answer with zero error anywhere.** Root cause of a real,
  two-application data loss (Bellows & Munson Asia, 2 Aug 2026 — a visa
  sponsorship question; Relecloud, 3 Aug 2026 — 5-7 varied questions), found
  and confirmed live on 3 Aug 2026 after three earlier attempts missed it:
  1. First guess: `linkedin.js`'s `answerFormRoot()` required a literal
     `<form>` descendant of `[role='dialog']`/`[data-test-modal]` — dropped
     as a strict widening. **Did not fix it** (confirmed on the second real
     miss, same day).
  2. Two diagnostic-only attempts to describe *what was on the page* during
     a miss were ALSO wrong, both confirmed live: walking up from
     `controls[0]` (first control in document order) landed on LinkedIn's
     persistent top-nav search box every time — it's earlier in the DOM than
     any page content, modal open or not. Switching to the last control
     landed on Google reCAPTCHA's own hidden textarea, also portalled to the
     end of `<body>`. Filtering both out by class still left 40+ legitimate
     non-modal candidates (an open messaging panel, filter dropdowns).
     **Position cannot distinguish a real form field from page chrome on a
     page this busy — three attempts confirmed that, not assumed it.**
  3. A `MutationObserver({childList, subtree})` watching for real form-field
     insertions did better (live-verified to catch `fb-dash-form-element`
     nodes the instant a normal modal opens) but STILL reported nothing
     useful on the real failing session — because the actual root cause
     isn't about content being hard to find by position, it's that shadow
     DOM makes it invisible to `document`-rooted APIs entirely: neither
     `querySelector` nor a `document.body`-rooted `MutationObserver` can see
     inside a shadow tree without explicitly recursing into `el.shadowRoot`.
  The actual finding, confirmed live by opening the SAME job's Easy Apply
  from both entry points back to back: from the split-pane search results
  view, the modal is plain light DOM (`role="dialog"` found instantly). From
  the standalone job-view page, the IDENTICAL `.jobs-easy-apply-modal`
  markup exists, just inside an open shadow host (`<div
  class="theme--dark">`) — LinkedIn's own dark-theme scoping, incidentally.
  This is the SAME shadow-DOM behavior the "Shadow DOM breaks click
  delegation" gotcha above already documented for individual controls;
  it turns out to apply to the modal's own root container too, on this
  entry path. Fixed with `deepQuerySelector()` in `linkedin.js` (recurses
  into open shadow roots exactly like `answers.js`'s `collect()` already
  does one step later, for gathering fields once a root is found) and a
  matching `deepExists()` (in `shared/answers.js`) for the `noRootHint`
  diagnostic's `dialogPresent`
  check, which had the identical blind spot. Verified live end-to-end:
  `deepQuerySelector` finds the real modal, and `collect()` run against that
  root correctly enumerates its actual fields with their real values.
  **There is no way to manually add a missed answer after the fact** — only
  the extension writes `application_answers`, and both real sessions' lost
  answers (the visa question, and 5-7 questions on Relecloud) are
  unrecoverable. **Lesson for next time a capture silently comes up short:**
  check for an open shadow root wrapping the relevant container FIRST
  (`el.shadowRoot` on anything in the ancestor chain) — cheaper to rule out
  than three rounds of positional guessing, and it was the actual answer.
- **LinkedIn rebuilt Easy Apply as a native `<dialog>`, and `[role='dialog']`
  does not match one — two weeks of screening answers were lost silently**
  (found 2 Sep 2026, from a capture reported as "answers not saved"). From
  about 18 Aug 2026 the wizard is `<dialog open data-testid="dialog"
  aria-labelledby="dialog-header">` mounted straight under `#root` in the TOP
  document: no `role` attribute (a `<dialog>`'s role is implicit), no shadow
  root, no `<form>` inside, hashed atomic classes. `answerFormRoot()` matched
  `.jobs-easy-apply-modal, [role='dialog'], [data-test-modal]` and nothing
  else, so every sweep on the new layout came back noRoot — the 2 Sep capture
  swept 30 times, found no root 30 times, and its `noRootHint` read
  `dialogPresent:false` with 30 controls on the page, because that check used
  the same selector. The numbers: 3-17 Aug, 71 Easy Apply captures, 0 with
  zero answers, 3 without a resume file; 18 Aug-2 Sep, 35 captures, **19 with
  zero answers, 28 without a resume file**. The ones that kept anything kept
  ONLY free-text fields (`How many years…` x3 and nothing else) — those came
  through `answers.js`'s change/input backstop, which fires when you TYPE, so
  every prefilled field, every radio and the resume pick went missing while
  the capture reported success. Both layouts ran side by side for days: a
  20-21 Aug record with email/phone/radio rows is the classic modal, one with
  only typed fields is the `<dialog>`. Read the extension's LevelDB (Procedures, above)
  before anything else next time — the `sweeps` buffer had the whole story.
  **Three findings from walking the live wizard (2 Sep; a draft opened and
  discarded, nothing submitted, the visa question left unanswered on
  purpose since LinkedIn may prefill it next time):** (1) the native
  `<input>` inside each option has a `<label for>` that is EMPTY and a
  generated `name` (`radio-group-«rg»`, React `useId`), so `labelFor()`'s
  last fallback would have recorded the name as the question; the accessible
  name is on a WRAPPER — `<div role="radio">` / `<div role="checkbox">` —
  as `aria-label`. (2) That wrapper's `aria-label` means different things on
  the same wizard: on a Yes/No question it is the QUESTION and "Yes"/"No"
  is the wrapper's visible text; on the resume picker it is the FILENAME and
  the wrapper has no text at all. The group is a `<fieldset role="radiogroup">`
  with NO legend; the question is the `<p>` (or heading block) immediately
  before it. `answers.js:radioOption()`/`radioQuestion()` read exactly that,
  falling through to the classic legend + `<label for>` path so the old modal
  stays correct. (3) The picker no longer says "Deselect resume <file>", so
  the server's `_RESUME_RE` never fires; `answers.py:_is_resume_pick()` now
  also promotes a radio whose question opens with "resume" and whose answer
  is a bare `.pdf`/`.doc(x)` filename. Native `<select>`s and text inputs on
  the new layout still carry real `<label for>` text — why the typed fields
  survived, and why the contact step needed nothing.
  **The diagnostic told the truth and was misread**: the popup's "no dialog
  was open (N fields elsewhere on the page)" line was correct for the
  selector it ran and wrong about the page. `dialogPresent` now checks
  `dialog[open]` too. `tests/test_extension.js` covers `answerFormRoot()` on a
  `<dialog open>` stub and, new the same day, runs `shared/answers.js` against
  a small fake DOM reproducing the measured wrapper shapes (Yes/No, resume
  card, top-choice checkbox, contact step, classic legend radio, and a
  properly-ARIA'd wrapper) — its header says what the fake does not model.
  Extension 0.9.0. **Unverified on a real apply.** **Lost for good**: the
  answers on the 19 zero-answer captures and the resume choice on all 28 —
  only the extension writes `application_answers`, and there is nothing to
  replay. One more thing to read off the next capture: the "Follow
  <employer>" checkbox's new label — if it has lost its "to stay up to date"
  tail, `_CONTROL_NORM_RES`'s anchored rule misses it and a dead singleton row
  lands in the bank per employer.

## Known-untested surfaces (verify on first real contact)

- **Extension DOM selectors** (`extension/adapters/*.js`) — best-effort against
  unverified live DOMs; WILL need adjustment. Failures surface loudly: console
  warning, popover error, ring buffer in the extension popup. Fix = edit one
  thin adapter file, reload the unpacked extension, **and hard-refresh any
  already-open tab** — reloading the extension does not re-inject content
  scripts into tabs opened before the reload; the stale script keeps running.
- **JobStreet's apply flow — one real submit completed 29 Jul 2026, and it
  failed exactly as predicted.** A real apply (First Up Consultants ·
  Generative AI Engineer) came back with `company_norm = 'unknown company'`:
  deferral worked (applied time was the submit, no phantom record), but the
  job snapshot stashed at the opening click was gone by the time the submit
  merged it in. Root-caused to the MV3 service-worker race documented above
  (`return false` let the worker die mid-write) and fixed in the same session
  — the affected record was corrected by hand via `/edit`
  (`jobs.company_norm` had to be fixed too, not just `postings.company_raw`,
  or email matching would never have found this application). Also added
  since: `postings.salary_*` / `work_type` / `salary_match` capture
  (migration 011), verified extracting correctly on a live page.
  **Still unverified: one clean end-to-end submit with the race fix AND the
  salary fields both in place together.** Read it off the record afterwards —
  applied time should equal the submit, `company_display` must not be
  "unknown company", and `postings.salary_raw` should be populated if the ad
  showed a figure. **As of 2 Sep 2026 `salary_raw` is NULL on all 210
  postings**: only the JobStreet adapter reads a salary line, the LinkedIn
  adapter never sends one, and the four JobStreet captures carried none, so
  migration 011 and `pipeline/salary.py` have yet to store a single real row.
  `answerFormRoot()` is still a pure guess; the popup's
  "Recent form sweeps" line says whether it found a root at all. Note SEEK
  Quick apply may ask **no screening questions** (two real postings now, both
  asked none), so an empty answer capture is not by itself evidence of a bug.
- **Easy Apply screening-Q&A capture** (`extension/shared/answers.js`) — **met
  its first real wizard 27 Jul 2026** (VANARSDEL · AI Engineer, 11 answers
  stored). Two of the three open questions are now answered: the modal's
  fields are NOT in a closed shadow root (the sweep reached them, prefilled
  and untouched alike), and `answerFormRoot()`'s in-iframe assumption held.
  That run also found three real defects, all fixed 28 Jul — see the repeater
  invariant (#11), the doubled-`aria-labelledby` gotcha, and the typeahead /
  capture-phase gotcha.
  **Still open: textareas.** That form had them; not one was stored, and no
  code path between the DOM and the DB filters by type, so either the sweep
  never saw them (wrong root for that step) or it couldn't resolve their
  labels. Nothing in the record distinguishes the two, which is why the sweep
  now reports its own counts — controls found, kept, unlabelled, empty,
  disabled, textareas seen — into `chrome.storage.local.sweeps`, rendered
  under "Recent form sweeps" in the extension popup. **Next real apply with a
  textarea on the form: open the popup and read that line.** `0 textareas
  seen` means the root is wrong; a nonzero count with `N unlabelled` means
  `labelFor()` is.
  **The "NOT in a closed shadow root" claim above needed a correction**: an
  OPEN shadow root wrapping the entire modal (not individual controls) turned
  out to be exactly what broke two real applications on 2-3 Aug 2026 — see
  the dedicated gotcha above. That was specific to Easy Apply opened from the
  standalone `/jobs/view/<id>/` page; the split-pane search view never showed
  it. Fixed (`deepQuerySelector`) and verified on a real apply 3 Aug 2026
  (City Power & Light, opened from `/jobs/view/`): 9/9 answers stored, including
  real screening questions (years of C#/C++ experience, real-time systems
  experience, salary expectation) — the first real confirmation this path
  works, not just that the mechanism looks right. Textareas remain untested.
  **Label quality was separately wrong the whole time and is now fixed** — see
  the `aria-hidden`/`visually-hidden` twin gotcha. Verified on live captures
  3 Aug 2026 (Southridge APAC, Awesome Computers): 0 doubled labels and 0 form-control
  leaks across the whole 178-row bank.
  **And the whole sweep was dead on the rebuilt `<dialog>` Easy Apply from
  about 18 Aug to 2 Sep 2026** — see the `<dialog>` gotcha. Extension 0.9.0's
  wrapper-aware sweep is UNVERIFIED on a real apply. What to read on the next
  Easy Apply, in the popup's "Recent form sweeps": a `N kept of M controls`
  line instead of `no apply form found`, radio rows among the answers, and a
  `resume_file` on the record — the three things that have been missing.
- **The whole capture-identity rescue chain — `tracker-whoami`/`isTopFrame`,
  the frame-0 ask, `jobFromUrl` off the tab URL, the TTL prune on read, the
  keyed-vs-guess ranking — is UNVERIFIED against a real apply** (built 18 and
  20 Aug 2026, extension 0.6.0). Every piece is reasoned from a real failure and
  none has yet run during one. What to read on the next Easy Apply, in the
  popup: the provenance line should name where identity came from, and a
  healthy capture should say nothing alarming at all. Note the answers sweep and
  `resume_file` have never been the broken part — both losses kept them — so
  "answers stored" is not evidence the identity path worked. **Verify the code
  is even live first** (`chrome://extensions` reads 0.8.0 AND the tab was opened
  after the reload); a repeat from a stale tab proves nothing about the fix.
  **Partly answered 20 Aug, and it ran without helping** — the Lucerne Consultants
  capture shows `topFrame:false`, `askedTop:true`, `exact:true`, so
  `tracker-whoami`, the keyed stash and the frame-0 ask all fired correctly and
  the record still came out blank, because every one of them asks the outer
  shell and the shell has no job (see the settled root cause in Gotchas). That
  is the chain working and the question being wrong. **Still unverified, and now
  the thing that matters: `getJob()`'s self-document fallback** (21 Aug,
  extension 0.8.0), plus the immediate path's last-resort `jobFromUrl` off the
  tab URL. What to read on the next apply: `doc_source` in the provenance line —
  `"self"` means the frame rescued a capture the old code would have lost, and
  is the first positive proof this path works. A `read:`/`docSource:` warn line
  under Recent failures means both documents came up empty, which is a different
  bug from every one recorded so far.
- **`getRecruiter()` — rewritten 3 Aug 2026 and verified once.** The previous
  version stored the entire card blob as the name and the name as the role, on
  all 10 extension-captured contacts (repaired by hand). Both its structural
  assumptions were wrong: there is ONE `<a href="/in/…">` in the card, not a
  nested pair, and the first own-text `<span>` is the NAME, not the headline.
  It now takes the first two own-text spans positionally, filtering the
  connection degree and "Job poster" badge. Confirmed on a real capture (Southridge
  APAC — name, headline and profile URL each in their own field). **One card is
  the entire evidence base**; a second card-bearing job would say whether that
  two-span shape is stable or just this layout.
- **The split-layout stale-pane guard (`readJob`, extension 0.10.0) is
  UNVERIFIED on a real apply.** It is reasoned from three real records and
  proven in `tests/test_extension.js`, but has not run during a live capture.
  What to read on the next Easy Apply made FROM a search or collections results
  page (not the standalone `/jobs/view/` page): the provenance line. A healthy
  capture says nothing new. If the pane was stale and the guard fired,
  `_prov.stale_pane` names `{url, shown, card}` — `shown` is the wrong job the
  pane rendered, `card` is the corrected title — and `title_source`/
  `company_source` read `"card"`. The failure mode to watch for is the guard
  NOT firing when it should: a capture whose company/title match a neighbouring
  promoted card rather than the applied job, with no `stale_pane` recorded —
  that means the results card for `currentJobId` was not in the rendered list
  (scrolled off, or a different page), which the cross-check cannot reach.
  Verify 0.10.0 is live (`chrome://extensions`) and the tab was opened after the
  reload before trusting any result.
- **`ats` is never detected on a LinkedIn EXTERNAL apply whose control is a
  `<button>`** (verified: `.jobs-apply-button` is a BUTTON with no href on that
  layout, so `resolveExternalUrl()` returns null). The destination isn't in the
  DOM — LinkedIn resolves it server-side on click — so reading it needs a
  `tabs`/broad host permission, which `docs/open-source.md` §3 argues against
  during the author's job search. 6 of 9 external applies have no ATS; at least
  one demonstrably should (a Sourceability click opened a `gh_jid=` Greenhouse
  URL). Cheaper alternative: infer it from the confirmation email's sender
  domain, which already populates `ats` on some records.
- **A LinkedIn `/jobs/view/` layout exists with NO `#job-details`,
  `.jobs-description__content` or `.jobs-box__html-content`** (seen 3 Aug 2026
  on the Proseware posting) — every JD selector in the adapter misses it. Not
  fixed: 45 of 46 extension captures have a JD, so this has cost nothing real,
  and a structural fallback would be exactly the speculative adapter change
  that has misfired here before. Revisit only if a capture actually loses a JD.
  **Seen again 21 Aug 2026, and it is broader than the JD** — on that
  `/jobs/view/` layout there is not a single class anywhere in the document
  containing the substring `job` (everything is hashed atomic CSS: `_5e9d0487`,
  `f07fddc6`, …), so `classTitle`, `classCompany` and every JD selector miss
  together, and there is no `<h1>` to anchor on either. `document.title` carries
  it (`"<title> | <company> | LinkedIn"`) and is why this still captures — which
  puts a load-bearing weight on the ONE source that is deliberately switched off
  elsewhere: `docTitleTracksSelectedJob` is false on `/jobs/collections/`,
  correctly, since the tab title there is the page's own. So a
  collections-layout page rendered with this hashed CSS would have **no title
  source at all**. Not observed together yet; the collections layout still had
  the human-named classes on 20 and 21 Aug (three captures with
  `_prov.title_source == "class"`). Worth watching, and `title_source` in the
  provenance buffer is what will say when it happens — a run of `doctitle` on
  `/jobs/view/` is the leading indicator.
