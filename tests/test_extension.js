/* Extension adapter tests — the first tests this codebase has had for
 * `extension/`, added 21 Aug 2026 alongside the getJob() document-selection fix.
 *
 * Why here and not next to the Python suites' concerns: everything under
 * extension/ has been shipped on `node --check` alone, which catches a typo and
 * nothing else. The identity path in particular has now been misdiagnosed twice
 * on plausible reasoning (an unreachable top frame, twice mitigated the wrong
 * way — see CLAUDE.md), and the fix for it is *still* unverifiable against a
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
    const m = /^([a-zA-Z]*)((?:#[\w-]+|\.[\w-]+|\[[^\]]+\])*)$/.exec(s);
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
  const body = node("body", {}, [root]);
  const doc = {
    nodeType: 9,
    body,
    addEventListener() {},
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
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
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
  return sandbox.window.__trackerAnswers.take();
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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
