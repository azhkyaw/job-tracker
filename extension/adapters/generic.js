/* Any other job page — an employer's career site or its applicant tracking
 * system (docs/career-sites.md). Loaded after shared/jobposting.js, which does
 * all the reading of the JOB; this file finds the APPLICATION on the page.
 *
 * Two ways in:
 *  - Phase A (24 Sep 2026): injected by the toolbar popup into the page in
 *    front of the user, on their click — activeTab + scripting, no standing
 *    access to any site. The capture IS that click.
 *  - Phase B (the same day): a static content script on the ATS hosts where
 *    application forms live (manifest.json). There it watches for the SUBMIT
 *    of the application form and captures then, with the form's answers.
 *
 * Nothing here is per-vendor except one selector list. The form and its
 * submit were read live on four vendors' real apply pages (Lever, Greenhouse,
 * Ashby, Workable — 24 Sep 2026, read-only, nothing typed or sent), and what
 * they share is structural: the application is where the RESUME goes, it
 * never asks for a password, and it is sent by a control that says so. One
 * of the four (Ashby) has no <form> element at all, which is why the root is
 * found by what it contains rather than by its tag. Workday and
 * SuccessFactors put the form behind a candidate sign-in and are UNVERIFIED —
 * the same rules are expected to hold there (see .claude/rules/extension.md).
 */
(() => {
  const J = window.__trackerJobPosting;

  // Controls that hold an answer — not buttons, not a search box.
  const SKIP_TYPES = ["hidden", "submit", "button", "reset", "image", "search"];
  const answerable = (el) => el.tagName !== "INPUT" ||
    !SKIP_TYPES.includes((el.getAttribute("type") || "text").toLowerCase());
  const controlsIn = (root) =>
    [...root.querySelectorAll("input, select, textarea")].filter(answerable);
  const hasPassword = (root) => root.querySelectorAll("input[type='password']").length > 0;
  const isFile = (el) => (el.getAttribute("type") || "").toLowerCase() === "file";

  function inside(node, ancestor) {
    for (let x = node; x; x = x.parentElement) if (x === ancestor) return true;
    return false;
  }

  function commonAncestor(nodes) {
    for (let a = nodes[0].parentElement; a; a = a.parentElement) {
      if (nodes.every((n) => inside(n, a))) return a;
    }
    return null;
  }

  // An apply flow by its address: Workday's …/apply/…, Ashby's /application,
  // a SuccessFactors career site's career_ns=job_application. Needed for the
  // steps of a wizard that carry no file input of their own.
  const APPLY_PATH = /(^|\/)(apply|application)(\/|$)/i;

  /* The element holding the application's answerable controls, or null when
   * this page has none.
   *
   * A page qualifies when it holds a file input (the resume) or its address is
   * an apply flow, and never when the controls share a container with a
   * password field — that is a candidate sign-in, and its username is not an
   * answer. Where the file input sits in a <form> free of passwords, that form
   * is the root (Lever, Greenhouse, Workable). Otherwise it is the nearest
   * container of every VISIBLE answerable control on the page (Ashby's
   * `ashby-job-posting-right-pane`: all 21 visible controls). */
  function applicationRoot(doc, loc) {
    const all = controlsIn(doc);
    if (all.length < 2) return null;
    const files = all.filter(isFile);
    const applyFlow = APPLY_PATH.test(loc.pathname || "") ||
      /career_ns=job_application/i.test(loc.search || "");
    if (!files.length && !applyFlow) return null;
    for (const f of files) {
      const form = f.closest("form");
      if (form && !hasPassword(form)) return form;
    }
    // The controls a person can SEE decide the container. Measured live on
    // Ashby: 21 controls sit in its form pane and the 22nd is reCAPTCHA's
    // hidden response field, portalled to <body> — counting it made the whole
    // page the root. Hidden controls inside the container are still swept
    // (answers.js decides what is machinery); they just cannot move it.
    const rendered = (el) => !el.getClientRects || el.getClientRects().length > 0;
    const seen = all.filter(rendered);
    const root = commonAncestor(seen.length ? seen : all);
    return root && !hasPassword(root) ? root : null;
  }

  // The words that send an application — matched whole, and only on a control
  // INSIDE the application root, so a job page's own "Apply" button (outside
  // Greenhouse's form, measured) and a sign-in's "Submit" (no root) never fire.
  const SUBMIT_WORDS = /^(submit|submit (my |your )?application|send (my |your )?application|apply|apply now|complete (my |your )?application|finish)$/i;
  // The two vendors whose submit carries a language-independent hook, read
  // off their live apply pages: Lever's button#btn-submit (type=button — it
  // posts through script, past hCaptcha) and Workable's data-ui="apply-button".
  const SUBMIT_HOOKS = ["#btn-submit", "[data-ui='apply-button']"];
  const INVISIBLE = /[­​-‏⁠-⁤﻿]/g;
  const label = (el) => (el.textContent || el.value || "")
    .replace(INVISIBLE, "").replace(/\s+/g, " ").trim();

  function isSubmitControl(el, doc, loc) {
    if (!el || !el.tagName) return false;
    const type = (el.getAttribute("type") || "").toLowerCase();
    const buttonish = el.tagName === "BUTTON" ||
      (el.tagName === "INPUT" && (type === "submit" || type === "button")) ||
      el.getAttribute("role") === "button";
    if (!buttonish) return false;
    const hooked = SUBMIT_HOOKS.some((s) => el.matches && el.matches(s));
    if (!hooked && !SUBMIT_WORDS.test(label(el))) return false;
    const root = applicationRoot(doc, loc);
    return !!root && inside(el, root);
  }

  const adapter = {
    platform: "other",
    applySelectors: [],
    // A submit here is an application sent on the employer's own system; when
    // the tab was opened by a job board's "Apply on company website", the
    // capture completes THAT record instead of starting its own (§8).
    linksOpener: true,
    getJob() {
      return J.read(document, location);
    },
    // Identity from a URL alone — capture.js's last resort when the page read
    // comes back empty. Same id read() would have derived from the same URL.
    jobFromUrl(href) {
      const id = J.idFrom(href);
      return id ? { platform_job_id: id.platform_job_id, url: id.url } : null;
    },
    answerFormRoot() {
      return applicationRoot(document, location);
    },
    // The listing and its apply page share an id on every vendor measured
    // (/apply, /application and Workday's /apply/… suffixes are not ids), so
    // the answers store and the listing's stash both survive the move.
    answerFormKey() {
      const id = J.idFrom(location.href);
      return id ? id.platform_job_id : location.href;
    },
    isCompletion(el) {
      return isSubmitControl(el, document, location);
    },
    // Pure forms of the two rules, for tests/test_extension.js.
    applicationRoot,
    isSubmitControl,
  };
  window.__trackerAdapter = adapter;

  /* Remember the job while its listing is on screen. Apply pages drop the
   * JobPosting the listing published (Lever, Workable, Personio), so without
   * this the submit would file the apply page's tab title. Keyed by the id
   * the apply page will ask for; the worker expires it unsent (2 h) when
   * nothing is submitted. Only a STRUCTURED read is worth remembering, and a
   * client-rendered one may not exist yet at load — hence the retries. */
  function stashListing() {
    let job = null;
    try { job = J.read(document, location); } catch (e) { return true; }
    const src = job && job._prov && job._prov.title_source;
    if (src !== "jsonld" && src !== "microdata") return false;
    try {
      chrome.runtime.sendMessage({ type: "tracker-stash-job", key: adapter.answerFormKey(), job })
        .catch(() => {});
    } catch (e) { /* no extension context: a test, or a reloaded extension */ }
    return true;
  }
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id && !stashListing()) {
    setTimeout(stashListing, 1500);
    setTimeout(stashListing, 5000);
  }
})();
