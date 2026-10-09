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
 *     — LinkedIn uses them both ways round on the same wizard. By 25 Sep the
 *     wrapper was gone and the name sat on the input itself, still both ways
 *     round; radioGroup() tells them apart by reading the whole group at once.
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

  function loadRec() {
    try {
      const raw = sessionStorage.getItem(KEY);
      if (!raw) return null;
      const rec = JSON.parse(raw);
      if (rec.key !== formKey() || Date.now() - rec.at > MAX_AGE_MS) return null;
      return rec;
    } catch (e) { return null; }
  }

  // `rooted`: a sweep of THIS form found its root, on this page or an earlier
  // one. adapters/generic.js reads it (formContinues): a later wizard step
  // whose address never says "apply" and which has no file input
  // (SmartRecruiters' /screening, 30 Sep 2026) continues a form found before.
  // Answers the edit backstop kept on a page with no form never set it, so a
  // job-alert box typed into does not make the next "Submit" an application.
  // Not on the page a capture just took the store on (`takenOn`, set by
  // take()): the submit click's own delayed sweeps (0 and 300 ms, for a
  // typeahead, below) and any later click there refill the store from the
  // form the capture already sent, and the next full page load then reports
  // it as a form "left holding answers" (2 Oct 2026: a Phenom review step's
  // store, found on disk, saved 100 ms after its capture). In memory they are
  // kept: a second submit there still sends them, and the server's upsert
  // never deletes what a capture did not ask.
  let takenOn = null;
  function save(items) {
    if (takenOn !== null && takenOn === String(location.href)) return;
    try {
      sessionStorage.setItem(KEY, JSON.stringify(
        { key: formKey(), at: Date.now(), items, rooted }));
    } catch (e) { /* private mode / quota — in-memory still works this step */ }
  }

  const rec0 = loadRec();
  let items = (rec0 && rec0.items) || {};  // "question_norm#occurrence" -> {question, answer, type, i}
  let rooted = !!(rec0 && rec0.rooted);
  let seq = Object.keys(items).length;
  let key = formKey();
  let stats = null;       // per-sweep diagnostics, read by take()
  // element -> the key a sweep last recorded from it, so a field emptied in
  // place can give that key back (giveBack, below).
  let swept = new WeakMap();

  // These SPAs swap jobs without a page load, so the frame (and this store)
  // outlives the form it was filled for. Re-check before every read or write:
  // job B must never inherit job A's answers.
  function syncKey() {
    const k = formKey();
    if (k === key) return;
    key = k;
    items = {};
    rooted = false;
    seq = 0;
    stats = null;
    takenOn = null;
    swept = new WeakMap();
  }

  // pipeline/answers.py:norm_question, character for character (it also strips
  // a trailing required-marker; this key being FINER than the server's is
  // harmless). Letters, marks and digits in any script are kept, and a # or +
  // run glued to a letter is spelled, so C# / C++ / C stay three questions.
  // Coarser is NOT harmless: until 24 Sep 2026 this kept [a-z0-9] only, and a
  // "C#" question on one wizard step and a "C++" one on the next shared the
  // key "…with c#0" — the later sweep overwrote the earlier answer before the
  // capture was sent. tests/question_norms.json holds both sides to one list.
  const normKey = (q) => q.normalize("NFKC").toLowerCase()
    .replace(/(?<=\p{L})[#+]+/gu, (run) => run.replace(/#/g, " sharp ").replace(/\+/g, " plus "))
    .replace(/[^\p{L}\p{M}\p{N}]+/gu, " ").trim();

  // pipeline/answers.py:_SENSITIVE_RE, word for word: questions whose ANSWER
  // is identity or protected-characteristic data (an ID number, date of birth,
  // race, religion, gender, disability …). The question is kept and the value
  // replaced HERE, so it never leaves the browser — not even into this tab's
  // sessionStorage. The server withholds the same answers again as a second
  // line; tests/sensitive_questions.json holds both to one list. Whole words of
  // the key, so "languages" holds no "age"; nationality and work authorisation
  // are deliberately absent, since the visa analysis reads them.
  const REDACTED = "(withheld)";
  const SENSITIVE = new RegExp(
    "(?:^| )(?:nric|fin|passport|mykad" +
    "|national (?:id|identity|identification|registration)" +
    "|identification (?:no|number)|ic (?:no|number)" +
    "|date of birth|dob|birth ?date|birthday|year of birth|age" +
    "|race|ethnic\\w*|religio\\w*|marital|gender|sex|sexual|veteran" +
    "|disabilit\\w*|disabled|self identif\\w*)(?= |$)");
  const isSensitive = (norm) => SENSITIVE.test(norm);
  // What the store holds for an answer — the one place a value is decided, so
  // record() and onEdit's "did the sweep already see this?" test agree.
  const stored = (norm, answer) =>
    isSensitive(norm) ? REDACTED : String(answer).slice(0, MAX_ANSWER);

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
      answer: stored(norm, answer),
      type,
      i: prev ? prev.i : seq++,
    };
    return k;
  }

  /* A field a sweep read an answer from, and now finds empty, was emptied in
   * place, by the user or by the page, and its answer goes. Until 8 Oct 2026
   * an empty field was only skipped, so the store kept whatever an earlier
   * sweep had read there: a SuccessFactors form sent one paragraph as the
   * answer to four "Brief Job Responsibilities" its candidate had left blank
   * on purpose. Only when the SAME element recorded that SAME key: a later
   * wizard step's blank field under one label is another question, and
   * leaves the earlier step's answer alone. */
  function giveBack(el, k) {
    if (swept.get(el) === k) delete items[k];
    swept.delete(el);
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
   * no visually-hidden twin still resolves instead of coming back empty.
   *
   * And it reads the FLAT tree, as assistive tech does: a shadow host's own
   * shadow root rather than its light children, and a <slot>'s assigned
   * nodes (or its fallback) rather than its empty self. A slot has no box of
   * its own (display: contents), so the rects guard skips it. SmartRecruiters'
   * screening step (30 Sep 2026) slots every question's words into its
   * label: <label for><slot name="label"></slot>*</label>, read alone as "*",
   * which keys as nothing, and every answer on the step was dropped. */
  function labelText(node) {
    if (!node) return "";
    const kids = (n) => (n.tagName === "SLOT" && n.assignedNodes
      ? n.assignedNodes({ flatten: true }) : (n.shadowRoot || n).childNodes);
    const walk = (n) => {
      let out = "";
      for (const c of kids(n)) {
        if (c.nodeType === 3) { out += c.textContent; continue; }
        if (c.nodeType !== 1) continue;
        if (c.getAttribute("aria-hidden") === "true") continue;
        if (c.tagName !== "SLOT" && c.getClientRects && c.getClientRects().length === 0) continue;
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
      // A control is never a label either: in a row of a code's boxes, each
      // box's neighbour is the box before it.
      if (prev.matches && prev.matches("input,select,textarea")) return null;
      if (prev.querySelector && prev.querySelector("input,select,textarea")) return null;
      const kids = prev.children ? Array.from(prev.children) : [];
      const head = kids.length >= 2 ? labelText(kids[0]) : "";
      return (head.length > 1 ? head : labelText(prev)) || null;
    }
    return null;
  }

  function collect(root, out, sel = "input,select,textarea") {
    if (!root || !root.querySelectorAll) return out;
    for (const el of root.querySelectorAll(sel)) out.push(el);
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) collect(el.shadowRoot, out, sel);   // open roots only
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
    // A file field is named by its GROUP: the only <label for> a styled
    // upload widget has is its own button's ("Attach"), while the question,
    // "Resume/CV*", names the role="group" around it (Greenhouse's job
    // boards, read live 29 Sep 2026) — or a fieldset's legend.
    if ((el.type || "").toLowerCase() === "file") {
      const g = closestDeep(el, "[role='group'],fieldset");
      const named = g && (g.tagName === "FIELDSET"
        ? labelText(g.querySelector("legend")) : ariaName(g));
      if (named) return named;
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
    // Its TEXT only when it wraps this control alone: a combobox's wrapper also
    // holds the listbox it opens, and its text is the choice plus every option.
    // Darwinbox's dropdown (read live 8 Oct 2026) keeps a hidden <select> with
    // the choice inside <div role="combobox">, beside a search box and a
    // listbox of 300 countries, and the select's question was stored as
    // "Singapore Search and Select Singapore Remove item Afghanistan…".
    const widget = closestDeep(el, WIDGET);
    if (widget && widget !== el) {
      const alone = collect(widget, []).every((c) => c === el);
      const t = ariaName(widget) || (alone ? labelText(widget) : "");
      if (t) return t;
    }
    const fs = closestDeep(el, "fieldset");
    if (fs) {
      const legend = fs.querySelector("legend");
      if (legend && labelText(legend)) return labelText(legend);
    }
    // An input mirroring a Workday listbox button is named as the button is:
    // the field's <label for> points at the button (listboxShown, below).
    const mirrored = mirroredButton(el);
    if (mirrored && mirrored.id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(mirrored.id)}"]`);
      if (l && labelText(l)) return labelText(l);
    }
    // Nothing names the control itself: the question is the block just before
    // its field. Lever's custom questions (read live 2 Oct 2026) are
    // <div class="application-label">…</div> beside the field's own <div>,
    // with no <label>, and every text one carries the placeholder "Type your
    // response". That is an instruction, not a question, so this sits above
    // the placeholder. Only for a control on screen: a hidden one named by
    // its own attribute is a captcha's field (machinery(), below).
    if (!el.getClientRects || el.getClientRects().length > 0) {
      const before = precedingText(el);
      if (before) return before;
    }
    const host = componentName(el);
    if (host) return host;
    return (el.getAttribute("placeholder") || el.name || "").trim() || null;
  }

  /* A control inside a COMPONENT is named as the component is named. Darwinbox
   * builds its application form from web components (read live 8 Oct 2026):
   * <label>First Name *</label> sits beside <dbx-textinput>, whose open shadow
   * root holds the <input placeholder="Enter Here">, and nothing inside the
   * root names it, so every field was stored as "Enter Here" or "Select
   * Date". precedingText() climbs parentElement, which ends at the top of the
   * shadow root; the label is beside the HOST. So: what names the host, read
   * the way labelFor() reads a control, when nothing inside named the control
   * itself. The block before the host is read only when the HOST is on
   * screen, as precedingText(el) is only for a control on screen; the control
   * itself may be hidden: the dropdown keeps its choice in a hidden <select>. */
  function componentName(el) {
    const r = el.getRootNode ? el.getRootNode() : null;
    const host = r && r.host;
    if (!host) return null;
    const root = host.getRootNode ? host.getRootNode() : document;
    if (host.id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(host.id)}"]`);
      if (l && labelText(l)) return labelText(l);
    }
    const wrapping = closestDeep(host, "label");
    if (wrapping && labelText(wrapping)) return labelText(wrapping);
    const own = ariaName(host);
    if (own) return own;
    if (!host.getClientRects || host.getClientRects().length > 0) return precedingText(host);
    return null;
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
   * correct — it has no wrappers, so none of the new steps fire. The third
   * layout, with the name on the input itself, only the whole group can read:
   * radioGroup(), below. */
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

  /* A fieldset's QUESTION: its <legend>, else its CAPTION — the first child,
   * when that child holds no control and labels none. Ashby's application
   * forms (read live 7 Oct 2026) ask every multiple-choice question so:
   * <fieldset><label for="…">Are you authorized to work in…?</label>, then
   * the options, each an <input> with its own <label for>, and the caption's
   * `for` names no control at all. With no legend to read, a radio group took
   * its FIRST OPTION's label for its question (radioQuestion's last resort),
   * so "Yes, I will require … to sponsor my employment" was stored as both
   * question and answer; and a set of checkboxes was read one box at a time,
   * each option a "question" answered Yes or No. Read only for a fieldset of
   * ONE question (holdsOnly): a section's fieldset opens with the section's
   * heading, which names none of the controls inside. */
  function caption(fs) {
    if (!fs || fs.tagName !== "FIELDSET") return null;
    const legend = fs.querySelector("legend");
    if (legend && labelText(legend)) return labelText(legend);
    const first = fs.children[0];
    if (!first || first.tagName === "LEGEND") return null;
    if ((first.matches && first.matches("input,select,textarea")) ||
        (first.querySelector && first.querySelector("input,select,textarea"))) return null;
    const id = first.tagName === "LABEL" ? first.getAttribute("for") : null;
    if (id) {
      const root = first.getRootNode ? first.getRootNode() : document;
      const target = (root.getElementById && root.getElementById(id)) || document.getElementById(id);
      if (target && target.matches && target.matches("input,select,textarea")) return null;
    }
    return labelText(first) || null;
  }

  // Does `fs` hold nothing but `members` — one question's options?
  const holdsOnly = (fs, members) => collect(fs, []).every((c) => members.includes(c));

  /* A fieldset of checkboxes that is ONE question (caption(), holdsOnly()),
   * or null: its boxes, read as one answer, the labels of the boxes ticked.
   * Two boxes at least: a lone checkbox is a statement ticked or not ("I have
   * read and agree…"), and its own label is right. Cached per sweep. */
  let boxSets = new Map();
  function boxSetOf(el) {
    const fs = closestDeep(el, "fieldset");
    if (!fs) return null;
    if (!boxSets.has(fs)) {
      const boxes = collect(fs, [], "input[type='checkbox']");
      boxSets.set(fs, boxes.length >= 2 && holdsOnly(fs, boxes) && caption(fs)
        ? { fs, boxes } : null);
    }
    return boxSets.get(fs);
  }

  function radioQuestion(el, option, shared, members) {
    const fs = closestDeep(el, "fieldset");
    const legend = fs && fs.querySelector("legend");
    if (legend && labelText(legend)) return labelText(legend);
    const group = closestDeep(el, "[role='radiogroup'],fieldset");
    if (group) {
      const own = ariaName(group);
      if (own) return own;
    }
    if (fs && members && holdsOnly(fs, members)) {
      const cap = caption(fs);
      if (cap) return cap;
    }
    if (shared) return shared;
    const w = closestDeep(el, "[role='radio']");
    if (w && w !== el) {
      const own = ariaName(w);
      if (own && own !== option) return own;
    }
    // No group element at all: the smallest box holding every option stands
    // in for one. Lever's custom Yes/No questions (2 Oct 2026) are a bare
    // <ul> of <label><input type=radio>Yes</label>, the question in the block
    // before the field, and labelFor(el) below names the first OPTION.
    const box = group || (members && members.length > 1 ? around(members) : null);
    if (box) {
      const before = precedingText(box);
      if (before) return before;
    }
    // Never the first option's own label: that names an answer, not the
    // question, and with nothing else to read it was stored as both (Ashby,
    // 3 Oct 2026). No question is better than a wrong one; the sweep counts
    // the group as unlabelled.
    const own = labelFor(el);
    return own && own !== option ? own : null;
  }

  // The nearest ancestor of every one of `members`.
  function around(members) {
    const holds = (box, m) => { for (let x = m; x; x = x.parentElement) if (x === box) return true; return false; };
    for (let n = members[0].parentElement; n; n = n.parentElement) {
      if (members.every((m) => holds(n, m))) return n;
    }
    return null;
  }

  /* One radio GROUP's question and answer, read from every member at once.
   *
   * By 25 Sep 2026 LinkedIn had dropped the role="radio" wrapper and moved the
   * name onto the native input, still meaning two things (measured live
   * 30 Sep): on a Yes/No question every input's aria-label is the QUESTION and
   * "Yes"/"No" is a <p> in a sibling <div>; on the resume picker each input's
   * aria-label is its FILENAME. One member alone cannot say which it holds, and
   * radioOption() read the question as the answer on every radio for five
   * days (33 answers, 16 applications). The group can: a name EVERY member
   * carries names the group, not an option, so it is the question, and the
   * option is the checked member's own row. Names that differ (filenames,
   * or the Yes/No text of every earlier layout) are options, as before. */
  function radioGroup(members) {
    const names = members.map(radioOption);
    const shared = members.length > 1 && names[0] &&
      names.every((n) => n === names[0]) ? names[0] : null;
    const on = members.findIndex((m) => m.checked);
    const question = radioQuestion(members[0], shared ? null : names[0], shared, members);
    if (on === -1) return { question, answer: null };
    // No row text means no answer, never the input's value: a radio with no
    // value attribute reports "on".
    const answer = shared ? optionRow(members[on], members) : (names[on] || members[on].value);
    return { question, answer };
  }

  // The visible text of a member's own row: its largest ancestor that holds
  // no other member of the group.
  function optionRow(el, members) {
    const theirs = new Set();
    for (const m of members) {
      if (m !== el) for (let n = m.parentElement; n; n = n.parentElement) theirs.add(n);
    }
    let row = null;
    for (let n = el.parentElement; n && !theirs.has(n); n = n.parentElement) row = n;
    return (row && labelText(row)) || null;
  }

  /* Controls the page DRAWS: role="radio", "checkbox" or "switch" on an
   * element with no native control in it or in its shadow root, its state in
   * aria-checked. SmartRecruiters' screening step (read live 30 Sep 2026) asks
   * its Yes/No questions so: <spl-radio role="radio" aria-checked
   * label="Yes">, slotted into the <fieldset role="radiogroup"
   * aria-labelledby> inside <spl-radio-group>. collect() takes native
   * controls only, so both visa questions on that step were invisible. An
   * ARIA wrapper AROUND a native input (the rebuilt Easy Apply's) is not one:
   * its input answers, and labelFor() reads the wrapper for its name. */
  const DRAWN = "[role='radio'],[role='checkbox'],[role='switch']";
  function drawn(root) {
    return collect(root, [], DRAWN).filter((el) => el.tagName !== "INPUT" &&
      !collect(el, []).length && !(el.shadowRoot && collect(el.shadowRoot, []).length));
  }

  // The parent in the FLAT tree: a slotted element's slot, else its parent,
  // else the host of the shadow root it tops. A drawn radio's group is its
  // ancestor there, not in the light DOM, where the group is a host whose
  // fieldset the radio is slotted into.
  const flatParent = (n) => n.assignedSlot || n.parentElement ||
    ((n.getRootNode && n.getRootNode()) || {}).host || null;
  function flatClosest(el, sel) {
    for (let x = flatParent(el); x; x = flatParent(x)) if (x.matches && x.matches(sel)) return x;
    return null;
  }

  /* A drawn radio group's question and answer. The question is the group's
   * own name, else its legend, else the block before it. The answer is the
   * checked option's shown text, else its own name, and never the question
   * itself: an option named only by its group's question is the artefact of
   * 25-29 Sep 2026, the question stored as its own answer. */
  function drawnGroup(group, members) {
    const legend = group.querySelector && group.querySelector("legend");
    const question = ariaName(group) || (legend && labelText(legend)) || precedingText(group) || null;
    const on = members.find((m) => m.getAttribute("aria-checked") === "true");
    const answer = on ? (labelText(on) || ariaName(on)) : null;
    return { question, answer: answer && answer !== question ? answer : null };
  }

  // Placeholder options ("Select an option") are the absence of an answer, not
  // an answer — recording them would fill the bank with noise. Dashes either
  // side are decoration ("— Make a Selection —", iCIMS, 3 Oct 2026); Workday's
  // empty dropdown reads "Select One" (9 Oct 2026).
  const PLACEHOLDER = /^[\s\-–—]*(select an option|select one|select(\.\.\.|…)|please select|make a selection|choose(\.\.\.| an option)?)?[\s\-–—]*$/i;

  /* A live region speaks a widget's state to a screen reader; it is never
   * the widget's value. SuccessFactors' comboboxes keep one beside the input
   * ("One or more results available. Press Up or Down Arrow Keys…"), iCIMS's
   * another ("1 result available. Use down and up arrow keys…"), and those,
   * with the dropdown's arrow drawn by an icon font as a Private Use Area
   * character (U+E1EF), stood as the answer to blank comboboxes: 22 rows on
   * 7 applications, 3-8 Oct 2026. Neither is a choice anyone sees. */
  const SPOKEN = /^(status|alert|log|marquee|timer)$/i;
  const announces = (n) => {
    const live = (n.getAttribute("aria-live") || "").toLowerCase();
    return (live !== "" && live !== "off") || SPOKEN.test(n.getAttribute("role") || "");
  };

  /* The choice a combobox SHOWS rather than holds. React-select, on
   * Greenhouse's job boards (read live 29 Sep 2026): its <input
   * role="combobox"> is emptied after every pick and the chosen option is
   * drawn in a sibling <div class="select__single-value">; with nothing
   * picked, a placeholder that the input names in aria-describedby. So: the
   * text of the nearest ancestor that has any, leaving out the input and
   * whatever its aria-describedby names (the placeholder, the error), at
   * most three levels up, and never a container that holds the question (a
   * <label>, or what aria-labelledby names), whose text would come back as
   * the question's own answer. */
  function shownChoice(el) {
    const ids = (a) => (el.getAttribute(a) || "").split(/\s+/).filter(Boolean);
    const skip = new Set(ids("aria-describedby"));
    const question = ids("aria-labelledby")
      .map((id) => document.getElementById(id)).filter(Boolean);
    const holds = (n, q) => { for (let x = q; x; x = x.parentElement) if (x === n) return true; return false; };
    const own = (n) => {
      let out = "";
      for (const c of n.childNodes) {
        if (c.nodeType === 3) { out += c.textContent; continue; }
        if (c.nodeType !== 1 || c === el || c.getAttribute("aria-hidden") === "true") continue;
        if (skip.has(c.getAttribute("id")) || announces(c)) continue;
        out += " " + own(c);
      }
      return out;
    };
    for (let n = el.parentElement, hops = 0; n && hops < 3; n = n.parentElement, hops++) {
      if (n.tagName === "LABEL" || (n.querySelector && n.querySelector("label")) ||
          question.some((q) => holds(n, q))) return null;
      const t = own(n).replace(/\p{Co}/gu, "").replace(/\s+/g, " ").trim();
      if (t) return PLACEHOLDER.test(t) ? null : t.slice(0, MAX_ANSWER);
    }
    return null;
  }

  /* Workday draws a dropdown as a <button aria-haspopup="listbox"> showing the
   * choice ("United States of America", "Select One" before any), and keeps
   * the choice's internal id in an <input type="text"> beside it that is not
   * displayed; the field's <label for> names the button, not the input. The
   * sweep reads inputs, so it took that input and stored its value: 77
   * answers on 22 applications, every one a 32-hex id nobody can read (read
   * live 9 Oct 2026 on a Workday "Introduce Yourself" form: three dropdowns,
   * each a button and an input sharing one parent, the button's `value` the
   * same id, both empty while the button says "Select One"). That mirror is
   * the test, not visibility: there the input was display:none and no label
   * named it, yet the stored ids sit under their questions, so on the
   * application's own step the input was reachable some other way. An input
   * beside exactly one listbox button holding its value answers with what
   * the button shows; a placeholder is no answer. A search box beside a
   * toggle never mirrors one, and an EMPTY input mirrors nothing (it reads
   * as no answer already, and must not borrow a neighbour's shown choice).
   * `undefined`: not this shape, read the input. labelFor() names such an
   * input as its button is named (mirroredButton), since the field's label
   * names the button: read as the block before it, the input was named by
   * the button's own text, its answer. */
  function mirroredButton(el) {
    if (el.tagName !== "INPUT" || !el.parentElement || !(el.value || "").trim()) return null;
    const btns = [...el.parentElement.children]
      .filter((n) => n.tagName === "BUTTON" && n.getAttribute("aria-haspopup") === "listbox");
    return btns.length === 1 && (btns[0].getAttribute("value") || "") === el.value ? btns[0] : null;
  }

  function listboxShown(el) {
    const btn = mirroredButton(el);
    if (!btn) return undefined;
    const t = text(btn).replace(/\p{Co}/gu, "").trim();
    return t && !PLACEHOLDER.test(t) ? t.slice(0, MAX_ANSWER) : null;
  }

  function valueOf(el) {
    const tag = el.tagName;
    const type = (el.type || "").toLowerCase();
    const shown = listboxShown(el);
    if (shown !== undefined) return shown;
    // A file field answers with the NAME of the file chosen, never its path
    // (the browser reports C:\fakepath\…): which resume went with the
    // application, where the form uploads one rather than picking it
    // (Greenhouse's job boards, 29 Sep 2026; pipeline/answers.py promotes it).
    if (type === "file") {
      const names = [...(el.files || [])].map((f) => f && f.name).filter(Boolean);
      return names.length ? names.join(", ").slice(0, MAX_ANSWER) : null;
    }
    if (["hidden", "submit", "button", "reset", "image", "password"]
        .includes(type)) return null;
    if (type === "checkbox") return el.checked ? "Yes" : "No";
    if (tag === "SELECT") {
      const opt = el.selectedOptions && el.selectedOptions[0];
      const v = opt ? text(opt) : "";
      return v && !PLACEHOLDER.test(v) ? v : null;
    }
    const v = (el.value || "").trim();
    if (v) return v;
    return el.getAttribute && el.getAttribute("role") === "combobox" ? shownChoice(el) : null;
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

  /* Page MACHINERY, not a question: a control nobody can see whose only name
   * is its own name/placeholder attribute — labelFor()'s last fallback. A
   * captcha's response field is exactly that (reCAPTCHA's
   * `g-recaptcha-response`, hCaptcha's `h-captcha-response`): hidden, named
   * for code, and holding a token. On Easy Apply it never mattered, since the
   * captcha sits outside the dialog; on an ATS it sits INSIDE the application
   * (Lever's hCaptcha writes into its form, and on Ashby the reCAPTCHA field
   * is the one control outside the form's pane — measured live 24 Sep 2026),
   * where the sweep would have filed the token as the answer to a question
   * called "g-recaptcha-response".
   * BOTH conditions, never visibility alone: the rebuilt Easy Apply hides its
   * native radios and checkboxes behind ARIA wrappers, and those have a real
   * label (the wrapper's) — skipping on visibility is how every radio answer
   * would be lost again. */
  const rendered = (el) => !el.getClientRects || el.getClientRects().length > 0;
  /* And a search box that FILTERS a native <select>: a combobox text input in
   * the same field as the select. iCIMS's dropdowns (read live 4 Oct 2026)
   * keep the choice on a visibility:hidden <select> with its own <label>
   * ("Country", "Singapore"), which the sweep reads; the box beside it, named
   * only by its placeholder "— Type to Search —", holds whatever was typed to
   * find the option ("singa"), and the edit backstop stored that as the
   * answer, 9 times on the first iCIMS apply. Two levels up at most: the
   * select shares the box's field, not merely its form. Darwinbox's
   * (8 Oct 2026) is <input type="search" role="textbox"> beside a hidden
   * <select>, so a search-typed box counts as a combobox's does. */
  function filterBox(el) {
    if (el.tagName !== "INPUT") return false;
    if (el.getAttribute("role") !== "combobox" &&
        (el.getAttribute("type") || "").toLowerCase() !== "search") return false;
    for (let n = el.parentElement, hops = 0; n && hops < 2; n = n.parentElement, hops++) {
      if (n.querySelector && n.querySelector("select")) return true;
    }
    return false;
  }
  /* And an OPTION's own box. Darwinbox draws each option of a dropdown as
   * <div role="option"><input type="checkbox">…</div> (8 Oct 2026): the sweep
   * read every one as a question, "No" for each country not picked, 1,790 of
   * one application's 1,824 entries. An option is a choice, never a
   * question; the dropdown's answer is its select's. */
  const OPTION = "[role='option']";
  function machinery(el, question) {
    if (filterBox(el) || closestDeep(el, OPTION)) return true;
    if (rendered(el)) return false;
    const own = ((el.getAttribute && el.getAttribute("placeholder")) || el.name || "").trim();
    return !!own && question === own;
  }

  function sweep() {
    syncKey();
    let root = null;
    try { root = adapter.answerFormRoot(); } catch (e) { root = null; }
    if (!root) {
      bump({ noRoot: 1, hint: noRootHint() });
      return;
    }
    rooted = true;

    const controls = collect(root, []);
    const radioGroups = new Map();     // name -> its member inputs, in order
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
                   noLabel: 0, noValue: 0, textarea: 0, drawn: 0 };

    boxSets = new Map();
    const sets = new Set();            // the checkbox sets met, each read once below
    for (const el of controls) {
      if (el.tagName === "TEXTAREA") seen.textarea++;
      if (el.disabled) { seen.disabled++; continue; }
      if ((el.type || "").toLowerCase() === "checkbox") {
        const set = boxSetOf(el);
        if (set) { sets.add(set); continue; }
      }
      if ((el.type || "").toLowerCase() === "radio") {
        // One entry per GROUP, keyed by name, read once every member is in
        // hand: radioGroup() needs them all to tell the question from the
        // options.
        const name = el.name || labelFor(el) || "";
        if (!name) continue;
        if (!radioGroups.has(name)) radioGroups.set(name, []);
        radioGroups.get(name).push(el);
        continue;
      }
      const question = labelFor(el);
      // Before the label check: an option's box often has no name at all,
      // and 531 of them counted as "no label resolved" on Darwinbox's form.
      if (machinery(el, question)) continue;
      const answer = valueOf(el);
      // Counted before the value check so an unlabelled control shows up in
      // the diagnostic even when it's also empty — "no label resolved" is the
      // failure that loses a real answer; "empty" is the user not filling it.
      if (!question) { seen.noLabel++; continue; }
      // Numbered filled or not, so a blank field keeps its place among
      // same-labelled ones and the next one keeps its key.
      const occurrence = next(question);
      if (!answer) { seen.noValue++; giveBack(el, `${normKey(question)}#${occurrence}`); continue; }
      swept.set(el, record(question, answer, kindOf(el), occurrence));
      seen.kept++;
    }
    for (const members of radioGroups.values()) {
      const g = radioGroup(members);
      if (g.question && g.answer) {
        record(g.question, g.answer, "radio", next(g.question));
        seen.kept++;
      } else if (g.answer) {
        seen.noLabel++;                // answered, but nothing names the question
      }
    }
    // A checkbox set (boxSetOf): one question, answered by the boxes ticked.
    for (const { fs, boxes } of sets) {
      const question = caption(fs);
      const ticked = boxes.filter((b) => b.checked && !b.disabled)
        .map((b) => labelFor(b) || b.value).filter(Boolean);
      if (!ticked.length) { seen.noValue++; continue; }
      record(question, ticked.join(", ").slice(0, MAX_ANSWER), "checkbox", next(question));
      seen.kept++;
    }
    // Drawn controls (drawn(), above): a radio read with its group, a
    // checkbox or switch on its own, answering as a native checkbox does.
    const drawnGroups = new Map();     // group element -> its drawn radios
    for (const el of drawn(root)) {
      seen.drawn++;
      if (el.getAttribute("aria-disabled") === "true") { seen.disabled++; continue; }
      if (el.getAttribute("role") === "radio") {
        const g = flatClosest(el, "[role='radiogroup'],fieldset") || flatParent(el);
        if (!g) continue;
        if (!drawnGroups.has(g)) drawnGroups.set(g, []);
        drawnGroups.get(g).push(el);
        continue;
      }
      const question = ariaName(el) || labelText(el) || null;
      if (!question) { seen.noLabel++; continue; }
      record(question, el.getAttribute("aria-checked") === "true" ? "Yes" : "No", "checkbox", next(question));
      seen.kept++;
    }
    for (const [g, members] of drawnGroups) {
      const r = drawnGroup(g, members);
      if (r.question && r.answer) {
        record(r.question, r.answer, "radio", next(r.question));
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
      // A box of a checkbox set is the set's to answer, as a radio is its
      // group's: recorded alone it would be an option standing as a question.
      if ((el.type || "").toLowerCase() === "checkbox") {
        boxSets = new Map();
        if (boxSetOf(el)) { trySweep(); return; }
      }
      const answer = valueOf(el);
      const question = labelFor(el);
      // A field only this backstop answered for, emptied: its answer goes too.
      if (!answer && editKey.has(el)) {
        syncKey();
        delete items[editKey.get(el)];
        editKey.delete(el);
        save(items);
        return;
      }
      if (!question || !answer || machinery(el, question)) return;
      syncKey();

      // The sweep is the primary source and the only thing that knows a
      // field's occurrence, so let it run first and defer to it. Only if it
      // came back without this exact answer is the control somewhere the sweep
      // can't go — then claim an index of our own and keep reusing it, so
      // later keystrokes correct the same entry instead of piling up.
      trySweep();
      const norm = normKey(question);
      // Compare what the store WOULD hold: a withheld answer never equals the
      // live value, and comparing raw would claim a second slot for it.
      const want = stored(norm, answer);
      for (const k of Object.keys(items)) {
        if (k.startsWith(`${norm}#`) && items[k].answer === want) return;
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
    // Exposed for tests/test_extension.js's parity checks against the server.
    normKey,
    isSensitive,
    // The question a control asks, or null: the sweep's own rule, read by
    // generic.js to tell an application's fields from a code's boxes.
    labelFor,
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
    /* A store this page did not fill: an EARLIER page's form, still holding
     * answers no capture took (take() clears the store). capture.js reports
     * it once, from the next page, so a submit no rule recognised leaves a
     * line in the popup instead of nothing: on 29 Sep 2026 a SuccessFactors
     * Submit read as nothing, and its 13 answers were found only by reading
     * this tab's sessionStorage off disk. The answers stay where they are,
     * marked so the report is not repeated; the next form's save() replaces
     * the record. Read at load, before any sweep on this page can do that. */
    leftover() {
      try {
        const rec = JSON.parse(sessionStorage.getItem(KEY) || "null");
        if (!rec || rec.reported || rec.key === formKey()) return null;
        const n = Object.keys(rec.items || {}).length;
        if (!n || !(Date.now() - rec.at <= MAX_AGE_MS)) return null;
        sessionStorage.setItem(KEY, JSON.stringify({ ...rec, reported: true }));
        return { at: rec.at, answers: n };
      } catch (e) { return null; }
    },
    take() {
      try { sweep(); } catch (e) {}
      const out = Object.values(items)
        .sort((a, b) => a.i - b.i)
        .map(({ question, answer, type }) => ({ question, answer, type }));
      items = {};
      rooted = false;
      seq = 0;
      stats = null;
      takenOn = String(location.href);
      try { sessionStorage.removeItem(KEY); } catch (e) {}
      return out;
    },
  };
})();
