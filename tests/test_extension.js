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

function matches(node, sel) {
  return sel.split(",").some((s) => {
    s = s.trim();
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
        const am = /^\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]$/.exec(part);
        const want = am[2] !== undefined ? am[2] : am[3] !== undefined ? am[3] : am[4];
        if (want === undefined) { if (!node.hasAttribute(am[1])) return false; }
        else if (node.getAttribute(am[1]) !== want) return false;
      }
    }
    return true;
  });
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

function loadAnswers(first) {
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
    sessionStorage: { getItem: () => null, setItem(k, v) { writes.push(v); }, removeItem() {} },
    document: doc,
    location: { href: "https://www.linkedin.com/jobs/view/1/" },
  };
  sandbox.window = {
    document: doc, location: sandbox.location,
    __trackerAdapter: { answerFormRoot: () => root, answerFormKey: () => "1" },
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

/* ------------------------------ adapters/generic.js on an ATS's own pages
 *
 * The application form and its submit, as read LIVE on 24 Sep 2026 from four
 * vendors' real apply pages (read-only — nothing typed or sent): Lever,
 * Greenhouse, Ashby (which has no <form> at all) and Workable. Placeholder
 * names; the structure is the measured one. The sign-in and wizard shapes
 * are the rules' own reasons, not a live read — Workday and SuccessFactors
 * put the form behind a candidate sign-in. */

const GENERIC_SRC = fs.readFileSync(path.join(ROOT, "extension/adapters/generic.js"), "utf8");

function loadGeneric(kids, href, title = "") {
  const doc = pageDoc(kids, title);
  const loc = makeLoc(href);
  const sandbox = { URL, URLSearchParams, console, setTimeout: () => 0 };
  sandbox.window = { document: doc, location: loc };
  sandbox.document = doc;
  sandbox.location = loc;
  vm.createContext(sandbox);
  vm.runInContext(JOBPOSTING_SRC, sandbox);
  vm.runInContext(GENERIC_SRC, sandbox);
  return sandbox.window.__trackerAdapter;
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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
