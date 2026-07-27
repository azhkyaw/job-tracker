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

  let items = load();          // question_norm -> {question, answer, type, i}
  let seq = Object.keys(items).length;
  let key = formKey();

  // These SPAs swap jobs without a page load, so the frame (and this store)
  // outlives the form it was filled for. Re-check before every read or write:
  // job B must never inherit job A's answers.
  function syncKey() {
    const k = formKey();
    if (k === key) return;
    key = k;
    items = {};
    seq = 0;
  }

  const normKey = (q) => q.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

  function record(question, answer, type) {
    if (!question || !answer) return;
    const k = normKey(question);
    if (!k) return;
    const prev = items[k];
    items[k] = {
      question: question.slice(0, 300),
      answer: String(answer).slice(0, MAX_ANSWER),
      type,
      i: prev ? prev.i : seq++,
    };
  }

  /* ------------------------------------------------------- DOM helpers */

  const text = (n) => ((n && (n.innerText || n.textContent)) || "")
    .replace(/\s+/g, " ").trim();

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

  function collect(root, out) {
    if (!root || !root.querySelectorAll) return out;
    for (const el of root.querySelectorAll("input,select,textarea")) out.push(el);
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) collect(el.shadowRoot, out);   // open roots only
    }
    return out;
  }

  function labelFor(el) {
    const root = el.getRootNode ? el.getRootNode() : document;
    const byId = (id) =>
      (root.getElementById && root.getElementById(id)) || document.getElementById(id);

    const ids = el.getAttribute("aria-labelledby");
    if (ids) {
      const t = ids.split(/\s+/).map(byId).filter(Boolean).map(text).join(" ").trim();
      if (t) return t;
    }
    if (el.id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && text(l)) return text(l);
    }
    const wrapping = closestDeep(el, "label");
    if (wrapping && text(wrapping)) return text(wrapping);
    const aria = (el.getAttribute("aria-label") || "").trim();
    if (aria) return aria;
    const fs = closestDeep(el, "fieldset");
    if (fs) {
      const legend = fs.querySelector("legend");
      if (legend && text(legend)) return text(legend);
    }
    return (el.getAttribute("placeholder") || el.name || "").trim() || null;
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

  function sweep() {
    syncKey();
    let root = null;
    try { root = adapter.answerFormRoot(); } catch (e) { root = null; }
    if (!root) return;

    const controls = collect(root, []);
    const radioGroups = new Map();     // name -> {question, answer}

    for (const el of controls) {
      if (el.disabled) continue;
      if ((el.type || "").toLowerCase() === "radio") {
        // The group's question is the fieldset legend; the answer is the label
        // of whichever member is checked. Keyed by name so the group is
        // recorded once, not once per option.
        const name = el.name || labelFor(el) || "";
        if (!name) continue;
        const fs = closestDeep(el, "fieldset");
        const legend = fs && fs.querySelector("legend");
        const question = (legend && text(legend)) || labelFor(el);
        const g = radioGroups.get(name) || { question, answer: null };
        if (!g.question) g.question = question;
        if (el.checked) g.answer = labelFor(el) || el.value;
        radioGroups.set(name, g);
        continue;
      }
      const answer = valueOf(el);
      if (!answer) continue;
      const question = labelFor(el);
      if (!question) continue;
      record(question, answer, kindOf(el));
    }
    for (const g of radioGroups.values()) {
      if (g.question && g.answer) record(g.question, g.answer, "radio");
    }
    save(items);
  }

  /* ------------------------------------------------------------ hooks */

  // Capture phase: the page's own click handler is what advances the wizard,
  // and it runs after this — so the fields being read are still the ones the
  // user was looking at when they clicked.
  document.addEventListener("click", () => { try { sweep(); } catch (e) {} }, true);

  // Backstop for controls inside CLOSED shadow roots, which sweep() cannot
  // reach: input/change are composed, so composedPath()[0] is the real target
  // even though ev.target has been retargeted to the host.
  const onEdit = (ev) => {
    try {
      const el = (ev.composedPath && ev.composedPath()[0]) || ev.target;
      if (!el || !el.tagName ||
          !["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName)) return;
      if ((el.type || "").toLowerCase() === "radio") { sweep(); return; }
      const answer = valueOf(el);
      const question = labelFor(el);
      if (question && answer) {
        syncKey();
        record(question, answer, kindOf(el));
        save(items);
      }
    } catch (e) {}
  };
  document.addEventListener("change", onEdit, true);
  document.addEventListener("input", onEdit, true);

  /* Read by shared/capture.js at the moment of the apply click. Sweeps once
   * more first (the final step's own fields aren't in the store yet), then
   * clears — the next application starts empty rather than inheriting this
   * one's answers. */
  window.__trackerAnswers = {
    take() {
      try { sweep(); } catch (e) {}
      const out = Object.values(items)
        .sort((a, b) => a.i - b.i)
        .map(({ question, answer, type }) => ({ question, answer, type }));
      items = {};
      seq = 0;
      try { sessionStorage.removeItem(KEY); } catch (e) {}
      return out;
    },
  };
})();
