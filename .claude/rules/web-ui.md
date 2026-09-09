---
paths:
  - "pipeline/web.py"
  - "pipeline/templates/**"
  - "pipeline/trace.py"
  - "pipeline/analytics.py"
  - "tests/test_web.py"
  - "tests/test_phase3.py"
---

# Web UI: design system, templates, routes, analytics

Moved here VERBATIM from CLAUDE.md on 9 Sep 2026 so it loads when Claude reads a
matching file instead of in every session (it can also be Read directly).
Dates are the key to each case; the record itself is in the database. The
tenancy invariant (#6) and the event-log invariant (#2) are still in CLAUDE.md.

## UI design system (redesigned 28 Jul 2026)

All CSS lives in one `<style>` block in `pipeline/templates/base.html` — no build step,
no framework, no separate stylesheet. Fonts: **Archivo** (variable, `wdth`
axis) + **DM Mono**, from Google Fonts.

1. **Colour means exactly one thing: the state of the wait.** Blue
   (`--accent`) = someone engaged. Amber (`--age`) = time passing, unanswered.
   Rust (`--rejected`) = closed. Green (`--offer`) = an offer. **Everything
   else is grey on purpose.** The status tokens (`--interested`, `--applied`,
   `--viewed`, …) keep their names because templates index them by status
   string, but they map onto those four roles — they are NOT four more hues.
   Adding a colour so something stands out defeats the scheme: the page is
   achromatic at rest so live threads are the only things carrying chroma.
2. **The dark palette is written TWICE** — once under
   `@media (prefers-color-scheme: dark)` and once under
   `:root[data-theme="dark"]`, in that order, because both selectors have
   equal specificity and source order is what lets a light pin win on a dark
   OS. Change one, change the other. (A missed second block is exactly how a
   contrast fix half-landed during the redesign.)
3. **Hierarchy runs on Archivo's width axis, not only size/weight.**
   `font-stretch:118%` for nameplates and numerals, 100% for prose. To give
   something more presence, go wider before going bigger.
4. **`pipeline/trace.py` owns ALL trace geometry** and is pure — no DB, no
   template knowledge. Both the list and the detail page call
   `trace.build(rows, events_by_app, now, reminder_days)`, which annotates
   rows with `pts` / `tail` / `cap` / `silent_days` and returns the shared
   axis. Every trace on a page shares ONE axis (first event on record → now);
   that is what makes rows comparable, so never scale a row to its own span.
   The list fetches every event in one `= ANY(...)` query — one query per row
   to draw one screen is the N+1 this view would die of.
5. **A row's only horizontal mark is data.** There is deliberately no baseline
   rule per trace — an earlier draft had one and it competed with the tails,
   making the amber unreadable. Week gridlines and the "today" rule are the
   only chrome.
6. **Charts are single-series by construction.** One hue for magnitude, the
   panel title names the measure, no legend. **Never a dual-axis chart** — the
   weekly panels show applications and replies as two small multiples
   precisely because they differ ~10x, each scaled to its own peak and
   labelled as such.
7. **Never print a rate on a thin sample.** `analytics.MIN_RATE_N` (5) gates
   every per-dimension `response_rate`; below it the row shows counts and an
   em dash. "0% on n=16" describes the sample, not the technology.
8. The list sorts by **submission date, newest first** by default (`_SORTS` /
   `_DEFAULT_SORT` in `web.py` — `silence` until 29 Jul 2026, `activity` until
   7 Aug 2026, `applied` since). Each replacement fixed the last one's failure.
   `silence` (longest unanswered first) sank engaged threads by construction,
   because a reply IS recent activity: on 52 real applications the single
   interview invite sat at row 36 under 34 rows of nothing happening.
   `activity` fixed that but ordered by a mix of what YOU did and what THEY
   did, so a row's position answered no single question and an arriving
   rejection pushed a dead thread to the top. Submission date is the one date
   the user controls and can predict, which is what makes the page scannable as
   the application log it is. Both older arguments are still made elsewhere and
   neither needs the list to repeat it: silence by the `/follow-ups` queue
   (rule 9, with rows and a one-click action), engagement by the trace's own
   colour — which is the entire point of an achromatic-at-rest scheme (rule 1).
   `?sort=activity` and `?sort=silence` keep the old orderings.
   Two things ride with **whatever the default currently is**, and NOT with an
   explicit sort choice (`leads_pinned` in the context): **inbound leads
   awaiting a decision** (`_LEADS_FIRST` — `origin='inbound' AND
   status='interested'`, gated on status because origin is immutable, so a lead
   you pursued must not stay pinned forever) lead the list under a `.tl-sep`
   label, since they have no applied event and every time-based sort was
   ranking them by a number that measures nothing; and a **tiebreaker**
   (`_TIEBREAK`), because a form date anchors at local noon so a day's backfill
   shares one instant to the second — 15 of those 52 rows sat in 3 tie groups
   with no defined order at all. **`_LEADS_FIRST` moves when the default
   moves** — it is a property of the default, not of any sort key, and it
   matters more under `applied` than it did under `activity`: a lead has no
   `applied` event at all, so without the pin every one of them collapses into
   the `NULLS LAST` bucket at the very BOTTOM of the page, which is a worse
   answer than the interleaving that motivated the pin originally.
   `_SORTS["applied"]` spells its ORDER BY out rather than composing
   `_TIEBREAK`, whose own first key is `applied_at` — composing them would
   repeat the sort's primary key, and a later edit to `_TIEBREAK` would
   silently re-sort the default list.
   **The default sorts on `started_at`, not `applied_at`** (11 Aug 2026): the
   submission when there is one, else the first event on record — which for an
   inbound is the date the recruiter approached, since invariant #9 forbids
   fabricating an `applied` event for one. Identical to `applied_at` on every
   row that HAS an applied event (verified against all 152 real rows: zero
   differ), so "newest submission first" is unchanged for them; it only decides
   the rows where `applied_at` is NULL, which on real data are exactly the 11
   inbound ones, and which previously fell through to `a.id` — insertion order,
   i.e. OLDEST approach first, the reverse of the rest of the page. Both halves
   were wrong: the 4 pinned leads were ordered backwards inside the pin, and the
   7 pursued ones (two of them live interview threads) sat below 140
   applications. An inbound's date is the date they approached; sorting by it is
   the same rule the rest of the list follows, not an exception to it.
9. **The follow-up queue is work; the applications table is a record — and
   since 21 Aug 2026 they are two PAGES.** The queue lives at `/follow-ups`
   (`pipeline/templates/follow_ups.html`), with a counted nav entry and an amber
   `N need follow-up` link on the list. Its half of this rule is unchanged and
   still load-bearing: it gets real rows and a one-click `follow_up_sent`
   (posting `redirect_to=/follow-ups`, so the page shortens as you clear it),
   because on real data it IS the day's task list. **Never demote it back to a
   sentence of links.**
   The staging tells the story: a plain block (Jul), then collapsed into a
   `<details>` with an `fu=1` param to survive the reload that shortened it
   (28 Jul), then out. Each step was the same complaint — the list is opened to
   READ far more often than to work, and opening it with someone else's to-do
   list above the first trace taxes every one of those visits. Collapsing
   halved the tax; a separate page removes it, and lets each page be the whole
   screen for what it is. Two things went with the move and should not come
   back: the `fu=1` param (a page of its own is open by definition) and the
   8-row cap with its "N more waiting" disclosure (that existed only to stop
   the queue burying the table underneath it — there is no table underneath it
   now). The number vindicates the split: the block was written for 18 rows and
   the queue now stands at **98**.
   `analytics.reminders()` and `reminder_count()` share one `_REMINDER_WHERE`
   for a reason — two copies would let the nav badge promise a different number
   of rows than the page it links to.
9b. **How you applied is on the row, in GREY** (21 Aug 2026): `on-platform` /
   `employer site`, read from the applied event's own `payload.external` — the
   same event the date comes from, so the two can't describe different
   submissions. Rule 1 is why it has no colour of its own: it is a property of
   the SUBMISSION, not of anyone's response to it, and chroma here is reserved
   for the state of the wait. **Three states stay three** — the template tests
   `is sameas true` / `is sameas false`, never truthiness, because NULL means
   "never recorded" and 11 real rows (every inbound lead, which has no applied
   event at all) would otherwise be labelled as on-platform applies that never
   happened. It also forced `.tl .ro` from an ellipsing block into a flex row:
   a badge appended to `text-overflow:ellipsis` is clipped away entirely on
   exactly the longest titles, silently and only where the column is tightest.
   Measured after the fix — 199 rows, 0 badges clipped, and still 0 with the
   name column forced to 560px where 182 titles truncate.
10. **The name column takes the free space; the trace is capped** (`.tl`
   grid). Verified against 47 real applications: applied in one burst, so
   every trace is the same line at the same length while agency company/role
   names were being ellipsed. The trace still shares one axis (rule 4) and
   still earns its place on the detail page — it just doesn't get 40% of a
   list row to repeat "still nothing" 47 times.
11. **The funnel strip and its legend are the status filter** (29 Aug 2026;
    `web.py`'s `status` query param, validated against `FUNNEL_ORDER`) — not
    a separate dropdown next to `sort`/`q`. A segment's own href is
    `?status=<its key>`, so there was nothing new to keep in sync with what
    the funnel already renders. The funnel is still computed from `origin`
    alone, never `status`, so every OTHER segment stays visible and clickable
    while one is selected — a filter chip row, not a redraw of itself.
    Selection reads through **opacity**, not a new hue (`.filtered .seg
    {opacity:.3}` / `.active{opacity:1}`) — rule 1 reserves chroma for the
    state of a wait, so "selected" had to be expressed a different way; the
    legend mirrors rule 8's `.tabs a.active` treatment instead (ink + bold)
    since it's text, not a color block. A `:has()` rule previews the same
    dimming on hover before a click commits to it — no JS, this app has none,
    ever. `list_url()` (a template macro, not a Python helper) builds every
    link on the page from `origin`/`q`/`sort`/`status` together — a genuine
    fix alongside the new feature, not just a refactor: the origin tabs and
    the search-clear link each had their own hand-built href before this,
    and both silently dropped `q` on click. On the query side, `status`
    matches `s.status = 'confirmation'` too when the filter is `applied` —
    the same collapse `_display()` already applies to the funnel's own
    counts, so a filter and the count that names it can't disagree about
    what one status label covers.
12. **Why it closed is a qualifier on `rejected`, never a status of its own**
    (9 Sep 2026). A visa rejection is still a closed thread, so it keeps rust
    (rule 1); the reason rides beside it as a grey badge on the list row — the
    same call as rule 9b's on-platform badge, a property of the close, not of
    the wait — and as the selected option of a small why-select on every
    rejected timeline line, whatever the event's source. A `visa_blocked` type
    was costed and refused: a CHECK entry, a precedence slot, a trace role,
    BOTH response-type lists, a funnel segment and a palette token, and it
    forks the vocabulary ("rejected for salary" would then want its own type).
    The event log was already right about the state; what was wrong was that
    40 of 51 real rejections came by email and could not carry a reason at
    all, since the edit route refuses email-sourced events on principle.
    `set_rejection_reason` is the narrow door: it writes ONLY `payload.reason`,
    so the email's own facts (type, date) stay read-only and the user's
    annotation does not.
    The funnel unfolds one level: with `rejected` selected, the legend grows a
    why-chip row (`?status=rejected&reason=<key>`, active through weight —
    rule 11's treatment), counted by `analytics.rejection_reasons` over the
    same origin tab the funnel counts and off the SAME event the row badge
    wears (the newest rejected event carrying a reason, else the newest), so a
    chip's number is the number of rows it shows — two definitions of "which
    rejected event" would let the count and the rows disagree. `unrecorded` is
    a filter value and NOT a payload value: an event carries a vocabulary key
    or carries none, and `unstated` (the employer gave no reason, and that was
    written down) is a real answer. A reason filter IS a rejected filter, so
    the route folds `?reason=` into `status=rejected`; the funnel and legend
    links drop `reason` on purpose, the tabs, search and sort carry it. The
    analytics table splits out `inbound` because on real data 5 of the 6 visa
    rejections were recruiters who approached first and then dropped the
    thread — a different fact about the market than "my applications get
    rejected for visa". Counts only: nothing here is a rate.

## Gotchas learned the hard way

- **A top-level `{% set %}` in the PARENT template shadows the child's render
  context in every block.** `base.html` set `q = queue_alert()` for the stall
  band (8 Sep 2026), and from that moment the list's search box rendered
  `value="None"`, its "Nothing matches" state never rendered, and its Clear
  link never showed — with a stalled queue it would have been worse, since
  `q` was then the health DICT and `list_url()` would have urlencoded it into
  every href on the page. No test asserted on the box's value, so it went
  unseen for a day and was found by eye. Now `stall`; keep base.html's
  variables distinct from every context key any page passes, the same rule
  the globals already follow (see the `theme` collision below).

- **An output-column alias works in `ORDER BY` only as a BARE NAME.** Wrap it in
  anything — `lower(company_display)`, `COALESCE(applied_at, x)` — and Postgres
  resolves the name against the FROM clause instead, raising `column
  "company_display" does not exist`. `?sort=company` on the list 500ed this way
  from the day it was written until 20 Aug 2026 (reported as a real traceback,
  not found by a test). Two ways out, both used in `web.py`'s list query: expose
  the whole expression as its own alias and order by that bare name
  (`started_at`), or source the value from a `LEFT JOIN LATERAL` so ORDER BY can
  compute over real FROM columns (`pc.company_raw`) — the latter also keeps one
  definition of "which posting's company do we show" for the SELECT and the sort
  to share. The trap is that the legal and illegal forms look identical, and
  every OTHER key in `_SORTS` happened to be bare names.
- Starlette `TemplateResponse` must use keyword form
  (`request=`, `name=`, `context=`).
- **FastAPI `Form(...)` (no default) 422s on an empty-but-present field
  before your route body runs** — even one you meant to validate yourself
  with a friendly message. Use `Form("")` and validate manually (confirmed
  pre-existing on `/login`, not new).
- **Dark Reader is installed in the dev Chrome profile, and when active it
  defeats colour verification — including `getComputedStyle`.** Its dynamic
  mode injects real overriding CSS, so resolved `background-color` comes back
  neutralised (`.funnel .seg`, `.legend i`, `.spark .bar` all read as the card
  colour while being correctly sized). **It is not always on, though** —
  confirmed quiet against a locally-served page the same day, where resolved
  colours matched the authored tokens exactly and tracked `data-theme` flips
  correctly. Probe first (compare `getComputedStyle` on one element against
  the `:root` token it should equal); if they match, resolved styles are
  trustworthy and WCAG contrast can be computed straight from them — that's
  what caught a real AA failure on `--withdrawn` (3.43:1) that looking never
  would have. Only fall back to computing contrast from the authored hexes in
  source when the probe shows a mismatch. `:root` custom properties, all
  geometry, and font checks DO survive either way. Two more that hold either
  way (folded in from a memory, 9 Sep 2026): `document.fonts.check(...)` plus
  resolved `font-family`/`font-stretch`; and since the OS scheme cannot be
  flipped from script, render a second copy of the page with the
  `@media (prefers-color-scheme: dark)` block stripped to see the other half of
  a two-scheme stylesheet.
- **A `TemplateResponse` context key can silently shadow a Jinja global of
  the same name.** `web.py` exposes per-request state (`theme()`, `dt`,
  `dtt`) as `templates.env.globals`/`filters`, reached via `request.state`
  in `@pass_context` functions — if any route's context dict reuses that
  name (e.g. `_settings_ctx`'s `"theme"` key shadowing the `theme()`
  global), Jinja resolves the local context first, and `{{ theme() }}`
  raises `TypeError: 'str' object is not callable`. Keep new
  global/filter names distinct from every context dict key.
  **A Jinja MACRO cannot see the render context at all**, which is why
  `DEFAULT_SORT` (used by `applications.html`'s `list_url()` to decide whether
  to omit `sort=` from an href) is a global and not a context key. Register a
  global that derives from a module constant **next to that constant**, not up
  with the others: `templates.env.globals[...]` at the top of `web.py` runs at
  import, before `_DEFAULT_SORT` exists, and raises `NameError` at module load.
- **Anything a template hardcodes about a DEFAULT will silently fight the
  default when it moves.** `list_url()` compared `sort` against a literal
  `'activity'` to decide what to omit from a URL; once the default became
  `applied`, every funnel/tab/search-clear click would have carried
  `sort=activity` and silently reset a chosen sort — the exact bug the macro
  was introduced to fix for `q`. Compare against the constant, never a literal.
- **Verifying an authenticated page via claude-in-chrome:**
  Try navigating straight to the page first — the dev profile often already
  has a live session from the user's own concurrent use of the app, which
  makes this trivial. Only when there's no active session: `document.cookie`
  silently can't overwrite an existing httponly
  session cookie, and the browser tool blocks `file://`. Fetch the rendered
  HTML with curl + a real session cookie, serve it via a local `python -m
  http.server`, then navigate/screenshot that. Mint the cookie directly via
  `pipeline.auth.create_session(conn, user_id)` in a one-off script rather
  than needing the real login password.
- **Jinja prints Python `None` as the literal string `"None"`**, not empty,
  when interpolated directly (`{{ x }}`). Bit us in
  `<input value="{{ e.extraction.company }}">` when `company` was `null` —
  rendered `value="None"`. Always `... or ''` on a value that can be `None`,
  not just a truthiness check on its container.
- **A scoped CSS selector does NOT protect you from a weaker rule setting a
  property it never mentions.** `.spark .bar` (chart bar) and `.bar` (the
  toolbar/button-row utility, `class="bar"` on forms) shared a name; the
  utility's `margin-bottom:.9rem` applied to every chart bar because the
  scoped rule only declares width/height/background. Result: every weekly bar
  floated 14px off its axis and the tallest one covered its own panel title —
  in both panels, always, not a data edge case. Specificity decides conflicts
  per-property, not per-rule. Chart element names now stay out of the utility
  namespace (`.spark .wk`). Measure geometry (`getBoundingClientRect`) when a
  chart looks subtly off; the overlap was invisible until the numbers came out.
- **A flex segment's label can overflow invisibly.** `.funnel .seg` sizes via
  `flex-grow` off a real count with only a `min-width` floor, and `.funnel`
  has `overflow:hidden` — a long single-word label (e.g. "interviewing") on a
  low-count segment silently clipped instead of wrapping. Fixed with
  `overflow-wrap:anywhere` on `.funnel .seg .l`; same pattern could bite any
  new flex-sized-by-count UI.
- **`pipeline/analytics.py`'s response-type lists are two places, not one:**
  `_RESPONSE_TYPES` (shared by `weekly()`) and `reminders()`'s separate
  `NOT EXISTS` list. A new status-driving event type has to be added to
  BOTH or it silently won't clear an application from the Needs-follow-up
  queue — the exact bug the `engaged` type's own motivating use case would
  have hit.
- **`pipeline/trace.py`'s `_ROLE` map silently defaults an unmapped event
  `type` to `"applied"` (neutral grey) rather than erroring.** `recruiter_outreach`
  fell through this way until 2 Aug 2026 — every inbound lead's trace dot
  rendered identically to a real application's, despite the "inbound" badge
  and pinned divider already distinguishing them elsewhere on the row. A new
  event type needs an explicit `_ROLE` entry (and a look at whether it's
  "you did this" — add to `_OWN` too) or it inherits the wrong color instead
  of failing loudly.

## Known-untested surfaces (verify on first real contact)

- **The redesigned palette, on a real screen.** Layout, type, spacing,
  responsive behaviour and contrast were all verified (contrast numerically —
  0 WCAG AA failures in both themes); the *rendered colour* never was,
  because Dark Reader owns the dev browser (see Gotchas). Nobody has yet
  confirmed with their own eyes that the amber/blue/rust system reads the way
  it is supposed to. Check this before putting screenshots in a README.
