/* Application-form Q&A capture (loaded BEFORE shared/capture.js).
 *
 * Opt-in per platform: the adapter provides answerFormRoot(), returning the
 * element whose form controls are the application form — or null when this
 * page/frame has none. Adapters without it get no answer capture and no cost.
 *
 * Three things make this harder than "read the form at submit":
 *
 *  1. It's a WIZARD. LinkedIn's Easy Apply replaces the step's fields in place
 *     (contact -> resume -> questions -> review), so by the time "Submit
 *     application" is clicked the earlier steps' inputs are gone from the DOM.
 *     Snapshots are therefore taken on every click, in the CAPTURE phase —
 *     before the page's own handler advances the step — and accumulated.
 *  2. Most answers are PREFILLED. LinkedIn remembers what you answered last
 *     time, so the user often touches nothing at all; an input/change listener
 *     alone would record an empty form. The DOM sweep is the primary source;
 *     the change listener only backstops fields the sweep can't reach.
 *  3. Some controls live in SHADOW ROOTS (see the composedPath gotcha in
 *     shared/capture.js). Open ones the sweep descends into. Closed ones it
 *     can't — but their events still cross the boundary composed, so the
 *     change/input listener catches those, which is exactly why both exist.
 *  4. Labels REPEAT. A work-history section is a repeater: "Industry", "City",
 *     "Month of From", once per employer you list. They are N answers sharing
 *     one label, not one answer given N times, so the store is keyed
 *     `question#occurrence` — occurrence being the field's index among
 *     same-labelled fields in that sweep. Keying on the label alone is what
 *     silently kept only the last entry of a real multi-employer history
 *     (VANARSDEL, 27 Jul 2026); see migration 010.
 *  5. The control's NAME may live on an ARIA wrapper, not on the control. The
 *     rebuilt Easy Apply (Aug 2026, measured live 2 Sep) renders
 *     <div role="radio"|"checkbox"> around a bare native input whose own
 *     <label for> is EMPTY; labelFor() reads the wrapper the way assistive
 *     tech would, and radioOption()/radioQuestion() sort out which of the
 *     wrapper's name and its visible text is the question and which the answer
 *     — LinkedIn uses them both ways round on the same wizard.
 *
 * The store is mirrored into sessionStorage so a step that reloads the modal
 * iframe doesn't reset it (same origin + same tab = same store, whichever
 * frame writes it).
 */
(() => {
  const adapter = window.__trackerAdapter;
  if (!adapter || !adapter.answerFormRoot) return;

  const KEY = "__tracker_form_answers";
  const MAX_AGE_MS = 2 * 60 * 60 * 1000;   // a stale form is not this form
  const MAX_ANSWER = 4000;

  /* ------------------------------------------------------------- store */

  // Which application this store belongs to. When it changes (next job), the
  // previous job's answers are dropped rather than bleeding into it.
  const formKey = () => {
    try {
      return (adapter.answerFormKey && adapter.answerFormKey()) ||
             String((window.top || window).location.href);
    } catch (e) { return String(location.href); }
  };

  function load() {
    try {
      const raw = sessionStorage.getItem(KEY);
      if (!raw) return {};
      const rec = JSON.parse(raw);
      if (rec.key !== formKey() || Date.now() - rec.at > MAX_AGE_MS) return {};
      return rec.items || {};
    } catch (e) { return {}; }
  }

  function save(items) {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(
        { key: formKey(), at: Date.now(), items }));
    } catch (e) { /* private mode / quota — in-memory still works this step */ }
  }

  let items = load();     // "question_norm#occurrence" -> {question, answer, type, i}
  let seq = Object.keys(items).length;
  let key = formKey();
  let stats = null;       // per-sweep diagnostics, read by take()

  // These SPAs swap jobs without a page load, so the frame (and this store)
  // outlives the form it was filled for. Re-check before every read or write:
  // job B must never inherit job A's answers.
  function syncKey() {
    const k = formKey();
    if (k === key) return;
    key = k;
    items = {};
    seq = 0;
    stats = null;
  }

  const normKey = (q) => q.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

  // occurrence is the field's index among same-labelled fields; see note 4.
  // Keyed rather than appended so a re-sweep of the SAME step overwrites in
  // place — the click listener fires many times per step, and the store has to
  // be idempotent under that or every click would duplicate the form.
  function record(question, answer, type, occurrence) {
    if (!question || !answer) return;
    const norm = normKey(question);
    if (!norm) return;
    const k = `${norm}#${occurrence || 0}`;
    const prev = items[k];
    items[k] = {
      question: question.slice(0, 300),
      answer: String(answer).slice(0, MAX_ANSWER),
      type,
      i: prev ? prev.i : seq++,
    };
  }

  // How many occurrences of this question the store already holds — the index
  // a control the sweep can't see should claim.
  function countFor(norm) {
    let n = 0;
    while (Object.prototype.hasOwnProperty.call(items, `${norm}#${n}`)) n++;
    return n;
  }

  /* ------------------------------------------------------- DOM helpers */

  const text = (n) => ((n && (n.innerText || n.textContent)) || "")
    .replace(/\s+/g, " ").trim();

  /* A label as assistive technology would read it: subtrees marked
   * aria-hidden="true" are skipped.
   *
   * LinkedIn's Easy Apply labels carry the standard accessibility double — the
   * visible copy is aria-hidden so a screen reader doesn't announce it twice,
   * and a `.visually-hidden` twin beside it carries the accessible name:
   *
   *   <label for="…">
   *     <span aria-hidden="true">Email address</span>
   *     <span class="visually-hidden">Email address</span>
   *   </label>
   *
   * BOTH are rendered — visually-hidden clips, it does not display:none — so
   * innerText returns "Email address Email address". Confirmed live against a
   * real Easy Apply form on 3 Aug 2026, and it had already reached the answer
   * bank on 19 distinct questions. Cosmetic on one application, corrosive
   * across many: "City" and "City City" normalise to different keys, so one
   * question splits into two rows that never group again — and the grouped
   * /answers view is the entire point of storing these.
   *
   * The 28 Jul aria-labelledby de-duplication could not have caught this. That
   * one compares label PARTS against each other, and these fields carry no
   * aria-labelledby at all; the doubling lives inside a single <label for=…>,
   * which is a different branch of labelFor() entirely.
   *
   * Two deliberate guards: skip nodes with no client rects, so this keeps
   * innerText's display:none behaviour rather than silently gaining
   * textContent's (a visually-hidden span still HAS rects — that is exactly
   * how it differs from a hidden one); and fall back to the raw text when
   * skipping leaves nothing, so a label whose only content is aria-hidden with
   * no visually-hidden twin still resolves instead of coming back empty. */
  function labelText(node) {
    if (!node) return "";
    const walk = (n) => {
      let out = "";
      for (const c of n.childNodes) {
        if (c.nodeType === 3) { out += c.textContent; continue; }
        if (c.nodeType !== 1) continue;
        if (c.getAttribute("aria-hidden") === "true") continue;
        if (c.getClientRects && c.getClientRects().length === 0) continue;
        out += " " + walk(c);
      }
      return out;
    };
    return walk(node).replace(/\s+/g, " ").trim() || text(node);
  }

  // .closest() stops at a shadow boundary; hop to the host and keep going.
  function closestDeep(el, sel) {
    let node = el;
    while (node) {
      const hit = node.closest ? node.closest(sel) : null;
      if (hit) return hit;
      const root = node.getRootNode ? node.getRootNode() : null;
      node = root && root.host ? root.host : null;
    }
    return null;
  }

  // The accessible name an element carries on ITSELF — aria-labelledby, then
  // aria-label. Nothing inferred from <label> elements or from content; that is
  // labelFor()'s job, and the two are deliberately not the same question.
  function ariaName(el) {
    const ids = el.getAttribute("aria-labelledby");
    if (ids) {
      const root = el.getRootNode ? el.getRootNode() : document;
      const t = ids.split(/\s+/)
        .map((id) => (root.getElementById && root.getElementById(id)) ||
                     document.getElementById(id))
        .filter(Boolean).map(labelText).join(" ").trim();
      if (t) return t;
    }
    return (el.getAttribute("aria-label") || "").trim() || null;
  }

  // ARIA widget roles a native control may be wrapped in on the rebuilt Easy
  // Apply. The wrapper, not the input, is what carries the name there.
  const WIDGET = "[role='radio'],[role='checkbox'],[role='switch']," +
                 "[role='combobox'],[role='textbox'],[role='spinbutton']";

  // The text block immediately BEFORE a control group — how the rebuilt Easy
  // Apply labels a Yes/No question (<p>Will you…?*</p>, then a legendless
  // <fieldset role="radiogroup">) and the resume picker (a heading block, then
  // the cards). Two guards: a sibling that holds controls of its own is the
  // PREVIOUS question, not this one's label, so it yields nothing; and a block
  // of several children is read by its first child — the heading — so the
  // description line under "Resume*" does not ride into the question text.
  function precedingText(node) {
    for (let n = node, hops = 0; n && hops < 3; n = n.parentElement, hops++) {
      const prev = n.previousElementSibling;
      if (!prev) continue;
      if (prev.querySelector && prev.querySelector("input,select,textarea")) return null;
      const kids = prev.children ? Array.from(prev.children) : [];
      const head = kids.length >= 2 ? labelText(kids[0]) : "";
      return (head.length > 1 ? head : labelText(prev)) || null;
    }
    return null;
  }

  function collect(root, out) {
    if (!root || !root.querySelectorAll) return out;
    for (const el of root.querySelectorAll("input,select,textarea")) out.push(el);
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) collect(el.shadowRoot, out);   // open roots only
    }
    return out;
  }

  // Same shadow-piercing need as collect(), for the noRoot diagnostic's own
  // "was a dialog actually open" check — a plain document.querySelector was
  // confirmed live (3 Aug 2026) to report false even with a real dialog on
  // screen, because it was inside an open shadow root. Open roots only.
  function deepExists(root, selector) {
    if (root.querySelector(selector)) return true;
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot && deepExists(el.shadowRoot, selector)) return true;
    }
    return false;
  }

  function labelFor(el) {
    const root = el.getRootNode ? el.getRootNode() : document;
    const byId = (id) =>
      (root.getElementById && root.getElementById(id)) || document.getElementById(id);

    const ids = el.getAttribute("aria-labelledby");
    if (ids) {
      // Join the parts, but NOT blindly: LinkedIn routinely points
      // aria-labelledby at both a wrapper and the label nested inside it, so a
      // naive join yields "Country Country" / "Location (city) Location
      // (city)" — seen on real captures. Those normalise differently from the
      // same question labelled cleanly elsewhere, which splits the /answers
      // bank in two. Drop a part already covered by one we kept, keeping
      // whichever text is fuller.
      const kept = [];
      for (const part of ids.split(/\s+/).map(byId).filter(Boolean).map(labelText)) {
        if (!part) continue;
        const lp = part.toLowerCase();
        const at = kept.findIndex((k) => {
          const lk = k.toLowerCase();
          return lk === lp || lk.includes(lp) || lp.includes(lk);
        });
        if (at === -1) kept.push(part);
        else if (part.length > kept[at].length) kept[at] = part;
      }
      const t = kept.join(" ").trim();
      if (t) return t;
    }
    if (el.id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && labelText(l)) return labelText(l);
    }
    const wrapping = closestDeep(el, "label");
    if (wrapping && labelText(wrapping)) return labelText(wrapping);
    const aria = (el.getAttribute("aria-label") || "").trim();
    if (aria) return aria;
    // The rebuilt Easy Apply (Aug 2026) gives the native control NO name of its
    // own — its <label for> exists and is empty — and hangs the name on an ARIA
    // widget wrapping it: <div role="checkbox" aria-label="Mark job as a top
    // choice"> around a bare <input type="checkbox">. Read the wrapper the way
    // assistive tech would. This has to sit ABOVE the placeholder/name fallback:
    // those inputs carry generated names ("radio-group-«rg»") that would
    // otherwise be recorded as the question.
    const widget = closestDeep(el, WIDGET);
    if (widget && widget !== el) {
      const t = ariaName(widget) || labelText(widget);
      if (t) return t;
    }
    const fs = closestDeep(el, "fieldset");
    if (fs) {
      const legend = fs.querySelector("legend");
      if (legend && labelText(legend)) return labelText(legend);
    }
    return (el.getAttribute("placeholder") || el.name || "").trim() || null;
  }

  /* A native radio's OPTION text and its group's QUESTION.
   *
   * Two layouts. The classic Easy Apply modal is textbook: <fieldset><legend>
   * holds the question and each <input type="radio"> has a <label for> reading
   * "Yes"/"No" (or, on the resume picker, "Deselect resume <file>.pdf"). The
   * rebuilt one (Aug 2026, live-measured 2 Sep) wraps each input in a
   * <div role="radio">, leaves the input's own <label for> EMPTY, and puts the
   * name on the wrapper — inconsistently: a Yes/No option's wrapper has
   * aria-label = the QUESTION with "Yes"/"No" as visible text inside it, while
   * a resume card's wrapper has aria-label = the FILENAME and no text inside
   * at all. The group's <fieldset role="radiogroup"> has no legend; the
   * question sits in the sibling <p> (or heading block) just before it.
   *
   * So: option = the wrapper's visible text, else its own name. Question =
   * legend, else the group's own name, else the wrapper's name when that is
   * not the option just read, else the block before the group. Every step
   * falls through to the classic path, which is what keeps the old modal
   * correct — it has no wrappers, so none of the new steps fire. */
  function radioOption(el) {
    const w = closestDeep(el, "[role='radio']");
    if (w && w !== el) {
      const shown = labelText(w);
      if (shown) return shown;
      const own = ariaName(w);
      if (own) return own;
    }
    return labelFor(el) || el.value || null;
  }

  function radioQuestion(el, option) {
    const fs = closestDeep(el, "fieldset");
    const legend = fs && fs.querySelector("legend");
    if (legend && labelText(legend)) return labelText(legend);
    const group = closestDeep(el, "[role='radiogroup'],fieldset");
    if (group) {
      const own = ariaName(group);
      if (own) return own;
    }
    const w = closestDeep(el, "[role='radio']");
    if (w && w !== el) {
      const own = ariaName(w);
      if (own && own !== option) return own;
    }
    if (group) {
      const before = precedingText(group);
      if (before) return before;
    }
    return labelFor(el);
  }

  // Placeholder options ("Select an option") are the absence of an answer, not
  // an answer — recording them would fill the bank with noise.
  const PLACEHOLDER = /^(select an option|please select|choose(\.\.\.| an option)?|-+|—)$/i;

  function valueOf(el) {
    const tag = el.tagName;
    const type = (el.type || "").toLowerCase();
    if (["file", "hidden", "submit", "button", "reset", "image", "password"]
        .includes(type)) return null;
    if (type === "checkbox") return el.checked ? "Yes" : "No";
    if (tag === "SELECT") {
      const opt = el.selectedOptions && el.selectedOptions[0];
      const v = opt ? text(opt) : "";
      return v && !PLACEHOLDER.test(v) ? v : null;
    }
    const v = (el.value || "").trim();
    return v || null;
  }

  function kindOf(el) {
    if (el.tagName === "SELECT") return "select";
    if (el.tagName === "TEXTAREA") return "textarea";
    const t = (el.type || "text").toLowerCase();
    return t === "checkbox" ? "checkbox" : (t === "radio" ? "radio" : t);
  }

  /* --------------------------------------------------------- the sweep */

  /* Diagnostics. The DOM scrape is structural, not selector-based, so when it
   * misses something there is no console error to read — the answer is just
   * absent, which is how a real capture lost every textarea on its form and
   * nobody could say whether the sweep never saw them or couldn't label them.
   * These counts ride along with the capture and land in the extension popup.
   * `textareaSeen` and `noLabelMax` are maxima across the wizard's steps, not
   * just the final one: the interesting step is usually not the last. */
  function bump(s) {
    if (!stats) stats = { sweeps: 0, noRoot: 0, textareaSeen: 0, noLabelMax: 0, last: null,
                          noRootHint: null };
    stats.sweeps++;
    if (s.noRoot) { stats.noRoot++; stats.noRootHint = s.hint; return; }
    stats.textareaSeen = Math.max(stats.textareaSeen, s.textarea);
    stats.noLabelMax = Math.max(stats.noLabelMax, s.noLabel);
    stats.last = s;
  }

  // A real apply (Bellows & Munson, 2 Aug 2026) swept 38 times and never once
  // resolved a root, even with the modal demonstrably open — and there was
  // nothing to look at afterwards to say why, since the application was
  // already submitted by the time anyone went looking. This breadcrumb is
  // adapter-agnostic on purpose (shared/ has no platform knowledge): it can't
  // say the new selector is still wrong, but it separates "nothing dialog-like
  // was ever open" from "something was open and unlabelled inputs existed
  // right there on the page" — which is the difference between "the user
  // wasn't in the form yet" and "the root selector needs another look".
  // Confirmed on a second real miss (Relecloud, 3 Aug 2026, 5-7 varied
  // screening questions, genuinely open) that `dialogPresent` alone isn't
  // enough: it came back false the WHOLE session — that posting's modal used
  // none of the four known markers, not just a missing <form>.
  //
  // Two position-based attempts at describing WHAT was there both failed,
  // live-tested against the real page the same day: `controls[0]` (first
  // control in document order) landed on LinkedIn's persistent top-nav search
  // box every time, modal open or not — it's earlier in the DOM than any page
  // content. The last control was Google reCAPTCHA's own hidden textarea,
  // ALSO portalled to the end of <body>, with no modal open at all. Filtering
  // both out still left 40+ candidates once a messaging panel happened to be
  // open — a busy SPA has too many legitimate non-modal controls for position
  // to mean anything.
  //
  // What actually works, live-tested the same day: a real Easy Apply modal on
  // this page renders 3 levels deep inside an EXISTING wrapper (not a fresh
  // top-level node), so watching `document.body` alone misses it — but a
  // `subtree: true` observer catches the actual field elements
  // (`fb-dash-form-element`, `artdeco-text-input`) arriving in real time, the
  // instant the modal opens, regardless of where they end up in the tree or
  // how much unrelated chrome surrounds them. That's a direct answer instead
  // of a position guess, so it replaces the DOM-snapshot approach entirely.
  // Only runs in the top frame (an iframe's `answerFormRoot()` always
  // succeeds — see the adapter — so an iframe never has a noRoot miss to
  // explain) and only past a config gate so JobStreet/Indeed pay nothing for
  // a mechanism aimed at a LinkedIn-specific failure mode.
  const recentFormInserts = [];
  if (window === window.top && adapter.trackFormInserts) {
    try {
      new MutationObserver((muts) => {
        for (const m of muts) {
          for (const node of m.addedNodes) {
            if (node.nodeType !== 1 || !node.querySelector) continue;
            if (!node.querySelector("input,select,textarea,form")) continue;
            let depth = 0, n = node;
            while (n && n !== document.body) { n = n.parentElement; depth++; }
            recentFormInserts.unshift({
              tag: node.tagName,
              cls: (typeof node.className === "string" ? node.className : "").slice(0, 80),
              role: node.getAttribute && node.getAttribute("role"),
              depthFromBody: depth,
            });
            if (recentFormInserts.length > 5) recentFormInserts.length = 5;
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    } catch (e) { /* document.body not ready at very early injection */ }
  }

  function noRootHint() {
    return {
      // Both spellings of "a dialog": the classic role attribute and the
      // native element the rebuilt Easy Apply uses (implicit role, so a
      // `[role='dialog']` check reported "no dialog open" for two weeks of
      // captures that were made inside one).
      dialogPresent: deepExists(document, "dialog[open], [role='dialog']"),
      controlsOnPage: document.querySelectorAll("input,select,textarea").length,
      recentInserts: recentFormInserts.slice(0, 5),
    };
  }

  function sweep() {
    syncKey();
    let root = null;
    try { root = adapter.answerFormRoot(); } catch (e) { root = null; }
    if (!root) {
      bump({ noRoot: 1, hint: noRootHint() });
      return;
    }

    const controls = collect(root, []);
    const radioGroups = new Map();     // name -> {question, answer}
    // Occurrence counter, reset per sweep so re-sweeping one step lands on the
    // same keys and overwrites rather than appending (see record()). Fields
    // from an EARLIER wizard step keep whatever keys they were given — this
    // sweep can't see them, so nothing here touches them.
    const occ = new Map();
    const next = (q) => {
      const n = normKey(q);
      const i = occ.get(n) || 0;
      occ.set(n, i + 1);
      return i;
    };
    const seen = { controls: controls.length, kept: 0, disabled: 0,
                   noLabel: 0, noValue: 0, textarea: 0 };

    for (const el of controls) {
      if (el.tagName === "TEXTAREA") seen.textarea++;
      if (el.disabled) { seen.disabled++; continue; }
      if ((el.type || "").toLowerCase() === "radio") {
        // One entry per GROUP, keyed by name: the question from the group, the
        // answer from whichever member is checked — see radioOption() for the
        // two layouts this has to read.
        const name = el.name || labelFor(el) || "";
        if (!name) continue;
        const option = radioOption(el);
        const g = radioGroups.get(name) || { question: null, answer: null };
        if (!g.question) g.question = radioQuestion(el, option);
        if (el.checked) g.answer = option || el.value;
        radioGroups.set(name, g);
        continue;
      }
      const question = labelFor(el);
      const answer = valueOf(el);
      // Counted before the value check so an unlabelled control shows up in
      // the diagnostic even when it's also empty — "no label resolved" is the
      // failure that loses a real answer; "empty" is the user not filling it.
      if (!question) { seen.noLabel++; continue; }
      if (!answer) { seen.noValue++; continue; }
      record(question, answer, kindOf(el), next(question));
      seen.kept++;
    }
    for (const g of radioGroups.values()) {
      if (g.question && g.answer) {
        record(g.question, g.answer, "radio", next(g.question));
        seen.kept++;
      }
    }
    save(items);
    bump(seen);
  }

  /* ------------------------------------------------------------ hooks */

  const trySweep = () => { try { sweep(); } catch (e) {} };

  // Capture phase: the page's own click handler is what advances the wizard,
  // and it runs after this — so the fields being read are still the ones the
  // user was looking at when they clicked.
  //
  // The two delayed sweeps are for the other kind of click: picking a
  // TYPEAHEAD suggestion doesn't advance anything, it writes into the box the
  // user was typing in — asynchronously, after this handler. Reading only in
  // the capture phase stored the half-typed prefix ("singa" for a city of
  // Singapore, seen on a real capture). Re-reading afterwards costs a DOM walk
  // over one modal and corrects it; if the click DID advance the step, the old
  // fields are simply gone and their stored answers stay untouched, because a
  // sweep only overwrites the questions it can currently see.
  document.addEventListener("click", () => {
    trySweep();
    setTimeout(trySweep, 0);
    setTimeout(trySweep, 300);
  }, true);

  // Backstop for controls inside CLOSED shadow roots, which sweep() cannot
  // reach: input/change are composed, so composedPath()[0] is the real target
  // even though ev.target has been retargeted to the host.
  const editKey = new WeakMap();   // unsweepable element -> the key it claimed

  const onEdit = (ev) => {
    try {
      const el = (ev.composedPath && ev.composedPath()[0]) || ev.target;
      if (!el || !el.tagName ||
          !["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName)) return;
      if ((el.type || "").toLowerCase() === "radio") { trySweep(); return; }
      const answer = valueOf(el);
      const question = labelFor(el);
      if (!question || !answer) return;
      syncKey();

      // The sweep is the primary source and the only thing that knows a
      // field's occurrence, so let it run first and defer to it. Only if it
      // came back without this exact answer is the control somewhere the sweep
      // can't go — then claim an index of our own and keep reusing it, so
      // later keystrokes correct the same entry instead of piling up.
      trySweep();
      const norm = normKey(question);
      for (const k of Object.keys(items)) {
        if (k.startsWith(`${norm}#`) && items[k].answer === answer) return;
      }
      let k = editKey.get(el);
      if (!k) {
        k = `${norm}#${countFor(norm)}`;
        editKey.set(el, k);
      }
      record(question, answer, kindOf(el), Number(k.slice(k.lastIndexOf("#") + 1)));
      save(items);
    } catch (e) {}
  };
  document.addEventListener("change", onEdit, true);
  document.addEventListener("input", onEdit, true);

  /* Read by shared/capture.js at the moment of the apply click. Sweeps once
   * more first (the final step's own fields aren't in the store yet), then
   * clears — the next application starts empty rather than inheriting this
   * one's answers. */
  window.__trackerAnswers = {
    // What the sweep saw on its way here, for the popup's diagnostic list.
    // Read before take() clears the store, or not at all.
    diagnostics() {
      return stats && stats.last
        ? { sweeps: stats.sweeps, noRoot: stats.noRoot,
            textareaSeen: stats.textareaSeen, noLabelMax: stats.noLabelMax,
            kept: Object.keys(items).length, last: stats.last }
        : { sweeps: (stats && stats.sweeps) || 0,
            noRoot: (stats && stats.noRoot) || 0,
            textareaSeen: 0, noLabelMax: 0, kept: Object.keys(items).length,
            last: null, noRootHint: stats && stats.noRootHint };
    },
    take() {
      try { sweep(); } catch (e) {}
      const out = Object.values(items)
        .sort((a, b) => a.i - b.i)
        .map(({ question, answer, type }) => ({ question, answer, type }));
      items = {};
      seq = 0;
      stats = null;
      try { sessionStorage.removeItem(KEY); } catch (e) {}
      return out;
    },
  };
})();
