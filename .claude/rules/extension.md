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
- **Recovering a form's answers when its submit was missed** (29 Sep 2026).
  Until a capture takes them, answers.js keeps them in the HIRING SYSTEM's
  sessionStorage (`__tracker_form_answers`, per tab and origin), and Chrome
  persists that at `<profile>/Session Storage/`. Its older entries sit in
  snappy-COMPRESSED `.ldb` tables, so a byte search finds nothing there; the
  extension's own storage is a different LevelDB. Copy the directory's
  `*.log` and `*.ldb`, then run
  `job-tracker-snapshots/tools/session_answers.py <copy> <origin part>` (question
  keys and counts only; `--json` writes the values, outside the repo). Its
  positive control is `__tracker_ats_tenant`, which generic.js writes on
  every SuccessFactors page: if that is found and the store is not, there
  was no store. Repair through `POST /captures` (TestClient, the bearer
  check patched for the one call) with the record's own platform id, the
  answers, `ats` and `ats_job_id`, and WITHOUT `completed`, which would
  stamp the repair's time; then `/edit` for the applied time, reading the
  edit form back and asserting it equals the record first. A later sweep on
  a page of the same origin overwrites the store, so copy it early.
  **A CLOSED tab's store is deleted, not gone** (3 Oct 2026): Chrome drops
  the tab's namespace, so `session_answers.py`, which rebuilds the final
  state, finds no origin at all. The superseded row survives in an older
  `.ldb` until a compaction merges it away. Walk EVERY version (each
  table's rows and each log batch's puts, ignoring later deletions), find
  the origin's `namespace-…` row for its map id, then that map's
  `__tracker_form_answers`. That day's quick apply was recovered this way
  from a copy taken five hours later; a compaction ran within the next
  hour and removed it.
- **Checking a rule against a LIVE page without sending anything**
  (29 Sep 2026). `generic.js`'s rules take the document and address as
  parameters, so paste them verbatim into claude-in-chrome's javascript_tool
  (stubbing only what they use from jobposting.js) and evaluate them on the
  real DOM, with a fake `loc` for an address the tab does not have. Open an
  application form by its address in a fresh tab, never through a job
  page's "Apply": on SuccessFactors that button can SEND a quick
  application (0.19.0). Type nothing and click nothing, and close the tab.

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
- **The question key threw away the characters that carried the meaning**
  (24 Sep 2026, found by that day's data audit). `norm_question` and the
  extension's `normKey` both kept `[a-z0-9]`, so "…experience with C#?" and
  "…with C++?" keyed alike — asked on ONE real form (3 Aug), stored as
  occurrence 0 and 1 of one key, i.e. a repeater that never was, and shown on
  `/answers` as one question answered "1" and "10" — and a question asked in
  Chinese keyed as the bare `c`. The rule now, identical on both sides: NFKC,
  lowercase, a `#`/`+` run glued to a LETTER spelled (`c sharp`,
  `c plus plus`; so "C Sharp" keys with "C#", while "5+ years" and "# of
  years" are unchanged), then letters, combining marks and digits of any
  script kept by Unicode category. Measured before it was written: of 888
  stored answers, 6 keys change, 1 group splits, none merge. Spelled, not
  kept, so the key's alphabet stays one the `norm#occurrence` store key and
  any future URL can carry. **Two things the obvious fix would have missed.**
  The extension's key is NOT cosmetic although the server re-derives its own:
  the occurrence counter restarts every sweep, so two questions sharing a key
  on DIFFERENT wizard steps collide at `…#0` and the later step's answer
  overwrites the earlier one before the capture is sent — reproduced in
  `tests/test_extension.js` (the old key keeps only the C# answer); the real
  form had both on one step, which is why nothing was lost that time. And a
  key rule change strands every stored row under the old key, which nothing
  but Python may recompute, so `answers.renorm()` / `cli renorm-answers`
  re-derives them — renumbering occurrence only in groups a row left or
  joined, two-phase through negative values so UNIQUE never trips — and is
  what any future change to the rule runs next. Applied to the dev DB the same
  day (snapshot `job-tracker-snapshots/2026-09-24-answer-keys.json`). The two
  implementations are held together by `tests/question_norms.json`, read by
  both `test_captures.py` and `test_extension.js`; add a case there, not to
  either suite alone. Extension 0.10.1. `tests/test_web.py` had a third,
  hand-rolled copy of the rule in a fixture — now `norm_question` itself.
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
  **Open it from the job's own `/jobs/view/<id>/` page, not from the classic
  search results** (30 Sep 2026): on the same job, the classic search pane
  opened the old textbook modal and the job's page opened the current
  `<dialog>` wizard. A probe run from search would have shown nothing wrong.
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

- **Then the wrapper went too, and every radio saved its question as its
  answer for five days** (25-29 Sep 2026, found 30 Sep from one record the
  author pointed at; extension 0.23.1).
  - **What changed:** LinkedIn dropped the `<div role="radio">` wrapper and
    put the `aria-label` on the native input itself. The input's
    `<label for>` is still empty. On a Yes/No question every input's label
    is the QUESTION, and "Yes"/"No" is a `<p>` in a sibling `<div>`. On the
    resume picker each input's label is its FILENAME, as before.
  - **Why it read wrong:** with no wrapper, `radioOption()` fell through to
    `labelFor()`, which skips the empty `<label for>` and returns the
    input's own `aria-label`. The question came from the `<p>` before the
    group. The signature is exact: the question ends in `*`, and the answer
    is the same text without it.
  - **The damage:** 33 of 33 LinkedIn radio rows from 25 Sep on, across 16
    applications, and 0 of the 114 captured before (2 Aug on). Eight were
    sponsorship or work-authorisation questions (7 applications), which
    `/analytics` then counts as "asked" rather than "needs".
  - **Seen on 25 Sep and not traced:** `answers.py`'s sponsorship rule was
    written that day to ignore "an answer that is the question's own text
    (a capture artefact)". Designing around an artefact without finding its
    cause cost four more days of answers.
  - **Why the classic search page didn't show it:** the classic job search
    still opens the textbook modal (`<fieldset>`, a real `<label for>`
    "Yes"), which reads correctly. The standalone `/jobs/view/<id>/` page
    opens the new one. LinkedIn's banner says classic search is being
    retired, so assume the new layout.
  - **Fixed as a rule, not a selector:** `radioGroup()` reads a group from
    all its members at once. A name that EVERY member carries names the
    group, so it becomes the question, and the answer is the checked
    member's own row (its largest ancestor holding no other member). Names
    that differ (filenames, and the Yes/No text of every earlier layout)
    stay the options. Two resume cards live held the same file, so a
    repeated name is not a shared one; only a name on every member is.
  - **Proven** in `tests/test_extension.js`, whose fixture reproduces the
    live markup and gives the real record's exact signature on the old
    code. **Not yet seen through a real submit.** On the next Easy Apply,
    check that the radio rows read Yes/No.
  - **Where it was measured:** a live Easy Apply opened from the job's own
    page, stepped through with Next, read with `javascript_tool`, then
    closed with **Discard** (30 Sep). The tab froze at the questions step
    (screenshots timed out), but the DOM probes still answered.

- **A captcha's hidden response field reads as a question, and its token as
  the answer** (found 24 Sep 2026, before it cost anything).
  - **How:** `labelFor()`'s last fallback names a control by its own `name`
    attribute. reCAPTCHA's `g-recaptcha-response` and hCaptcha's
    `h-captcha-response` are textareas named for code and holding a token.
  - **Why it never showed on Easy Apply:** the captcha sits outside the dialog
    the sweep reads. On an ATS it does not: Lever's hCaptcha writes its field
    INSIDE the application form. On Ashby's live page the reCAPTCHA field was
    the one control of 22 outside the form's pane, and counting it made
    `generic.js`'s root the whole `<body>`.
  - **The fix is two conditions together:**
    - `answers.js:machinery()` skips a control that is unrendered AND named
      only by its own attribute.
    - Visibility alone would re-lose every radio answer: the rebuilt Easy
      Apply hides its native radios behind labelled ARIA wrappers (the
      `<dialog>` gotcha above).
  - **Also:** the ATS root is placed by visible controls only, and the server
    drops the three captchas' response keys as a second line.
  - **The general lesson:** a control's `name` is a fallback for a label, and a
    hidden control with nothing but a name is page machinery.

- **A content-script path narrowed from OUTSIDE a sign-in was wrong, and the
  miss was silent** (24 Sep 2026, the first real SuccessFactors apply after
  0.12.0).
  - **What was narrowed, and why:** `*://*.successfactors.com/career*`, so
    the script would never run on the SAP HR systems served from the same
    domain.
  - **Where the form actually is:** after sign-in, SuccessFactors serves the
    application at `/portalcareer?career_ns=job_application…`, and after any
    postback (the register step, a Save, an upload) at
    `/portalcareer?_s.crb=…`. The second address carries no `career_ns` at
    all.
  - **The loss:** two real applications to one employer went unrecorded, and the ring
    buffers held nothing about them. The reconstruction came from Chrome's
    History database (paths and parameter NAMES only), plus the extension's
    `active_permissions` in `Secure Preferences` to prove 0.12.0 was loaded
    (it had been, 11 s before).
  - **Fixed three ways:**
    - the manifest now also covers `/portalcareer*`;
    - `generic.js` roots on a `<form>` of five or more fields that holds a
      submit-worded control, so the form is found whatever the address says
      (verified in the live signed-in form: `form#careerform`, 59 fields, and
      only "Apply" of 74 buttons, at both addresses);
    - a click on a submit-worded control the rule rejects now lands in the
      popup's failure list with the reason (`nearMiss`).
  - **The lessons:**
    - A path guessed for a page nobody has seen is a guess, whatever the
      manifest's syntax makes it look like.
    - The failing branch needs its own diagnostic — the lesson of the
      `withStashedJob` breadcrumb, again.
  - **Also read off that form:** no company anywhere on the page (no
    JobPosting, no `og:site_name`, only the URL's `company=` tenant), and an
    `<h1>` title with the requisition number appended ("… (1234)"). A capture
    from the form alone files as "unknown company". The listing has both, so
    per-site opt-in on the employer's domain (phase C) is the fix.
  - **The same miss on the HOST, 3 Oct 2026 (0.26.2):** SAP's newer data
    centres serve SuccessFactors from `sapsf.com` / `sapsf.eu`
    (`career44.sapsf.com/portalcareer?_s.crb=…`), which neither the vendor
    table nor the manifest knew, so no script ran on a classic form the
    author was half-way through. Caught BEFORE the submit only because the
    author asked. Both lists now carry it, and `tests/test_extension.js`
    loops `VENDORS` against the manifest, so a SuccessFactors host added to
    one list alone goes red. With a Save, an extension reload and a tab
    reload before the submit, the handoff still bound: one record on the
    LinkedIn posting, 60 answers, `ats_job_id` `career44.sapsf.com/<tenant>/<req>`.

- **A tab's own toolbar icon does NOT reset when the tab navigates** (Chrome
  docs: it "automatically resets when the tab is closed").
  **Corrected 28 Sep 2026, from Chromium's source, not run:** every committed
  main-frame, cross-document navigation calls `ClearAllValuesForTab`
  (`extension_action_runner.cc` `DidFinishNavigation`), which clears the
  tab's icon, badge, title and declarative icons, so a per-tab icon DOES
  reset on navigation. The declarativeContent design below stands on its own
  merits (no content-script hello, no worker wake-up per page); the reason
  given here for it was wrong.
  - **Why that rules out the obvious design:** content scripts saying hello
    and `chrome.action.setIcon({tabId})` would leave the "capturing here"
    icon on every page browsed to afterwards, unless the worker also watched
    `tabs.onUpdated`, waking on every page load in every tab and racing the
    content scripts.
  - **Built instead (extension 0.14.0, 24 Sep 2026):** the icon is a
    `declarativeContent` rule that Chrome evaluates on each navigation and
    undoes itself.
    - Its pages are the manifest's content-script patterns plus the enabled
      sites, translated by `jobposting.js:matchPatternRegex`. The test loops
      every manifest pattern through it.
    - It wants image DATA, not paths, so the worker decodes the PNGs with
      `OffscreenCanvas`.
    - The icons themselves come from `scripts/make_icons.py`.

- **A requested origin must fit inside ONE declared optional pattern, every
  scheme of it** (28 Sep 2026: "Always capture on <site>" had never worked
  once, from its first build on 24 Sep).
  - **The failure:** the popup asked for `*://host/*` (http AND https)
    while the manifest declared `https://*/*` and `http://*/*` apart.
    Chromium checks each requested pattern against each declared one
    (`URLPatternSet::ContainsPattern` → `URLPattern::Contains`, which needs
    every scheme of the request in ONE pattern), so the request was
    "unlisted" and `permissions.request` rejected with "Only permissions
    specified in the manifest may be requested", before any prompt.
  - **Why nobody saw it:** the click handler had no catch, so the rejection
    was unhandled and the popup showed nothing. The `pendingSite` note,
    written just before, was the only trace: one sat in storage from
    25 Sep, and `Secure Preferences` held no runtime grant. That pair reads
    exactly like a user dismissing the prompt, which is why the source was
    read rather than the user's click assumed.
  - **Fixed:** the manifest declares the one pattern `*://*/*`; the popup
    catches the rejection, says it, and clears the note;
    `tests/test_extension.js` checks every origin the extension requests
    against the manifest with Chromium's containment rule (red on the old
    manifest for every site).

- **An apply flow's own tail can look like the job's id, and one wrong id
  broke two links at once** (28 Sep 2026, the first LinkedIn → ATS apply
  ever to keep its answers; extension 0.21.1).
  - **What happened:** an employer on Oracle Recruiting Cloud. The handoff
    bound the new tab correctly (`via: "opener"`, `atsJobId …/2087`), and
    the submit captured all 29 answers. But the form sits at
    `…/job/2087/apply/section/1`, and `idFrom` took the LAST id-shaped
    segment, the section number: the page's id was `…/1`.
  - **What that broke:** `handoffFits` refused the binding (both sides knew
    an id and they differed, `2087 ≠ 1`); the keyed stash missed
    (`exact: false`); and the title fallback failed too, since the form's
    only `<h1>` was the section heading "Work Summary". So the submit filed
    its own record, and the LinkedIn popover answered 10 s later filed a
    second. On a multi-section form each section would also have keyed its
    answers apart (`…/1`, `…/3`).
  - **Fixed as a rule, not an Oracle entry:** `idFrom` (and
    `joburl.generic_id`) look for the id in the part of the path BEFORE the
    first `apply`/`application` segment (`jobposting.js:APPLY_SEGMENTS`,
    which `generic.js:APPLY_PATH` is now built from), and in the tail only
    when that part has none, which is where JazzHR keeps its id
    (`/apply/<id>/<slug>`). No stored posting changed id but this one.
  - **How it was found:** the provenance, handoffs and failures buffers,
    read from the LevelDB (Procedures above). The handoff had worked, which
    no guess would have said.
  - **Repaired** by `/edit` on the form's record (the job page's address),
    the ATS id set, `merge_jobs` keeping the LinkedIn record, and the
    popover's duplicate `applied` event removed. Snapshot first
    (`2026-09-28-oracle-handoff-twins.json`); `/edit` truncates the applied
    time to HH:MM, so the exact instant was restored FROM THE SNAPSHOT: a
    re-run that reads the live row after a first `/edit` restores the
    truncated value.

- **An ATS form built from web components was invisible, and its loss was
  silent** (29 Sep 2026, the second real LinkedIn → ATS apply; extension
  0.22.0).
  - **What the page is:** SuccessFactors' newer candidate experience, UI5
    web components throughout (`ui5-input-xweb-*`, `ui5-button-xweb-*`).
    `form#careerform`'s LIGHT DOM holds 26 controls, every one
    `type=hidden`; each of the 22 real fields is a `<ui5-input>` with its
    `<input>` in an OPEN shadow root; "Submit" is a `<ui5-button>` whose
    inner `<button role=button aria-label="Submit">` holds only a `<slot>`.
    Two SuccessFactors UIs are live, per tenant: the 24 Sep one (above) is
    the classic, light-DOM form.
  - **What `generic.js` saw:** zero answerable controls, so
    `applicationRoot()` returned null at its first line; the inner button's
    `textContent` is "" (the word is the host's, slotted), and the host is
    neither a BUTTON nor `role=button`. So: no sweep, no capture, AND no
    near miss, because the near-miss logger only fires for a control that
    already looks like a submit. The 13 answers that existed came from
    answers.js's change/input backstop, i.e. only the fields the candidate
    TYPED; every field prefilled from the SuccessFactors profile was lost.
    Same failure class as LinkedIn's shadow-root modal (3 Aug) and its
    `<dialog>` (2 Sep): a container the reader did not recognise, silently.
  - **Fixed as rules:** every `generic.js` query descends into open shadow
    roots (`deepAll`) and every walk up steps out to the host (`up`,
    `closestDeep`), which answers.js had done since August; a control's
    label is its text, else its slotted text, else its value, else its
    `aria-label`. Rule 2 then finds the form by its Submit on ANY address,
    including the crumb-only one the tab really had. The page prints no
    requisition, so `pageId` also reads one from a field NAMED as one
    (`<meta name="jobRequisitionId">`, hidden `career_job_req_id`).
  - **The silence, answered:** a form left holding answers that no capture
    took is now reported from the next page (`answers.js:leftover()`,
    `capture.js`), once, on hiring systems only and never with a sign-in in
    view. A form that continues is not a leftover: the classic
    SuccessFactors form postbacks and signs in mid-form with the store
    (correctly) still full, and across its postbacks the store's key, the
    requisition read off the page, stays the same, while a sign-in in view
    suppresses the report. A page BETWEEN steps that has a different key and
    no password field would still report falsely; none has been seen.
  - **How it was found:** the extension's buffers showed a bound handoff and
    then nothing; Chrome's History put the form's 15 minutes and the send;
    the tab's session storage, read off disk (Procedures), held the
    untaken store; and the rules were then run, read-only, against the
    live form (Procedures). Nothing was guessed before those four reads.
  - **The first real submit through 0.22.0 was still refused** (3 Oct 2026,
    a LinkedIn → SuccessFactors candidate experience on `career10`,
    0.26.1): "the button is outside the application form". The handoff
    bound and the sweep held 40 answers, but the root was wrong. The page's
    only file input belongs to `<ui5-file-uploader>`, which keeps it in a
    PRIVATE `<form class="ui5-file-uploader-form">` (one control) inside
    its shadow root. Rule 1 took the nearest form around a file input, and
    once `deepAll` could see that input, the nearest form was the widget's.
    So 0.22.0's shadow reading CREATED this miss on any page with the
    uploader: the 29 Sep model had "Browse" as a plain button, with no
    uploader form. Fixed as a rule (`formAround`): a form holding nothing
    but uploads is the widget's machinery, and the walk goes on to the
    next form out (`form#careerform`). The test fails on the old code with
    the real near-miss text. Repaired by the 29 Sep procedure: 40 answers
    from the tab's session storage through `POST /captures`, `ats` and
    `ats_job_id` set, the applied time untouched (no `completed`); snapshot
    `2026-10-03-sf-outside-form-repair.json`. Found in this order: the
    failures buffer (refused, then 40 answers left over), History (the
    form, then the `#/applications` landing that follows a submit), and
    the form reopened by address in a fresh tab, where the uploader's
    shadow form was counted. Nothing was clicked or typed there.

- **Greenhouse's newer job-board page gave a capture with no employer, no
  JD, no location, no country and no resume** (29 Sep 2026, the first real
  submit on `job-boards.greenhouse.io`; extension 0.23.0). The submit and
  the root worked (the 24 Sep read held); what failed was READING, five
  ways, each measured on the live page (read-only, nothing typed):
  - **The listing:** no JSON-LD and no `og:site_name`, so no company, JD or
    location from structured data. The employer is in the tab title only
    ("Job Application for <title> at <company>") and a logo's alt; the JD
    and location sit in `.job__description` / `.job__location`.
    `jobposting.js:titleEmployer` takes what follows " at " right after the
    job's OWN title (weak, and never an " at " inside the title);
    `LISTING_DOM` holds the vendor's two selectors, read only when no
    JobPosting filled the field. The canonical link says `http://` on an
    https page; `canonicalUrl` keeps https.
  - **Country, a react-select:** its `<input role="combobox">` is EMPTIED
    after each pick and the choice drawn in `.select__single-value`; with
    nothing picked, a placeholder the input names in `aria-describedby`.
    `valueOf` read `el.value` only, so the field counted as "no value"
    (the sweep's `noValue: 2` said so before the page was opened).
    `answers.js:shownChoice` reads the nearest ancestor's text, leaving out
    the input and what `aria-describedby` names, never climbing into a
    container that holds the question.
  - **Resume, a file input:** `valueOf` returned null for every file field
    (on purpose: the browser reports `C:\fakepath\…`), and the input's only
    `<label for>` is its "Attach" button's; the question, "Resume/CV*", is
    the `role="group"` around it. A file field now answers with
    `files[0].name` under its group's name, and `pipeline/answers.py`
    promotes a file field under a "Resume…" heading to `resume_file` (any
    extension; a path never).
  - Repaired by hand the same day: the company, JD and location read off
    the page and written through `/edit` (the edit form read back first,
    the applied instant restored after it); the country and the resume file
    were not guessed.

- **An ATS wizard's later step had no root, and its web-component questions
  read as nothing** (30 Sep 2026, a LinkedIn → SmartRecruiters apply checked
  live BEFORE its submit; extension 0.24.0).
  - **Step 1 worked by luck:** its resume upload is a file input, and rule 3
    roots any page that holds one and is not a listing, so its 26 answers
    were swept. Step 2 (`…/publication/<UUID>/screening`) has no `<form>`,
    no file input and no "apply" in its address: `applicationRoot()` found
    nothing, and the Submit (whose slotted label read right) was turned down
    with "no application form found on this page".
  - **Its Yes/No questions had no native control:** `<spl-radio
    role="radio" aria-checked label="Yes">`, slotted into the `<fieldset
    role="radiogroup" aria-labelledby>` inside `<spl-radio-group>`'s shadow
    root. `collect()` takes `input, select, textarea` only, so both visa
    questions (authorised to work, sponsorship) were invisible even with a
    root.
  - **Every label was slotted:** `<spl-textarea>`'s own `<label for>` holds
    `<slot name="label">` and a `*`. Read as `*`, it keys as nothing, and
    `record()` drops the row without a trace. The years combobox resolved to
    no label at all.
  - **Fixed as three rules:**
    - `generic.js:formContinues()`: a page that is not a listing, whose
      answers store holds THIS form's key marked `rooted` (a sweep found
      the root on an earlier step), continues that form. `inFlow` feeds
      rule 3 and `reviewStep`. The mark is what keeps the edit backstop's
      answers (a job-alert box typed into) from making the next "Submit"
      an application.
    - `answers.js:drawn()`: role `radio`/`checkbox`/`switch` with no native
      control inside, its state in `aria-checked`. A radio is read with its
      group, found up the FLAT tree (`assignedSlot`), and an option named
      only by the question is never its answer.
    - `answers.js:labelText()` reads the flat tree: a host's shadow root, a
      slot's assigned nodes. A slot has no box (`display: contents`), so it
      skips the rects guard.
  - **How it was found:** the rules pasted into `javascript_tool` against
    the live page (Procedures), each new rule run there before any code was
    written. The tests are red on the old code with the live signature: the
    step sweeps `[]`.
  - **Verified on the real submit:** 37 answers, the drawn radios as
    Yes/No, the slotted questions whole, `drawn: 4` on the sweep line.
  - **Once, across the upgrade:** a store written before 0.24.0 carries no
    mark, so a form in flight continues only after an earlier step is swept
    again by the new code.

- **One job, two ids on one host: the handoff refused its own job, and the
  apply was filed twice** (30 Sep 2026, the same apply; extension 0.24.1).
  - **What happened:** SmartRecruiters' listing is `/<Co>/<number>-<slug>`
    and its form `/oneclick-ui/…/publication/<UUID>`
    (`docs/career-sites.md` §4.2; §7 had predicted it). The handoff bound
    on the listing held the number, the submit sent the UUID, and
    `handoffFits` read that as another job's form. The fallback then got no
    `openerTabId` from Chrome at the submit (provenance `linked:
    "tab+title"`, `candidates: 1`), though the tab's first page had one.
    Why is NOT established. So the capture filed under the SmartRecruiters
    listing, and the LinkedIn popover, answered 15 s later, filed the
    board's twin.
  - **Fixed as a rule:** `jobposting.js:learnsAlias()`. A page on the
    binding's host, reached from a page whose id the binding knows (its
    `document.referrer`, `generic.js:arrivedFrom`), that is not itself a
    listing, is the same job under a second id; the worker adds it to the
    binding's `aliases`. `handoffFits` accepts an alias only when the
    submit page's title is the bound job's (`sameJob`).
  - **Two guards, because a wrong alias is a silent wrong merge:** a
    listing never becomes an alias (a similar job's listing is reached the
    same way), and the title must agree at the submit.
  - **Measured live before building:** the form's first page has
    `document.referrer` = the listing's full address (no referrer policy
    anywhere on the site), neither form route publishes a JobPosting, and
    its `<h1>` is the job's title.
  - **Repaired:** merged keeping the LinkedIn record, the popover's
    `applied` removed (worklog task 44).

- **A sign-in with no password field filed an application** (1 Oct 2026, a
  LinkedIn → Greenhouse job-board apply; extension 0.24.2).
  - **The page:** `my.greenhouse.io/users/sign_in?…source=quick_apply…`,
    the MyGreenhouse sign-in that Greenhouse's job boards offer for
    autofill. The manifest's `*://*.greenhouse.io/*` covers it, so
    `generic.js` runs there. It asks for an email, then an emailed security
    code. There is no `type=password` anywhere, so the one sign-in guard
    (`hasPassword`) never fired.
  - **What fired:** rule 2 of `applicationRoot()`, a `<form>` holding a
    submit-worded control and five or more answerable controls. The sweep at
    the Submit click read 8 controls, every one filled and none labelled.
    The markup was not read, since reaching that step emails a code. The
    capture filed title "MyGreenhouse", "unknown company", and one answer:
    the email typed on the first step, kept by the edit backstop. 37 s later
    the real submit on the job board made its own correct record.
  - **Fixed as a rule:** rule 2 counts only controls that ASK something
    (`generic.js:asking`), by the sweep's own `labelFor`, which answers.js
    now exposes. So the rule and the sweep's `noLabel` count cannot
    disagree, and the measurement (8 of 8 unlabelled) refuses the page
    directly. A one-time-code test (`autocomplete`, `maxlength=1` runs) was
    the other option and was not taken: the boxes' markup was never seen.
  - **The fallback:** the popup's injection loads no answers.js, so there
    every control counts as before. `tests/test_extension.js`'s
    `loadGeneric` now loads answers.js after generic.js, as the manifest
    does. The UI5 fixture's inputs got labels (modelled as `aria-label`:
    the real page's typed answers resolved through `labelFor`, but how is
    not on record).
  - **What to expect next time:** the sign-in's Submit is a near miss in the
    popup's failures ("no application form found on this page"). That line
    is the refusal working, not a lost application.
  - **How it was found:** the provenance, sweeps and handoffs buffers from
    the LevelDB (Procedures). The sign-in capture's provenance shows
    `linked: null` and `source: "doctitle"`, on a host the handoff did not
    hold.

- **A hiring system served under an employer's own domain** (2 Oct 2026,
  Eightfold; extension 0.25.0).
  - **The page, read live** (the author signed in; nothing typed or sent):
    `careers.<employer>` runs Eightfold's app, its scripts from `vscdn.net`.
    The job is `/careers/job/<pid>` and the form `/careers/apply?pid=<pid>`,
    one id for both, and the server HTML of both carries a JobPosting. The
    form is plain light DOM: one `<form>` holding the resume's hidden file
    input, "Submit application" as `type=submit`, section toggles as
    `<button type=button>`. The chosen resume shows in a COMBOBOX whose
    value is the file's name, labelled "Upload your resume". Comboboxes
    keep the picked option in the input itself, unlike Greenhouse's
    react-select.
  - **What already worked:** rule 1 roots the form, the submit words
    match, `read()` takes the JobPosting, `?pid=` was already an id
    parameter, and `vendorOf` reads `eightfold` off the scripts.
  - **What did not, all because the host is not the vendor's:**
    `claimHandoff` returned at once off a hiring system's host, so a
    LinkedIn → employer-domain apply had only the opener check at the
    submit, which filed a twin on 30 Sep. And `atsJobId()` was null, so
    no `jobs.ats_job_id`.
  - **Fixed as rules:**
    - `generic.js:hiringSystem()`: the vendor's host, or a page under any
      domain that runs a vendor's app AND holds the application form. The
      form is the test, not the vendor alone: a SuccessFactors career site
      loads the vendor's files too, and its listing number is not its
      form's id (P4). `atsJobId()` reads it.
    - `claimHandoff` runs on every page, sending the PAGE's own id. Off a
      vendor's host the page is a `site`: `pickDeparture` binds it from the
      opener only, and `handoffFits` asks for the title when the id is not
      known on both sides. It re-claims at 1.5 s and 5 s, since a form may
      render after load.
    - The worker's re-bind is now `jobposting.js:rebind()`, pure and
      tested. It keeps an id to its own host: a career site's listing
      number must not follow the tab onto its hiring system's form.
    - `pipeline/answers.py:_RESUME_HEAD` allows two words before "resume",
      and a text field's bare document name counts, so the combobox
      promotes to `resume_file`.
  - **Each rule was undone alone and its own check went red** (a mutation
    run): the old `atsJobId` gate, an id-less site binding fitting any
    title, a site binding from the tab's list, an id carried across hosts,
    and no second-job guard.
  - **Still the author's click:** a custom domain gets the scripts only once
    enabled ("Always capture on this site"), which no manifest can list.

- **The stale pane came back, and the guard could not see it** (30 Sep
  2026, found 2 Oct from a twin the author pointed at; extension 0.25.1).
  - **What happened:** three Easy Apply captures in three minutes were each
    filed as "Northwind Labs · AI Engineer", a job applied to two weeks
    earlier, under three different and correct job ids, with no JD. Each
    one's LinkedIn confirmation then found no record under its real
    employer and made its own. Each stored id's own page named the real
    job and said "Application submitted".
  - **What the log said:** every capture ran in the preload frame
    (`topFrame: false`, url `/preload/`), layout `search`, the wrong job
    already in the keyed stash at the opening click (`exact: true`), and no
    `stale_pane`.
  - **Why no guard fired, two ways, measured:**
    - on `/jobs/search-results/` no element carries a job id as an
      attribute, so the card lookup (`data-occludable-job-id`) is dead there;
    - a read of the preload frame itself has no id of its own (its address
      names none), so neither guard ran, and the read borrowed the top's id
      unchecked. That fits the log best.
    - Which of the two happened on 30 Sep is not established: `layout:
      "search"` covers `/jobs/search/` and `/jobs/search-results/` alike.
  - **Disproved on the way:** that a read with no JD element anchors the
    structural title search on the results list. Live, from `doc.body`,
    the only matching `<p>` is the selected job's top card.
  - **Fixed as rules (`linkedin.js`):**
    - the pane names its own job: its description's container is
      `JobDetails_AboutTheJob_<id>`, and its top card links to
      `/jobs/view/<id>` (measured on three live panes: three links, no other
      id in the document). Exactly one named job that is not the URL's
      drops the content and keeps the id; a box naming several decides
      nothing; a `/jobs/view/` page is never second-guessed;
    - `readJob(doc, loc, expectId)`: the preload frame's read is checked,
      by both guards, against the id it is about to borrow;
    - a read the check emptied keeps its `stale_pane` (it returned null
      before), and `getJob()` hands it on to the top's read it falls back to;
    - `answerFormRoot()` takes the Easy Apply modal in ANY frame, and a
      subframe's first `<form>` only when it holds none. Every 30 Sep capture
      (and two on 8 Sep, the first stale-pane day) stored "Filter results by:
      Date posted" / "Any time" as an answer: the preload frame is a whole
      page, and its first form can be the search's filters.
      `answers.py:_CONTROL_NORM_RES` drops `^filter results by` as the
      second line; the 5 stored rows were removed.
  - **What the fix trades:** a stale pane now saves an id-only record,
    "unknown company", visible and repairable, where it saved another job's
    name. Its LinkedIn confirmation will still make a twin.
  - **Tests:** 12 new checks; the old code gives the 30 Sep records' exact
    signature (the applied id, the other job's company and title, no JD, no
    breadcrumb). A mutation run undid each part alone, and each turned its
    own check red.
  - **Repaired:** the three records' company, title and location from each
    job's own page (`/edit`, the form read back first), the applied instant
    restored, and one moved to its real submit (14:10:04, from the
    provenance: the server was unreachable and the record was written on
    the retry 16 s later, after its own confirmation). The three
    confirmations re-filed, their duplicates deleted. Snapshots in
    `job-tracker-snapshots/2026-10-02-*`. The JDs stay empty: the hidden
    tab never loaded them.

- **A wizard whose address names its step emptied its answers at every
  "Next"** (2 Oct 2026, an employer's Phenom career site; extension 0.25.2).
  - **The flow:** Phenom's own apply on the career site's host,
    `…/apply?jobSeqNo=<job>&step=N&stepname=<name>`, six steps, the submit
    on `applicationReview`. Not a Workday handoff: `docs/career-sites.md`
    had assumed one from the survey's page state.
  - **Why:** no id rule matched (`jobSeqNo` is in no list, no path segment
    is id-shaped), so `idFrom` fell back to path plus query, and the query
    held the step. That id is `answerFormKey()`, and answers.js's
    `syncKey()` / `loadRec()` drop the store when the key changes, which is
    right between two jobs and wrong between two steps. The capture kept
    one answer, the review step's.
  - **How it was found:** provenance (`exact: false`, `linked:
    "tab+title"`, the tab on `step=6`), no `sweeps` entry for the capture,
    and the tab's session storage (`tools/session_answers.py`): one store,
    keyed `…&step=6&stepname=applicationreview`, `rooted: true`. Rooted
    means the form rule worked there, so the earlier steps were swept and
    then dropped, not missed.
  - **Fixed as a rule, not a `jobseqno` entry:** on an apply address, the
    fallback leaves out a parameter whose name says `step`, rule 2's
    reasoning applied to the query (Oracle's `…/apply/section/1`, above).
    The rest of the query stays, so a job in an unknown parameter still
    keys apart. A step parameter by another name only keeps the old
    per-step key, never merges two jobs. `jobseqno` was not added to
    `ID_PARAMS`: it would not have linked the form to its listing either,
    whose id is the path `…/job/<req>/<slug>`. **That last reason was
    wrong** (found 3 Oct 2026): the path's id IS the `jobSeqNo` value
    (`SEGMENT_IDS`' letters-and-digits rule reads the same token), so the
    two ids agree once the parameter is read. 0.26.0 reads it, as a rule
    about parameter names (the next entry).
  - **Also seen, and fixed in 0.25.3:** the store on disk was saved 100 ms
    after the capture's `take()`, because the submit click's delayed
    re-sweeps (0 and 300 ms, the typeahead fix) refilled it from the review
    page. The next full page load could then report it as a form "left
    holding answers". `answers.js:save()` now writes nothing while the page
    is still at the address a capture took the store on (`takenOn`); memory
    keeps the re-sweep for a second submit there (the server's upsert
    deletes nothing a capture did not ask), and a new address, or a new
    key, saves as before. On the old code the test reproduces the store
    found on disk.
  - **Also seen, and fixed in 0.25.4:** a Workday requisition with two
    letter groups (`_PT-JR012345`) missed `idFrom`'s Workday segment rule
    (one group of up to five letters), so the posting's id was the whole
    apply address. Letter groups may now repeat, joined by hyphens, each
    still capped at five letters (worklog task 51).

- **A question written BESIDE its field, not on it, was named by whatever
  the control carried** (2 Oct 2026, Lever; extension 0.25.5).
  - **The page:** Lever's custom questions put the words in a
    `<div class="application-label">` next to the field's own `<div>`. The
    standard fields sit inside a `<label>` and always read right; the custom
    ones have no `<label>`, `<fieldset>`, legend or role.
  - **What each read as:** a Yes/No question as its first OPTION
    (`labelFor()` reaches the input's wrapping `<label>`, "Yes"); the select
    by its `name` (`opportunityLocationId`); every text question by its
    placeholder, "Type your response", so they would have keyed together
    as occurrences of one question.
  - **Fixed as a rule:** a control nothing names takes the block just
    before its field (`precedingText`), ABOVE the placeholder (an
    instruction, not a question) and only when on screen, since a hidden
    control named by its own attribute is a captcha's field (`machinery()`).
    A radio group with no group element uses the smallest box holding all
    its options. `precedingText()` now refuses a neighbour that IS a
    control, so a row of code boxes labels at most its first box
    (`generic.js:asking()` counts labelled fields).
  - **Found before it cost anything:** the submit was held, the live form
    read in a separate tab, and the real submit then saved all 14 answers
    under their questions (worklog task 52).
  - **Don't "correct" a capture from memory.** The record's "No" answers
    were reported as wrong and rewritten, then restored: the author had
    clicked No. The answers store's history in Session Storage's `.log`
    (every version, not only the last) showed each answer recorded at the
    moment of its pick, which is what the page held.

- **Chrome forgets a tab's opener, and an enabled site's first claim can
  come too late to see it** (3 Oct 2026, a LinkedIn → Phenom apply;
  extension 0.26.0; worklog task 54).
  - **What happened:** two records. The Phenom review step's submit filed
    one with the 21 answers and no name (`linked: null, candidates: 0`).
    The LinkedIn popover filed the other 11 s later. LinkedIn's Apply HAD
    stashed the job under its tab, and the entry was never taken.
  - **Why, from three records:** the storage write order (the LevelDB log
    read in sequence, not just its final state), Chrome's History
    (`visits.opener_visit`, `from_visit`), and Chromium's source. The site
    was enabled from the popup 37 s after the click, with the form already
    open, so nothing ran when the tab's first page loaded. Every later
    claim found no opener, and no handoff was ever written.
    `TabStripModel` forgets EVERY tab's opener in the window on a non-link
    navigation anywhere (typed, bookmark, keyword), on a user-gesture
    switch to a tab that is neither this one's opener nor opened by it, and
    when a tab is opened from a link in the foreground.
    `chrome.tabs`' `openerTabId` is that same opener
    (`extension_tab_util.cc`). Which of those fired here is not known. The
    30 Sep SmartRecruiters apply had lost its opener by the submit,
    probably the same way.
  - **NOT the cause:** LinkedIn's `safety/go` interstitial. Every LinkedIn
    external apply goes through it (History `opener_visit = 0` on all of
    them), the ones that bound included. History's opener is not the tab
    strip's.
  - **Fix 1, the kept opener:** `background.js` writes each tab's opener
    down at `tabs.onCreated` (no `tabs` permission needed) and uses it once
    Chrome's is gone (`jobposting.js:keepOpener` / `openerOf`). Chrome's
    forgetting guards something real: a TYPED address starts a new task. So
    a kept opener only links on the host its job's Apply LEFT FOR, or a
    subdomain of it: the stash's `dest`, which `capture.js` resolves at the
    click (`departsTo`). Through a redirector or a career-site → ATS hop it
    links nothing, which leaves a duplicate, never a wrong merge. It shows
    as `opener-kept` in the provenance line.
    **First met live 7 Oct 2026, refusing as designed** (worklog task 63):
    two applies to one employer from one LinkedIn tab, both Apply links
    leaving for the employer's own career site, which hands over to
    SuccessFactors on another host. The first bound through Chrome's live
    opener (still there 34 s after the click); for the second it was gone,
    the kept opener's `dest` was the career site, not the ATS, and the
    submit filed beside the popover's record (`candidates: 2, linked:
    null`). /triage's "Filed twice?" band now offers that merge. A kept
    opener has not yet been seen LINKING.
  - **Fix 2, the form's id:** that alone would not have linked this apply.
    The Phenom form's id was the whole-query fallback, which the handoff
    reads as "no id", and on an enabled site a binding with no id needs
    the submit's title, which this submit did not read. `idFrom` now takes
    a parameter NAMED for the job (`JOB_PARAM`, anchored:
    `jobSeqNo`, `job_post_id`; not `jobApplicationId`, `filter_reqid`,
    `jobTitle`, `jobFamilyGroup`) with an id-shaped value, before the
    fallback only. Replayed first: of 19 stored addresses it changes 1, and
    of 662 visited job-site addresses 28, every one the job's own id.
    `tests/test_extension.js` replays the sequence. On the old code the
    fixture's Phenom ids fail and `keepOpener` does not exist.

- **An application form that also creates the candidate's account was
  refused as a sign-in** (3 Oct 2026, the first iCIMS apply; extension
  0.27.1; worklog task 58).
  - **The page:** iCIMS's candidate profile is one `form#profileForm`: the
    resume upload, 108 labelled questions, and "Password" / "Password
    (Re-enter)". Every rule of `applicationRoot` refused a container holding
    a password, so that step had no root: no sweep, and only what the edit
    backstop caught (fields TYPED into) reached the store.
  - **The signature:** First Name present and Last Name (prefilled) absent;
    every dropdown absent; the typed search text present instead.
  - **Fixed as a rule:** `generic.js:signIn`. A password marks a sign-in
    only where fewer than 15 other questions are asked: a sign-in or a
    registration asks for an identity, an application for a career.
  - **How it was found, after a wrong guess:** "no root on the whole visit"
    was disproved by the sweeps buffer (0 noRoot over 267 sweeps), but that
    buffer keeps one entry per capture, the LAST page's. What settled it:
    answers.js's own `labelFor`/`valueOf`, pasted verbatim into
    `javascript_tool` on the live profile page (read-only), read every
    field right; then the form's shape; then History's step addresses
    (paths and parameter names only). When the readers are right and the
    fields are missing, ask whether the step had a root.
- **iCIMS's dropdowns keep the choice on a hidden native `<select>`; the box
  beside it is a filter** (read live 4 Oct 2026; extension 0.27.2). The
  select is `visibility:hidden; position:fixed`, has its own `<label for>`,
  and holds only the chosen option; a drawn `<a role=combobox>` shows it; a
  search `<input role=combobox>` in a `.dropdown-container`, named only by
  aria-label "— Type to Search —" and `aria-hidden` only while closed, holds
  what was typed ("singa"). `answers.js:filterBox` treats a combobox input
  with a `<select>` within two levels as machinery, in the sweep and the
  backstop. The react-select fixture (no native select) is the control.
- **The server kept only the first 60 answers of a capture** (found 4 Oct
  2026; `answers.MAX_ITEMS`, 500 since). Set on 28 Jul against a runaway
  scrape, when only Easy Apply existed, and silent: the capture reported
  success with 60. It cut the END of every long ATS form, the screening
  step: 14 records, 24 Sep - 3 Oct, one recovered. **An application with
  exactly N answers is this class**: compare the extension's store
  (Session Storage) with the database's count when checking a long form.
- **Copy Session Storage FIRST when a capture looks short** (4 Oct 2026).
  The 3 Oct procedure (Procedures, above) recovers a superseded store from
  an older table, but Chrome compacted that LevelDB minutes after a submit,
  and 13 forms' earlier versions were gone; only the newest survived.
  `cp "<profile>/Session Storage/"*.log *.ldb MANIFEST-* CURRENT <dir>`
  costs a second, before any investigation.
- **A resume upload was never promoted while its widget named the input**
  (4 Oct 2026, `answers.py:_RESUME_WORD`). The rule wanted "resume" in the
  label's first words; widgets label the input with their own chrome ("My
  Computer (Opens new window) Upload your resume/CV…", "Upload options",
  "Choose a file or drop it here"). Every file field stored by then was a
  resume and none had reached `resume_file`. An upload is the resume when
  its label or its file's own name says resume/CV.
- **A fieldset with no legend still has a caption, and a set of checkboxes is
  ONE question** (found 7 Oct 2026 by that evening's data audit; extension
  0.27.3, worklog task 66).
  - **The shape, read live on an Ashby form:** each multiple-choice
    question is a `<fieldset>` whose first child is a `<label>` holding the
    question, its `for` naming no control; each option sits three levels
    down, an `<input>` with its own `<label for>`. No legend, no
    aria-labelledby, and the checkboxes of one question carry different
    `name`s, so neither grouping signal the reader knew (legend, shared
    name) could fire.
  - **What it stored** on the 3 Oct apply: a checkbox set as four questions
    ("United States", "Singapore", "No", "N.A. …") answered Yes or No; and
    two radio groups named by their FIRST OPTION's label, radioQuestion's
    last resort, so "Yes, I will require <employer> to sponsor my
    employment" was stored as both question and answer.
  - **The rule** (`answers.js:caption`, `boxSetOf`): a fieldset's question
    is its legend, else its first child when that child holds no control
    and labels none; read only for a fieldset holding nothing but one
    question's options (`holdsOnly`), since a section's fieldset opens with
    a heading. A checkbox set (two boxes or more) answers once, with the
    labels of the boxes ticked; a lone box keeps its own label. The edit
    backstop leaves a set's box to the sweep, as it does a radio. And the
    last resort no longer returns an option's label: a group nothing names
    is counted unlabelled, not stored under an answer.
  - **Proven** in `tests/test_extension.js` from the live markup; on the old
    code the fixture gives the real record's rows exactly. The record was
    repaired from the live form's captions (snapshot
    `2026-10-07-ashby-captioned-answers.json`).
  - **The same class, older, not repairable:** the audit found option rows
    on 7 more applications (a country list, a skills list, Full-time and
    Part-time, agree and disagree pairs): three LinkedIn Easy Apply forms,
    two Workday, one Greenhouse, one Phenom.
    Their questions were never stored. Left as they are: the author's call
    whether to delete them.
- **Darwinbox: a form of web components, a Submit that says its word twice,
  and a confirmation outside the form** (8 Oct 2026; extension 0.28.0,
  worklog task 67). The first apply on it (an energy group's tenant,
  reached from LinkedIn, the site enabled from the popup) filed nothing.
  Read from the extension's buffers, then live on the SUBMITTED
  application, whose form still opens (step buttons only; Save, Submit and
  the fields untouched), and in the portal's own scripts:
  - **Address:** listing `/ms/candidatev2/main/careers/jobDetails/<14 hex>`,
    form `/ms/candidatev2/main/applications/<same id>`; `idFrom` reads the
    id from both, Python agrees (`tests/job_urls.json`). The tenant's host
    also serves its HR system, hence the manifest's path limit. The listing
    publishes no JobPosting and its tab reads "Job Details Page"; the
    handoff's job carried the identity.
  - **The form:** one `<form>` holding a seven-step wizard AND its
    navigation; the last step's `<button type=submit>` is
    `<span class=text>Submit</span><span class=text-2>Submit</span>`, the
    second `display:none`. `label()` read textContent, "Submit Submit", no
    submit word, so rule 2 never took the form, rule 3 rooted the visible
    fields only, and nothing was a completion. Pressing that Submit, when
    the form validates, emits to a page that opens a confirmation (strings
    `commonForm.applicationModalTitle` / `submitModalDesc`, buttons
    `common.submit` / `common.cancel`) appended to `<body>`; its Submit is
    the one that sends, and it was the near miss logged. Fixed by
    `generic.js:shown()` (a control says what it shows; a `<slot>` renders
    no box, so UI5's inner buttons fall through to `slotted()` as before)
    and `confirmsApplication()` (a submit-worded control in a dialog that
    asks nothing, over a page whose root is outside it). Both presses now
    count: the form's captures, the dialog's (past the 3 s guard) moves the
    applied time to the real send with `completed`. A form that fails
    validation still captures at the first press, as every vendor's does.
  - **The fields:** `dbx-textinput`, `dbx-dropdown`, `dbx-date-picker`,
    `dbx-radio-group`, each control in the host's open shadow root and its
    `<label>` beside the host (a dropdown's beside the host's wrapper).
    `precedingText` climbs `parentElement`, which ends at the shadow root,
    so fields fell to the placeholder. `answers.js:componentName` names a
    control by its host, read as labelFor reads a control, just before the
    placeholder fallback, so nothing that resolved before changes. The
    dropdown is Choices.js-shaped: `div[role=combobox]` > hidden `<select>`
    holding the choice, the shown choice with a "Remove item" button,
    `<input type=search role=textbox aria-label="Search and Select">`, and
    `div[role=listbox]` of `div[role=option]`, each with an
    `<input type=checkbox>`. The wrapper's text named the select (now only a
    wrapper holding the control alone names it by text), every option box
    was a question (now machinery: `[role=option]`), and the search box is
    a filter box like iCIMS's (`filterBox` now takes `type=search`). The
    radios have `name=""` and `<label for="undefined_Yes">` against
    `id="_Yes"`; named by their host, both members share the question and
    `radioGroup`'s shared-name path answers with the row's text.
  - **Measured on the real form with the new rules pasted in:** every step's
    fields named by their labels (Salutation, First Name, From Date,
    Educational category, Current Country…), both Yes/No questions answered
    (a minimum-age one, withheld by `isSensitive`, and a work-pass one), 72
    machinery controls skipped on the last step. The old store had no real
    question in 1,824 entries, so the record was filed with none.
  - **A site enabled before its vendor joined the manifest** would run two
    copies of the scripts on the pages both reach, capturing every submit
    twice: `background.js:syncSites` now skips a host the manifest reaches
    (`jobposting.js:hostCovered`).
  - **Probing a live page from claude-in-chrome:** the tab was frozen
    (hidden: rAF never fired, a 100 ms timer took 1,012 ms) and a fetch of
    the extension's sources from a local server never settled (a frozen
    tab, or Chrome's local-network prompt); pasting the functions into
    `javascript_tool` works synchronously, and top-level function
    declarations persist between calls (a `const` does not).

- **A blank combobox answered with what a screen reader hears, and a blank
  field with what an earlier sweep read** (8 Oct 2026, 0.28.1-0.28.3;
  worklog task 68). Found on one SuccessFactors classic apply, then sized
  over every stored answer.
  - **The combobox:** an empty `input[role=combobox]` sends `shownChoice`
    up to three ancestors for the choice it shows (react-select's rule).
    On SuccessFactors those hold a live region ("One or more results
    available. Press Up or Down Arrow Keys…") and the dropdown's arrow, an
    icon font's Private Use Area glyph (U+E1EF); on an iCIMS select2, its
    status region beside "— Make a Selection —". 22 stored answers on 7
    applications, 3-8 Oct, every one a blank field. A live region
    (`aria-live` not `off`, `role=status|alert|log|marquee|timer`) is never
    a value, and neither is a Private Use Area character
    (`answers.js:announces`, `\p{Co}`; `answers.clean` strips the glyphs
    too). Modelled on ARIA: SuccessFactors' region was not seen in the DOM
    (the portal wanted a sign-in), only its text in the stored rows.
  - **The blank:** four repeated "Brief Job Responsibilities" were sent
    with one paragraph each, though the candidate left them blank; neither
    capture's last sweep had read them. A sweep skipped an empty field, and
    the store kept what an earlier sweep had read there, for good. Now a
    field found empty gives back the key the SAME element recorded
    (`giveBack`, a `WeakMap` reset with the form key), and blank fields are
    numbered among same-labelled ones, so the next repeat keeps its own key
    (numbering only the filled ones would have moved the second repeat
    onto the first's key and left a stale copy under its own). The
    element test is what keeps a later wizard step's blank "Phone" from
    removing an earlier step's answer.
  - **A tab title's owner can be a tenant code.** A Career Site Builder
    listing with no `hiringOrganization` and no `og:site_name`, titled
    "… Job Details | <TENANT>PRD", where `<TENANT>PRD` is its own
    `ssoCompanyId`: `siteOwner` would have filed the code as the employer,
    had the site been enabled. Such an owner is now no company.

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
  wrapper-aware sweep then worked on real applies: every LinkedIn radio row
  captured 3-24 Sep reads Yes/No, and 35 of the 50 LinkedIn captures in that
  window carry both answers and a `resume_file` (the rest include
  LinkedIn → employer-site applies, whose form is not Easy Apply). That was
  measured from the data on 30 Sep; which layout each row came from is not
  on record.
  **From 25 Sep the wrapper was gone, and every radio saved its question as
  its answer** (the gotcha after the `<dialog>` one). 0.23.1's
  `radioGroup()` is UNVERIFIED on a real submit: on the next Easy Apply,
  the radio rows on the record must read Yes/No, not the question, and
  `resume_file` must still be a bare filename.
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
  Verify 0.10.0 or later is live (`chrome://extensions`; 0.10.1 since 24 Sep
  2026 adds only the `normKey` change) and the tab was opened after the
  reload before trusting any result.
  **It did not fire on 30 Sep 2026** (the gotcha above). 0.25.1 adds the
  pane's own id as a second check and runs both on the preload frame's read;
  that is proven in tests only. On the next Easy Apply from a search page,
  a `stale_pane` with `named` (and `card: null`) is the new check firing,
  and the record then reads "unknown company" with the right id: re-file
  its confirmation onto it rather than letting the twin stand.
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
- **The popup's capture on a page with no adapter (extension 0.11.0,
  24 Sep 2026) has never run live** (`docs/career-sites.md` phase A).
  `shared/jobposting.js`'s reader has: evaluated inside a real SuccessFactors
  career page, it returned the page's title, company, location, posted date,
  vendor and full JD. The path around it has not. That path: the popup's
  first message finds no listener; `chrome.scripting.executeScript` injects
  `jobposting.js` + `generic.js` + `capture.js` into the active tab under
  activeTab; the second message lands. What to read on the first run: the
  popover appears on the page; the record's posting reads platform `other`,
  an id of the form `<host>/<token>`, and an `ats`; the provenance line says
  `via microdata` or `via jsonld`. On a platform page, the popup saying "The
  extension was reloaded since this page opened" is the guard working (a
  dead content script must not get the generic reader, or a LinkedIn job
  files as `other`), not a failure. `tests/test_extension.js` covers the
  reader and the id rule, NOT `capture.js`, whose new `explicit` path (the
  popup's "applied" writes at once, even for an employer site) is reasoned,
  not tested.
- **Phase B (extension 0.12.0, 24 Sep 2026): capture on an ATS form's
  submit, and the link to the job board record that opened the tab — never
  run live through the extension.**
  - **Verified:** the rules (`generic.js`: root and submit) were run inside
    the live apply pages of Ashby, Workable and Greenhouse. Each picked the
    right root and exactly one submit button. Lever was read as fetched HTML.
  - **Not verified, in order of doubt:**
    1. ~~Whether LinkedIn's external button sets `openerTabId` on the tab it
       opens~~ — **it does** (28 Sep 2026: the handoff bound `via: "opener"`
       on a real LinkedIn → Oracle apply; the gotcha above).
    2. Workday, whose form sits behind a candidate sign-in and was never
       seen. The wizard rule — a Workday step with no file input is still the
       application, by its `/apply` address — is reasoned. SuccessFactors WAS
       read, signed in (the gotcha above): the rule finds its form and
       submit, and a real submit through the extension is still to come.
    3. The receipt handoff to the board's tab (needs host permission for the
       board; LinkedIn has it).
  - **What to read on the next external apply, in the popup:**
    - The provenance line should say "completed the job board's record".
    - A warn line with candidates but no link means the titles disagreed and
      a second record was filed; check for a duplicate.
    - Neither, with the applied record carrying no answers, means `linked`
      never ran: the submit was not detected. Check that `chrome://extensions`
      reads 0.12.0 and that the tab was opened after the reload.
  - **Not covered by `tests/test_extension.js`:** `capture.js` and
    `background.js` — the weak-field merge, the link, the stash — are
    reasoned, not tested.
- **Phase C (extension 0.13.0, 24 Sep 2026): enabling an employer's own
  career domain, and filing its ATS submit onto the listing the tab showed —
  never run live.**
  - **What cannot be automated:** enabling happens in the extension's popup,
    a chrome-extension:// page claude-in-chrome may not touch. The first run
    is the user's.
  - **What to check after enabling:**
    - `chrome://extensions` shows the site under "Site access".
    - The LevelDB (Procedures, above) has `enabledSites` and an
      `externalJobs` entry for the tab, holding the listing's job.
  - **After the application is sent:** the provenance line should read
    "filed onto the listing this tab showed first", and the record should
    carry the listing's `<host>/<token>` id, company and JD, with the form's
    answers.
  - **Enabling never got as far as a prompt until 0.18.1** (the gotcha
    above: Chrome refused every request). So none of what follows has run.
  - **Untested assumptions, in order of doubt:**
    1. That the permission prompt's closing the popup is really handled by
       `permissions.onAdded` (the `pendingSite` note).
    2. That `registerContentScripts` registrations survive an unpacked
       reload. `syncSites()` on `onInstalled` re-derives them either way.
    3. That `tracker-ping` keeps a page from being injected twice.
  - **Covered by tests:** `pickListed` and `siteOf` are pure and in
    `tests/test_extension.js`. The popup, worker and registration code are
    not.
- **The receipt's "Company" question (0.15.0, 24 Sep 2026) has never been
  seen.** What it replaces was seen: a live SuccessFactors submit (23:44)
  was captured perfectly — 60 answers, one withheld, the real submit time —
  and filed as "unknown company", because the form names no employer and
  the site had not been enabled. It was repaired by hand through `/edit`.
  - **What should happen next time on such a form:**
    - the receipt stays open (no countdown) with a Company field,
      pre-filled from SuccessFactors' `?company=` tenant or the career site
      the tab came from, and naming that site to turn on;
    - Save fills the record through `/captures/{id}/tag`, which only ever
      replaces the "unknown company" placeholder.
  - **The title arrives without the "(1234)" suffix:** `stripRequisition`
    removes it only when it equals the address's own requisition id.
  - **Untested:** whether `document.referrer` still names the career site
    after SuccessFactors' sign-in step. It probably does not; then only the
    tenant can suggest, and after a postback not even that.
- **The toolbar icon's two states (0.14.0) have never been seen in a real
  toolbar.** The rule's regexes are tested; the rule itself, and the PNG
  decode in the worker, are not.
  - **Check after reloading:** grey with a hollow dot on any ordinary page,
    blue with an amber dot on LinkedIn, on an ATS form, and on an enabled
    career site. Back to grey on navigating away.
  - **If it stays grey everywhere,** `syncIconRule` threw; it is caught so
    capture never suffers. The service worker's console (chrome://extensions
    → "service worker") names the error.
  - **The third state (0.20.0, 28 Sep 2026), grey with a FILLED dot on a job
    page that is not captured, rests on reading Chromium, not on a run:**
    `PageStateMatcher({css: ['[itemtype$="JobPosting"]']})` at rule priority
    100 against the capturing rule's 200 (`background.js:LISTING_CSS`). Check
    on an employer's Career Site Builder listing (microdata): filled dot
    while the site is off, blue once it is on. A JSON-LD-only job page stays
    hollow, by design (CSS conditions see only displayed elements). If an
    enabled site's listing shows the filled dot, the priorities did not
    order the two rules the way the source reads.
- **A wizard's review-step "Submit" (0.15.1, 28 Sep 2026): SEEN LIVE since**
  (checked 4 Oct 2026): 11 Workday applies, 28 Sep - 3 Oct, were captured
  on the Workday page with their answers, and a Workday wizard's only
  Submit is its review step's. What follows is the item as written before
  that: **never seen on a real review page.** The rule is deduced from the logged reason
  of the 25 Sep Workday miss (`generic.js` finds no root on an apply address
  only with fewer than two controls, or a password in the container), and
  the application it cost reached the tracker only through LinkedIn's
  popover, answerless. What to read on the next Workday apply:
  - the record carries the earlier steps' answers and `ats` workday, and the
    popup's failure list has no `"Submit" was not captured` line;
  - if that line reads "the button is outside the application form", the
    review page held two or more VISIBLE controls (a consent checkbox), the
    root rule found them, and the footer's Submit sits outside it. That shape
    was not built for, on purpose: nothing has shown it yet.
- **An ATS page's id read off the page (0.16.0, 28 Sep 2026,
  `jobposting.js:pageId`) has run only against the tests' fake pages.** On a
  real SuccessFactors form it was measured by eye, not run: the `<h1>` and
  tab title end "(51234)" on three tenants. What to read on the next
  SuccessFactors apply: the posting id is `career{N}.successfactors.{com,eu}/<requisition>`,
  the title carries no "(N)", the stored url has no `_s.crb`, and after a
  sign-in mid-form the answers typed before it are still in the record.
  Manual entry has no page, so a pasted `_s.crb` address still gives the
  crumb id: paste the listing's address instead.
- **The `ats_job_id` a capture sends (0.17.0, 28 Sep 2026) travels through
  `capture.js`, which no test covers.** `generic.js:atsJobId()` is tested;
  its read in `proceed()` (the submit's own tick, before `withStashedJob`
  can replace the identity) and the two `buildPayload` call sites are not.
  What to read on the next ATS submit: the record's job has `ats_job_id`
  set (`SELECT ats_job_id FROM jobs …`) to the form's own id, also when the
  capture completed a job board's record, whose posting id stays the
  board's.
- **The handoff (0.18.0, 28 Sep 2026) has run only against tests.** Its
  rules (`jobposting.js:pickDeparture`, `handoffFits`, `tenantOf`,
  `atsHandoff`) are tested; the worker's `handoffs` store, `claimHandoff`,
  and the `tracker-claim-handoff` / `tracker-take-external` plumbing are
  not. What to read:
  - after the first hiring-system page loads: the LevelDB (Procedures,
    above) holds `handoffs[<tab id>]` with the listing's job, `via`
    "opener" or "tab", and the host; a later page with the job's id fills
    `atsJobId`;
  - on the submit: the provenance line reads "matched by the handoff from
    the listing" (or "from the tab that opened this one"), and the record
    carries the listing's identity and the form's answers;
  - the job board path (LinkedIn's external apply) binds through
    `openerTabId`, which Chrome DOES set there: seen live once (28 Sep 2026,
    `via: "opener"` with the right `atsJobId`), when the submit's own id was
    what failed (the gotcha above, fixed in 0.21.1). The submit took the
    binding on 1 Oct 2026, on a Greenhouse job board: provenance
    `linked: "opener+handoff"`, one record with the board's identity, the
    form's 8 answers and `ats_job_id`;
  - an employer-branded listing binds only once its site is enabled, and
    enabling has not yet completed once (docs/career-sites.md §16.1, P3);
  - SuccessFactors ids now carry the tenant (`<host>/<tenant>/<id>`): one
    without it on a SuccessFactors host means the visit's earlier pages
    never named it, i.e. the tab's `sessionStorage` held no
    `__tracker_ats_tenant`.
- **SuccessFactors quick apply (0.19.0, 28 Sep 2026) rests on ONE sighting.**
  The landing `isRedirectToAppSent=true` was seen once (Relecloud, 25 Sep:
  job page "Apply" at 10:31:27, landing at 10:31:30, the confirmation email
  the same minute); `jobposting.js:QUICK_APPLY_SENT` is that one address.
  The note and the landing are tested; the click listener that writes the
  note and `capture.js`'s landing `proceed` are not. What to read on the
  next job-page "Apply" on SuccessFactors:
  - no `"Apply" was not captured` line in the popup's failures any more;
  - a record at the landing, `completed`, with no answers, the job page's
    title without its "(N)", and `jobs.ats_job_id` = `<host>/<tenant>/<N>`;
  - if the landing carries NO `isRedirectToAppSent=true` (another tenant,
    another flow), nothing files and nothing says so: the note simply
    expires after ten minutes. The confirmation email still makes the
    record, and matches it by the requisition it prints (the email
    lookup), so the loss would be the answers-free capture only.
- **Completing a thin record, and a tenant's name (P4, 0.21.0, 28 Sep 2026)
  have run only against tests.** The server routes are tested
  (`tests/test_captures.py`); the popup's "Attach this page to an
  application…", `tracker-recent-records` / `tracker-attach-listing` and the
  receipt taking the server's `company_suggestion` are not. What to read:
  - on an employer's listing, the popup's attach lists the last 30 days'
    records, thin ones first and marked "no job description" / "no
    employer"; the pick says "Attached to …" and the record's page shows a
    second posting with the listing's JD;
  - "Capture this job as applied" on a Career Site Builder listing whose
    number is its requisition joins the thin SuccessFactors record (the
    candidate id) instead of filing a second one;
  - the next nameless SuccessFactors submit on a tenant already named
    opens its receipt with that name filled in.
- **Web-component forms (0.22.0, 29 Sep 2026) have run only against the
  tests' fake shadow DOM and, as rules, against the live form's DOM** (the
  rules evaluated read-only in the page; the extension itself has not run
  the new code on a real apply). Neither `capture.js`'s path from an inner
  shadow `<button>` to the capture nor the leftover report is tested. What
  to read on the next candidate-experience apply:
  - a capture at the Submit, `completed`, carrying the PREFILLED fields
    (last name, email) as well as the typed ones: the sweep now reaches
    the UI5 inputs, and their labels resolve through answers.js's
    `labelFor` the way the backstop's did (the typed answers were labelled
    right, so the path is the same, but a prefilled field has never been
    swept);
  - `jobs.ats_job_id` = `career4.successfactors.com/<tenant>/<requisition>`
    and no `_s.crb` in the answers' key;
  - no "A form was left holding N answers" line in the popup's failures.
    That line appearing after a submit means the Submit was STILL not
    recognised (read the page's button shape again); appearing after a
    form left unsent is the report working. It did appear, on 3 Oct 2026,
    and it was the uploader's private form (above); 0.26.1's `formAround`
    has met that page only as a test and as DOM counted by hand.
  - Closed shadow roots stay unreadable to every rule here. answers.js's
    comment says its edit backstop reaches them through
    `composedPath()[0]`; that has never been checked on a real closed root.
    A vendor that closes its components would show as a form with no root
    and, at most, typed answers.
- **Greenhouse job-board reading (0.23.0, 29 Sep 2026) was measured on the
  live page with an EMPTY form**: nothing was typed or picked there, so
  react-select's chosen-value element (`.select__single-value`) and a
  chosen file are react-select's and the platform's documented behaviour,
  modelled in the tests, not seen. What to read on the next Greenhouse
  job-board submit: the record names its employer and carries the JD and
  location; its answers include the country picked; `resume_file` is the
  file uploaded, with no "Resume/CV*" row among the answers. A combobox
  answer that reads as a label or a placeholder means `shownChoice` climbed
  into the wrong container.
- **Continuing a form, drawn controls and flat-tree labels (0.24.0, 30 Sep
  2026) have met ONE real submit**, a SmartRecruiters screening step, where
  drawn radios, slotted labels and the continued form all read right. Drawn
  checkboxes and switches are modelled in tests only. Flat-tree reading
  changes every label a sweep reads: a label holding a shadow host now reads
  what the host renders, not its light children. No such label had been
  seen, and the suite's labels read as before. What to read on the next ATS
  wizard whose later step has no file input: the sweep line's `drawn`
  count, and that step's answers on the record.
- **A hiring system under an employer's domain (0.25.0, 2 Oct 2026) has
  run only against tests**, with a fixture built from the live Eightfold
  form; enabling a site has never completed live either. What to read on
  the first apply there, after "Always capture on <site>":
  - the LevelDB's `handoffs[<tab>]` holds `site: true`, the job board's
    job, and `atsJobId` `<host>/<pid>` from the job's own page;
  - the provenance line says `…+handoff`, and one record carries the job
    board's identity, the form's answers and `ats_job_id` `<host>/<pid>`;
  - `resume_file` is the combobox's file name, with no "Upload your
    resume" row among the answers;
  - a picked option in a combobox (the visa question) reads as the option,
    since only an EMPTY one was seen.
- **A step-free key across a wizard (0.25.2, 2 Oct 2026) has run only
  against tests**, whose addresses are the live flow's. What to read on the
  next Phenom apply: the record carries every step's answers, and the tab's
  answers store, if read before the submit, is keyed without `step`. Phenom
  steps may be in-page (pushState) or full loads; the test covers the full
  load (`loadRec`), and the in-page path goes through the same `formKey()`
  in `syncKey()`.
- **The handoff's learned second id (0.24.1, 30 Sep 2026) has run only
  against tests.** Its inputs were measured live (the referrer, no
  JobPosting on the form); `background.js`'s `claimHandoff` wiring is not
  tested. What to read on the next LinkedIn → SmartRecruiters apply: the
  LevelDB's `handoffs[<tab>]` holds `aliases: ["<host>/<UUID>"]`, the
  provenance line says `…+handoff`, and there is one record, not two, even
  with the popover answered. No `aliases` means the form's page load
  carried no referrer or read as a listing.

- **0.27.1's sign-in size test and 0.27.2's filter box have met the live
  iCIMS profile only as code evaluated in it (read-only) and as tests**
  (4 Oct 2026). On the next iCIMS apply: Last Name, Country, School and
  Degree on the record as chosen, no "— Type to Search —" rows, and an
  answer count above 60 if the form is long. 0.27.0's receipt line for a
  listing that asks for the CV by email has never been seen either.

- **0.27.3's captioned fieldsets and checkbox sets have met the live Ashby
  form only as markup read from it (read-only) and as tests** (7 Oct 2026).
  On the next Ashby apply, or any form with a multiple-choice question of
  checkboxes: one row per question, the question being the caption and the
  answer the options ticked; no row whose question is "Yes", "No", a
  country or another option. A legendless fieldset on LinkedIn or Workday
  that opens with a section heading must NOT become one row: their
  fieldsets were not measured.

- **0.28.0's Darwinbox rules have met the live form only as code pasted into
  a submitted application's form, and as tests** (8 Oct 2026). On the next
  Darwinbox apply (reload the extension first; the enabled tenant is now
  the manifest's, and `syncSites` drops its registered copy): ONE record,
  bound to the job board's listing when reached from one; the receipt at
  the form's Submit, and the applied time moved to the confirmation's
  Submit when that dialog carries `role="dialog"`/`aria-modal` (its
  markup was never seen: pressing it would have re-sent); answers named
  by their labels, a dropdown by its choice, each Yes/No question once,
  no row named "Enter Here", "Select Date", "Search and Select" or a
  country. Still open: a form whose step 1 shows no file input (a resume
  already attached) roots nothing before the last step, since
  `/applications/` is no apply-flow segment and `applications` cannot join
  `APPLY_SEGMENTS` without breaking `idFrom`; and what `read()` takes for
  the title of a Darwinbox apply NOT reached from a job board, whose
  listing publishes no JobPosting and whose tab reads "Job Details Page"
  (unmeasured).

- **0.28.1-0.28.3 have run only as tests** (8 Oct 2026). On the next
  SuccessFactors classic form, or any form with a type-ahead dropdown left
  blank: no answer reading "results available", "arrow keys" or a lone
  invisible glyph (`SELECT count(*) FROM application_answers WHERE answer ~
  '[\uE000-\uF8FF]' OR answer ~* 'arrow keys'` stays 0). If one appears,
  the region is marked some other way: read its element live. On a form
  where a field is filled by the page and then emptied (a résumé parse, a
  section reset): the record carries no answer for it. If it does, the
  page REMOVED the field rather than emptying it, which no sweep can see.
  And on a Career Site Builder listing titled with its tenant, once its
  site is enabled: the receipt asks for the company instead of filing the
  code.

- **0.29.1 puts Oracle Recruiting Cloud on the static list, untested live**
  (9 Oct 2026, worklog task 82). It was in `VENDORS` since phase A and
  captured once (28 Sep, through LinkedIn's handoff), but the manifest
  never listed it, so a tenant ran the scripts only once enabled from the
  popup: `*://*.oraclecloud.com/hcmUI/CandidateExperience/*`, the path
  every stored listing and form step sits under, which leaves out the
  tenant's own HR pages (`/hcmUI/faces/…`). A tenant enabled earlier is
  dropped by `syncSites` (`hostCovered`). On the next Oracle apply, with
  the tab opened after the reload: the toolbar icon is blue on the listing
  and every section, and ONE record carries the listing's
  `<tenant host>/<requisition>` id, the form's answers and `ats` oracle.
- **0.29.0's Taleo support has captured only RE-OPENED applications**
  (8 Oct 2026, worklog task 74). Both real applies on Oracle Taleo's
  classic career section were missed at the submit (the first before the
  support existed, the second probably before the extension was reloaded);
  each was filed afterwards by re-opening the submitted application from
  "My submissions" and pressing through its steps, which captured with the
  listing's stash (JD, the listing's `<tenant>.taleo.net/<job number>` id)
  when the listing had been visited first. What to read on the next
  Taleo apply, opened from its listing: ONE record at the final submit,
  the job number as its id, the title without "(Job Number: …)", the
  listing's JD and the form's answers, and its confirmation (which names
  neither title nor number) matched by company. A flow entered without
  the listing (a direct link to `flow.jsf`) reads its title and number
  from the "Applying for:" line and has no JD.
