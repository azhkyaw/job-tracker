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
    querySelector: (s) => nodes[s] || null,
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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
