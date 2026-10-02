/* Extension adapter tests — the first tests this codebase has had for
 * `extension/`, added 21 Aug 2026 alongside the getJob() document-selection fix.
 *
 * Why here and not next to the Python suites' concerns: everything under
 * extension/ has been shipped on `node --check` alone, which catches a typo and
 * nothing else. The identity path in particular has now been misdiagnosed twice
 * on plausible reasoning (an unreachable top frame, twice mitigated the wrong
 * way — see .claude/rules/extension.md), and the fix for it is *still* unverifiable against a
 * real apply without waiting for one to happen and go wrong.
 *
 * The fix is testable, though, because `readJob(doc, loc)` takes the document it
 * reads as a parameter. That is the whole reason this file can exist: the four
 * frame geometries below are just four pairs of stub documents, and the one that
 * matters — the job living in a subframe while window.top holds an empty shell —
 * needs no browser at all to reproduce.
 *
 * Deliberately NOT covered: shared/capture.js. It is an IIFE that installs
 * listeners and calls chrome.runtime.* the moment it loads, so harnessing it
 * means faking the whole extension API, which is a bigger job than the bug it
 * would have caught. The adapter is where the layout knowledge lives.
 *
 * shared/answers.js IS covered since 2 Sep 2026, against a small fake DOM
 * (bottom of this file). It touches only the DOM and sessionStorage at load,
 * so the fake is ~60 lines: attribute selectors, closest(), label/aria
 * lookups, no combinators — answers.js uses none. The fixtures reproduce the
 * control shapes MEASURED on the rebuilt Easy Apply the day it was found to
 * have been losing every prefilled answer for two weeks; a fixture that does
 * not match a real page is worth less than none, so add one only from a live
 * read.
 *
 *   node tests/test_extension.js
 */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "extension/adapters/linkedin.js"), "utf8");

/* ------------------------------------------------------------ tiny fake DOM */

// One element. `q()` reads textContent, the JD read uses innerText — the real
// adapter distinguishes them (innerText needs layout, which is exactly how a
// frozen tab returns "" for a JD that is present), so keep both fields even
// though these stubs set them the same.
function el(text, children) {
  return {
    tagName: "DIV",
    textContent: text,
    innerText: text,
    children: children || [],
    parentElement: null,
    querySelectorAll: () => [],
  };
}

function span(text) {
  const e = el(text);
  e.tagName = "SPAN";
  return e;
}

/* A document is a map from EXACT selector string to text. Exact-match is the
 * point: it pins the selector strings the adapter ships, so renaming one
 * without updating the layout knowledge fails here rather than in the field. */
function makeDoc({ title = "", sel = {} } = {}) {
  const nodes = {};
  for (const [k, v] of Object.entries(sel)) nodes[k] = v && v.tagName ? v : el(v);
  const body = el("");
  return {
    title,
    body,
    // A selector LIST is tried part by part, each part still exact-match —
    // answerFormRoot() hands deepQuerySelector one comma-joined string.
    querySelector: (s) => s.split(",").map((x) => nodes[x.trim()]).find(Boolean) || null,
    querySelectorAll: () => [],
  };
}

function makeLoc(href) {
  const u = new URL(href);
  return { href, search: u.search, pathname: u.pathname, hostname: u.hostname };
}

/* Load the adapter into a fresh context wired to a given frame geometry.
 * `top === own` models the top frame; two different objects model a subframe. */
function loadAdapter({ ownDoc, ownLoc, topDoc, topLoc }) {
  const isTop = topDoc === undefined;
  const sandbox = { URL, URLSearchParams, console };
  sandbox.document = ownDoc;
  sandbox.location = ownLoc;
  sandbox.window = { document: ownDoc, location: ownLoc };
  sandbox.window.top = isTop
    ? sandbox.window
    : { document: topDoc, location: topLoc };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return sandbox;
}

/* ------------------------------------------------------------ the fixtures */

const JOB_ID = "4419563851";
const COLLECTIONS = `https://www.linkedin.com/jobs/collections/recommended/?currentJobId=${JOB_ID}`;
// The frame LinkedIn boots whole pages into. Its own URL names no job at all,
// which is why a self-read has to borrow the id from the top.
const PRELOAD = "https://www.linkedin.com/preload/?_bprMode=vanilla";

const jobPage = () =>
  makeDoc({
    title: "Top job picks for you | LinkedIn",
    sel: {
      ".job-details-jobs-unified-top-card__job-title":
        "Backend / Systems Agentic Engineer (SG)",
      ".job-details-jobs-unified-top-card__company-name a": "Woodgrove Finance",
      "#job-details": "About the job\nWoodgrove is on an audacious mission...",
      ".job-details-jobs-unified-top-card__tertiary-description-container":
        el("", [
          el("", [
            span("Singapore, Singapore"),
            span("·"),
            span("Reposted 2 weeks ago"),
          ]),
        ]),
    },
  });

// The outer document while the real page renders inside the preload iframe:
// LinkedIn's own nav chrome, no job card, no JD. The URL still names the job,
// which is the ONLY thing the old code could get out of it.
const shellPage = () => makeDoc({ title: "Top job picks for you | LinkedIn" });

// Easy Apply's modal iframe: the wizard's fields and nothing identifying.
const modalPage = () => makeDoc({ title: "" });

/* ------------------------------------------------------------------ runner */

let pass = 0;
let fail = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) pass++;
  else fail++;
  console.log(`  ${ok ? "ok  " : "FAIL"} ${name}`);
  if (!ok) console.log(`       want ${JSON.stringify(want)}\n       got  ${JSON.stringify(got)}`);
}

/* ---------------------------------------------------------------- the tests */

console.log("getJob(): which document the job is read from");
{
  // 1. Top frame, job present. The ordinary case, and the one every other
  //    branch must not disturb.
  const s = loadAdapter({ ownDoc: jobPage(), ownLoc: makeLoc(COLLECTIONS) });
  const j = s.window.__trackerAdapter.getJob();
  check("top frame: title", j.title, "Backend / Systems Agentic Engineer (SG)");
  check("top frame: company", j.company, "Woodgrove Finance");
  check("top frame: id", j.platform_job_id, JOB_ID);
  check("top frame: doc_source", j._prov.doc_source, "top");
  check("top frame: layout", j._prov.layout, "collections");
  check("top frame: location off tertiary line", j.location, "Singapore, Singapore");
  check("top frame: reposted", j.reposted, true);
  check("top frame: posted_label strips 'Reposted '", j.posted_label, "2 weeks ago");
}
{
  // 2. THE REGRESSION THIS FILE EXISTS FOR. The page renders inside the
  //    full-viewport preload iframe; window.top is the leftover shell. Reading
  //    the top — which is what the adapter did until 21 Aug — yields an id and
  //    nothing to name it with, and the capture is discarded as unidentifiable.
  const s = loadAdapter({
    ownDoc: jobPage(),
    ownLoc: makeLoc(PRELOAD),
    topDoc: shellPage(),
    topLoc: makeLoc(COLLECTIONS),
  });
  const j = s.window.__trackerAdapter.getJob();
  check("preload frame: title comes from this frame", j.title,
        "Backend / Systems Agentic Engineer (SG)");
  check("preload frame: company comes from this frame", j.company, "Woodgrove Finance");
  check("preload frame: doc_source", j._prov.doc_source, "self");
  // The id/url pair is the sharp edge: a self-read's url falls back to this
  // frame's own href, which is TRUTHY and wrong, so a plain gaps-only merge
  // keeps the preload url and drops the top's real id. Both must move together.
  check("preload frame: id borrowed from the top", j.platform_job_id, JOB_ID);
  check("preload frame: url is canonical, NOT the preload href", j.url,
        `https://www.linkedin.com/jobs/view/${JOB_ID}/`);
  check("preload frame: layout reported as the tab's, not the frame's",
        j._prov.layout, "collections");
}
{
  // 3. Easy Apply's modal iframe. The reason the adapter walks UP in the first
  //    place, and the case the fix must not break: the modal's own document has
  //    no job card, so the top has to win.
  const s = loadAdapter({
    ownDoc: modalPage(),
    ownLoc: makeLoc(PRELOAD),
    topDoc: jobPage(),
    topLoc: makeLoc(COLLECTIONS),
  });
  const j = s.window.__trackerAdapter.getJob();
  check("modal iframe: title still comes from the top", j.title,
        "Backend / Systems Agentic Engineer (SG)");
  check("modal iframe: doc_source stays 'top'", j._prov.doc_source, "top");
  check("modal iframe: url canonical", j.url,
        `https://www.linkedin.com/jobs/view/${JOB_ID}/`);
}
{
  // 4. Neither document has anything and the URL names no job — getJob() must
  //    return null so capture()'s guard fires, rather than a hollow object that
  //    reads as success.
  const s = loadAdapter({
    ownDoc: modalPage(),
    ownLoc: makeLoc(PRELOAD),
    topDoc: shellPage(),
    topLoc: makeLoc("https://www.linkedin.com/feed/"),
  });
  check("nothing anywhere: getJob() is null", s.window.__trackerAdapter.getJob(), null);
}
{
  // 5. An id with no title is NOT a usable read — that is the bar capture.js
  //    applies before it will save, and the bar getJob() uses to decide whether
  //    to fall through to this frame. If they ever disagree, a blind record
  //    saves again.
  const s = loadAdapter({ ownDoc: shellPage(), ownLoc: makeLoc(COLLECTIONS) });
  const j = s.window.__trackerAdapter.getJob();
  check("id-only read still returns the id", j.platform_job_id, JOB_ID);
  check("id-only read is not 'usable'", s.usableJob(j), false);
  check("a titled read is 'usable'", s.usableJob({ title: "x" }), true);
  check("a jd-only read is 'usable'", s.usableJob({ jd_text: "x" }), true);
}

console.log("\ngetJob(): a stale detail pane on the split search layout");
{
  // The bug: on /jobs/search the pane rendered a DIFFERENT job than the URL's
  // currentJobId — Contoso Markets's promoted card, stuck at the top of the results —
  // so the id was right (the job applied to) and the content was Contoso Markets's.
  // Three real applications filed this way on 8 Sep 2026. The fix cross-checks
  // the pane against the results card whose data-*-job-id equals currentJobId,
  // and re-sources title/company from the card while dropping the wrong JD.
  //
  // Validate against the OLD code: delete the stale-pane block in linkedin.js
  // and this goes red with title "Full Stack Engineer, AI systems" / company
  // "Contoso Markets" — the exact signature of the three real records.
  const REAL_ID = "4426471965";
  const SEARCH = `https://www.linkedin.com/jobs/search/?currentJobId=${REAL_ID}&keywords=AI%20Engineer`;
  // A results card carries the job's OWN title (doubled: visible span + a
  // visually-hidden a11y copy, identical halves) and company.
  const cardStub = (titleText, companyText) => {
    const sel = {
      "a.job-card-container__link": el(titleText),
      ".artdeco-entity-lockup__subtitle": el(companyText),
    };
    return {
      tagName: "DIV",
      querySelector: (s) => s.split(",").map((x) => sel[x.trim()]).find(Boolean) || null,
    };
  };
  const stalePaneDoc = (cardTitle, cardCompany) =>
    makeDoc({
      title: "AI Engineer Jobs | LinkedIn", // search page's own title, no job in it
      sel: {
        // the PANE — showing Contoso Markets, the wrong job
        ".job-details-jobs-unified-top-card__job-title": "Full Stack Engineer, AI systems",
        ".job-details-jobs-unified-top-card__company-name a": "Contoso Markets",
        "#job-details": "About the job\nAbout Contoso Markets\nThere are over 5 billion users...",
        // the results LIST card for currentJobId — the real job
        [`[data-occludable-job-id="${REAL_ID}"]`]: cardStub(cardTitle, cardCompany),
      },
    });

  const s = loadAdapter({
    ownDoc: stalePaneDoc("AI Agent EngineerAI Agent Engineer", "Northwind Labs"),
    ownLoc: makeLoc(SEARCH),
  });
  const j = s.window.__trackerAdapter.getJob();
  check("stale pane: title re-sourced from the currentJobId card", j.title, "AI Agent Engineer");
  check("stale pane: company re-sourced from the card", j.company, "Northwind Labs");
  check("stale pane: id is currentJobId, the job applied to", j.platform_job_id, REAL_ID);
  check("stale pane: the wrong job's JD is dropped", j.jd_text, null);
  check("stale pane: title_source records the card", j._prov.title_source, "card");
  check("stale pane: breadcrumb names what the pane showed",
        j._prov.stale_pane, { url: REAL_ID, shown: "Full Stack Engineer, AI systems", card: "AI Agent Engineer" });

  // Control: when the pane and the card AGREE, nothing is touched — the guard
  // must not fire on a healthy split-layout read (verified live 9 Sep 2026).
  const healthy = loadAdapter({
    ownDoc: stalePaneDoc("Full Stack Engineer, AI systemsFull Stack Engineer, AI systems", "Contoso Markets"),
    ownLoc: makeLoc(SEARCH),
  });
  const h = healthy.window.__trackerAdapter.getJob();
  check("agreeing pane: title stays the pane's", h.title, "Full Stack Engineer, AI systems");
  check("agreeing pane: JD is kept", h.jd_text, "About the job\nAbout Contoso Markets\nThere are over 5 billion users...");
  check("agreeing pane: no stale_pane breadcrumb", h._prov.stale_pane, undefined);
  check("agreeing pane: title_source is the class selector, not the card", h._prov.title_source, "class");
}

console.log("\ngetJob(): the pane names its own job (/jobs/search-results/, read live 2 Oct 2026)");
{
  // On /jobs/search-results/ no element carries a job id as an attribute, so
  // the card guard above can never fire there. Three real Easy Apply captures
  // on 30 Sep were each filed as the job the pane still showed ("Northwind Labs · AI
  // Engineer", applied to two weeks before), each under the right
  // currentJobId and with no JD. The pane names its own job twice, measured
  // on three live panes: its description's container is
  // JobDetails_AboutTheJob_<id>, and its top card links to /jobs/view/<id>
  // (three links, and no other job's id anywhere in the document).
  const APPLIED = "4466100001", SHOWN = "4362000002";
  const SR = (id) => `https://www.linkedin.com/jobs/search-results/?currentJobId=${id}&keywords=AI%20Engineer`;
  const nodeDoc = (kids, title = "") => {
    const body = node("body", {}, kids);
    return { title, body, querySelector: (s) => body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s) };
  };
  const topCard = (ids, company, title, where) => node("div", {}, [
    node("p", {}, [company]),
    node("p", {}, [node("a", { href: `https://www.linkedin.com/jobs/view/${ids[0]}/` }, [title])]),
    node("p", {}, [node("span", {}, [where]), node("span", {}, ["·"]), node("span", {}, ["2 weeks ago"])]),
    ...ids.map((i) => node("a", { href: `/jobs/view/${i}/?trk=share` }, ["Share"])),
  ]);
  const pane = (ids, company, title, where, { jd = true } = {}) => node("div", {}, [node("div", {}, [
    topCard(ids, company, title, where),
    ...(jd ? [node("div", { id: `JobDetails_AboutTheJob_${ids[0]}` }, [`About the job ${company} is hiring.`])] : []),
  ])]);
  // The results list: cards with text and no job id, as measured.
  const list = () => node("ul", {}, [node("li", {}, [node("div", {}, ["AI Engineer"]), node("div", {}, ["Northwind Labs"])])]);
  const read = (kids, href, title) =>
    loadAdapter({ ownDoc: nodeDoc(kids, title), ownLoc: makeLoc(href) }).window.__trackerAdapter.getJob();
  const pick = (j) => [j.platform_job_id, j.company, j.title, j.jd_text, j.location];

  const ok = read([list(), pane([APPLIED], "Fabrikam", "Generative AI Engineer", "Central Region, Singapore")],
                  SR(APPLIED), "Generative AI Engineer | Fabrikam | LinkedIn");
  check("a pane naming the URL's job is read as it stands",
        [...pick(ok), ok._prov.stale_pane],
        [APPLIED, "Fabrikam", "Generative AI Engineer", "About the job Fabrikam is hiring.",
         "Central Region, Singapore", undefined]);
  const stale = read([list(), pane([SHOWN], "Northwind Labs", "AI Engineer", "Singapore, Singapore")], SR(APPLIED), "");
  check("the 30 Sep shape: a pane naming ANOTHER job gives none of its content, only the URL's id",
        pick(stale), [APPLIED, null, null, null, null]);
  check("…and says so", stale._prov.stale_pane, { url: APPLIED, shown: "AI Engineer", card: null, named: SHOWN });
  check("…also with no description to name it, by the top card's links alone (30 Sep had none)",
        pick(read([list(), pane([SHOWN], "Northwind Labs", "AI Engineer", "Singapore, Singapore", { jd: false })], SR(APPLIED), "")),
        [APPLIED, null, null, null, null]);
  check("a box naming two jobs decides nothing: left as read",
        pick(read([pane([SHOWN, "4400000009"], "Northwind Labs", "AI Engineer", "Singapore, Singapore", { jd: false })], SR(APPLIED), ""))
          .slice(0, 3), [APPLIED, "Northwind Labs", "AI Engineer"]);
  const VIEW = `https://www.linkedin.com/jobs/view/${APPLIED}/`;
  check("a job's own page (no currentJobId) is never second-guessed by the links around it",
        pick(read([pane(["4400000009"], "Fabrikam", "Generative AI Engineer", "Central Region, Singapore", { jd: false })], VIEW, ""))
          .slice(0, 3), [APPLIED, "Fabrikam", "Generative AI Engineer"]);
  // The geometry of 30 Sep: the Easy Apply in the preload frame, the top a
  // search page. With the top's stale pane dropped, the frame's own read wins.
  const s = loadAdapter({
    ownDoc: nodeDoc([pane([APPLIED], "Fabrikam", "Generative AI Engineer", "Central Region, Singapore")]),
    ownLoc: makeLoc(PRELOAD),
    topDoc: nodeDoc([list(), pane([SHOWN], "Northwind Labs", "AI Engineer", "Singapore, Singapore")]),
    topLoc: makeLoc(SR(APPLIED)),
  });
  const g = s.window.__trackerAdapter.getJob();
  check("preload frame under a stale top: this frame's job, the top's id",
        [g.platform_job_id, g.company, g.title, g._prov.doc_source], [APPLIED, "Fabrikam", "Generative AI Engineer", "self"]);
  // The other way round, which fits 30 Sep's log better (the capture in the
  // preload frame, no stale_pane recorded): the STALE pane is in the preload
  // frame, whose own address names no job, so no guard ran, and the read
  // borrowed the top's id unchecked. Checked now against the id it borrows.
  const shellTop = nodeDoc([node("nav", {}, ["Jobs"])]);
  const p = loadAdapter({
    ownDoc: nodeDoc([list(), pane([SHOWN], "Northwind Labs", "AI Engineer", "Singapore, Singapore", { jd: false })]),
    ownLoc: makeLoc(PRELOAD), topDoc: shellTop, topLoc: makeLoc(SR(APPLIED)),
  }).window.__trackerAdapter.getJob();
  check("a stale pane IN the preload frame, the top a shell: the borrowed id, none of the other job",
        [p.platform_job_id, p.company, p.title, p.jd_text], [APPLIED, null, null, null]);
  check("…and the breadcrumb survives the fall back to the top's read",
        p._prov.stale_pane, { url: APPLIED, shown: "AI Engineer", card: null, named: SHOWN });
}
{
  // The classic search page's card guard in the same geometry: the results
  // card for the borrowed id re-sources title and company.
  const REAL_ID = "4426471965";
  const card = { tagName: "DIV", querySelector: (s) => (
    { "a.job-card-container__link": el("AI Agent EngineerAI Agent Engineer"),
      ".artdeco-entity-lockup__subtitle": el("Northwind Labs") })[s.split(",")[0].trim()] || null };
  const preloadDoc = makeDoc({ sel: {
    ".job-details-jobs-unified-top-card__job-title": "Full Stack Engineer, AI systems",
    ".job-details-jobs-unified-top-card__company-name a": "Contoso Markets",
    "#job-details": "About the job\nAbout Contoso Markets",
    [`[data-occludable-job-id="${REAL_ID}"]`]: card,
  } });
  const j = loadAdapter({ ownDoc: preloadDoc, ownLoc: makeLoc(PRELOAD), topDoc: shellPage(),
                          topLoc: makeLoc(`https://www.linkedin.com/jobs/search/?currentJobId=${REAL_ID}`) })
    .window.__trackerAdapter.getJob();
  check("classic search in the preload frame: the card for the borrowed id re-sources the job",
        [j.platform_job_id, j.title, j.company, j.jd_text, j._prov.title_source],
        [REAL_ID, "AI Agent Engineer", "Northwind Labs", null, "card"]);
}

console.log("\njobFromUrl(): last-resort identity off the tab URL");
{
  const s = loadAdapter({ ownDoc: shellPage(), ownLoc: makeLoc(COLLECTIONS) });
  const a = s.window.__trackerAdapter;
  check("currentJobId param", a.jobFromUrl(COLLECTIONS).platform_job_id, JOB_ID);
  check("/jobs/view/<id>/ path",
        a.jobFromUrl(`https://www.linkedin.com/jobs/view/${JOB_ID}/`).platform_job_id, JOB_ID);
  check("canonical url built from the id", a.jobFromUrl(COLLECTIONS).url,
        `https://www.linkedin.com/jobs/view/${JOB_ID}/`);
  // The preload url is the one a blind frame would hand over if the background
  // ever cached a frame url instead of the tab's — it must yield nothing.
  check("preload url names no job", a.jobFromUrl(PRELOAD), null);
  check("non-LinkedIn host rejected",
        a.jobFromUrl("https://evil.example.com/jobs/view/123/"), null);
  check("garbage rejected", a.jobFromUrl("not a url"), null);
  check("empty rejected", a.jobFromUrl(""), null);
}

console.log("\nisExternal(): decides whether a capture asks before it writes");
{
  const s = loadAdapter({ ownDoc: shellPage(), ownLoc: makeLoc(COLLECTIONS) });
  const a = s.window.__trackerAdapter;
  const btn = (label) => ({ getAttribute: () => label, textContent: "" });
  check("'Easy Apply to this job' is internal", a.isExternal(btn("Easy Apply to this job")), false);
  check("'Apply to X on company website' is external",
        a.isExternal(btn("Apply to Backend / Systems Agentic Engineer (SG) on company website")), true);
}

console.log("\nanswerFormRoot(): where the screening-question sweep reads from");
{
  // The rebuilt Easy Apply (Aug 2026) is a native <dialog open> in the top
  // document. `[role='dialog']` never matched it — an element's implicit role
  // is not an attribute — and every sweep on that layout came back noRoot for
  // two weeks. The first shape below is the one that is live today.
  const dialog = el("Apply to Northwind Labs");
  dialog.tagName = "DIALOG";
  const s1 = loadAdapter({ ownDoc: makeDoc({ sel: { "dialog[open]": dialog } }),
                           ownLoc: makeLoc(COLLECTIONS) });
  check("top frame, <dialog open>: the dialog is the root",
        s1.window.__trackerAdapter.answerFormRoot() === dialog, true);
  const legacy = el("");
  const s2 = loadAdapter({ ownDoc: makeDoc({ sel: { "[role='dialog']": legacy } }),
                           ownLoc: makeLoc(COLLECTIONS) });
  check("top frame, classic role=dialog modal still matches",
        s2.window.__trackerAdapter.answerFormRoot() === legacy, true);
  const s3 = loadAdapter({ ownDoc: shellPage(), ownLoc: makeLoc(COLLECTIONS) });
  check("top frame, nothing dialog-like: null, never the page",
        s3.window.__trackerAdapter.answerFormRoot(), null);
  // A SUBFRAME took its first <form>, assuming a subframe is the modal's
  // own iframe. The preload frame is a whole page, where a search page's
  // filter form can come first: "Filter results by: Date posted" / "Any
  // time" was stored as an answer by all three 30 Sep captures, each made in
  // that frame, and by two on 8 Sep, the first stale-pane day (its frames are
  // not on record). The modal wins wherever it is.
  const filter = el("Filter results by: Date posted");
  const modal = el("Apply to Northwind Labs");
  const s4 = loadAdapter({ ownDoc: makeDoc({ sel: { form: filter, ".jobs-easy-apply-modal": modal } }),
                           ownLoc: makeLoc(PRELOAD), topDoc: shellPage(), topLoc: makeLoc(COLLECTIONS) });
  check("subframe holding a page: the Easy Apply modal, not the page's first form",
        s4.window.__trackerAdapter.answerFormRoot() === modal, true);
  const wizard = el("");
  const s5 = loadAdapter({ ownDoc: makeDoc({ sel: { form: wizard } }),
                           ownLoc: makeLoc(PRELOAD), topDoc: jobPage(), topLoc: makeLoc(COLLECTIONS) });
  check("subframe that IS the modal (no dialog in it): its form, as before",
        s5.window.__trackerAdapter.answerFormRoot() === wizard, true);
}

/* ------------------------------------------ a fake DOM for shared/answers.js
 *
 * Just enough element for what answers.js reads: attributes, children and text
 * nodes, closest()/querySelectorAll() over a selector subset (tag, #id, .class,
 * [attr], [attr=value], comma lists — no combinators), the label/aria lookups,
 * and the form-control properties (type, name, value, checked, selectedOptions).
 * getClientRects() always reports a box, so labelText()'s "skip unrendered"
 * guard is a no-op here — the aria-hidden guard is still exercised. */

const ANSWERS_SRC = fs.readFileSync(path.join(ROOT, "extension/shared/answers.js"), "utf8");

// A selector list; each selector a compound, or compounds joined by the
// DESCENDANT combinator (linkedin.js's "…company-name a"), split on spaces
// outside brackets. No other combinator.
function matches(node, sel) {
  return sel.split(",").some((s) => {
    const chain = s.trim().split(/\s+(?![^[]*\])/);
    if (!compound(node, chain[chain.length - 1])) return false;
    let anc = node.parentElement;
    for (let i = chain.length - 2; i >= 0; i--) {
      while (anc && !compound(anc, chain[i])) anc = anc.parentElement;
      if (!anc) return false;
      anc = anc.parentElement;
    }
    return true;
  });
}

function compound(node, s) {
  {
    if (s === "*") return true;
    const m = /^([a-zA-Z][a-zA-Z0-9]*)?((?:#[\w-]+|\.[\w-]+|\[[^\]]+\])*)$/.exec(s);
    if (!m) throw new Error(`fake DOM: selector not supported: ${s}`);
    if (m[1] && node.tagName !== m[1].toUpperCase()) return false;
    for (const part of (m[2] || "").match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]/g) || []) {
      if (part[0] === "#") {
        if (node.getAttribute("id") !== part.slice(1)) return false;
      } else if (part[0] === ".") {
        if (!(node.getAttribute("class") || "").split(/\s+/).includes(part.slice(1))) return false;
      } else {
        // [a], [a=v], and the prefix / contains forms [a^=v] / [a*=v] that
        // linkedin.js's description and job-link selectors use.
        const am = /^\[([\w-]+)(?:([\^*]?)=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]$/.exec(part);
        const want = am[3] !== undefined ? am[3] : am[4] !== undefined ? am[4] : am[5];
        const got = node.getAttribute(am[1]);
        if (want === undefined) { if (!node.hasAttribute(am[1])) return false; }
        else if (am[2] === "^" ? !(got || "").startsWith(want)
               : am[2] === "*" ? !(got || "").includes(want) : got !== want) return false;
      }
    }
    return true;
  }
}

function node(tag, attrs = {}, kids = []) {
  const n = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    _attrs: { ...attrs },
    _doc: null,
    childNodes: [],
    parentElement: null,
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? String(this._attrs[k]) : null;
    },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); },
    getClientRects() { return [{}]; },
    getRootNode() { return this._doc; },
    get children() { return this.childNodes.filter((c) => c.nodeType === 1); },
    get textContent() { return this.childNodes.map((c) => c.textContent).join(""); },
    get innerText() { return this.textContent; },
    get previousElementSibling() {
      const sib = this.parentElement ? this.parentElement.children : [];
      const i = sib.indexOf(this);
      return i > 0 ? sib[i - 1] : null;
    },
    querySelectorAll(sel) {
      const out = [];
      const walk = (x) => { for (const c of x.children) { if (matches(c, sel)) out.push(c); walk(c); } };
      walk(this);
      return out;
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    matches(sel) { return matches(this, sel); },
    closest(sel) {
      for (let x = this; x; x = x.parentElement) if (matches(x, sel)) return x;
      return null;
    },
    get id() { return this._attrs.id || ""; },
    get name() { return this._attrs.name || ""; },
    get type() { return this._attrs.type || ""; },
    get value() { return this._attrs.value || ""; },
    get checked() { return !!this._attrs.checked; },
    get disabled() { return !!this._attrs.disabled; },
    get selectedOptions() { return this.children.filter((c) => c._attrs.selected); },
  };
  for (const k of kids) {
    const c = typeof k === "string" ? { nodeType: 3, textContent: k } : k;
    if (c.nodeType === 1) c.parentElement = n;
    n.childNodes.push(c);
  }
  return n;
}

/* An OPEN shadow root on `host`, as SuccessFactors' candidate experience
 * builds its form from UI5 web components (read live 29 Sep 2026). Like the
 * platform: the root's top-level children have no parentElement, every node
 * inside answers getRootNode() with the root, the root's .host is the
 * element, the host's own children stay its light DOM (its textContent), and
 * a <slot> reports those children as its assigned nodes: a named slot the
 * ones whose slot attribute names it, the default slot the rest, and each
 * assigned element knows its slot (assignedSlot), as SmartRecruiters' radio
 * group needs (30 Sep 2026). */
function attachShadow(host, kids) {
  const root = {
    nodeType: 11,
    host,
    childNodes: [],
    get children() { return this.childNodes.filter((c) => c.nodeType === 1); },
    querySelectorAll(sel) {
      const out = [];
      const walk = (x) => { for (const c of x.children) { if (matches(c, sel)) out.push(c); walk(c); } };
      walk(this);
      return out;
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    getElementById(id) { return this.querySelectorAll("*").find((x) => x.getAttribute("id") === id) || null; },
  };
  const own = (x) => {
    x.getRootNode = () => root;
    if (x.tagName === "SLOT") {
      const name = x.getAttribute("name") || "";
      x.assignedNodes = () => host.childNodes.filter((c) =>
        (c.nodeType === 1 ? c.getAttribute("slot") || "" : "") === name);
      for (const c of x.assignedNodes()) if (c.nodeType === 1) c.assignedSlot = x;
    }
    for (const c of x.children) own(c);
  };
  for (const k of kids) {
    const c = typeof k === "string" ? { nodeType: 3, textContent: k } : k;
    root.childNodes.push(c);
    if (c.nodeType === 1) own(c);
  }
  host.shadowRoot = root;
  return host;
}

/* Load answers.js over a fake document whose <body> holds `root`, with an
 * adapter that hands that root back as the apply form. Returns take()'s
 * output: what the sweep would send with the capture. */
function sweepOf(root) {
  return sweepStepsOf([root]);
}

/* A WIZARD: each root is one step. Every step but the last is swept by a
 * click (the capture-phase listener, as a "Next" press would), then the root
 * is swapped for the next step's and take() sweeps the final one — so what
 * earlier steps left in the store is exactly what survives to the capture. */
function sweepStepsOf(steps) {
  const sandbox = loadAnswers(steps[0]);
  for (let i = 1; i < steps.length; i++) {
    for (const fn of sandbox._listeners.click || []) fn({});
    sandbox._setRoot(steps[i]);
  }
  return sandbox.window.__trackerAnswers.take();
}

// `storage`: a sessionStorage stand-in holding what an EARLIER page of the
// tab's visit left (memoryStorage, below); by default an empty one that only
// records writes. `noRoot`: the adapter finds no application on the page.
function loadAnswers(first, storage = undefined, { noRoot = false } = {}) {
  let root = first;
  const listeners = {};
  const writes = [];        // every value handed to sessionStorage, in order
  const body = node("body", {}, [root]);
  const doc = {
    nodeType: 9,
    body,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    querySelector: (s) => body.querySelector(s),
    querySelectorAll: (s) => body.querySelectorAll(s),
    getElementById: (id) => body.querySelectorAll("*").find((n) => n.getAttribute("id") === id) || null,
  };
  const stamp = (n) => { n._doc = doc; for (const c of n.children) stamp(c); };
  stamp(body);
  const sandbox = {
    console,
    setTimeout: () => 0,
    CSS: { escape: (s) => s },
    sessionStorage: storage || { getItem: () => null, setItem(k, v) { writes.push(v); }, removeItem() {} },
    document: doc,
    location: { href: "https://www.linkedin.com/jobs/view/1/" },
  };
  sandbox.window = {
    document: doc, location: sandbox.location,
    __trackerAdapter: { answerFormRoot: () => (noRoot ? null : root), answerFormKey: () => "1" },
  };
  sandbox.window.top = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(ANSWERS_SRC, sandbox);
  sandbox._listeners = listeners;
  sandbox._writes = writes;
  sandbox._setRoot = (next) => {
    body.childNodes = [next];
    next.parentElement = body;
    stamp(next);
    root = next;
  };
  return sandbox;
}

console.log("\nanswers.js sweep: the rebuilt Easy Apply's control shapes (measured live 2 Sep 2026)");
{
  // A Yes/No screening question: the question in a <p>, then a LEGENDLESS
  // <fieldset role="radiogroup">. Each option is <div role="radio"> whose
  // aria-label is the QUESTION, wrapping a bare native radio (generated name,
  // an EMPTY <label for>) and a <p> holding the visible "Yes"/"No".
  const question =
    "Will you now or in the future require sponsorship for employment visa status?";
  const option = (id, shown, on) =>
    node("div", { role: "radio", "aria-checked": String(on), "aria-label": question }, [
      node("div", {}, [
        node("input", { type: "radio", name: "radio-group-rl", id, value: "on",
                        ...(on ? { checked: true } : {}) }),
        node("label", { for: id }),
      ]),
      node("p", {}, [shown]),
    ]);
  const yesNo = (picked) => node("div", {}, [
    node("p", {}, [question + "*"]),
    node("fieldset", { role: "radiogroup" }, [
      option("rm", "Yes", picked === "Yes"),
      option("rn", "No", picked === "No"),
    ]),
  ]);
  check("Yes/No radio: question from the wrapper's aria-label, answer from its visible text",
        sweepOf(yesNo("No")), [{ question, answer: "No", type: "radio" }]);
  check("Yes/No radio: nothing picked, nothing recorded", sweepOf(yesNo(null)), []);
}
{
  // The resume picker: a heading block ("Resume*" plus a description line),
  // then a radiogroup of cards. Here the wrapper's aria-label is the FILENAME
  // and the wrapper has no text of its own — the visible filename sits on the
  // card OUTSIDE the role=radio element. Opposite convention to the Yes/No
  // group above, on the same wizard.
  const card = (file, on) => node("div", {}, [
    node("p", {}, [file]),
    node("div", { role: "radio", "aria-checked": String(on), "aria-label": file }, [
      node("input", { type: "radio", name: "radio-group-rg", id: `r-${file}`, value: "on",
                      ...(on ? { checked: true } : {}) }),
      node("label", { for: `r-${file}` }),
    ]),
  ]);
  const resumeStep = node("div", {}, [
    node("div", {}, [
      node("p", {}, ["Resume*"]),
      node("p", {}, ["Select or upload a resume in DOC, DOCX, or PDF format that is less than 2MB"]),
    ]),
    node("fieldset", { role: "radiogroup" }, [
      card("Contoso-resume-AI-engineer.pdf", true),
      card("Contoso-resume-dotnet-engineer.pdf", false),
    ]),
  ]);
  check("resume card: heading as question, filename as answer (pipeline/answers.py promotes it)",
        sweepOf(resumeStep),
        [{ question: "Resume*", answer: "Contoso-resume-AI-engineer.pdf", type: "radio" }]);
}
{
  // A checkbox named only by its role=checkbox wrapper. Before the wrapper
  // step in labelFor(), this resolved to the input's generated name — or to
  // nothing — and the server-side drop rule never saw the text it matches on.
  const topChoice = node("div", { role: "checkbox", "aria-checked": "false",
                                  "aria-label": "Mark job as a top choice" }, [
    node("input", { type: "checkbox", id: "tc" }),
    node("label", { for: "tc" }),
    node("p", {}, ["Mark job as a top choice"]),
  ]);
  check("checkbox: named by the role=checkbox wrapper",
        sweepOf(topChoice), [{ question: "Mark job as a top choice", answer: "No", type: "checkbox" }]);
}
{
  // The contact step is still textbook on the new layout — a real <label for>
  // on a native <select> and <input> — which is why typed fields were the only
  // thing that survived the two weeks, and why nothing here needed to change.
  const contact = node("div", {}, [
    node("label", { for: "em" }, ["Email address*"]),
    node("select", { id: "em" }, [node("option", { selected: true }, ["jane@example.com"])]),
    node("label", { for: "ph" }, ["Mobile phone number*"]),
    node("input", { type: "tel", id: "ph", value: "88888888" }),
    node("label", { for: "pc" }, ["Phone country code*"]),
    node("select", { id: "pc" }, [node("option", { selected: true }, ["Select an option"])]),
  ]);
  check("contact step: label[for] still wins, placeholder option still dropped",
        sweepOf(contact), [
          { question: "Email address*", answer: "jane@example.com", type: "select" },
          { question: "Mobile phone number*", answer: "88888888", type: "tel" },
        ]);
}
{
  // The classic modal's radio group — legend + <label for> — must come out
  // exactly as before: no wrapper, so none of the new steps fire.
  const classic = node("fieldset", {}, [
    node("legend", {}, ["Are you legally authorised to work in Singapore?"]),
    node("input", { type: "radio", name: "auth", id: "a1", value: "y", checked: true }),
    node("label", { for: "a1" }, ["Yes"]),
    node("input", { type: "radio", name: "auth", id: "a2", value: "n" }),
    node("label", { for: "a2" }, ["No"]),
  ]);
  check("classic radio group: legend + label[for], unchanged",
        sweepOf(classic),
        [{ question: "Are you legally authorised to work in Singapore?", answer: "Yes", type: "radio" }]);
}
{
  // A wrapper that follows the ARIA convention properly — aria-label is the
  // option, the question is the block before the group — must resolve too;
  // the "wrapper's name is the question" reading is a LinkedIn quirk, not a
  // rule the sweep may depend on.
  const proper = node("div", {}, [
    node("p", {}, ["Do you have a valid work pass?"]),
    node("fieldset", { role: "radiogroup" }, [
      node("div", { role: "radio", "aria-label": "Yes" }, [
        node("input", { type: "radio", name: "wp", id: "w1", value: "on", checked: true }),
        node("label", { for: "w1" }),
      ]),
      node("div", { role: "radio", "aria-label": "No" }, [
        node("input", { type: "radio", name: "wp", id: "w2", value: "on" }),
        node("label", { for: "w2" }),
      ]),
    ]),
  ]);
  check("wrapper aria-label as the option: question falls back to the block before the group",
        sweepOf(proper), [{ question: "Do you have a valid work pass?", answer: "Yes", type: "radio" }]);
}

console.log("\nanswers.js sweep: the name moved onto the input (in use by 25 Sep, measured live 30 Sep 2026)");
{
  // No role="radio" wrapper any more. The native input carries the aria-label
  // itself, and on a Yes/No question that label is the QUESTION on every
  // option, while "Yes"/"No" is a <p> in a sibling <div>. Its <label for> is
  // still empty. Read one input at a time, the question came back as its own
  // answer on every radio from 25 to 29 Sep.
  const question = "Have you completed the following level of education: Bachelor's Degree?";
  const option = (id, shown, on) => node("div", {}, [
    node("div", {}, [
      node("div", {}, [
        node("input", { type: "radio", name: "radio-group-rr", id, "aria-label": question,
                        ...(on ? { checked: true } : {}) }),
        node("label", { for: id }),
      ]),
      node("div", {}, [node("p", {}, [shown])]),
    ]),
  ]);
  const yesNo = (picked) => node("div", {}, [
    node("p", {}, [question + "*"]),
    node("fieldset", { role: "radiogroup", "aria-describedby": "error-message-rr" }, [
      node("div", {}, [
        option("rs", "Yes", picked === "Yes"),
        option("rt", "No", picked === "No"),
      ]),
    ]),
  ]);
  check("Yes/No on the input: the label every option shares is the question, the row is the answer",
        sweepOf(yesNo("Yes")), [{ question, answer: "Yes", type: "radio" }]);
  check("Yes/No on the input: the other option", sweepOf(yesNo("No")),
        [{ question, answer: "No", type: "radio" }]);
  check("Yes/No on the input: nothing picked, nothing recorded", sweepOf(yesNo(null)), []);

  // The resume picker on the same wizard: each input's aria-label is its
  // FILENAME, and the card around it reads "PDF<file><date>". Two cards may
  // hold the same file (they did, live), so one repeated name is not a shared
  // one; only a name on EVERY member is.
  const card = (id, file, day, on) => node("div", {}, [
    node("div", {}, ["PDF"]),
    node("div", {}, [node("p", {}, [file]), node("p", {}, [day])]),
    node("div", {}, [
      node("input", { type: "radio", name: "radio-group-r10", id, "aria-label": file,
                      ...(on ? { checked: true } : {}) }),
      node("label", { for: id }),
    ]),
  ]);
  const resumeStep = node("div", {}, [
    node("div", {}, [
      node("p", {}, ["Resume*"]),
      node("p", {}, ["Select or upload a resume in DOC, DOCX, or PDF format that is less than 2MB"]),
    ]),
    node("fieldset", { role: "radiogroup" }, [
      card("c1", "Contoso-resume-AI-engineer.pdf", "9/29/2026", false),
      card("c2", "Contoso-resume-AI-engineer.pdf", "9/29/2026", false),
      card("c3", "Contoso-resume-dotnet-engineer.pdf", "9/28/2026", true),
    ]),
  ]);
  check("resume card on the input: the filename stays the answer, not the card's text",
        sweepOf(resumeStep),
        [{ question: "Resume*", answer: "Contoso-resume-dotnet-engineer.pdf", type: "radio" }]);
}

console.log("\nanswers.js sweep: Greenhouse's job-board form (read live 29 Sep 2026)");
{
  // Country: a react-select. Its <input role="combobox"> is emptied after a
  // pick; the choice is drawn in .select__single-value, and with nothing
  // picked a placeholder the input names in aria-describedby shows instead.
  // Beside it, react-select's own required input: no id, no label. The
  // question's <label> sits outside the select's container.
  const country = (picked) => node("div", { class: "field-wrapper" }, [
    node("label", { id: "country-label", for: "country" }, ["Country*"]),
    node("div", { class: "select__container" }, [
      node("div", { class: "select__control" }, [
        node("div", { class: "select__value-container" }, [
          picked ? node("div", { class: "select__single-value" }, [picked])
                 : node("div", { class: "select__placeholder", id: "react-select-country-placeholder" },
                        ["Select..."]),
          node("div", { class: "select__input-container" }, [
            node("input", { id: "country", role: "combobox", type: "text", class: "select__input",
                            "aria-labelledby": "country-label",
                            "aria-describedby": "react-select-country-placeholder country-error" }),
          ]),
        ]),
        node("div", { class: "select__indicators" }, [node("span", { class: "select__indicator-separator" })]),
      ]),
      node("input", { type: "text", required: "" }),
    ]),
  ]);
  check("a react-select answers with the option it shows, under its label",
        sweepOf(country("Singapore")), [{ question: "Country*", answer: "Singapore", type: "text" }]);
  check("…and nothing when only its placeholder shows", sweepOf(country(null)), []);
  // Resume: <input type=file class="visually-hidden">, whose only <label for>
  // is the "Attach" button's, inside <div role="group"
  // aria-labelledby="upload-label-resume"> named "Resume/CV*".
  const upload = (file) => {
    const input = node("input", { type: "file", id: "resume", class: "visually-hidden",
                                  accept: ".pdf,.doc,.docx,.txt,.rtf" });
    if (file) input.files = [{ name: file }];
    return node("div", { class: "file-upload", role: "group", "aria-labelledby": "upload-label-resume" }, [
      node("div", { class: "label", id: "upload-label-resume" }, ["Resume/CV*"]),
      node("div", { class: "file-upload__wrapper" }, [
        node("div", { class: "button-container" }, [
          node("div", { class: "secondary-button" }, [
            node("div", {}, [node("button", { class: "btn" }, ["Attach"]),
                             node("label", { class: "visually-hidden", for: "resume" }, ["Attach"]), input]),
          ]),
        ]),
      ]),
    ]);
  };
  check("a file field answers with the file's name, under its group's name (not 'Attach')",
        sweepOf(upload("Jane-Doe_resume.pdf")),
        [{ question: "Resume/CV*", answer: "Jane-Doe_resume.pdf", type: "file" }]);
  check("…and nothing before a file is chosen", sweepOf(upload(null)), []);
}

console.log("\nanswers.js sweep: a SmartRecruiters screening step, drawn by web components (read live 30 Sep 2026)");
{
  // The screening step of a LinkedIn → SmartRecruiters apply, placeholder
  // names, the structure the measured one. Every question's words are
  // SLOTTED into its label: <spl-textarea>'s own <label for> holds a <slot>
  // and a "*", so read alone it says "*", which keys as nothing, and the row
  // was dropped. The Yes/No questions have no native control at all:
  // <spl-radio role="radio" aria-checked label="Yes">, slotted into the
  // <fieldset role="radiogroup" aria-labelledby> inside <spl-radio-group>,
  // whose label holds the question through a named slot.
  const Q1 = "Are you legally authorized to work in the country that you are applying to?";
  const Q2 = "Will you now or in the future require sponsorship for employment?";
  const Q3 = "Are you related to a current employee? If yes, please specify.";
  const textarea = (id, question, value) => attachShadow(
    node("spl-textarea", { id, name: id }, [node("span", { slot: "label" }, [question])]),
    [node("label", { id: `${id}-label`, for: `${id}-in` }, [node("slot", { name: "label" }), "*"]),
     node("textarea", { id: `${id}-in`, ...(value ? { value } : {}) })]);
  const radio = (label, on, attrs = {}) => attachShadow(
    node("spl-radio", { label, role: "radio", "aria-checked": on ? "true" : "false",
                        value: label === "Yes" ? "1" : "0", ...attrs }),
    [node("div", { class: "circle" }), node("label", {}, [label])]);
  const group = (id, question, picked) => attachShadow(
    node("spl-radio-group", { id }, [node("span", { slot: "label" }, [question]),
                                     radio("Yes", picked === "Yes"), radio("No", picked === "No")]),
    [node("fieldset", { role: "radiogroup", "aria-labelledby": `${id}-label` }, [
      node("label", { id: `${id}-label`, for: id }, [node("slot", { name: "label" })]),
      node("slot"),
    ])]);
  const step = (a, b) => node("div", {}, [group("g1", Q1, a), group("g2", Q2, b), textarea("q3", Q3, "No")]);
  check("a slotted label reads as its question, and radios with no <input> as their group's answer",
        sweepOf(step("No", "Yes")),
        [{ question: `${Q3}*`, answer: "No", type: "textarea" },
         { question: Q1, answer: "No", type: "radio" },
         { question: Q2, answer: "Yes", type: "radio" }]);
  check("…a drawn group with nothing picked records nothing", sweepOf(step(null, null)),
        [{ question: `${Q3}*`, answer: "No", type: "textarea" }]);
  // An option whose only name is the question itself is the capture artefact
  // of 25-29 Sep (the question stored as its own answer), never an answer.
  const named = node("div", { role: "radiogroup", "aria-label": Q2 }, [
    node("div", { role: "radio", "aria-checked": "true", "aria-label": Q2 }),
    node("div", { role: "radio", "aria-checked": "false", "aria-label": Q2 })]);
  check("…and an option named only by the question is not its answer", sweepOf(node("div", {}, [named])), []);
  // A drawn checkbox or switch answers like a native one.
  const box = (on) => node("div", { role: "checkbox", "aria-checked": on ? "true" : "false",
                                    "aria-label": "Keep me informed about future roles" });
  check("a drawn checkbox answers Yes/No under its own name",
        [sweepOf(node("div", {}, [box(true)])), sweepOf(node("div", {}, [box(false)]))],
        [[{ question: "Keep me informed about future roles", answer: "Yes", type: "checkbox" }],
         [{ question: "Keep me informed about future roles", answer: "No", type: "checkbox" }]]);
}

console.log("\nanswers.js normKey: one rule with pipeline/answers.py:norm_question");
{
  // The same list tests/test_captures.py holds the server to. Until 24 Sep 2026
  // both kept [a-z0-9] only: "C#" and "C++" keyed alike, and a question in
  // Chinese keyed as its one Latin letter.
  const { cases } = JSON.parse(
    fs.readFileSync(path.join(__dirname, "question_norms.json"), "utf8"));
  const { normKey } = loadAnswers(node("div")).window.__trackerAnswers;
  for (const [q, want] of cases) check(`normKey ${JSON.stringify(q)}`, normKey(q), want);
}
{
  // Why the extension's key matters although the server re-derives its own:
  // the store is keyed norm#occurrence, and the occurrence counter restarts on
  // every sweep. With C++ on one wizard step and C# on the next, the old key
  // gave both "…with c#0", and the second step's sweep overwrote the first's
  // answer before the capture was ever sent.
  const ask = (q, id, v) => node("div", {}, [
    node("label", { for: id }, [q]),
    node("input", { type: "number", id, value: v }),
  ]);
  check("C++ on one step and C# on the next both reach the capture",
        sweepStepsOf([ask("How many years of work experience do you have with C++?", "cpp", "1"),
                      ask("How many years of work experience do you have with C#?", "cs", "10")]),
        [{ question: "How many years of work experience do you have with C++?", answer: "1", type: "number" },
         { question: "How many years of work experience do you have with C#?", answer: "10", type: "number" }]);
}

console.log("\nanswers.js isSensitive: one list with pipeline/answers.py:is_sensitive");
{
  // The same list tests/test_captures.py holds the server to. The extension
  // decides on the KEY, the server on the question — so the case runs through
  // normKey first, exactly as record() does.
  const { cases } = JSON.parse(
    fs.readFileSync(path.join(__dirname, "sensitive_questions.json"), "utf8"));
  const { normKey, isSensitive } = loadAnswers(node("div")).window.__trackerAnswers;
  for (const [q, want] of cases) check(`isSensitive ${JSON.stringify(q)}`, isSensitive(normKey(q)), want);
}
{
  // A sensitive answer is withheld at record(), before it reaches the store —
  // so it never reaches this tab's sessionStorage either, which is where a
  // wizard's earlier steps wait. Two steps, so the first is saved there.
  const gender = node("div", {}, [
    node("label", { for: "g" }, ["Gender*"]),
    node("select", { id: "g" }, [node("option", { selected: true }, ["Female"])]),
  ]);
  const python = node("div", {}, [
    node("label", { for: "y" }, ["How many years of experience do you have with Python?"]),
    node("input", { type: "number", id: "y", value: "8" }),
  ]);
  const sandbox = loadAnswers(gender);
  for (const fn of sandbox._listeners.click || []) fn({});
  sandbox._setRoot(python);
  check("sensitive answer withheld, question kept, the rest untouched",
        sandbox.window.__trackerAnswers.take(), [
          { question: "Gender*", answer: "(withheld)", type: "select" },
          { question: "How many years of experience do you have with Python?",
            answer: "8", type: "number" }]);
  check("the real value was never written to sessionStorage",
        sandbox._writes.length > 0 && sandbox._writes.every((w) => !w.includes("Female")),
        true);
}

console.log("\nanswers.js leftover: a form the visit left with answers no capture took (29 Sep 2026)");
{
  // The shape of the real loss: a form keyed by its requisition, 13 answers,
  // and the next page (the candidate's profile) keyed by something else. The
  // loader's adapter keys every page "1".
  const KEY = "__tracker_form_answers";
  const items = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) =>
    [`q${i}#0`, { question: `Q${i}`, answer: "a", type: "text", i }]));
  const left = (rec) => {
    const s = memoryStorage();
    if (rec) s.setItem(KEY, JSON.stringify(rec));
    return [s, loadAnswers(node("div"), s).window.__trackerAnswers];
  };
  const [s, a] = left({ key: "career4.successfactors.com/sf1001/61234", at: Date.now() - 60_000, items: items(13) });
  const got = a.leftover();
  check("another form's unsent answers are reported, with their count", got && got.answers, 13);
  check("…once: the store is marked", a.leftover(), null);
  check("…and the answers stay where they were, for a repair",
        Object.keys(JSON.parse(s.getItem(KEY)).items).length, 13);
  check("this page's own form is not a leftover",
        left({ key: "1", at: Date.now(), items: items(3) })[1].leftover(), null);
  check("an empty store, or one past the two-hour window, is not either",
        [left({ key: "x", at: Date.now(), items: {} })[1].leftover(),
         left({ key: "x", at: Date.now() - 3 * 3600_000, items: items(2) })[1].leftover(),
         left(null)[1].leftover()], [null, null, null]);
}

console.log("\nanswers.js store: whether a sweep ever found the form (30 Sep 2026)");
{
  // generic.js reads the mark: a later wizard step at an address that never
  // says "apply" continues a form an earlier step's sweep found.
  const KEY = "__tracker_form_answers";
  const s = memoryStorage();
  const a = loadAnswers(node("div", {}, [node("label", { for: "fn" }, ["First name"]),
                                         node("input", { type: "text", id: "fn", value: "Jane" })]), s);
  for (const fn of a._listeners.click) fn({});
  check("a sweep that found the form marks the store", JSON.parse(s.getItem(KEY)).rooted, true);
  // The edit backstop on a page with no application: a job-alert box.
  const t = memoryStorage();
  const box = node("input", { type: "text", id: "em", value: "jane@contoso.com" });
  const b = loadAnswers(node("div", {}, [node("label", { for: "em" }, ["Email"]), box]), t, { noRoot: true });
  for (const fn of b._listeners.input) fn({ composedPath: () => [box] });
  const rec = JSON.parse(t.getItem(KEY));
  check("…an answer only the backstop kept, on a page with no form, does not",
        [!!rec, rec && rec.rooted], [true, false]);
}

/* ------------------------------------ shared/jobposting.js: any job page
 *
 * The generic reader (docs/career-sites.md §5). Every page below reproduces a
 * shape MEASURED in the 24 Sep 2026 survey of 18 ATS vendors (§4 of that doc:
 * live server HTML or the vendor's own bundle code), with placeholder names —
 * the same rule as the answers.js fixtures above: a fixture that does not
 * match a real page is worth less than none. */

const JOBPOSTING_SRC = fs.readFileSync(path.join(ROOT, "extension/shared/jobposting.js"), "utf8");

function loadJobPosting() {
  const sandbox = { URL, URLSearchParams, console, window: {} };
  vm.createContext(sandbox);
  vm.runInContext(JOBPOSTING_SRC, sandbox);
  return sandbox.window.__trackerJobPosting;
}

// A page: the fake DOM's nodes under one body, and a tab title.
function pageDoc(kids, title = "") {
  const body = node("body", {}, kids);
  return {
    title,
    querySelector: (s) => body.querySelector(s),
    querySelectorAll: (s) => body.querySelectorAll(s),
  };
}
const ld = (obj) => node("script", { type: "application/ld+json" },
                        [typeof obj === "string" ? obj : JSON.stringify(obj)]);
const J = loadJobPosting();
// The fields read() owns, without _prov — compared whole, so a field that
// starts arriving unexpectedly fails here too.
const fields = (j) => j && Object.fromEntries(
  ["platform_job_id", "url", "company", "title", "jd_text", "location",
   "posted_label", "salary_raw", "work_type", "ats"].map((k) => [k, j[k]]));

console.log("\njobposting.js idFrom: one list with pipeline/joburl.py:generic_id");
{
  const { cases } = JSON.parse(fs.readFileSync(path.join(__dirname, "job_urls.json"), "utf8"));
  for (const [url, want] of cases) {
    const got = J.idFrom(url);
    check(`idFrom ${url}`, got ? got.platform_job_id : null, want);
  }
}

console.log("\njobposting.js read(): JSON-LD, microdata and the fallbacks");
{
  // Lever: JSON-LD on the job page; nested Organization; addressLocality
  // carrying the whole place with region and country NULL; employmentType
  // "Full-time", which is not a schema.org value; HTML description.
  const LEVER = "https://jobs.lever.co/contoso/53e23908-0da6-47a5-a482-39be676e9ee6";
  const j = J.read(pageDoc([ld({
    "@context": "https://schema.org", "@type": "JobPosting",
    title: "Senior Backend Engineer",
    hiringOrganization: { "@type": "Organization", name: "Contoso Markets", logo: null },
    description: "<p>Build <b>APIs</b> &amp; services.</p><ul><li>Go</li><li>Postgres</li></ul>",
    datePosted: "2026-04-30", employmentType: "Full-time",
    jobLocation: { "@type": "Place", address: { addressLocality: "Singapore, Singapore",
                                               addressRegion: null, addressCountry: null } },
  })], "Contoso Markets - Senior Backend Engineer"), makeLoc(LEVER));
  check("JSON-LD (Lever shape): every field", fields(j), {
    platform_job_id: "jobs.lever.co/53e23908-0da6-47a5-a482-39be676e9ee6",
    url: LEVER, company: "Contoso Markets", title: "Senior Backend Engineer",
    jd_text: "Build APIs & services.\n\n• Go\n• Postgres", location: "Singapore, Singapore",
    posted_label: "30 Apr 2026", salary_raw: null, work_type: "Full time", ats: "lever" });
  check("JSON-LD: title_source", j._prov.title_source, "jsonld");
}
{
  // SuccessFactors Career Site Builder on an employer's own domain: MICRODATA
  // only, hiringOrganization as a bare <meta content>, datePosted in Java's
  // Date.toString() form, the place in streetAddress alone — and the vendor
  // named nowhere but in the host its scripts load from.
  const SF = "https://careers.contoso.com/job/Singapore-AVP%2C-Software-Engineer/1234567890/";
  const j = J.read(pageDoc([
    node("div", { itemscope: "", itemtype: "http://schema.org/JobPosting" }, [
      node("span", { itemprop: "title" }, ["AVP, Software Engineer"]),
      node("meta", { itemprop: "hiringOrganization", content: "Contoso" }),
      node("meta", { itemprop: "datePosted", content: "Thu Sep 24 00:00:00 UTC 2026" }),
      node("meta", { itemprop: "validThrough", content: "Tue Dec 01 16:00:00 UTC 2026" }),
      node("div", { itemprop: "jobLocation", itemscope: "", itemtype: "http://schema.org/Place" }, [
        node("div", { itemprop: "address", itemscope: "", itemtype: "http://schema.org/PostalAddress" }, [
          node("meta", { itemprop: "streetAddress", content: "Singapore, SG" })])]),
      node("span", { itemprop: "description" }, ["Design and build trading platform services."]),
    ]),
    node("script", { src: "//rmkcdn.successfactors.com/0a1b2c3d/js/app.js" }),
  ], "AVP, Software Engineer Job Details | Contoso"), makeLoc(SF));
  check("microdata (SuccessFactors shape): every field", fields(j), {
    platform_job_id: "careers.contoso.com/1234567890", url: SF, company: "Contoso",
    title: "AVP, Software Engineer", jd_text: "Design and build trading platform services.",
    location: "Singapore, SG", posted_label: "24 Sep 2026", salary_raw: null,
    work_type: null, ats: "successfactors" });
  check("microdata: title_source", j._prov.title_source, "microdata");
  check("microdata: vendor recorded as the layout", j._prov.layout, "successfactors");
}
{
  // Teamtailor and JazzHR together: an Organization block before the
  // JobPosting, a @graph, entity-ENCODED HTML (decoded twice), a trailing space
  // on the name (Personio), a jobLocation array whose first entry is all empty
  // strings, an ISO datePosted with an offset, employmentType as an array, and
  // Ashby's numeric baseSalary with Workable's string minValue.
  const j = J.read(pageDoc([
    ld({ "@type": "Organization", name: "Contoso" }),
    ld({ "@context": "http://schema.org/", "@graph": [
      { "@type": "WebPage", name: "Careers" },
      { "@type": "JobPosting", title: "SMB Account Executive",
        hiringOrganization: { name: "Contoso " },
        description: "&lt;h4&gt;About us&lt;/h4&gt;&lt;p&gt;We sell R&amp;amp;D tools.&lt;/p&gt;",
        jobLocation: [{ address: { addressLocality: "", addressRegion: "", addressCountry: "" } },
                      { address: { addressLocality: "Singapore", addressCountry: "SG" } }],
        datePosted: "2026-01-18T19:56:45+01:00", employmentType: ["FULL_TIME"],
        baseSalary: { "@type": "MonetaryAmount", currency: "SGD",
                      value: { "@type": "QuantitativeValue", minValue: "134400.00",
                               maxValue: 176400, unitText: "YEAR" } } }] }),
  ]), makeLoc("https://contoso.teamtailor.com/jobs/7069638-smb-account-executive"));
  check("@graph, encoded HTML, arrays, salary: every field", fields(j), {
    platform_job_id: "contoso.teamtailor.com/7069638",
    url: "https://contoso.teamtailor.com/jobs/7069638-smb-account-executive",
    company: "Contoso", title: "SMB Account Executive",
    jd_text: "About us\n\nWe sell R&D tools.", location: "Singapore, SG",
    posted_label: "18 Jan 2026", salary_raw: "SGD 134,400 – 176,400 per year",
    work_type: "Full time", ats: "teamtailor" });
}
{
  // Greenhouse publishes NO JobPosting; the employer's site embeds its form
  // in an iframe. The <h1> is the job, the tab title wraps it.
  const j = J.read(pageDoc([
    node("meta", { property: "og:title", content: "Job Application for Staff Engineer at Northwind Labs" }),
    node("meta", { property: "og:site_name", content: "Northwind Labs" }),
    node("h1", {}, ["Staff Engineer"]),
    node("iframe", { src: "https://boards.greenhouse.io/embed/job_app?for=northwind&token=4377390009" }),
  ], "Northwind Labs - Staff Engineer"), makeLoc("https://careers.northwind.example/jobs?gh_jid=4377390009"));
  check("no JobPosting: <h1> title, og:site_name company, no JD", fields(j), {
    platform_job_id: "careers.northwind.example/4377390009",
    url: "https://careers.northwind.example/jobs?gh_jid=4377390009",
    company: "Northwind Labs", title: "Staff Engineer", jd_text: null, location: null,
    posted_label: null, salary_raw: null, work_type: null, ats: "greenhouse" });
  check("no JobPosting: title_source says which fallback answered", j._prov.title_source, "h1");
}
{
  // A hand-templated block with a raw newline inside a string — illegal JSON,
  // common in the wild — still reads; and a page listing several postings
  // picks the one whose url is this page.
  const raw = '{"@type":"JobPosting","title":"Data\nEngineer","hiringOrganization":"Fabrikam"}';
  const j = J.read(pageDoc([ld(raw)]), makeLoc("https://careers.fabrikam.example/jobs/88"));
  check("raw control character in JSON-LD: recovered", j && [j.title, j.company], ["Data Engineer", "Fabrikam"]);
  const two = J.read(pageDoc([ld([
    { "@type": "JobPosting", title: "First", url: "https://careers.fabrikam.example/jobs/1" },
    { "@type": "JobPosting", title: "Second", url: "https://careers.fabrikam.example/jobs/2" }])]),
    makeLoc("https://careers.fabrikam.example/jobs/2"));
  check("several postings: the one whose url is this page", two.title, "Second");
}
{
  check("a page naming no job at all: null, so capture's guard fires",
        J.read(pageDoc([]), makeLoc("https://careers.contoso.com/")), null);
  check("atsOfUrl: JazzHR's applytojob host", J.atsOfUrl("https://contoso.applytojob.com/apply/x"), "jazzhr");
  check("atsOfUrl: an employer's own host is no vendor", J.atsOfUrl("https://careers.contoso.com/"), null);
  check("htmlToText: flattened text with entities (Workday)",
        J.htmlToText("Design &amp; build   services &nbsp;today"), "Design & build services today");
}

console.log("\nanswers.js: a captcha's hidden response field is machinery, not a question");
{
  // Lever's hCaptcha writes its response field INSIDE the application form.
  // It is unrendered and named only by its own name attribute — labelFor()'s
  // last fallback — so the token would have been filed as an answer.
  const token = node("textarea", { name: "h-captcha-response", value: "P1_eyJ0eXAiOiJKV1Qi.token" });
  token.getClientRects = () => [];
  // A HIDDEN control with a REAL label must survive: the rebuilt Easy Apply
  // hides its native checkboxes behind a labelled ARIA wrapper.
  const box = node("input", { type: "checkbox", id: "tc", checked: true });
  box.getClientRects = () => [];
  const form = node("form", {}, [
    node("label", { for: "nm" }, ["Full name"]), node("input", { type: "text", id: "nm", value: "Jane Doe" }),
    node("div", { role: "checkbox", "aria-label": "I agree to the privacy notice" }, [box]),
    token,
  ]);
  check("the captcha token is skipped; a hidden control with its own label is kept",
        sweepOf(form), [
          { question: "Full name", answer: "Jane Doe", type: "text" },
          { question: "I agree to the privacy notice", answer: "Yes", type: "checkbox" }]);
}

console.log("\njobposting.js sameJob: linking an ATS submit to the external apply that opened it");
{
  // The board and the ATS word one role differently; the rule must link the
  // first pair and refuse the rest (background.js:takeExternal).
  const cases = [
    ["Software Engineer (Real-time Collaborative Platform – Full Stack)", "Software Engineer", true],
    ["Senior Backend Engineer", "Senior Backend Engineer - Singapore", true],
    ["AVP, Software Engineer", "AVP Software Engineer", true],
    ["Data Engineer", "Senior Frontend Engineer", false],
    // shares exactly half its words and is two different roles (matching.md)
    ["Senior AI Engineer", "Agentic AI Engineer", false],
    // a one-word title is inside every title, so containment needs two words
    ["Engineer", "Senior Platform Engineer", false],
    ["", "Software Engineer", false],
  ];
  for (const [a, b, want] of cases) check(`sameJob ${JSON.stringify(a)} ~ ${JSON.stringify(b)}`, J.sameJob(a, b), want);
}
{
  // A fallback title is marked weak — capture.js lets this job's keyed stash
  // (the listing's JSON-LD) replace it. Lever's apply page, as read live:
  // no JobPosting, no <h1>, the title only in an <h2> and the tab title.
  const j = J.read(pageDoc([node("h2", {}, ["Software Engineer"])], "Contoso - Software Engineer"),
                   makeLoc("https://jobs.lever.co/contoso/53e23908-0da6-47a5-a482-39be676e9ee6/apply"));
  check("fallback title is read, and marked weak", [j.title, j._prov.weak],
        ["Contoso - Software Engineer", ["title"]]);
  const k = J.read(pageDoc([ld({ "@type": "JobPosting", title: "Software Engineer",
                                 hiringOrganization: "Contoso" })]),
                   makeLoc("https://jobs.lever.co/contoso/53e23908-0da6-47a5-a482-39be676e9ee6"));
  check("a structured read has nothing weak", k._prov.weak, []);
}

console.log("\njobposting.js pickListed: which remembered job a submit belongs to");
{
  // The measured case (24 Sep 2026, placeholder names): an employer's career
  // site sends the candidate to its SuccessFactors form in the same tab, and
  // the form's <h1> is the listing's title with the requisition number
  // appended. Two sibling roles were browsed in that tab first.
  const entry = (title, id) => ({ at: 1, job: { title, platform_job_id: id } });
  const platformRole = entry("VP - Platform AI Engineer", "careers.contoso.com/1000001");
  const appliedRole = entry("VP - Applied AI Engineer", "careers.contoso.com/1000002");
  const pick = (entries, title) => { const p = J.pickListed(entries, title); return p && [p.entry.job.platform_job_id, p.byTitle]; };
  check("the form's '… (1234)' title picks its own listing, not the sibling role",
        pick([appliedRole, platformRole], "VP - Platform AI Engineer (1234)"), ["careers.contoso.com/1000001", true]);
  check("…and the sibling's form picks the sibling",
        pick([appliedRole, platformRole], "VP - Applied AI Engineer (1235)"), ["careers.contoso.com/1000002", true]);
  check("a title matching NO listing links nothing (browsed on to another job)",
        pick([appliedRole], "Head of Data Platform (1299)"), null);
  check("a title matching TWO listings links nothing, however likely either is",
        pick([appliedRole, entry("VP - Applied AI Engineer", "careers.contoso.com/77")], "VP - Applied AI Engineer"), null);
  check("no title and one listing: the tab relationship alone", pick([appliedRole], null),
        ["careers.contoso.com/1000002", false]);
  check("no title and two listings: nothing", pick([appliedRole, platformRole], null), null);
  check("an empty list: nothing", pick([], "VP - Applied AI Engineer"), null);
}
{
  const site = (u) => { const s = J.siteOf(u); return s && [s.host, s.pattern]; };
  check("siteOf: one host, both schemes, nothing wider",
        site("https://Careers.Contoso.com/job/Engineer/42/?locale=en_GB"),
        ["careers.contoso.com", "*://careers.contoso.com/*"]);
  check("siteOf: chrome:// cannot be enabled", site("chrome://extensions/"), null);
  check("siteOf: file:// cannot be enabled", site("file:///C:/jobs.html"), null);
  check("siteOf: garbage", site("not a url"), null);
}

console.log("\njobposting.js: an employer the capture could not name");
{
  // SuccessFactors' form <h1> appends the requisition number (measured live
  // 24 Sep 2026: "AVP, Software Engineer (1234)", address career_job_req_id=1234).
  const SF = "https://career10.successfactors.com/portalcareer?company=Contoso&career_ns=job_application&career_job_req_id=1234";
  const id = J.idFrom(SF);
  check("the page's own requisition number is stripped",
        J.stripRequisition("AVP, Software Engineer (1234)", id), "AVP, Software Engineer");
  check("…in square brackets, or written 'Req #1234', too",
        [J.stripRequisition("AVP, Software Engineer [1234]", id),
         J.stripRequisition("AVP, Software Engineer (Req #1234)", id)],
        ["AVP, Software Engineer", "AVP, Software Engineer"]);
  check("a number that is NOT this page's id stays: it may be part of the name",
        J.stripRequisition("Graduate Programme (2027)", id), "Graduate Programme (2027)");
  check("words in brackets stay: 'Engineer (Backend)' and 'Engineer (Web)' are two jobs",
        J.stripRequisition("Senior Engineer (Backend)", id), "Senior Engineer (Backend)");
  check("a title that IS only the number is left alone",
        J.stripRequisition("(1234)", id), "(1234)");
  const j = J.read(pageDoc([node("h1", {}, ["AVP, Software Engineer (1234)"])],
                           "Career Opportunities: Apply for AVP, Software Engineer (1234)"), makeLoc(SF));
  check("read(): the form's title comes out bare, and says what it was",
        [j.title, j._prov.title_stripped], ["AVP, Software Engineer", "AVP, Software Engineer (1234)"]);
}

console.log("\njobposting.js pageId: the job's id when the address has lost it (28 Sep 2026)");
{
  // After any postback SuccessFactors' form sits at /portalcareer?_s.crb=<a
  // session crumb>: the URL rule falls back to path plus query, an id that
  // changes at every sign-in, and 28 Sep's session timed out mid-form. The
  // page still prints the requisition in its <h1> and tab title
  // (docs/career-sites.md §16).
  const form = (crumb) => [
    pageDoc([node("h1", {}, ["Principal AI Engineer (51234)"])],
            "Career Opportunities: Apply for Principal AI Engineer (51234)"),
    makeLoc(`https://career2.successfactors.eu/portalcareer?_s.crb=${crumb}`)];
  check("idFrom says when it fell back",
        J.idFrom("https://career2.successfactors.eu/portalcareer?_s.crb=AbC%3d").by, "path");
  check("pageId: the printed requisition, on the ATS's host, with no query kept",
        J.pageId(...form("AbC%3d")),
        { platform_job_id: "career2.successfactors.eu/51234",
          url: "https://career2.successfactors.eu/portalcareer", by: "page" });
  check("…the same id after a sign-in hands out a new crumb",
        J.pageId(...form("XyZ%2f")).platform_job_id, J.pageId(...form("AbC%3d")).platform_job_id);
  // The first address names the tenant (P2: SuccessFactors ids carry it, one
  // host serving many employers); the postback page gets it as a hint from
  // the visit's earlier page (generic.js keeps it in sessionStorage).
  check("…and, with the tenant an earlier page named, the same id the form's first address gives",
        [J.pageId(...form("AbC%3d"), { tenant: "LitwareBK" }).platform_job_id,
         J.idFrom("https://career2.successfactors.eu/career?company=litwarebk&career_ns=job_application&career_job_req_id=51234")
           .platform_job_id],
        ["career2.successfactors.eu/litwarebk/51234", "career2.successfactors.eu/litwarebk/51234"]);
  check("…a hint that is not tenant-shaped is ignored",
        J.pageId(...form("AbC%3d"), { tenant: "a b/c" }).platform_job_id, "career2.successfactors.eu/51234");
  const titleOnly = J.pageId(pageDoc([], "Career Opportunities: Apply for Principal AI Engineer (51234)"),
                             makeLoc("https://career2.successfactors.eu/portalcareer?_s.crb=AbC%3d"));
  check("…from the tab title alone when there is no <h1>", titleOnly.platform_job_id,
        "career2.successfactors.eu/51234");
  const j = J.read(...form("AbC%3d"));
  check("read(): the requisition leaves the title, the crumb leaves the address",
        [j.platform_job_id, j.url, j.title, j.ats],
        ["career2.successfactors.eu/51234", "https://career2.successfactors.eu/portalcareer",
         "Principal AI Engineer", "successfactors"]);
  // Where it must NOT act.
  const grad = J.pageId(pageDoc([node("h1", {}, ["Graduate Programme (2027)"])]),
                        makeLoc("https://careers.contoso.com/jobs/graduate-programme"));
  check("not on an ATS's host: a title's '(2027)' is not an id",
        grad.platform_job_id, "careers.contoso.com/jobs/graduate-programme");
  const wd = J.pageId(pageDoc([node("h1", {}, ["Senior Engineer (12345)"])]),
                      makeLoc("https://contoso.wd3.myworkdayjobs.com/Contoso/job/Engineer_R120291/apply"));
  check("an address that carries its id keeps it", wd.platform_job_id, "contoso.wd3.myworkdayjobs.com/r120291");
  const signIn = J.pageId(pageDoc([node("h1", {}, ["Sign In"])], "Career Opportunities: Sign In"),
                          makeLoc("https://career2.successfactors.eu/careers?company=litwarebk"));
  check("an ATS page that prints no number keeps the URL's id",
        [signIn.platform_job_id, signIn.by], ["career2.successfactors.eu/careers?company=litwarebk", "path"]);
  check("tenantOf: SuccessFactors' company= parameter, lower-cased",
        J.tenantOf("https://career2.successfactors.eu/careers?company=LitwareBK"), "litwarebk");
  check("tenantOf: a company= parameter on any other host is not a tenant",
        J.tenantOf("https://careers.contoso.com/jobs?company=northwind"), null);
  check("stripRequisition reads a tenant-carrying id's LAST segment",
        J.stripRequisition("Principal AI Engineer (51234)",
          J.idFrom("https://career2.successfactors.eu/career?company=litwarebk&career_job_req_id=51234")),
        "Principal AI Engineer");
}

console.log("\njobposting.js: the handoff from a listing to its hiring system (P2, 28 Sep 2026)");
{
  // The Career Site Builder listing of 28 Sep 2026, placeholder names: its
  // JobPosting scope holds ONLY the description; the title sits outside it;
  // no hiringOrganization, no og:site_name; the tab title names the owner;
  // and the site's inline config says where Apply hands over.
  const LIST = "https://jobs.litwarebank.com/job/Principal-AI-Engineer/51234-en_GB?&feedid=363857";
  const listing = () => pageDoc([
    node("span", { itemprop: "title" }, ["Principal AI Engineer"]),
    node("div", { itemscope: "", itemtype: "http://schema.org/JobPosting" },
         [node("span", { itemprop: "description" }, ["Job Summary. Design the data pipelines."])]),
    node("meta", { property: "og:title", content: "Principal AI Engineer" }),
    node("script", {}, ["var j2w = {}; j2w.init({ companyId: 'litwarebk', jobAlertEnabled: 'true' });"]),
    node("script", {}, [`{"ssoCompanyId" : 'litwarebk', "ssoUrl" : 'https://career2.successfactors.eu'}`]),
    node("script", { src: "//rmkcdn.successfactors.com/0a1b2c3d/js/app.js" }),
  ], "Principal AI Engineer Job Details | Litware Bank");
  const j = J.read(listing(), makeLoc(LIST));
  check("a JobPosting holding only the description still makes the page a listing",
        [j._prov.structured, j._prov.title_source, j.title, j.jd_text],
        [true, "og", "Principal AI Engineer", "Job Summary. Design the data pipelines."]);
  check("the employer, from the tab title's owner, marked weak",
        [j.company, j._prov.weak.includes("company")], ["Litware Bank", true]);
  check("atsHandoff: the data centre and tenant from the inline config",
        J.atsHandoff(listing()), { atsHost: "career2.successfactors.eu", tenant: "litwarebk" });
  check("atsHandoff: a page that says nothing of the kind gives null",
        J.atsHandoff(pageDoc([node("script", {}, ["var x = 1;"])])), null);
  check("hasPosting: the listing publishes one; a plain page does not",
        [J.hasPosting(listing()), J.hasPosting(pageDoc([node("h1", {}, ["Hi"])]))], [true, false]);
  check("a plain page is not structured",
        J.read(pageDoc([node("h1", {}, ["Engineer"])]), makeLoc("https://careers.contoso.com/x")) ._prov.structured,
        false);
  check("siteOwner: a trailing 'Careers' is the site's name, not the employer's",
        J.siteOwner("Senior Engineer | Contoso Careers", "Senior Engineer"), "Contoso");
  check("siteOwner: a page word, no pipe, or the job's own title is no owner",
        [J.siteOwner("Senior Engineer | Careers", "Senior Engineer"), J.siteOwner("Senior Engineer", null),
         J.siteOwner("Contoso | Senior Engineer", "Senior Engineer")], [null, null, null]);
  const onAts = J.read(pageDoc([node("h1", {}, ["Engineer"])], "Engineer | Fabrikam Talent"),
                       makeLoc("https://jobs.lever.co/fabrikam/53e23908-0da6-47a5-a482-39be676e9ee6"));
  check("on a hiring system's host the tab title's owner is never the company", onAts.company, null);

  // pickDeparture: which listing the hiring system's first page binds to.
  const NOW = 1_800_000_000_000;
  const entry = (url, extra = {}, ago = 60_000) => ({ at: NOW - ago, job: { url, title: "Principal AI Engineer", ...extra } });
  const csb = entry(LIST, { ats: "successfactors", handoff: { atsHost: "career2.successfactors.eu", tenant: "litwarebk" } });
  const sfPage = { host: "career2.successfactors.eu", vendor: "successfactors", tenant: "litwarebk" };
  const pd = (op, own, page = sfPage) => { const r = J.pickDeparture(op, own, page, NOW); return r && [r.via, r.entry.job.url]; };
  check("same tab: the listing the tab showed last binds", pd(null, [csb]), ["tab", LIST]);
  check("…refused when the listing hands over to another tenant",
        pd(null, [csb], { ...sfPage, tenant: "relecloud" }), null);
  check("…or to another data centre", pd(null, [csb], { ...sfPage, host: "career10.successfactors.com" }), null);
  check("…or names another vendor",
        pd(null, [entry(LIST, { ats: "workday" })]), null);
  check("…and a listing on the hiring system's own host is not a handoff (the keyed stash has it)",
        pd(null, [entry("https://career2.successfactors.eu/careers?career_ns=job_listing&company=litwarebk")]), null);
  check("only the MOST RECENT listing is considered: an older one is a guess",
        pd(null, [entry("https://jobs.fabrikam.com/job/9", { ats: "workday" }), csb]), null);
  const board = entry("https://www.linkedin.com/jobs/view/4400000001/", { platform: "linkedin" }, 20_000);
  check("a fresh opener entry (a job board's external click) wins over the tab's own",
        pd([board], [csb]), ["opener", "https://www.linkedin.com/jobs/view/4400000001/"]);
  check("…but a stale one does not: the tab's own listing binds",
        pd([entry("https://www.linkedin.com/jobs/view/4400000001/", {}, 20 * 60_000)], [csb]), ["tab", LIST]);
  check("nothing remembered: nothing binds", pd(null, []), null);

  // handoffFits: does a submit belong to its tab's binding?
  const b = { job: { title: "Principal AI Engineer" }, host: "career2.successfactors.eu",
              atsJobId: "career2.successfactors.eu/litwarebk/51234" };
  check("handoffFits: same host, same job id", J.handoffFits(b, { host: b.host, atsJobId: b.atsJobId }), true);
  check("handoffFits: an id on only one side does not refuse",
        [J.handoffFits({ ...b, atsJobId: null }, { host: b.host, atsJobId: b.atsJobId }),
         J.handoffFits(b, { host: b.host, atsJobId: null })], [true, true]);
  check("handoffFits: the tab went on to another job's form",
        J.handoffFits(b, { host: b.host, atsJobId: "career2.successfactors.eu/litwarebk/51232" }), false);
  check("handoffFits: another host, or no binding",
        [J.handoffFits(b, { host: "career10.successfactors.com", atsJobId: null }), J.handoffFits(null, { host: b.host })],
        [false, false]);

  // One job, two ids on one host (SmartRecruiters, 30 Sep 2026): the listing
  // is …/744000100000042 and its form the publication UUID. The live submit's
  // binding held the first, the form sent the second, the binding was refused
  // and the job was filed twice. The form page was reached FROM the listing
  // (its referrer, measured live) and is no listing itself.
  const SRH = "jobs.smartrecruiters.com";
  const LISTING = `${SRH}/744000100000042`, FORM = `${SRH}/7c1e5a90-2b4d-4f6e-9a3b-0d5e8f1c2a47`;
  const sr = { job: { title: "GenAI Engineer" }, host: SRH, atsJobId: LISTING };
  const formPage = { host: SRH, atsJobId: FORM, fromId: LISTING, listing: false };
  check("the live submit, refused: the form's own id against the listing's",
        J.handoffFits(sr, { host: SRH, atsJobId: FORM, title: "GenAI Engineer" }), false);
  check("learnsAlias: a form reached from the bound listing is the same job under a second id",
        J.learnsAlias(sr, formPage), true);
  check("…not a LISTING reached from it: a similar job's page is another job",
        J.learnsAlias(sr, { ...formPage, listing: true }), false);
  check("…not a page reached from a page the binding does not know, or from nowhere",
        [J.learnsAlias(sr, { ...formPage, fromId: `${SRH}/744000100000099` }),
         J.learnsAlias(sr, { ...formPage, fromId: null })], [false, false]);
  check("…not on another host, not an id it already knows, not with no binding",
        [J.learnsAlias(sr, { ...formPage, host: "jobs.lever.co" }),
         J.learnsAlias(sr, { ...formPage, atsJobId: LISTING }), J.learnsAlias(null, formPage)],
        [false, false, false]);
  const learned = { ...sr, aliases: [FORM] };
  check("handoffFits: the learned id fits when the form's title is the job's",
        J.handoffFits(learned, { host: SRH, atsJobId: FORM, title: "GenAI Engineer" }), true);
  check("…and not under another title, or none: an alias fits only where the title agrees",
        [J.handoffFits(learned, { host: SRH, atsJobId: FORM, title: "Senior Data Engineer" }),
         J.handoffFits(learned, { host: SRH, atsJobId: FORM, title: null })], [false, false]);
  check("…a third id still refuses, and the listing's own id still fits with no title",
        [J.handoffFits(learned, { host: SRH, atsJobId: `${SRH}/0badf00d-0000-4000-8000-000000000000`,
                                  title: "GenAI Engineer" }),
         J.handoffFits(learned, { host: SRH, atsJobId: LISTING })], [false, true]);

  // A site the user enabled: an employer's own domain, under which
  // Eightfold's candidate site runs (read live 2 Oct 2026). Only the opener
  // binds there, and a binding whose id is not known on both sides fits only
  // where the titles agree.
  const EF = "careers.fabrikam.com";
  const efPage = { host: EF, vendor: "eightfold", tenant: null, site: true };
  const elsewhere = entry("https://jobs.contoso.com/job/7");
  check("a site binds from a fresh opener", pd([board], [elsewhere], efPage),
        ["opener", "https://www.linkedin.com/jobs/view/4400000001/"]);
  check("…never from the tab's own list, which a hiring system's own host would take",
        [pd(null, [elsewhere], efPage), pd(null, [elsewhere], { ...efPage, site: false })],
        [null, ["tab", "https://jobs.contoso.com/job/7"]]);
  const A = `${EF}/446700000001`, B2 = `${EF}/446700000002`;
  const siteB = { job: { title: "Senior Platform Engineer" }, host: EF, atsJobId: null, site: true };
  check("handoffFits on a site with no id: the title must be the bound job's",
        [J.handoffFits(siteB, { host: EF, atsJobId: A, title: "Senior Platform Engineer" }),
         J.handoffFits(siteB, { host: EF, atsJobId: B2, title: "Data Scientist" }),
         J.handoffFits(siteB, { host: EF, atsJobId: null, title: null })], [true, false, false]);
  check("…with an id on both sides, the ids decide",
        [J.handoffFits({ ...siteB, atsJobId: A }, { host: EF, atsJobId: A, title: "Data Scientist" }),
         J.handoffFits({ ...siteB, atsJobId: A }, { host: EF, atsJobId: B2, title: "Senior Platform Engineer" })],
        [true, false]);

  // rebind: what a tab's binding becomes when one of its pages says where it
  // is (background.js:claimHandoff), given what pickDeparture chose.
  const li = { url: "https://www.linkedin.com/jobs/view/4400000001/", platform_job_id: "4400000001",
               title: "Senior Platform Engineer" };
  const chose = (job) => ({ entry: { job }, via: "opener" });
  const first = J.rebind(undefined, chose(li), { ...efPage, atsJobId: A }, NOW);
  check("rebind: a site's job page binds the opener's job with the page's own id, marked a site",
        [first.host, first.atsJobId, first.site, first.job.url], [EF, A, true, li.url]);
  check("…a second job's page in the same tab, the opener still fresh: the first job's binding stays",
        J.rebind(first, chose(li), { ...efPage, atsJobId: B2 }, NOW), null);
  check("…a page with no id keeps the binding's",
        J.rebind(first, chose(li), { ...efPage, atsJobId: null }, NOW).atsJobId, A);
  check("…a later page's id fills one the binding lacks; with nothing chosen and nothing to learn, no change",
        [J.rebind({ ...first, atsJobId: null }, null, { ...efPage, atsJobId: A }, NOW).atsJobId,
         J.rebind(first, null, { ...efPage, atsJobId: null }, NOW)], [A, null]);
  // A career site's listing (a site binding with the listing's number), then
  // its hiring system on ANOTHER host: that number is not the form's id.
  const onCsb = J.rebind(undefined, chose(li), { host: "jobs.litwarebank.com", vendor: "successfactors",
                                                 site: true, atsJobId: "jobs.litwarebank.com/51234" }, NOW);
  const onSf = J.rebind(onCsb, chose(li), { ...sfPage, atsJobId: "career2.successfactors.eu/litwarebk/51234" }, NOW);
  check("…on another host the binding takes that host's id, not the site's, and is a site binding no more",
        [onSf.host, onSf.atsJobId, !!onSf.site],
        ["career2.successfactors.eu", "career2.successfactors.eu/litwarebk/51234", false]);

  // atsCandidates (P4): what a listing BELIEVES its hiring system holds, for
  // the server to look up and never store.
  const h = { atsHost: "career2.successfactors.eu", tenant: "litwarebk" };
  check("atsCandidates: the listing's own number under its handover",
        J.atsCandidates({ platform_job_id: "jobs.litwarebank.com/51234" }, h),
        ["career2.successfactors.eu/litwarebk/51234"]);
  check("atsCandidates: none without a handover, with half of one, or with a number-less id",
        [J.atsCandidates({ platform_job_id: "jobs.litwarebank.com/51234" }, null),
         J.atsCandidates({ platform_job_id: "jobs.litwarebank.com/51234" }, { atsHost: h.atsHost, tenant: null }),
         J.atsCandidates({ platform_job_id: "careers.contoso.com/jobs/senior-engineer" }, h)],
        [[], [], []]);
}
{
  const at = (href) => makeLoc(href);
  const s = (href, ref) => { const r = J.suggestCompany(at(href), ref); return [r.name, r.site]; };
  const SF = "https://career10.successfactors.com/portalcareer?company=Contoso&career_ns=job_application";
  check("the tenant in the address names it; the listing's site is the one to turn on",
        s(SF, "https://careers.contoso.com/"), ["Contoso", "careers.contoso.com"]);
  check("after a postback (no tenant): the brand from the career site's own host",
        s("https://career10.successfactors.com/portalcareer?_s.crb=x", "https://careers.contoso.com/"),
        ["contoso", "careers.contoso.com"]);
  check("a tenant CODE is not a name",
        s("https://career10.successfactors.com/portalcareer?company=C0001234567P", ""), [null, null]);
  check("a job board's referrer names the board, not the employer",
        s("https://jobs.lever.co/contoso/x/apply", "https://www.linkedin.com/"), [null, null]);
  check("a hiring system's own referrer (its sign-in page) names nothing",
        s("https://career10.successfactors.com/portalcareer?_s.crb=x", "https://career10.successfactors.com/careers"),
        [null, null]);
  check("no referrer and no tenant: nothing to suggest",
        s("https://apply.workable.com/contoso/j/B4A1D41ABA/apply/", ""), [null, null]);
}

console.log("\njobposting.js matchPatternRegex: which pages the icon (and the popup) call covered");
{
  // One translation from Chrome match patterns to regexes, for the toolbar
  // icon's declarativeContent rule and the popup. The cases pin match-pattern
  // semantics, including the two a naive suffix test gets wrong.
  const cover = (p, url) => new RegExp(J.matchPatternRegex(p)).test(url);
  const cases = [
    ["*://*.linkedin.com/*", "https://www.linkedin.com/jobs/view/1/", true],
    ["*://*.linkedin.com/*", "https://linkedin.com/", true],
    ["*://*.linkedin.com/*", "https://evil-linkedin.com/jobs/", false],
    ["*://*.linkedin.com/*", "https://www.linkedin.com.evil.example/", false],
    ["*://*.successfactors.com/portalcareer*", "https://career10.successfactors.com/portalcareer?_s.crb=x", true],
    ["*://*.successfactors.com/career*", "https://career10.successfactors.com/careers?company=x", true],
    ["*://*.successfactors.com/career*", "https://performancemanager10.successfactors.com/sf/home", false],
    ["*://jobs.lever.co/*", "http://jobs.lever.co/contoso/x", true],
    ["*://jobs.lever.co/*", "https://jobs.lever.co.evil.example/", false],
    ["http://127.0.0.1/*", "http://127.0.0.1:8000/captures", true],
    ["*://careers.contoso.com/*", "https://careers.contoso.com/job/Engineer/42/", true],
  ];
  for (const [p, url, want] of cases) check(`${p} ~ ${url}`, cover(p, url), want);
  // Loop the REGISTRY, not a hand-picked list: every pattern the manifest
  // injects on must translate, or the icon stays grey on a covered site.
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "extension/manifest.json"), "utf8"));
  const bad = manifest.content_scripts.flatMap((cs) => cs.matches)
    .filter((p) => !J.matchPatternRegex(p));
  check("every manifest content-script pattern translates", bad, []);
}

console.log("\nmanifest: every origin the extension asks Chrome for is one it declared (28 Sep 2026)");
{
  // Chromium's rule: ONE declared optional pattern must contain the whole
  // request, every scheme of it (extensions/common/url_pattern.cc
  // URLPattern::Contains, applied per pattern by URLPatternSet::ContainsPattern
  // in permissions_api_helpers.cc). Anything else is refused before any
  // prompt: "Only permissions specified in the manifest may be requested".
  // "Always capture on <site>" asked for *://host/*, both schemes, against
  // https://*/* and http://*/* declared separately, so it never once worked,
  // and the popup, with no catch, said nothing (docs/career-sites.md §16).
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "extension/manifest.json"), "utf8"));
  const parts = (p) => { const m = /^([^:]+):\/\/([^/]+)(\/.*)$/.exec(p); return m && [m[1], m[2], m[3]]; };
  const schemes = (s) => (s === "*" ? ["http", "https"] : [s]);
  const hostIn = (o, i) => o === "*" || o === i || (o.startsWith("*.") && (i === o.slice(2) || i.endsWith(o.slice(1))));
  const pathIn = (o, i) => new RegExp("^" + o.split("*").map((x) => x.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
    .join(".*") + "$").test(i.replace(/\*$/, ""));
  const contains = (outer, inner) => {
    const [os, oh, op] = parts(outer), [is, ih, ip] = parts(inner);
    return schemes(is).every((s) => schemes(os).includes(s)) && hostIn(oh, ih) && pathIn(op, ip);
  };
  check("the containment rule itself: https://*/* does NOT contain *://host/*",
        contains("https://*/*", "*://careers.contoso.com/*"), false);
  const declared = (req) => manifest.optional_host_permissions.some((o) => contains(o, req));
  for (const url of ["https://jobs.litwarebank.com/job/Principal-AI-Engineer/51234-en_GB",
                     "http://careers.contoso.com/jobs/7", "https://careers.contoso.com/"]) {
    check(`"Always capture on" for ${url} asks for a declared origin`, declared(J.siteOf(url).pattern), true);
  }
  check("…and so does the options page's remote tracker server", declared("https://tracker.contoso.com/*"), true);
}

console.log("\njobposting.js read: a Greenhouse job-board page with no JobPosting (read live 29 Sep 2026)");
{
  // No JSON-LD, no og:site_name; the employer only in the tab title ("Job
  // Application for <title> at <company>") and a logo's alt; the JD and the
  // location in .job__description / .job__location; a canonical in http://.
  const GH = "https://job-boards.greenhouse.io/contoso/jobs/4377390009";
  const page = (title, h1 = "Senior Platform Engineer") => pageDoc([
    node("link", { rel: "canonical", href: GH.replace("https:", "http:") }),
    node("img", { alt: "Contoso Logo" }),
    node("h1", {}, [h1]),
    node("div", { class: "job__location" }, ["Singapore"]),
    node("div", { class: "job__description body" }, [
      node("p", {}, ["Contoso builds payment rails for the region."]),
      node("ul", {}, [node("li", {}, ["Own the ledger service in Go."])]),
    ]),
    node("form", { id: "application-form" }, [node("input", { type: "text", id: "first_name" })]),
  ], title);
  const job = J.read(page("Job Application for Senior Platform Engineer at Contoso"), makeLoc(GH));
  check("the employer from the tab title, right after the job's own title (weak)",
        [job.company, job._prov.weak.includes("company")], ["Contoso", true]);
  check("the description and location from the page's own blocks",
        [/payment rails/.test(job.jd_text || "") && /ledger service/.test(job.jd_text || ""), job.location],
        [true, "Singapore"]);
  check("an http:// canonical on an https page keeps https; the id is unchanged",
        [job.url, job.platform_job_id, job.ats], [GH, "job-boards.greenhouse.io/4377390009", "greenhouse"]);
  check("an ' at ' inside the job's own title is not the employer",
        J.read(page("Job Application for Engineer at Scale at Contoso", "Engineer at Scale"), makeLoc(GH)).company,
        "Contoso");
  check("no ' at <company>' after the title, or a tab title without the job's title: no employer",
        [J.read(page("Job Application for Senior Platform Engineer"), makeLoc(GH)).company,
         J.read(page("Careers at Contoso"), makeLoc(GH)).company], [null, null]);
}

/* ------------------------------ adapters/generic.js on an ATS's own pages
 *
 * The application form and its submit, as read LIVE on 24 Sep 2026 from four
 * vendors' real apply pages (read-only — nothing typed or sent): Lever,
 * Greenhouse, Ashby (which has no <form> at all) and Workable. Placeholder
 * names; the structure is the measured one. The sign-in and wizard shapes
 * are the rules' own reasons, not a live read — Workday and SuccessFactors
 * put the form behind a candidate sign-in. */

const GENERIC_SRC = fs.readFileSync(path.join(ROOT, "extension/adapters/generic.js"), "utf8");

// `storage`: a sessionStorage stand-in shared across loads, for a visit whose
// later page reads what an earlier one kept (the tenant hint).
function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
           removeItem: (k) => m.delete(k) };
}

// answers.js loads after generic.js, as the manifest has it on every ATS
// host: rule 2 counts the fields that ask something by the sweep's own
// labelFor (generic.js:asking). `answers: false` is the popup's injection,
// which carries no answers.js.
function loadGeneric(kids, href, title = "", storage = undefined, opts = {}) {
  return genericSandbox(kids, href, title, storage, opts).window.__trackerAdapter;
}

// The same load, returning the whole sandbox: its window also holds
// answers.js's __trackerAnswers, whose take() sweeps the form as a submit does.
function genericSandbox(kids, href, title = "", storage = undefined, { answers = true } = {}) {
  const doc = pageDoc(kids, title);
  const loc = makeLoc(href);
  const sandbox = { URL, URLSearchParams, console, setTimeout: () => 0, CSS: { escape: (s) => s } };
  if (storage) sandbox.sessionStorage = storage;
  sandbox.window = { document: doc, location: loc };
  sandbox.document = doc;
  sandbox.location = loc;
  vm.createContext(sandbox);
  vm.runInContext(JOBPOSTING_SRC, sandbox);
  vm.runInContext(GENERIC_SRC, sandbox);
  if (answers) {
    // What labelFor reaches for: the document a light-DOM node belongs to
    // (getRootNode), its ids, and a place to hang the sweep's listeners.
    for (const n of doc.querySelectorAll("*")) n._doc = doc;
    doc.getElementById = (id) => doc.querySelectorAll("*").find((n) => n.getAttribute("id") === id) || null;
    // Kept by type, so a test can click "Next" (the sweep's click listener).
    doc.addEventListener = (type, fn) => { (doc._on = doc._on || {})[type] = fn; };
    vm.runInContext(ANSWERS_SRC, sandbox);
  }
  return sandbox;
}
const text = (name) => node("input", { type: "text", name });
const file = (name) => node("input", { type: "file", name });
const button = (label, attrs = {}) => node("button", attrs, [label]);

console.log("\ngeneric.js: the application form and its submit, per vendor (read live 24 Sep 2026)");
{
  const LEVER = "https://jobs.lever.co/contoso/53e23908-0da6-47a5-a482-39be676e9ee6";
  const submit = button("Submit application", { id: "btn-submit", type: "button", "data-qa": "btn-submit" });
  const captcha = button("", { id: "hcaptchaSubmitBtn", type: "submit", class: "hidden" });
  const form = node("form", { id: "application-form", method: "POST" },
                    [text("name"), text("email"), file("resume"), node("textarea", { name: "comments" }), captcha, submit]);
  const a = loadGeneric([node("h2", {}, ["Software Engineer"]), form], `${LEVER}/apply`);
  check("Lever: the root is the form that takes the resume", a.answerFormRoot() === form, true);
  check("Lever: button#btn-submit (type=button, posts by script) is the submit", a.isCompletion(submit), true);
  check("Lever: the hidden hCaptcha submit is not", a.isCompletion(captcha), false);
  check("Lever: the listing and its /apply page share one key",
        a.answerFormKey() === loadGeneric([], LEVER).answerFormKey(), true);
}
{
  // Greenhouse: the job page carries the form inline; its own "Apply" button
  // (and "Autofill my application") sit ABOVE the form, and the resume
  // widget's own buttons sit inside it.
  const apply = button("Apply", { type: "button", "aria-label": "Apply" });
  const attach = button("Attach", { type: "button" });
  const submit = button("Submit application", { type: "submit" });
  const form = node("form", { id: "application-form", method: "get" },
                    [text("first_name"), text("email"), file("resume"), attach, file("cover_letter"), submit]);
  const a = loadGeneric([node("h1", {}, ["Forward Deployed Engineer"]), apply, form],
                        "https://job-boards.greenhouse.io/northwind/jobs/4377390009");
  check("Greenhouse: root is the inline form", a.answerFormRoot() === form, true);
  check("Greenhouse: 'Submit application' inside it is the submit", a.isCompletion(submit), true);
  check("Greenhouse: the page's own 'Apply' button, outside the form, is not", a.isCompletion(apply), false);
  check("Greenhouse: the resume widget's 'Attach' is not", a.isCompletion(attach), false);
}
{
  // Ashby: NO <form> element. The controls live in a container with a stable
  // class; the submit is a bare <button> with hashed classes and no type.
  const submit = button("Submit Application", { class: "_button_zyh3g_28 _primary_zyh3g_97" });
  const pane = node("div", { class: "ashby-job-posting-right-pane" },
                    [node("div", {}, [text("name"), text("email")]), node("div", {}, [file("resume"), file("cover")]),
                     node("div", {}, [text("linkedin")]), submit]);
  // …and, as on the live page, reCAPTCHA's hidden response field portalled to
  // <body>, OUTSIDE the pane. Counting it made the whole page the root.
  const captcha = node("textarea", { name: "g-recaptcha-response", class: "g-recaptcha-response" });
  captcha.getClientRects = () => [];
  const a = loadGeneric([node("div", { class: "header" }, [node("h1", {}, ["Senior Solutions Architect"])]), pane,
                         node("div", { class: "grecaptcha-badge" }, [captcha])],
                        "https://jobs.ashbyhq.com/fabrikam/d9b9d44f-0a87-4237-b101-360052373643/application");
  check("Ashby (no <form>): root is the container of every VISIBLE control, not <body>",
        a.answerFormRoot() === pane, true);
  check("Ashby: the typeless 'Submit Application' button is the submit", a.isCompletion(submit), true);
}
{
  // Workable: a data-ui hook on both the form and its submit.
  const submit = button("Submit application", { type: "submit", "data-ui": "apply-button" });
  const form = node("form", { "data-ui": "application-form" }, [text("firstname"), file("resume"), submit]);
  const a = loadGeneric([node("h1", {}, ["Senior Consultant"]), form],
                        "https://apply.workable.com/contoso/j/B4A1D41ABA/apply/");
  check("Workable: root is the application form", a.answerFormRoot() === form, true);
  check("Workable: data-ui='apply-button' is the submit", a.isCompletion(submit), true);
}
console.log("\ngeneric.js: SuccessFactors' signed-in form (read live 24 Sep 2026)");
{
  // form#careerform: 59 fields in COLLAPSED sections (display:none until the
  // candidate opens each), real <label for> labels, no file input (the resume
  // is an attachment widget), and "Save"/"Apply" as <span role=button>. After
  // any postback the address is /portalcareer?_s.crb=… — no career_ns — and
  // that is where a real application went unrecorded.
  const field = (id, lbl) => {
    const input = node("input", { type: "text", id });
    input.getClientRects = () => [];            // inside a closed section
    return [node("label", { for: id }, [lbl]), input];
  };
  const section = node("div", { class: "sectionContentClosed" },
    [...field("fn", "* First Name"), ...field("ln", "* Last Name"), ...field("em", "* Email"),
     ...field("ph", "* Contact Number"), ...field("cc", "* Country Code (e.g. +65)"),
     ...field("np", "Notice period")]);
  const save = node("span", { role: "button", id: "487:_saveBtn", class: "rcmSaveButton" }, ["Save"]);
  const apply = node("span", { role: "button", id: "487:_submitBtn", class: "rcmSaveButton" }, ["Apply"]);
  const form = node("form", { id: "careerform", name: "careerform" },
                    [node("div", { id: "rcmJobApplicationCtr" }, [section, save, apply])]);
  const a = loadGeneric([form], "https://career10.successfactors.com/portalcareer?_s.crb=x");
  check("SuccessFactors after a postback (no career_ns): root is the form", a.answerFormRoot() === form, true);
  check("SuccessFactors: the <span role=button> 'Apply' is the submit", a.isCompletion(apply), true);
  check("SuccessFactors: 'Save' is not", a.isCompletion(save), false);
  check("SuccessFactors: no near miss on the real submit", a.nearMiss(apply), null);
  const b = loadGeneric([form], "https://career10.successfactors.com/portalcareer?career_ns=job_application");
  check("SuccessFactors as first loaded (career_ns): root is still the form", b.answerFormRoot() === form, true);
}
{
  // The answer store and the listing stash are both keyed by answerFormKey().
  // Keyed by the crumb, a sign-in in the middle of the form (28 Sep 2026: a
  // session timeout) started both over. The requisition does not change.
  const t = "Career Opportunities: Apply for Principal AI Engineer (51234)";
  const key = (crumb) => loadGeneric([node("h1", {}, ["Principal AI Engineer (51234)"])],
    `https://career2.successfactors.eu/portalcareer?_s.crb=${crumb}`, t).answerFormKey();
  // The id the server keeps on the JOB (docs/career-sites.md §16.3 item 3):
  // the page's own, only on the vendor's host, and only a real id. A link to a
  // job board's record replaces the page's identity, so this travels apart.
  const sf = loadGeneric([node("h1", {}, ["Principal AI Engineer (51234)"])],
    "https://career2.successfactors.eu/portalcareer?_s.crb=AbC%3d", t);
  check("atsJobId: the requisition on SuccessFactors' form", sf.atsJobId(), "career2.successfactors.eu/51234");
  const crumbOnly = loadGeneric([node("h1", {}, ["Sign In"])],
    "https://career2.successfactors.eu/portalcareer?_s.crb=AbC%3d", "Career Opportunities: Sign In");
  check("atsJobId: a page whose only id is its crumb gives none", crumbOnly.atsJobId(), null);
  check("atsJobId: Workday's, from its address on every step",
        loadGeneric([], "https://contoso.wd3.myworkdayjobs.com/en-US/Contoso/job/Engineer_R200001/apply/autofillWithResume")
          .atsJobId(), "contoso.wd3.myworkdayjobs.com/r200001");
  check("atsJobId: an employer's own site is not a hiring system",
        loadGeneric([], "https://jobs.litwarebank.com/job/Principal-AI-Engineer/51234-en_GB").atsJobId(), null);
  check("SuccessFactors: the answers' key is the requisition, through a new crumb",
        [key("AbC%3d"), key("XyZ%2f")], ["career2.successfactors.eu/51234", "career2.successfactors.eu/51234"]);
  // One visit, one tab: the create-account page's address names the tenant,
  // the form after a postback does not, and the tab's own storage carries it.
  const visit = memoryStorage();
  const first = loadGeneric([node("h1", {}, ["Create an Account"])],
    "https://career2.successfactors.eu/career?company=LitwareBK&career_ns=job_application&career_job_req_id=51234",
    "Career Opportunities: Create an Account", visit);
  const later = loadGeneric([node("h1", {}, ["Principal AI Engineer (51234)"])],
    "https://career2.successfactors.eu/portalcareer?_s.crb=AbC%3d", t, visit);
  check("the tenant an earlier page named reaches the postback form's id, key and ATS id",
        [first.atsJobId(), later.answerFormKey(), later.atsJobId(), later.getJob().platform_job_id],
        Array(4).fill("career2.successfactors.eu/litwarebk/51234"));
}
console.log("\ngeneric.js: SuccessFactors' candidate experience, built from web components (read live 29 Sep 2026)");
{
  // A real application was lost here: form#careerform's light DOM held 26
  // controls, EVERY one type=hidden; the 22 fields a person fills were
  // <ui5-input>s with their <input> in an open shadow root; "Submit" was a
  // <ui5-button> whose inner <button role=button> held only a <slot>, with
  // aria-label "Submit" ("Browse Browse" on the upload widget's). The page
  // prints no requisition: its title ends "(Singapore)", and the number sits
  // in <meta name="jobRequisitionId"> and a hidden career_job_req_id input.
  // The tab's address after the first postback carried only the crumb.
  const hidden = (name, value) => node("input", { type: "hidden", name, ...(value ? { value } : {}) });
  // Each field asks its question: the 13 answers the edit backstop kept on
  // the day were named right through labelFor. HOW the inner <input> was
  // named is not on record; modelled as its aria-label.
  const uiInput = (q) => attachShadow(node("ui5-input-xweb-dynamic-content"),
                                      [node("input", { type: "text", "aria-label": q })]);
  const uiButton = (label, aria) => {
    const inner = node("button", { role: "button", ...(aria ? { "aria-label": aria } : {}) }, [node("slot")]);
    const host = attachShadow(node("ui5-button-xweb-candidate-experience", {}, label ? [label] : []), [inner]);
    return [host, inner];
  };
  // One page per load: a fake node has one parent, like a real one.
  const page = () => {
    const [, submit] = uiButton("Submit", "Submit");
    const [, close] = uiButton("Close", "Close");
    const [, browse] = uiButton("Browse", "Browse Browse");
    const [, icon] = uiButton("", null);
    const kids = [
      node("meta", { name: "jobRequisitionId", content: "61234" }),
      node("form", { id: "careerform", name: "careerform" }, [
        hidden("career_job_req_id", "61234"), hidden("_s.crb", "x"), hidden("clientId"),
        node("div", {}, ["First Name", "Last Name", "Email", "Phone Number", "Country", "Notice Period"]
                          .map(uiInput)),
        node("div", {}, [browse, icon]),
        node("div", { class: "footer" }, [submit, close]),
      ]),
    ];
    return { kids, submit, close, browse, icon };
  };
  const T = "Career Opportunities: Senior AI Engineer (Singapore)";
  const CRUMB = "https://career4.successfactors.com/portalcareer?_s.crb=x";
  const FIRST = "https://career4.successfactors.com/portalcareer?company=SF1001&career_ns=job_application" +
                "&career_job_req_id=61234&_s.crb=x";
  const p = page();
  const a = loadGeneric(p.kids, CRUMB, T, memoryStorage());
  const root = a.answerFormRoot();
  check("root is form#careerform, whose light DOM holds only hidden controls",
        root && root.getAttribute("id"), "careerform");
  check("the Submit's label is slotted in from its host", a.label && a.label(p.submit), "Submit");
  check("its inner <button> is the submit, on the crumb-only address", a.isCompletion(p.submit), true);
  check("…and no near miss", a.nearMiss(p.submit), null);
  check("Close, Browse and an icon-only button are not",
        [a.isCompletion(p.close), a.isCompletion(p.browse), a.isCompletion(p.icon)], [false, false, false]);
  const q = page();
  const b = loadGeneric(q.kids, FIRST, T, memoryStorage());
  const bRoot = b.answerFormRoot();
  check("the same on the form's first address",
        [bRoot && bRoot.getAttribute("id"), b.isCompletion(q.submit)], ["careerform", true]);
  // The requisition from the page's named fields, the tenant from the visit.
  const visit = memoryStorage();
  loadGeneric([], "https://career4.successfactors.com/career?company=SF1001&career_ns=job_application" +
                  "&career_job_req_id=61234", "", visit).atsJobId();   // the page keeps the tenant
  const c = loadGeneric(page().kids, CRUMB, T, visit);
  check("the id is the page's requisition field, not the crumb and not the title's '(Singapore)'",
        [c.answerFormKey(), c.atsJobId(), c.getJob().platform_job_id],
        Array(3).fill("career4.successfactors.com/sf1001/61234"));
  check("…the one the first address names", b.atsJobId(), "career4.successfactors.com/sf1001/61234");
  check("two requisition fields that disagree name nothing",
        loadGeneric([node("meta", { name: "jobRequisitionId", content: "61234" }),
                     hidden("career_job_req_id", "70001")], CRUMB, T).atsJobId(), null);
  check("a hidden field merely named 'id' is not a requisition",
        loadGeneric([hidden("id", "61234")], CRUMB, T).atsJobId(), null);
}

console.log("\ngeneric.js: a listing's own Apply leaves for the application (P2)");
{
  // A Career Site Builder listing: a JobPosting, an upload widget of its own
  // ("match your CV"), the cookie banner's checkboxes, and "Apply now" as
  // <a role=button>, which says "apply now" to the submit rule. Until P2 the
  // file input made rule 3 root the page, so that click would file one.
  const apply = node("a", { role: "button", href: "/talentcommunity/apply/51234/?locale=en_GB" }, ["Apply now"]);
  const cv = node("input", { type: "file", name: "cvMatch" });
  const box = (n) => node("input", { type: "checkbox", name: n });
  const a = loadGeneric([
    node("div", { itemscope: "", itemtype: "http://schema.org/JobPosting" },
         [node("span", { itemprop: "description" }, ["The job."])]),
    node("div", { class: "skills-match" }, [cv]), apply,
    node("div", { class: "cookie-banner" }, [box("req-cookies"), box("fun-cookies")]),
  ], "https://jobs.litwarebank.com/job/Principal-AI-Engineer/51234-en_GB");
  check("a listing with an upload widget has no application root", a.answerFormRoot() === null, true);
  check("…so its 'Apply now' is not an application", a.isCompletion(apply), false);
  // Ashby publishes its JobPosting on the application page too: an apply
  // address keeps that page an application.
  const submit = button("Submit Application");
  const pane = node("div", { class: "ashby-job-posting-right-pane" },
                    [text("name"), text("email"), file("resume"), submit]);
  const b = loadGeneric([ld({ "@type": "JobPosting", title: "Senior Solutions Architect" }), pane],
                        "https://jobs.ashbyhq.com/fabrikam/d9b9d44f-0a87-4237-b101-360052373643/application");
  check("…while a page that publishes one AND has an apply address is still the application",
        [b.answerFormRoot() === pane, b.isCompletion(submit)], [true, true]);
}

console.log("\ngeneric.js: SuccessFactors' quick apply sends from the job page (P3; seen 25 Sep 2026)");
{
  // Relecloud's shape, placeholder tenant: the job page at /careers with the
  // requisition in its heading; its own "Apply" sent the application; the tab
  // landed three seconds later on a /portalcareer address saying so.
  const JOBPAGE = "https://career2.successfactors.eu/careers?company=SF1001&career_ns=job_listing";
  const LANDING = "https://career2.successfactors.eu/portalcareer?_s.crb=AbC%3d&isQuickApplyPostLoginRedirect=true" +
                  "&navBarLevel=JOB_SEARCH&isRedirectToAppSent=true&company=SF1001&career_ns=job_application";
  const T = "Career Opportunities: Senior AI Engineer (170001)";
  const NOW = 1_800_000_000_000;
  const KEY = "__tracker_quick_apply";
  check("quickApplySent: the landing address", J.quickApplySent(LANDING), true);
  check("…not another SuccessFactors page, nor the flag on another vendor",
        [J.quickApplySent(JOBPAGE), J.quickApplySent("https://jobs.lever.co/x/y?isRedirectToAppSent=true")],
        [false, false]);
  const jobPage = (visit, kids) => {
    const apply = node("span", { role: "button" }, ["Apply"]);
    return [loadGeneric([node("h1", {}, ["Senior AI Engineer (170001)"]), ...(kids || []), apply], JOBPAGE, T, visit), apply];
  };
  const visit = memoryStorage();
  const [page, apply] = jobPage(visit);
  const note = page.quickApplyStart(apply, undefined, undefined, NOW);
  check("the job page's Apply leaves a note naming the job and its hiring-system id",
        [note && note.job.title, note && note.atsJobId, note && note.at],
        ["Senior AI Engineer", "career2.successfactors.eu/sf1001/170001", NOW]);
  check("…and is still no application by itself", page.isCompletion(apply), false);
  visit.setItem(KEY, JSON.stringify(note));          // what the click listener does
  const land = loadGeneric([], LANDING, "", visit);
  const got = land.landedCompletion(NOW + 3000);
  check("the landing files it: the job page's job and id",
        got && [got.job.title, got.atsJobId], ["Senior AI Engineer", "career2.successfactors.eu/sf1001/170001"]);
  check("…once: the note is consumed", land.landedCompletion(NOW + 4000), null);
  const stale = memoryStorage();
  stale.setItem(KEY, JSON.stringify(note));
  check("a note older than ten minutes files nothing",
        loadGeneric([], LANDING, "", stale).landedCompletion(NOW + 11 * 60_000), null);
  const waiting = memoryStorage();
  waiting.setItem(KEY, JSON.stringify(note));
  check("a page that is not the landing (a sign-in first) files nothing and keeps the note",
        [loadGeneric([], "https://career2.successfactors.eu/career?company=SF1001&loginFlowRequired=true", "", waiting)
           .landedCompletion(NOW + 5000), waiting.getItem(KEY) !== null], [null, true]);
  // No note where the press is not a quick apply.
  const field = (n) => { const i = node("input", { type: "text", name: n }); return i; };
  const formApply = node("span", { role: "button" }, ["Apply"]);
  const form = node("form", { id: "careerform" }, [field("a"), field("b"), field("c"), field("d"), field("e"), formApply]);
  // The form page NAMES its job, so only the form rule can refuse it (a
  // mutation run found the first version refused by the id rule instead).
  const onForm = loadGeneric([node("h1", {}, ["Principal AI Engineer (51234)"]), form],
                             "https://career2.successfactors.eu/portalcareer?_s.crb=x", "", memoryStorage());
  check("the form's own Apply leaves no note (the form hook files it)",
        onForm.quickApplyStart(formApply, undefined, undefined, NOW), null);
  const [signIn, signInApply] = jobPage(memoryStorage(), [node("input", { type: "password", name: "pw" })]);
  check("…nor one with a sign-in in view", signIn.quickApplyStart(signInApply, undefined, undefined, NOW), null);
  const bare = node("span", { role: "button" }, ["Apply"]);
  const noId = loadGeneric([node("h1", {}, ["Search Jobs"]), bare], JOBPAGE, "Career Opportunities", memoryStorage());
  check("…nor a page that names no job", noId.quickApplyStart(bare, undefined, undefined, NOW), null);
  const gh = node("button", {}, ["Apply"]);
  const other = loadGeneric([node("h1", {}, ["Engineer (4377)"]), gh],
                            "https://job-boards.greenhouse.io/northwind/jobs/4377390009", "", memoryStorage());
  check("…nor another vendor's job page", other.quickApplyStart(gh, undefined, undefined, NOW), null);
}

console.log("\ngeneric.js: what must NOT be an application");
{
  // A job-alert or talent-community sign-up: a form that says "Submit" but
  // has the fields of a sign-up, not an application.
  const submit = button("Submit", { type: "submit" });
  const a = loadGeneric([node("form", {}, [text("email"), text("keywords"), text("location"), submit])],
                        "https://jobs.lever.co/contoso");
  check("a three-field sign-up form saying 'Submit': no root", a.answerFormRoot() === null, true);
  check("...so its 'Submit' is not an application", a.isCompletion(submit), false);
  check("...and the miss says why (the popup shows it)", a.nearMiss(submit),
        "no application form found on this page");
}
{
  // A candidate sign-in on an apply path (Workday, SuccessFactors): a
  // password field anywhere in the container disqualifies it, so the
  // username is never swept as an answer and its button never "applies".
  const signIn = button("Submit", { type: "submit" });
  const a = loadGeneric([node("form", {}, [text("email"), node("input", { type: "password", name: "pw" }), signIn])],
                        "https://contoso.wd3.myworkdayjobs.com/Contoso/job/Engineer_R120291/apply");
  // (=== null, not the node itself: a wrongly-found root is a DOM node with
  // circular parent links, which check()'s JSON comparison cannot print.)
  check("sign-in on an apply path: no root", a.answerFormRoot() === null, true);
  check("sign-in: its 'Submit' is not an application", a.isCompletion(signIn), false);
  // A listing with a search box and nothing to upload: not an apply flow.
  const b = loadGeneric([node("input", { type: "search", name: "q" }), text("location")],
                        "https://jobs.lever.co/contoso/53e23908-0da6-47a5-a482-39be676e9ee6");
  check("a listing with no resume field and no apply path: no root", b.answerFormRoot() === null, true);
}
{
  // A sign-in with NO password field (1 Oct 2026): MyGreenhouse, which
  // Greenhouse's job boards offer for autofill, takes an emailed security
  // code, and its Submit filed an application ("MyGreenhouse", no employer,
  // the sign-in's email as its one answer). Rule 2 saw five fields and a
  // "Submit". The sweep at that click read 8 controls, every one filled and
  // none labelled; their markup was not read, so the boxes here are bare.
  const SIGN_IN = "https://my.greenhouse.io/users/sign_in?initiator=autofill&source=quick_apply" +
                  "&job_post_id=4377390009&job_board=northwind";
  const page = (labelled) => {
    const submit = button("Submit", { type: "submit" });
    const boxes = Array.from({ length: 8 }, (_, i) => labelled
      ? [node("label", { for: `q${i}` }, [`Question ${i + 1}`]), node("input", { type: "text", id: `q${i}` })]
      : [node("input", { type: "text" })]).flat();
    return { kids: [node("h1", {}, ["MyGreenhouse"]), node("form", {}, [...boxes, submit])], submit };
  };
  const p = page(false);
  const a = loadGeneric(p.kids, SIGN_IN, "MyGreenhouse");
  check("passwordless sign-in: eight fields that ask nothing are no application", a.answerFormRoot() === null, true);
  check("...so its 'Submit' files nothing", a.isCompletion(p.submit), false);
  check("...and the miss says why", a.nearMiss(p.submit), "no application form found on this page");
  // The control: the same form whose fields ask something is rule 2's.
  const q = page(true);
  const b = loadGeneric(q.kids, SIGN_IN, "MyGreenhouse");
  check("the same form with eight labelled fields is an application",
        [b.answerFormRoot() !== null, b.isCompletion(q.submit)], [true, true]);
  // The popup's injection carries no answers.js and keeps the old count.
  const r = page(false);
  check("without answers.js (the popup's injection) every control counts, as before",
        loadGeneric(r.kids, SIGN_IN, "MyGreenhouse", undefined, { answers: false }).isCompletion(r.submit), true);
}
{
  // A wizard step with no file input of its own (Workday's later steps) is
  // still the application, by its address; "Save and Continue" advances it
  // and only "Submit" sends it.
  const next = button("Save and Continue");
  const submit = button("Submit");
  const step = node("div", {}, [text("q1"), text("q2"), node("select", { name: "q3" }), next, submit]);
  const a = loadGeneric([step], "https://contoso.wd3.myworkdayjobs.com/Contoso/job/Engineer_R120291/apply/applyManually");
  check("wizard step on an apply path: root found without a file input", a.answerFormRoot() === step, true);
  check("wizard: 'Save and Continue' is not the submit", a.isCompletion(next), false);
  check("wizard: 'Submit' is", a.isCompletion(submit), true);
}

console.log("\ngeneric.js: a wizard's last step, which shows the answers as text (25 Sep 2026)");
{
  // Workday's "Review" step: every answer printed, NO controls, and "Submit"
  // in the page footer. Workday keeps one address for the whole wizard. A real
  // submit here was turned down with "no application form found on this
  // page": the case above puts "Submit" beside inputs, which a review step
  // never does (docs/career-sites.md §16).
  const WD = "https://contoso.wd3.myworkdayjobs.com/en-US/Contoso/job/Singapore/Senior-AI-Engineer_R200001/apply/autofillWithResume";
  const back = button("Back");
  const submit = button("Submit", { "data-automation-id": "pageFooterNextButton" });
  const review = node("div", { "data-automation-id": "applyFlowReviewPage" },
    [node("h2", {}, ["Review"]), node("div", {}, ["Email Address: jane@contoso.com"]),
     node("div", {}, ["How did you hear about us? LinkedIn"])]);
  const footer = node("div", { "data-automation-id": "pageFooter" }, [back, submit]);
  const a = loadGeneric([review, footer], WD);
  check("review step: there is no root to find", a.answerFormRoot() === null, true);
  check("review step: its 'Submit' sends the application", a.isCompletion(submit), true);
  check("review step: and is no near miss", a.nearMiss(submit), null);
  check("review step: 'Back' is not the submit", a.isCompletion(back), false);
  // The sign-in dialog left in the DOM, closed: a password not in view does
  // not make the review a sign-in.
  const pw = node("input", { type: "password", name: "password" });
  const em = text("email");
  pw.getClientRects = () => [];
  em.getClientRects = () => [];
  const b = loadGeneric([review, node("div", { role: "dialog" }, [em, pw]), footer], WD);
  check("review step with a closed sign-in dialog in the DOM: still the submit", b.isCompletion(submit), true);
}
{
  // Still refused. A sign-in IN VIEW on the same address, as its own
  // container rather than a <form> (the case further up has the form).
  const WD = "https://contoso.wd3.myworkdayjobs.com/en-US/Contoso/job/Singapore/Senior-AI-Engineer_R200001/apply/autofillWithResume";
  const signIn = button("Submit");
  const a = loadGeneric([node("div", {}, [text("email"), node("input", { type: "password", name: "password" }), signIn])], WD);
  check("a sign-in in view on an apply address: its 'Submit' is not an application", a.isCompletion(signIn), false);
  check("...and the miss says why", a.nearMiss(signIn), "no application form found on this page");
  // A page that is not an apply flow at all: SuccessFactors' own job page,
  // whose "Apply" can send a quick application (§16.3 item 6). That is a
  // different rule, not this one.
  const apply = node("span", { role: "button" }, ["Apply"]);
  const c = loadGeneric([node("h1", {}, ["Senior AI Engineer (170001)"]), apply],
                        "https://career2.successfactors.eu/careers?company=SF1001");
  check("a job page's 'Apply' (no apply address): still not an application", c.isCompletion(apply), false);
}

console.log("\ngeneric.js: a later step at an address that never says 'apply' (SmartRecruiters, 30 Sep 2026)");
{
  // A LinkedIn → SmartRecruiters apply, read live before its submit
  // (placeholder names). Step 1 had the resume's file input, so rule 3 found
  // it and its 26 answers were swept. Step 2, the screening questions, has
  // no <form>, no file input, and an address ending /screening: no rule
  // found it, and its web-component "Submit" (a slotted label, read right)
  // was turned down with "no application form found on this page". What
  // says it is the application is the visit itself: the answers store holds
  // this form's key, marked by a sweep that found its root.
  const SR = "https://jobs.smartrecruiters.com/oneclick-ui/company/Contoso/publication/" +
             "7c1e5a90-2b4d-4f6e-9a3b-0d5e8f1c2a47/screening";
  const FORM_KEY = "jobs.smartrecruiters.com/7c1e5a90-2b4d-4f6e-9a3b-0d5e8f1c2a47";
  const splButton = (label) => {
    const inner = node("button", { type: "button" }, [node("slot")]);
    return [attachShadow(node("spl-button", {}, [label]), [inner]), inner];
  };
  const page = (extra = []) => {
    const [backHost, back] = splButton("Back");
    const [submitHost, submit] = splButton("Submit");
    const questions = attachShadow(node("sr-screening-questions-form"), [
      node("input", { type: "text", id: "q1", role: "combobox" }),
      node("textarea", { id: "q2" }), node("textarea", { id: "q3" })]);
    const step = node("div", {}, [
      questions, node("spl-checkbox", {}, [node("input", { type: "checkbox", id: "consent" })]),
      node("div", { class: "nav" }, [backHost, submitHost])]);
    return { kids: [...extra, node("main", {}, [step])], step, back, submit };
  };
  const store = (rec) => {
    const s = memoryStorage();
    if (rec) s.setItem("__tracker_form_answers", JSON.stringify(rec));
    return s;
  };
  const earlier = { "first name#0": { question: "First name", answer: "Jane", type: "text", i: 0 } };
  const at = (rec, extra) => { const p = page(extra); return [p, loadGeneric(p.kids, SR, "", store(rec))]; };

  const [p0, a0] = at(null);
  check("the step's own key is the publication's, the one step 1 kept its answers under",
        a0.answerFormKey(), FORM_KEY);
  // Where the form was reached from, for the handoff to learn its second id
  // (jobposting.js:learnsAlias): the referrer, measured live as the listing's
  // full address, and only on this host.
  const came = (referrer) => a0.arrivedFrom && a0.arrivedFrom({ referrer }, makeLoc(SR));
  check("arrivedFrom: the listing the form was reached from, by its own id",
        came("https://jobs.smartrecruiters.com/Contoso/744000100000042-genai-engineer"),
        "jobs.smartrecruiters.com/744000100000042");
  check("…nothing from another host, from no referrer, or from a page with no id",
        [came("https://www.linkedin.com/"), came(""), came("https://jobs.smartrecruiters.com/Contoso")],
        [null, null, null]);
  check("with nothing from an earlier step: no root, and the Submit is the live near miss",
        [a0.answerFormRoot() === null, a0.isCompletion(p0.submit), a0.nearMiss(p0.submit)],
        [true, false, "no application form found on this page"]);
  const [p1, a1] = at({ key: FORM_KEY, at: Date.now() - 6 * 60_000, items: earlier, rooted: true });
  check("continuing a form an earlier step's sweep found: the step is the root",
        a1.answerFormRoot() === p1.step, true);
  check("…its Submit sends the application, with no near miss, and Back does not",
        [a1.isCompletion(p1.submit), a1.nearMiss(p1.submit), a1.isCompletion(p1.back)], [true, null, false]);
  // Never on the word of a store alone.
  const refused = [
    ["answers only the backstop kept (a box typed into, no form found)",
     { key: FORM_KEY, at: Date.now(), items: earlier, rooted: false }],
    ["another form's answers", { key: "jobs.smartrecruiters.com/6000000001200727", at: Date.now(),
                                 items: earlier, rooted: true }],
    ["answers past the store's two hours", { key: FORM_KEY, at: Date.now() - 3 * 3600_000,
                                             items: earlier, rooted: true }],
    ["an empty store", { key: FORM_KEY, at: Date.now(), items: {}, rooted: true }],
  ];
  for (const [why, rec] of refused) {
    const [p, a] = at(rec);
    check(`no root from ${why}`, [a.answerFormRoot() === null, a.isCompletion(p.submit)], [true, false]);
  }
  // A listing is never an application's later step: its own "Apply" only
  // leaves for one (the P2 rule above).
  const [p2, a2] = at({ key: FORM_KEY, at: Date.now(), items: earlier, rooted: true },
                      [ld({ "@type": "JobPosting", title: "Senior AI Engineer" })]);
  check("…nor on a page that publishes a JobPosting", [a2.answerFormRoot() === null, a2.isCompletion(p2.submit)],
        [true, false]);
  // A continuing form's last step may show its answers as text, with no
  // controls at all: its Submit sends, as on an apply address.
  const [submitHost, submit] = splButton("Submit");
  const review = loadGeneric([node("div", {}, [node("p", {}, ["First name: Jane"])]), submitHost], SR, "",
                             store({ key: FORM_KEY, at: Date.now(), items: earlier, rooted: true }));
  check("…and a controlless last step of it sends too", review.isCompletion(submit), true);
}

console.log("\nanswers.js: a wizard whose address names its step (Phenom's own apply, 2 Oct 2026)");
{
  // Phenom's apply runs on the employer's own career site, one step per
  // address: …/apply?jobSeqNo=<job>&step=N&stepname=<name>, with "Next"
  // between steps. The step was part of the form's key (jobposting.js:idFrom's
  // fallback), so every step emptied the answers store as if the job had
  // changed, and the review step's submit sent the last step's one answer of
  // a six-step form. Placeholder host and job.
  const APPLY = "https://jobs.contoso.com/global/en/apply?jobSeqNo=CONTOSOGLOBALR01234567EXTERNALENGLOBAL";
  const field = (id, lbl, value) => [node("label", { for: id }, [lbl]), node("input", { type: "text", id, value })];
  const stepPage = (...fields) => [node("form", {}, [...fields.flat(), button("Next", { type: "button" })])];
  const visit = memoryStorage();
  const step1 = genericSandbox(stepPage(field("fn", "First name", "Jane"), field("em", "Email", "jane@example.com")),
                               `${APPLY}&step=1&stepname=personalInformation`, "", visit);
  step1.document._on.click();           // "Next": the sweep keeps this step's answers
  const review = genericSandbox(stepPage(field("yrs", "Years of experience", "10"), field("np", "Notice period", "1 month")),
                                `${APPLY}&step=6&stepname=applicationReview`, "", visit);
  check("every step of the form has one key, the job's",
        review.window.__trackerAdapter.answerFormKey(), step1.window.__trackerAdapter.answerFormKey());
  check("the submit on the last step sends every step's answers",
        review.window.__trackerAnswers.take().map((a) => a.question),
        ["First name", "Email", "Years of experience", "Notice period"]);
  // The key still names the job: another job's form in the same tab starts empty.
  const again = memoryStorage();
  genericSandbox(stepPage(field("fn", "First name", "Jane"), field("em", "Email", "jane@example.com")),
                 `${APPLY}&step=1&stepname=personalInformation`, "", again).document._on.click();
  const other = genericSandbox(stepPage(field("yrs", "Years of experience", "10"), field("np", "Notice period", "1 month")),
                               APPLY.replace("R01234567", "R07654321") + "&step=6&stepname=applicationReview", "", again);
  check("…and another job's form in the same tab does not inherit them",
        other.window.__trackerAnswers.take().map((a) => a.question), ["Years of experience", "Notice period"]);

  // After the submit's capture took the store, the click's delayed sweeps
  // (and any later click on that page) refilled it from the review step: the
  // store on disk was saved 100 ms after the real capture, and the next full
  // load reports a stored form as "left holding answers".
  const KEY = "__tracker_form_answers";
  const tab = memoryStorage();
  const last = genericSandbox(stepPage(field("yrs", "Years of experience", "10"), field("np", "Notice period", "1 month")),
                              `${APPLY}&step=6&stepname=applicationReview`, "", tab);
  last.document._on.click();
  check("before the submit, the store holds the step",
        Object.keys(JSON.parse(tab.getItem(KEY)).items).length, 2);
  last.window.__trackerAnswers.take();
  last.document._on.click();            // the submit click's delayed sweep
  check("after the capture took it, a sweep on that page writes nothing back", tab.getItem(KEY), null);
  check("…though memory keeps them, for a second submit there",
        last.window.__trackerAnswers.take().length, 2);
  const next = `${APPLY}&step=7&stepname=moreQuestions`;
  Object.assign(last.location, makeLoc(next));
  last.document._on.click();            // a later page of the same form
  check("…and the form's next address saves again",
        Object.keys(JSON.parse(tab.getItem(KEY) || "{}").items || {}).length, 2);
}

console.log("\ngeneric.js: Eightfold's candidate site under an employer's domain (read live 2 Oct 2026)");
{
  // careers.<employer> serves Eightfold's app: its scripts come from
  // vscdn.net, the job is /careers/job/<pid> and its form
  // /careers/apply?pid=<pid>, and the server HTML of both carries a
  // JobPosting. The form, read with the applicant signed in (nothing typed,
  // nothing sent): one <form> holding the resume's hidden file input and, as
  // the visible choice, a COMBOBOX whose value is the chosen file's name,
  // labelled "Upload your resume"; section toggles as <button type=button>;
  // contact fields named by <label for>, an aria-label, or a placeholder
  // alone; comboboxes that keep the picked option in the input, each in a
  // box with no text of its own; a consent box; and "Submit application"
  // (type=submit). The visa question's picked "Yes" is modelled: it was
  // unanswered on the day.
  const EF = "https://careers.fabrikam.com", PID = "446700000001";
  const posting = () => ld({
    "@context": "http://schema.org", "@type": "JobPosting", title: "Senior Platform Engineer",
    description: "<p>Build the platform.</p>", datePosted: "2026-04-15T00:00:00", employmentType: "FULL_TIME",
    hiringOrganization: { "@type": "Organization", name: "Fabrikam" },
    jobLocation: [{ "@type": "Place", address: { "@type": "PostalAddress",
      addressCountry: { "@type": "Country", name: "SG" }, addressLocality: "Singapore", addressRegion: "" } }],
    url: `${EF}/careers/apply?pid=${PID}` });
  const vendor = () => node("script", { src: "https://static.vscdn.net/images/careers/demo/fabrikam/app.js" });
  const combo = (attrs) => node("div", { class: "select-module_select-input" },
                                [node("input", { type: "text", role: "combobox", "aria-expanded": "false", ...attrs })]);
  const asked = (id, q, attrs) => node("div", {}, [node("span", { id }, [q]), combo({ "aria-labelledby": id, ...attrs })]);
  const applyPage = () => {
    const upload = node("input", { type: "file" });
    upload.getClientRects = () => [];
    const [submit, toggle, cancel, uploadNew] = [button("Submit application", { type: "submit" }),
      button("Contact Information", { type: "button", "aria-expanded": "true" }),
      button("Cancel", { type: "button" }), button("Upload new", { type: "button" })];
    const field = (id, lbl, value, extra = {}) =>
      [node("label", { for: id }, [lbl]), node("input", { id, value, ...extra })];
    const form = node("form", { class: "form-3YMOs" }, [
      asked("Resume_resume_label", "Upload your resume", { id: "input-7", value: "Jane-Doe_resume.pdf" }),
      node("div", { class: "upload-module_upload" }, [upload, uploadNew]),
      toggle,
      ...field("Contact_Information_firstname", "First Name", "Jane"),
      ...field("Contact_Information_lastname", "Last Name", "Doe"),
      ...field("Contact_Information_email", "Email", "jane@example.com", { readonly: "" }),
      combo({ "aria-label": "Country code", value: "Singapore (+65)" }),
      node("input", { type: "text", id: "Contact_Information_phone", placeholder: "Phone Number", value: "91234567" }),
      asked("q_country", "Country", {}),
      asked("q_auth", "Can you, upon employment, submit verification of your legal right to work in Singapore?",
            { value: "Yes" }),
      node("label", { for: "tc" }, ["I consent"]),
      node("input", { type: "checkbox", id: "tc", name: "Terms_and_Conditions_consent", checked: true }),
      cancel, submit,
    ]);
    return { kids: [vendor(), posting(), node("h1", {}, ["Application Form"]), form],
             form, submit, toggle, cancel, uploadNew };
  };
  const APPLY = `${EF}/careers/apply?pid=${PID}`;
  const p = applyPage();
  const sb = genericSandbox(p.kids, APPLY, "Submit application for Senior Platform Engineer");
  const a = sb.window.__trackerAdapter;
  check("the root is the form that holds the resume's file input", a.answerFormRoot() === p.form, true);
  check("'Submit application' is the submit; a section toggle, Cancel and 'Upload new' are not",
        [a.isCompletion(p.submit), a.isCompletion(p.toggle), a.isCompletion(p.cancel), a.isCompletion(p.uploadNew)],
        [true, false, false, false]);
  const job = a.getJob();
  check("the job, from the JobPosting the page serves, under the ?pid= id; vendor eightfold",
        [job.platform_job_id, job.title, job.company, job.ats, /Singapore/.test(job.location || "")],
        ["careers.fabrikam.com/446700000001", "Senior Platform Engineer", "Fabrikam", "eightfold", true]);
  check("the form's page is the hiring system's, off its own host: the id is the job's on it",
        [a.hiringSystem(sb.document, sb.location), a.atsJobId()], [true, "careers.fabrikam.com/446700000001"]);
  check("what the submit sends: every named field, the resume combobox's file name, the picked option",
        sb.window.__trackerAnswers.take(), [
          { question: "Upload your resume", answer: "Jane-Doe_resume.pdf", type: "text" },
          { question: "First Name", answer: "Jane", type: "text" },
          { question: "Last Name", answer: "Doe", type: "text" },
          { question: "Email", answer: "jane@example.com", type: "text" },
          { question: "Country code", answer: "Singapore (+65)", type: "text" },
          { question: "Phone Number", answer: "91234567", type: "text" },
          { question: "Can you, upon employment, submit verification of your legal right to work in Singapore?",
            answer: "Yes", type: "text" },
          { question: "I consent", answer: "Yes", type: "checkbox" }]);
  // The job's own page: the same JobPosting and vendor, no form.
  const jobPage = genericSandbox([vendor(), posting(), node("h1", {}, ["Senior Platform Engineer"]),
                                  node("a", { href: `/careers/apply?pid=${PID}` }, ["Apply Now"])],
                                 `${EF}/careers/job/${PID}`, "Senior Platform Engineer | Fabrikam");
  const j = jobPage.window.__trackerAdapter;
  check("the job's page has the form's id, and no form: no root, not the hiring system's page",
        [j.getJob().platform_job_id, j.answerFormRoot(), j.hiringSystem(jobPage.document, jobPage.location), j.atsJobId()],
        ["careers.fabrikam.com/446700000001", null, false, null]);
  // An employer's own form with no hiring system's app behind it.
  const q = applyPage();
  q.kids.shift();
  const inHouse = genericSandbox(q.kids, APPLY, "");
  check("a form on an employer's domain with no vendor's app is no hiring system: no ATS id",
        [inHouse.window.__trackerAdapter.answerFormRoot() === q.form,
         inHouse.window.__trackerAdapter.hiringSystem(inHouse.document, inHouse.location),
         inHouse.window.__trackerAdapter.atsJobId()], [true, false, null]);
}

console.log("\ngeneric.js getJob: a listing proposes its hiring system's id (P4)");
{
  // The Career Site Builder listing of 28 Sep 2026, placeholder names: its
  // inline config names the data centre and tenant, and its own number is
  // the requisition on this site (not on every one: the server only looks).
  const kids = () => [
    node("span", { itemprop: "title" }, ["Principal AI Engineer"]),
    node("div", { itemscope: "", itemtype: "http://schema.org/JobPosting" },
         [node("span", { itemprop: "description" }, ["The job."])]),
    node("script", {}, [`{"ssoCompanyId" : 'litwarebk', "ssoUrl" : 'https://career2.successfactors.eu'}`]),
  ];
  const listing = loadGeneric(kids(), "https://jobs.litwarebank.com/job/Principal-AI-Engineer/51234-en_GB",
                              "Principal AI Engineer Job Details | Litware Bank");
  check("the listing's job carries the candidate id",
        listing.getJob().ats_job_candidates, ["career2.successfactors.eu/litwarebk/51234"]);
  const onAts = loadGeneric([node("h1", {}, ["Principal AI Engineer (51234)"]), ...kids()],
                            "https://career2.successfactors.eu/portalcareer?_s.crb=x");
  check("…a hiring system's own page proposes none (it has its real id)",
        onAts.getJob().ats_job_candidates, undefined);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
