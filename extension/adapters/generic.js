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
 * SuccessFactors put the form behind a candidate sign-in. SuccessFactors'
 * newer candidate experience (29 Sep 2026) builds it from web components, so
 * "contains" means through open shadow roots (deepAll, below); see
 * .claude/rules/extension.md.
 */
(() => {
  const J = window.__trackerJobPosting;

  /* Open shadow roots are part of the page. SuccessFactors' candidate
   * experience (read live 29 Sep 2026) builds the application from UI5 web
   * components: form#careerform holds 26 controls, every one type=hidden,
   * while the 22 fields a person fills are <ui5-input>s whose <input> sits in
   * an open shadow root, and its "Submit" is a <ui5-button> whose inner
   * <button> shows a slotted label. Read at the light DOM, that page had no
   * controls at all: no root, so no sweep (only TYPED answers survived,
   * through answers.js's edit backstop), and a Submit that read as nothing,
   * so not even a near miss was logged. A real application was lost to it.
   * So every query below descends into open roots and every walk up steps
   * out of one to its host, as answers.js's collect() and closestDeep()
   * already did. A closed root stays unreadable, as the platform intends. */
  function deepAll(root, sel) {
    const out = [...root.querySelectorAll(sel)];
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) out.push(...deepAll(el.shadowRoot, sel));
    }
    return out;
  }

  // The parent, stepping out of a shadow root to its host.
  function up(x) {
    if (x.parentElement) return x.parentElement;
    const r = x.getRootNode ? x.getRootNode() : null;
    return r && r !== x && r.host ? r.host : null;
  }

  function closestDeep(el, sel) {
    for (let x = el; x; x = up(x)) if (x.matches && x.matches(sel)) return x;
    return null;
  }

  // Controls that hold an answer — not buttons, not a search box.
  const SKIP_TYPES = ["hidden", "submit", "button", "reset", "image", "search"];
  const answerable = (el) => el.tagName !== "INPUT" ||
    !SKIP_TYPES.includes((el.getAttribute("type") || "text").toLowerCase());
  const controlsIn = (root) => deepAll(root, "input, select, textarea").filter(answerable);
  const passwordsIn = (root) => deepAll(root, "input[type='password']");
  const hasPassword = (root) => passwordsIn(root).length > 0;
  const isFile = (el) => (el.getAttribute("type") || "").toLowerCase() === "file";

  /* How many of these controls ask something: the ones answers.js's sweep
   * can put a question to (its labelFor, exposed for this). A sign-in with
   * no password field is otherwise just fields and a "Submit". MyGreenhouse's
   * (1 Oct 2026, the sign-in Greenhouse's job boards offer for autofill)
   * takes an emailed security code, and its Submit filed an application:
   * no employer, title "MyGreenhouse", the sign-in's email as its one
   * answer. The sweep at that click read 8 controls, every one filled and
   * none labelled (their markup was not read), so this counts by the
   * sweep's rule, not by a guess at what a code's boxes look like. Without
   * answers.js (the popup's own injection, which sweeps nothing) every
   * control counts, as before. */
  function asking(controls) {
    const A = window.__trackerAnswers;
    if (!A || !A.labelFor) return controls.length;
    return controls.filter((el) => {
      try { return !!A.labelFor(el); } catch (e) { return false; }
    }).length;
  }

  /* A container holding a password field is a candidate SIGN-IN, and its
   * username is not an answer, unless it asks far more than a sign-in can.
   * A sign-in, or an account's registration, asks for an identity: an email,
   * names, a phone, a country. iCIMS's candidate profile (the first iCIMS
   * apply, 3 Oct 2026, read live the next day) asks 81 visible questions in
   * one form#profileForm AND creates the account in it: "Password" and
   * "Password (Re-enter)". Refusing it on the password left the whole
   * profile step with no root, so its sweeps read nothing and only what the
   * candidate typed reached the store (Last Name, prefilled, never did; nor
   * did any dropdown, whose choice iCIMS sets by script). The passwords are
   * never swept as answers either way (answers.js:valueOf). */
  const SIGN_IN_MAX_ASKS = 15;
  const signIn = (root) => hasPassword(root) && asking(controlsIn(root).filter((el) =>
    (el.getAttribute("type") || "").toLowerCase() !== "password")) < SIGN_IN_MAX_ASKS;

  function inside(node, ancestor) {
    for (let x = node; x; x = up(x)) if (x === ancestor) return true;
    return false;
  }

  /* The form a resume's file input belongs to: the nearest one around it that
   * holds something besides uploads. A form holding nothing else is the
   * upload widget's own machinery. UI5's <ui5-file-uploader> keeps its
   * <input type=file> in a private <form> inside its shadow root (read live
   * on a SuccessFactors candidate experience, 3 Oct 2026); once deepAll could
   * see that input, the nearest form was that one-control form, it became the
   * application's root, and the real Submit sat outside it: a sent
   * application was turned down as "the button is outside the application
   * form". The form the widget sits in (there form#careerform) is next. */
  function formAround(el) {
    for (let form = closestDeep(el, "form"); form; form = closestDeep(up(form), "form")) {
      if (!controlsIn(form).every(isFile)) return form;
    }
    return null;
  }

  function commonAncestor(nodes) {
    for (let a = up(nodes[0]); a; a = up(a)) {
      if (nodes.every((n) => inside(n, a))) return a;
    }
    return null;
  }

  // An apply flow by its address: Workday's …/apply/…, Ashby's /application,
  // a SuccessFactors career site's career_ns=job_application. Needed for the
  // steps of a wizard that carry no file input of their own. The words are
  // jobposting.js's, where idFrom stops looking for the job's id.
  const APPLY_PATH = new RegExp(`(^|/)(${J.APPLY_SEGMENTS.join("|")})(/|$)`, "i");
  const applyFlowAt = (loc) => APPLY_PATH.test(loc.pathname || "") ||
    /career_ns=job_application/i.test(loc.search || "");
  const rendered = (el) => !el.getClientRects || el.getClientRects().length > 0;

  /* A later step of a form this visit already found. SmartRecruiters' apply
   * (read live 30 Sep 2026) keeps its resume on step 1, where rule 3 found
   * the form, and asks its screening questions on step 2, at an address
   * ending /screening with no <form> and no file input: nothing said
   * "application" there, and its Submit was turned down. What does say so is
   * the visit: shared/answers.js keeps the form's answers in this tab's
   * sessionStorage under the form's key (answerFormKey, here the publication
   * id both steps share), marked `rooted` by a sweep that found the form's
   * root. Answers its edit backstop kept on a page with no form never carry
   * the mark, and the store lasts as long as answers.js keeps it. */
  const ANSWERS_KEY = "__tracker_form_answers";
  const ANSWERS_MAX_AGE_MS = 2 * 60 * 60 * 1000;     // answers.js:MAX_AGE_MS
  function formContinues(doc, loc) {
    try {
      const rec = JSON.parse(sessionStorage.getItem(ANSWERS_KEY) || "null");
      if (!rec || !rec.rooted || !Object.keys(rec.items || {}).length) return false;
      if (!(Date.now() - rec.at <= ANSWERS_MAX_AGE_MS)) return false;
      const id = J.pageId(doc, loc, hints());
      return rec.key === (id ? id.platform_job_id : loc.href);
    } catch (e) { return false; }     // no storage: a test, or storage blocked
  }
  // An application's flow: its address says so, or the page continues a form
  // found on an earlier step, unless it is a LISTING, whose own "Apply" only
  // leaves for one (rule 3's P2 guard, below).
  const inFlow = (doc, loc) => applyFlowAt(loc) || (!J.hasPosting(doc) && formContinues(doc, loc));

  // The employer an earlier page of this visit named in its address
  // (SuccessFactors' `?company=`), kept in the hiring system's OWN
  // sessionStorage for the pages whose address has lost it: the form after a
  // postback. Same tab and same origin, so it outlives the postback and a
  // sign-in (docs/career-sites.md §16). Read wherever the page's id is.
  const TENANT_KEY = "__tracker_ats_tenant";
  function tenantHint() {
    try {
      const t = J.tenantOf(location.href);
      if (t) { sessionStorage.setItem(TENANT_KEY, t); return t; }
      return sessionStorage.getItem(TENANT_KEY);
    } catch (e) { return null; }       // no storage: a test, or storage blocked
  }
  const hints = () => ({ tenant: tenantHint() });

  // The job id of the page this one was reached from, on THIS host only: the
  // referrer. SmartRecruiters' form page names its listing so (measured live
  // 30 Sep 2026), and the listing's id is the one the handoff holds. An
  // address with no id of its own (a board's index) names nothing.
  function arrivedFrom(doc, loc) {
    let r;
    try { r = new URL(doc.referrer); } catch (e) { return null; }
    if (r.hostname.toLowerCase() !== String(loc.hostname || "").toLowerCase()) return null;
    const id = J.idFrom(r.href);
    return id && id.by !== "path" ? id.platform_job_id : null;
  }

  /* The element holding the application's answerable controls, or null when
   * this page has none. Never a candidate sign-in (signIn: a password field
   * in a container that asks no more than a sign-in does). In order:
   *  1. the <form> the resume's file input sits in (Lever, Greenhouse,
   *     Workable), never an upload widget's own (formAround);
   *  2. a <form> with the fields of an application (five that ask
   *     something) and a control inside it that says it sends one
   *     (SuccessFactors, whose address cannot be trusted — see below);
   *  3. on a page with a file input or in an application's flow (inFlow:
   *     its address, or a later step of a form found before), the nearest
   *     container of every VISIBLE answerable control (Ashby's
   *     `ashby-job-posting-right-pane`: all 21 visible controls; a Workday
   *     wizard step; SmartRecruiters' screening step). */
  function applicationRoot(doc, loc) {
    const all = controlsIn(doc);
    if (all.length < 2) return null;
    const files = all.filter(isFile);
    for (const f of files) {
      const form = formAround(f);
      if (form && !signIn(form)) return form;
    }
    // A form that SAYS it sends an application, with the fields of one. The
    // SuccessFactors form, read live 24 Sep 2026 (form#careerform: 59 fields,
    // no file input — its resume is an attachment widget — and "Apply" as a
    // <span role=button>), sits at an address that drops career_ns after any
    // postback (a Save, an upload, the register step), and a real application
    // was missed exactly there. Five fields at least, so a job-alert or
    // sign-up form (one to three, measured on the four other vendors) is not
    // taken for one, and five that ASK something (asking(), below). The
    // candidate experience's form (29 Sep 2026) is found here too, through
    // its Submit's slotted label: its address need not say "application"
    // either.
    for (const b of deepAll(doc, "button, input, [role='button']")) {
      if (!submitWorded(b)) continue;
      const form = closestDeep(b, "form");
      if (form && !signIn(form) && asking(controlsIn(form)) >= MIN_FORM_FIELDS) return form;
    }
    // Rule 3 needs an address that says "application", or a file input on a
    // page that is NOT a listing. A job page that publishes a JobPosting and
    // carries an upload widget of its own (a Career Site Builder's "match
    // your CV", 28 Sep 2026) would otherwise become an "application" whose
    // root holds the page's own <a role=button>Apply now</a>, and the click
    // that only LEAVES for the application would file one.
    if (!inFlow(doc, loc) && (!files.length || J.hasPosting(doc))) return null;
    // The controls a person can SEE decide the container. Measured live on
    // Ashby: 21 controls sit in its form pane and the 22nd is reCAPTCHA's
    // hidden response field, portalled to <body> — counting it made the whole
    // page the root. Hidden controls inside the container are still swept
    // (answers.js decides what is machinery); they just cannot move it.
    const seen = all.filter(rendered);
    const root = commonAncestor(seen.length ? seen : all);
    return root && !signIn(root) ? root : null;
  }

  /* A hiring system's own page: on the vendor's host (atsOfUrl), or on an
   * employer's domain that runs the vendor's app AND holds the application
   * form right here. Eightfold's candidate site is served as
   * careers.<employer> (read live 2 Oct 2026: scripts from vscdn.net, the job
   * at /careers/job/<pid>, its form at /careers/apply?pid=<pid>, one id for
   * both), so the page with its form is the hiring system's, whatever the
   * domain. The form is the test, not the vendor alone: a SuccessFactors
   * career site loads the vendor's files too, and its listing's number is not
   * the id its form will carry (docs/career-sites.md §16, P4). */
  function hiringSystem(doc, loc) {
    return !!J.atsOfUrl(loc.href) || (!!J.vendorOf(doc, loc) && !!applicationRoot(doc, loc));
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
  const clean = (s) => String(s || "").replace(INVISIBLE, "").replace(/\s+/g, " ").trim();
  // The text a web component shows THROUGH a control: its inner <button>
  // holds only a <slot>, and the words are the host's own children.
  const slotted = (el) => (el.querySelectorAll ? [...el.querySelectorAll("slot")] : [])
    .map((s) => (s.assignedNodes ? s.assignedNodes({ flatten: true }) : [])
      .map((n) => n.textContent || "").join(" "))
    .join(" ");
  // The text a control SHOWS: its own, leaving out every part that renders no
  // box. Darwinbox's submit (read live 8 Oct 2026) holds its word twice,
  // <span class="text">Submit</span><span class="text-2">Submit</span>, the
  // second display:none, and its textContent "Submit Submit" matched no
  // submit word: the form's own Submit was never taken for one, and the
  // application it sent went unrecorded. A <slot> renders no box either
  // (display: contents), so a web component's inner button reads nothing
  // here and falls through to slotted(), as before.
  const shown = (el) => {
    const walk = (n) => {
      let out = "";
      for (const c of n.childNodes || []) {
        if (c.nodeType === 3) { out += c.textContent; continue; }
        if (c.nodeType !== 1) continue;
        if (c.getClientRects && c.getClientRects().length === 0) continue;
        out += " " + walk(c);
      }
      return out;
    };
    return clean(walk(el));
  };
  // What a control says: what it shows, else its whole text (a control whose
  // every part is hidden), else what is slotted into it (<ui5-button>Submit
  // </ui5-button>, 29 Sep 2026), else an input's value, else its accessible
  // name.
  const label = (el) => shown(el) || clean(el.textContent) || clean(slotted(el)) ||
    clean(el.value) || clean(el.getAttribute && el.getAttribute("aria-label"));

  const MIN_FORM_FIELDS = 5;

  // A control that says it sends an application — whether or not it is inside
  // one. isSubmitControl() adds the "inside the application" half.
  function submitWorded(el) {
    if (!el || !el.tagName) return false;
    const type = (el.getAttribute("type") || "").toLowerCase();
    const buttonish = el.tagName === "BUTTON" ||
      (el.tagName === "INPUT" && (type === "submit" || type === "button")) ||
      el.getAttribute("role") === "button";
    if (!buttonish) return false;
    const hooked = SUBMIT_HOOKS.some((s) => el.matches && el.matches(s));
    return hooked || SUBMIT_WORDS.test(label(el));
  }

  /* A wizard's LAST step, which shows the answers as text. Workday's review
   * page has no controls, so there is no root, and its "Submit" is what sends
   * the application. Workday keeps one address for the whole wizard
   * (…/apply/autofillWithResume), and on 25 Sep 2026 a real submit there was
   * turned down with "no application form found on this page"; the
   * application reached the tracker only through the job board's popover,
   * with none of its answers (docs/career-sites.md §16). So: on an address
   * that says it is the application, a page with no root and no password
   * field IN VIEW is that step. A sign-in on the same address shows its
   * password and stays refused; one left in the DOM, closed, does not count.
   * Earlier steps' answers are already in answers.js's store. A later step of
   * a form found before (inFlow) counts as the flow too. */
  function reviewStep(doc, loc) {
    if (!inFlow(doc, loc)) return false;
    return !passwordsIn(doc).some(rendered);
  }

  /* A CONFIRMATION over the application. Darwinbox's candidate portal
   * (8 Oct 2026, read in its own code, since pressing it would send) answers
   * the form's "Submit" with a modal, "Submit" / "Cancel", and only the
   * modal's Submit sends. The modal is appended to <body>, outside the form,
   * so the press that sent a real application was turned down as "the button
   * is outside the application form". A dialog that asks nothing (no control
   * but a box to tick, a consent) over a page whose application is found
   * outside it is that application's last step. A dialog holding fields is a
   * form of its own, and one the root sits inside is the form itself. */
  const DIALOG = "dialog, [role='dialog'], [role='alertdialog'], [aria-modal='true']";
  function confirmsApplication(el, root) {
    const dlg = closestDeep(el, DIALOG);
    if (!dlg || inside(root, dlg)) return false;
    return controlsIn(dlg).every((c) => (c.getAttribute("type") || "").toLowerCase() === "checkbox");
  }

  function isSubmitControl(el, doc, loc) {
    if (!submitWorded(el)) return false;
    const root = applicationRoot(doc, loc);
    return root ? inside(el, root) || confirmsApplication(el, root) : reviewStep(doc, loc);
  }

  /* Why a submit-worded control was NOT taken for the application's submit,
   * or null when it was. The failing branch's own diagnostic: on 24 Sep 2026
   * a real SuccessFactors application went unrecorded and left nothing in
   * any buffer — the script never ran on its address, and had it run, a
   * rejected submit would have been just as silent. Only browser history
   * could reconstruct it. */
  function whyNotSubmit(el, doc, loc) {
    if (!submitWorded(el)) return null;
    const root = applicationRoot(doc, loc);
    if (!root) return reviewStep(doc, loc) ? null : "no application form found on this page";
    return inside(el, root) || confirmsApplication(el, root)
      ? null : "the button is outside the application form";
  }

  /* QUICK APPLY (jobposting.js:quickApplies; docs/career-sites.md §16.3
   * item 6). A SuccessFactors job page's own "Apply" can send the
   * application with no form. Its press leaves a NOTE in the hiring system's
   * own sessionStorage (this tab, this origin), and the page it lands on,
   * which says the application was sent, files it: capture.js asks
   * landedCompletion() at load. The note alone files nothing. A candidate who
   * is not signed in gets a sign-in first, and the note waits for ten minutes.
   * Only on a JOB page: no application form here (a form's own submit is the
   * form hook's), no sign-in in view, and a real id for the job. */
  const QA_KEY = "__tracker_quick_apply";
  const QA_WINDOW_MS = 10 * 60 * 1000;

  function quickApplyStart(el, doc = document, loc = location, now = Date.now()) {
    if (!J.quickApplies(loc.href) || !submitWorded(el) || applicationRoot(doc, loc)) return null;
    if (passwordsIn(doc).some(rendered)) return null;
    const id = J.pageId(doc, loc, hints());
    const job = J.read(doc, loc, hints());
    if (!job || !id || id.by === "path") return null;
    return { at: now, atsJobId: id.platform_job_id, job: { ...job, _prov: undefined } };
  }

  function landedCompletion(now) {
    if (!J.quickApplySent(location.href)) return null;
    let note = null;
    try {
      note = JSON.parse(sessionStorage.getItem(QA_KEY) || "null");
      sessionStorage.removeItem(QA_KEY);
    } catch (e) { return null; }
    if (!note || !note.job || !(now - note.at <= QA_WINDOW_MS)) return null;
    return { job: note.job, atsJobId: note.atsJobId || null };
  }

  const adapter = {
    platform: "other",
    applySelectors: [],
    // A submit here is an application sent on the employer's own system; when
    // the tab was opened by a job board's "Apply on company website", the
    // capture completes THAT record instead of starting its own (§8).
    linksOpener: true,
    getJob() {
      const job = J.read(document, location, hints());
      // A listing on an employer's own site that says where it hands over
      // proposes the id its hiring system may hold, for the server to look
      // up, never to store (P4; jobposting.js:atsCandidates).
      if (job && !J.atsOfUrl(location.href)) {
        const c = J.atsCandidates(job, J.atsHandoff(document));
        if (c.length) job.ats_job_candidates = c;
      }
      return job;
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
    // The listing and its apply page share an id: idFrom looks for it before
    // the /apply or /application segment, since what follows is the flow's
    // own state (Workday's /apply/autofillWithResume, Oracle's
    // /apply/section/1 — which read as job 1 until 28 Sep 2026), so the
    // answers store and the listing's stash both survive the move. The
    // PAGE's id, not the address's: SuccessFactors' form address is a session
    // crumb after any postback, and a sign-in mid-form (28 Sep 2026) would
    // otherwise start both over (jobposting.js:pageId).
    answerFormKey() {
      const id = J.pageId(document, location, hints());
      return id ? id.platform_job_id : location.href;
    },
    // The job's own id on its hiring system, which the server keeps on the
    // JOB (migration 018, docs/career-sites.md §16): this page's id, only on
    // a hiring system's own page (hiringSystem: the vendor's host, or its
    // form under an employer's domain) and only a real one, never a crumb.
    // capture.js sends it beside the identity, since a link to a job board's
    // record replaces the page's.
    atsJobId() {
      if (!hiringSystem(document, location)) return null;
      const id = J.pageId(document, location, hints());
      return id && id.by !== "path" ? id.platform_job_id : null;
    },
    isCompletion(el) {
      return isSubmitControl(el, document, location);
    },
    // Why a submit-worded control was turned down, or null (the popup's
    // near-miss line, below).
    nearMiss(el) {
      return whyNotSubmit(el, document, location);
    },
    // An application a quick apply sent from the previous page, or null.
    landedCompletion(now = Date.now()) {
      return landedCompletion(now);
    },
    // A candidate sign-in in view: it interrupts a form rather than ending
    // it, so capture.js does not report the form's answers as left unsent.
    signInInView() {
      return passwordsIn(document).some(rendered);
    },
    // Pure forms of the rules, for tests/test_extension.js.
    applicationRoot,
    hiringSystem,
    arrivedFrom,
    isSubmitControl,
    quickApplyStart,
    label,
  };
  window.__trackerAdapter = adapter;

  // A press on something that says "Apply"/"Submit" which the rule turned
  // down goes into the popup's failure list with the reason — so the next
  // missed application explains itself. The page's own address only, never
  // its query: a candidate portal's query carries session tokens.
  let lastMissAt = 0;
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
    document.addEventListener("click", (ev) => {
      if (Date.now() - lastMissAt < 3000) return;
      const path = ev.composedPath ? ev.composedPath() : [ev.target];
      const el = path.find((x) => x && x.tagName && submitWorded(x));
      if (!el) return;
      // A quick apply's press is not a miss: it leaves its note instead.
      const note = quickApplyStart(el, document, location, Date.now());
      if (note) {
        try { sessionStorage.setItem(QA_KEY, JSON.stringify(note)); } catch (e) { /* storage blocked */ }
        return;
      }
      const reason = whyNotSubmit(el, document, location);
      if (!reason) return;
      lastMissAt = Date.now();
      try {
        chrome.runtime.sendMessage({
          type: "tracker-capture-failure",
          detail: { platform: "other", at: Date.now(), url: location.origin + location.pathname,
                    error: `"${label(el).slice(0, 40)}" was not captured: ${reason}` },
        }).catch(() => {});
      } catch (e) { /* context gone */ }
    }, true);
  }

  /* Remember the job while its listing is on screen. Apply pages drop the
   * JobPosting the listing published (Lever, Workable, Personio), so without
   * this the submit would file the apply page's tab title. Keyed by the id
   * the apply page will ask for; the worker expires it unsent (2 h) when
   * nothing is submitted. Only a page that PUBLISHES a JobPosting is a
   * listing worth remembering, whatever its title was read from: until
   * 28 Sep 2026 this asked for a structured TITLE, and a Career Site Builder
   * page that writes its title outside its JobPosting was never stashed. A
   * client-rendered one may not exist yet at load, hence the retries. The
   * listing also says where it hands its applicant over, when it says
   * (jobposting.js:atsHandoff), for the handoff to check against. */
  function stashListing() {
    let job = null;
    try { job = J.read(document, location); } catch (e) { return true; }
    if (!(job && job._prov && job._prov.structured)) return false;
    const handoff = J.atsHandoff(document);
    if (handoff) job.handoff = handoff;
    try {
      // Keyed by the id: for an apply page on the same site (Lever's /apply).
      chrome.runtime.sendMessage({ type: "tracker-stash-job", key: adapter.answerFormKey(), job })
        .catch(() => {});
      // And under this TAB: for a hiring system on ANOTHER site, reached in
      // this same tab — an employer's career site sends you to its ATS, whose
      // form names neither the company nor the job the way the listing does
      // (docs/career-sites.md phase C; background.js:takeExternal).
      chrome.runtime.sendMessage({ type: "tracker-stash-external",
                                   job: { ...job, _prov: undefined } })
        .catch(() => {});
    } catch (e) { /* no extension context: a test, or a reloaded extension */ }
    return true;
  }
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id && !stashListing()) {
    setTimeout(stashListing, 1500);
    setTimeout(stashListing, 5000);
  }

  /* THE HANDOFF, from the hiring system's side (docs/career-sites.md §16.3
   * item 2). Its page, loading in the tab's top frame, tells the worker where
   * it is; the worker binds this tab to the listing the applicant just left
   * (jobposting.js:pickDeparture) and keeps that for days. Every later page
   * of the visit says so again, which adds the job's id once an address or a
   * heading shows it.
   * On a site the user enabled (an employer's own domain, which may serve a
   * hiring system's app: Eightfold, 2 Oct 2026) the page says it is a `site`:
   * there only the tab that opened this one can bind it, and a binding with
   * no id fits a submit only when the titles agree (handoffFits). Its form
   * may render after load, so it says so again twice, as stashListing does.
   * The binding takes the PAGE's own id, form or not: Eightfold's job page
   * and its form share one (`/careers/job/<pid>`, `?pid=<pid>`), and a
   * binding that learned no id at the job's page could later take a second
   * job's. The worker keeps an id to its own host. */
  function claimHandoff() {
    if (window !== window.top) return;
    const site = !J.atsOfUrl(location.href);
    let atsJobId = null, fromId = null, listing = false;
    try {
      const id = J.pageId(document, location, hints());
      atsJobId = id && id.by !== "path" ? id.platform_job_id : null;
    } catch (e) { atsJobId = null; }
    // Where this page was reached from, and whether it is a listing: how the
    // worker tells a job's second id from another job's (learnsAlias).
    try { fromId = arrivedFrom(document, location); } catch (e) { fromId = null; }
    try { listing = J.hasPosting(document); } catch (e) { listing = false; }
    try {
      chrome.runtime.sendMessage({ type: "tracker-claim-handoff", page: {
        host: location.hostname.toLowerCase(),
        vendor: site ? J.vendorOf(document, location) : J.atsOfUrl(location.href),
        tenant: tenantHint(), atsJobId, fromId, listing, site } }).catch(() => {});
    } catch (e) { /* no extension context: a test, or a reloaded extension */ }
  }
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
    claimHandoff();
    if (!J.atsOfUrl(location.href)) {
      setTimeout(claimHandoff, 1500);
      setTimeout(claimHandoff, 5000);
    }
  }
})();
