/* Shared capture core (design doc §6.1).
 *
 * The site adapter (loaded before this file) sets window.__trackerAdapter:
 *   platform        "linkedin" | "jobstreet" | "indeed"
 *   applySelectors  CSS selectors whose click means "the user is applying"
 *   isExternal(el)  optional: true when the button leaves for the employer site
 *   getJob()        {platform_job_id, url, company, title, jd_text,
 *                    location?, posted_label?, reposted?} | null — the last
 *                   three are optional/nullable: not every platform (or
 *                   every listing) exposes them
 *   getRecruiter()  optional: {name, url, role?} | null — a named hiring
 *                   contact shown on the job page (not the applicant's own
 *                   profile); role is the headline/title text under their
 *                   name, e.g. "Talent Acquisition", when the page has one
 *   resolveExternalUrl(el)
 *                   optional: given the clicked apply element, return the
 *                   real destination URL when the platform wraps outbound
 *                   links in a redirect/interstitial (e.g. LinkedIn's
 *                   linkedin.com/safety/go/?url=<dest>). Falls back to el.href.
 *   answerFormRoot()
 *                   optional: the element containing the in-page apply form's
 *                   controls, or null when this page/frame has none. Presence
 *                   of this method is what opts a platform into screening-Q&A
 *                   capture — see shared/answers.js, which owns all of it.
 *   answerFormKey() optional companion: a stable id for "which job's form is
 *                   being filled", so an abandoned form's answers can't
 *                   attach to the next job in an SPA. Defaults to top URL.
 *
 * This file owns everything else: delegated apply detection (works across the
 * SPAs' re-renders), the quick-tag popover (shadow DOM so page CSS can't touch
 * it), messaging to the background worker, and loud failure logging.
 */
(() => {
  const adapter = window.__trackerAdapter;
  if (!adapter) return;

  /* Every message out of this frame, in a form that cannot throw.
   *
   * chrome.runtime.sendMessage throws SYNCHRONOUSLY once the extension context
   * dies — "Extension context invalidated" — and a synchronous throw is not
   * something a trailing .catch() on the returned promise can see. Reloading an
   * unpacked extension does exactly that to every tab already open: content
   * scripts are NOT re-injected, so the old script keeps running against a port
   * that no longer exists (CLAUDE.md's known-untested note on adapter reloads,
   * seen from the other side).
   *
   * The consequence was silent and total. capture() ends in send(payload)
   * .then(showResult); if send() throws, the whole capture unwinds — no record,
   * no receipt, no failure entry, nothing in the ring buffer, because
   * recordFailure is itself another sendMessage. An apply just disappears, and
   * that is indistinguishable from the click never having been detected, which
   * is precisely the ambiguity that cost a whole debugging session on the
   * wideworld.ai external apply.
   *
   * So: never throw, and say which failure this is — the fix is a page refresh,
   * which the user has no way to guess from "not saved". */
  const RELOADED = "extension was reloaded — refresh this page, then capture " +
                   "again from the toolbar popup";
  function tell(message) {
    try {
      const p = chrome.runtime.sendMessage(message);
      return (p && typeof p.then === "function")
        ? p.catch((e) => ({ ok: false, error: (e && e.message) || "no response" }))
        : Promise.resolve(undefined);
    } catch (e) {
      return Promise.resolve({ ok: false, error: RELOADED, dead: true });
    }
  }

  const send = (payload) => tell({ type: "tracker-capture", payload });

  // Hostname suffix -> ATS vendor. Only for genuinely external applies (the
  // employer's own domain never matches these) — an in-house/direct careers
  // page correctly yields no match rather than a guess.
  const ATS_HOSTS = {
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "myworkdayjobs.com": "workday",
    "myworkday.com": "workday",
    "ashbyhq.com": "ashby",
    "icims.com": "icims",
    "smartrecruiters.com": "smartrecruiters",
    "jobvite.com": "jobvite",
    "bamboohr.com": "bamboohr",
    "taleo.net": "taleo",
    "successfactors.com": "successfactors",
    "workable.com": "workable",
    "breezy.hr": "breezy",
    "personio.com": "personio",
    "personio.de": "personio",
    "recruitee.com": "recruitee",
    "teamtailor.com": "teamtailor",
    "jazzhr.com": "jazzhr",
    // JazzHR serves customer job boards from applytojob.com, not jazzhr.com —
    // a real external apply resolved to wideworld.applytojob.com (4 Aug 2026) and
    // came back with no ATS despite being a textbook JazzHR board.
    "applytojob.com": "jazzhr",
    "paylocity.com": "paylocity",
  };

  function detectAts(urlStr) {
    if (!urlStr) return null;
    let hostname;
    try { hostname = new URL(urlStr).hostname.toLowerCase(); } catch (e) { return null; }
    for (const [domain, vendor] of Object.entries(ATS_HOSTS)) {
      if (hostname === domain || hostname.endsWith("." + domain)) return vendor;
    }
    return null;
  }

  function buildPayload(trigger, external, ats, job, recruiter, answers, tags, completed) {
    return {
      completed: !!completed,
      platform: adapter.platform,
      platform_job_id: job.platform_job_id || null,
      url: job.url || location.href,
      company: job.company || null,
      title: job.title || null,
      jd_text: job.jd_text || null,
      trigger,
      external: !!external,
      focused: tags.focused,
      note: tags.note || null,
      recruiter_name: (recruiter && recruiter.name) || null,
      recruiter_url: (recruiter && recruiter.url) || null,
      recruiter_role: (recruiter && recruiter.role) || null,
      location: job.location || null,
      posted_label: job.posted_label || null,
      reposted: job.reposted ?? null,
      salary_raw: job.salary_raw || null,
      work_type: job.work_type || null,
      salary_match: job.salary_match ?? null,
      ats: ats || null,
      answers: (answers && answers.length) ? answers : null,
    };
  }

  /* A completion signal by SELECTOR rather than by visible text — for
   * platforms that expose a stable hook on the final submit (SEEK's
   * data-automation attributes) and so don't need to be matched on wording
   * that changes with the site's copy. Same composedPath walk as
   * textMatchTarget, and the two are checked together. */
  function completionTarget(ev) {
    const sel = (adapter.completionSelectors || []).join(",");
    if (!sel) return null;
    const path = ev.composedPath ? ev.composedPath() : [ev.target];
    for (const el of path) {
      if (el && el.matches && el.matches(sel)) return el;
    }
    return null;
  }

  /* Visible text as a human reads it.
   *
   * JobStreet's submit button is literally "⁠Submit application" — a
   * WORD JOINER (U+2060) glued to the front. It renders as nothing, and
   * String.trim() does NOT remove it, because format characters (category Cf)
   * are not whitespace. An exact === against "Submit application" therefore
   * fails on a button that looks identical to the one it's meant to match,
   * with no error anywhere. Found on the real review page 29 Jul 2026, before
   * it ever cost a capture.
   *
   * Zero-width space/joiners, soft hyphens and BOM are the same hazard, and
   * NBSP is the one that bites on wrapped labels — strip the invisibles,
   * fold every kind of space to one. */
  const INVISIBLE = /[­​-‏⁠-⁤﻿]/g;
  const visibleText = (el) => ((el && el.textContent) || "")
    .replace(INVISIBLE, "")
    .replace(/\s+/g, " ")
    .trim();

  /* The apply control the click landed on, searched through shadow boundaries.
   *
   * This is closest() with the one property closest() lacks: ev.target is
   * retargeted to the shadow HOST for a click originating inside a shadow tree,
   * so walking up from it starts above the boundary and can never reach a
   * control inside. composedPath() carries the real path across every open or
   * closed boundary, and matching each entry against the selector list is
   * exactly what closest() does along the way.
   *
   * The submit-side hooks (textMatchTarget, completionTarget) were converted
   * when Easy Apply's wizard buttons turned out to be shadowed; this branch was
   * left on ev.target.closest() and kept the blind spot the whole time. It has
   * cost nothing observable yet — the external-apply button is light DOM on
   * every layout checked (0 shadow hosts on /jobs/collections/ and /jobs/view/,
   * 4 Aug 2026) — but LinkedIn demonstrably wraps whole containers in open
   * shadow roots on some entry paths, and this is the branch on which a miss is
   * silent: no error, no record, no popover, nothing to find afterwards. */
  function applyTarget(ev, sel) {
    if (!sel) return null;
    const path = ev.composedPath ? ev.composedPath() : [ev.target];
    for (const el of path) {
      if (el && el.matches && el.matches(sel)) return el;
    }
    return null;
  }

  function textMatchTarget(ev) {
    if (!adapter.applyTextMatches || !adapter.applyTextMatches.length) return null;
    // ev.target is retargeted to the shadow host for clicks originating inside
    // a shadow tree (open or closed) — LinkedIn's Easy Apply wizard renders
    // its buttons that way, so .closest() from here never reaches them.
    // composedPath() carries the real path through shadow boundaries for any
    // composed event (click always is), regardless of open/closed mode.
    const path = ev.composedPath ? ev.composedPath() : [ev.target];
    for (const el of path) {
      if (!el || !el.tagName) continue;
      const tag = el.tagName.toLowerCase();
      const isButtonish = tag === "button" || tag === "a" ||
        (el.getAttribute && el.getAttribute("role") === "button");
      if (isButtonish && adapter.applyTextMatches.includes(visibleText(el))) {
        return el;
      }
    }
    return null;
  }

  /* ---------------------------------------------------------- popover */

  // Palette lifted from the app's own tokens (templates/base.html) so the one
  // surface you see mid-apply looks like the product it feeds. Written twice —
  // light, then dark under prefers-color-scheme — for the same reason the app
  // does it: LinkedIn has a dark mode and a white card in it is a flashbang.
  const CSS = `
    :host{all:initial}
    .box{position:fixed;right:18px;bottom:18px;z-index:2147483647;
         box-sizing:border-box;width:300px;padding:13px 15px;
         background:#F7F9FA;color:#141C24;
         border:1px solid #CCD5DB;border-radius:10px;
         box-shadow:0 8px 28px rgba(20,28,36,.20);
         font:13px/1.45 system-ui,-apple-system,sans-serif}
    .box.err{border-left:3px solid #9A4231}
    .hd{display:flex;align-items:baseline;gap:6px;font-weight:600}
    .tick{color:#26694A;font-size:14px}
    .box.err .tick{color:#9A4231}
    .x{margin-left:auto;cursor:pointer;color:#5A6873;border:0;background:none;
       font:inherit;padding:0 2px;line-height:1}
    .sub{color:#5A6873;margin-top:2px;
         overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .qa{color:#5A6873;font-size:11.5px;margin-top:3px;
        font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
    .row{display:flex;gap:6px;margin-top:10px}
    button.t{flex:1;font:inherit;cursor:pointer;border:1px solid #CCD5DB;
             background:transparent;color:#141C24;border-radius:6px;padding:5px 10px}
    button.t:hover{border-color:#1F53BE;color:#1F53BE}
    button.t[aria-pressed="true"]{background:#1F53BE;border-color:#1F53BE;color:#FFFFFF}
    input{font:inherit;width:100%;box-sizing:border-box;margin-top:8px;
          border:1px solid #CCD5DB;border-radius:6px;padding:5px 8px;
          background:transparent;color:#141C24}
    input::placeholder{color:#5A6873}
    input:focus{outline:2px solid #1F53BE;outline-offset:-1px}
    .ft{display:flex;align-items:baseline;gap:8px;margin-top:8px;
        font-size:11.5px;color:#5A6873;min-height:1.3em}
    .s{flex:1}
    a{color:#1F53BE;text-decoration:none}
    a:hover{text-decoration:underline}
    @media (prefers-color-scheme: dark){
      .box{background:#181E25;color:#DBE3EA;border-color:#262F39;
           box-shadow:0 8px 28px rgba(0,0,0,.45)}
      .box.err{border-left-color:#E28D79}
      .tick{color:#6FC793}
      .box.err .tick{color:#E28D79}
      .x,.sub,.qa,.ft,input::placeholder{color:#8695A2}
      button.t{border-color:#262F39;color:#DBE3EA}
      button.t:hover{border-color:#7FB0FF;color:#7FB0FF}
      button.t[aria-pressed="true"]{background:#7FB0FF;border-color:#7FB0FF;color:#0A1017}
      input{border-color:#262F39;color:#DBE3EA}
      input:focus{outline-color:#7FB0FF}
      a{color:#7FB0FF}
    }`;

  let host = null;
  let unmount = null;        // tears down the live popover's own listeners
  function mount(html) {
    if (unmount) unmount();
    if (host) host.remove();
    host = document.createElement("div");
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `<style>${CSS}</style>${html}`;
    document.documentElement.appendChild(host);

    let timer = null;
    let owed = null;         // countdown still owed, deferred while unseen

    // Auto-dismiss is a convenience, never a deadline: engaging with the box
    // at all cancels it, because the one thing this must not do is vanish
    // mid-sentence while someone is typing a note.
    //
    // A HIDDEN tab is that same rule seen from the other side, and it cost a
    // real capture (3 Aug 2026, verified live end to end). LinkedIn's "Apply
    // on company website" opens the employer's site in a NEW TAB that takes
    // focus at once, so this box is born on a page nobody is looking at —
    // document.visibilityState already reads "hidden" by the time it mounts —
    // and 45s later it deletes itself unseen, while the applicant is still
    // filling in the real form on the other tab. On the external path the
    // popover holds the ONLY copy of the capture, because that path asks
    // before it writes (deliberately — see capture()), so the timer wasn't
    // dropping a tag, it was dropping the whole application, silently. Same
    // class as the navigating-apply receipt loss on JobStreet: the difference
    // is only that LinkedIn backgrounds the tab instead of tearing it down.
    //
    // So the countdown runs only while the tab is actually visible, and coming
    // back restarts it in full rather than resuming a remainder — the point is
    // to give the reader the whole window from the moment they can see it.
    function close() {
      document.removeEventListener("visibilitychange", onVisibility);
      clearTimeout(timer);
      if (unmount === close) unmount = null;
      if (host) { host.remove(); host = null; }
    }
    function onVisibility() {
      if (document.visibilityState === "hidden") clearTimeout(timer);
      else if (owed != null) { clearTimeout(timer); timer = setTimeout(close, owed); }
    }
    document.addEventListener("visibilitychange", onVisibility);
    unmount = close;

    const fade = (ms) => {
      clearTimeout(timer);
      owed = ms;
      if (document.visibilityState !== "hidden") timer = setTimeout(close, ms);
    };
    const hold = () => { clearTimeout(timer); owed = null; };

    const box = root.querySelector(".box");
    box.addEventListener("pointerenter", hold);
    box.addEventListener("focusin", hold);
    const xb = root.querySelector(".x");
    if (xb) xb.addEventListener("click", close);
    return { root, close, fade, hold };
  }

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  /* The receipt: shown AFTER the record is already saved. Everything on it is
   * optional, so losing it costs a tag, not an application. */
  function receiptPopover(d) {
    // Delivered — drop the worker's held copy so it can't surface again on the
    // next page this tab loads. Fire-and-forget; a failure here costs a
    // duplicate receipt at worst.
    try {
      chrome.runtime.sendMessage({ type: "tracker-receipt-shown" },
                                 () => void chrome.runtime.lastError);
    } catch (e) { /* context gone */ }
    if (!d.ok) return failurePopover(d);
    const qa = d.answers
      ? `<div class="qa">${d.answers} form answer${d.answers === 1 ? "" : "s"} kept</div>`
      : "";
    const link = d.apiBase && d.id
      ? `<a href="${esc(d.apiBase.replace(/\/$/, ""))}/applications/${esc(d.id)}"
            target="_blank" rel="noreferrer">view in tracker</a>` : "";
    const ui = mount(`
      <div class="box">
        <div class="hd"><span class="tick">&#10003;</span>
          <span>${d.enriched ? "Saved — enriched an existing record" : "Saved to tracker"}</span>
          <button class="x" title="Dismiss" aria-label="Dismiss">&times;</button></div>
        <div class="sub">${esc(d.label || "")}</div>
        ${qa}
        <div class="row">
          <button class="t" data-f="true"  aria-pressed="false">Tailored</button>
          <button class="t" data-f="false" aria-pressed="false">Generic</button>
        </div>
        <input type="text" placeholder="add a note&hellip;" aria-label="Note">
        <div class="ft"><span class="s"></span>${link}</div>
      </div>`);
    const status = ui.root.querySelector(".s");
    const note = ui.root.querySelector("input");
    const tag = (body, ok) =>
      tell({ type: "tracker-tag", id: d.id, body })
        .then((r) => {
          if (r && r.ok) { status.textContent = ok; }
          else { status.textContent = `Couldn't save that: ${(r && r.error) || "no response"}`; }
        })
        .catch(() => { status.textContent = "Couldn't save that — is the server up?"; });

    ui.root.querySelectorAll("button.t").forEach((b) =>
      b.addEventListener("click", () => {
        const focused = b.dataset.f === "true";
        ui.root.querySelectorAll("button.t").forEach(
          (o) => o.setAttribute("aria-pressed", String(o === b)));
        tag({ focused }, focused ? "Tagged tailored." : "Tagged generic.")
          .then(() => { if (!note.value.trim()) ui.fade(2500); });
      }));

    let sent = "";
    const commitNote = () => {
      const v = note.value.trim();
      if (!v || v === sent) return;
      sent = v;
      tag({ note: v }, "Note saved.").then(() => ui.fade(2500));
    };
    note.addEventListener("keydown", (e) => { if (e.key === "Enter") commitNote(); });
    note.addEventListener("blur", commitNote);
    note.addEventListener("focus", () => { status.textContent = "Enter to save the note"; });
    ui.fade(8000);
  }

  /* Save failed. No auto-dismiss and a retry button: this is the only state
   * where the capture still exists nowhere but in this page. */
  function failurePopover(d) {
    const ui = mount(`
      <div class="box err">
        <div class="hd"><span class="tick">!</span><span>Not saved</span>
          <button class="x" title="Dismiss" aria-label="Dismiss">&times;</button></div>
        <div class="sub">${esc(d.label || "")}</div>
        <div class="ft"><span class="s">${esc(d.error || "")}</span></div>
        <div class="row"><button class="t" data-r="1">Retry</button></div>
      </div>`);
    ui.hold();
    const status = ui.root.querySelector(".s");
    ui.root.querySelector("button[data-r]").addEventListener("click", () => {
      status.textContent = "Retrying…";
      send(d.payload).then((res) => showResult(d.payload, res));
    });
  }

  /* The job could not be read, on a path where nothing has been written yet.
   *
   * Silence here is what made the wideworld.ai loss (4 Aug 2026) invisible: the
   * external path writes nothing until the popover is answered, so returning at
   * the job guard discards the application outright and leaves one line in a
   * ring buffer the user has no reason to open. Reported by the user as "the
   * popup didn't appear" — which is exactly what it looks like from the page.
   * The tracker can't recover the application, but it can say so while the
   * employer's form is still open and adding it by hand costs nothing. */
  function notCapturedPopover() {
    const ui = mount(`
      <div class="box err">
        <div class="hd"><span class="tick">!</span><span>Not captured</span>
          <button class="x" title="Dismiss" aria-label="Dismiss">&times;</button></div>
        <div class="sub">Couldn't read this job from the page.</div>
        <div class="ft"><span class="s">Nothing was saved — add it by hand.</span></div>
      </div>`);
    ui.hold();
  }

  /* External applies only: the click opened the employer's site, which is not
   * proof anything was submitted, so this one still asks before writing. */
  function confirmPopover(onDone) {
    const ui = mount(`
      <div class="box">
        <div class="hd"><span>Capture this application?</span>
          <button class="x" title="Dismiss" aria-label="Dismiss">&times;</button></div>
        <div class="sub">You're applying on the employer's own site.</div>
        <div class="row">
          <button class="t" data-f="true">Tailored</button>
          <button class="t" data-f="false">Generic</button>
          <button class="t" data-f="skip">Skip tag</button>
        </div>
        <input type="text" placeholder="optional note&hellip;" aria-label="Note">
        <div class="ft"><span class="s"></span></div>
      </div>`);
    const status = ui.root.querySelector(".s");
    ui.root.querySelectorAll("button.t").forEach((b) =>
      b.addEventListener("click", () => {
        const f = b.dataset.f;
        status.textContent = "Saving…";
        onDone({ focused: f === "skip" ? null : f === "true",
                 note: ui.root.querySelector("input").value.trim() },
               (msg, ok) => { status.textContent = msg; if (ok) ui.fade(2200); });
      }));
    // NO auto-dismiss — the same rule failurePopover states, for the same
    // reason: this box is the ONLY copy of the capture. Nothing has been POSTed
    // yet on the external path, so a countdown that runs out doesn't drop a tag,
    // it drops the application.
    //
    // The 3 Aug fix made the countdown wait for visibilityState, which covers
    // the common geometry (employer site opens in a new tab of the SAME window,
    // LinkedIn goes hidden, timer suspends — verified live again on a real
    // external apply 4 Aug 2026). It does not cover the others, because
    // visibilityState tracks tab OCCLUSION, not window FOCUS: with the employer
    // site in a second Chrome window, or LinkedIn's tab dragged out into its
    // own, the LinkedIn tab stays "visible" the entire time it is sitting
    // unread behind another window — and the 45 seconds burn down exactly as
    // they did before the fix, while the applicant fills in the real form.
    // There is no event for "nobody is looking at this", so stop trying to time
    // it. The × dismisses; until then it waits.
    ui.hold();
  }

  /* ------------------------------------------------------------- capture */

  function showResult(payload, res) {
    const label = [payload.company, payload.title].filter(Boolean).join(" · ");
    const detail = (res && res.ok)
      ? { ok: true, id: res.application_id, label: res.label || label,
          answers: res.answers, enriched: res.enriched, apiBase: res.apiBase }
      : { ok: false, label, payload,
          error: (res && res.error) || "no response — is `serve` running?" };
    // Prefer the top frame. On Easy Apply this code is running inside the
    // modal's iframe, and LinkedIn destroys that iframe the moment the modal
    // closes — taking the receipt with it, seconds after it appeared. The
    // relay needs host permission for this site; when it isn't granted the
    // call just fails and the receipt renders right here, as before.
    relayToTop(detail).then((relayed) => { if (!relayed) receiptPopover(detail); });
  }

  function relayToTop(detail) {
    if (window === window.top) return Promise.resolve(false);
    return tell({ type: "tracker-relay-receipt", detail }).then((r) => !!(r && r.ok));
  }

  /* An immediate apply detected in a SUBFRAME is handed to the tab's top frame
   * to carry out, rather than handled where it was seen.
   *
   * A subframe is the wrong place for this capture twice over, and a real
   * external apply (wideworld.ai, 4 Aug 2026) lost an application to the first of
   * the two with nothing but one ring-buffer line to show for it:
   *
   *  1. It cannot READ the job. getJob() walks to window.top for the job DOM,
   *     and a frame that can't reach its top gets ITSELF back with no error —
   *     so it reads its own document, finds no title and no JD, and capture()
   *     returns at the job guard BEFORE confirmPopover() is ever reached. The
   *     recorded failure url is the frame's own address, which is how this one
   *     is recognised: linkedin.com/preload/?_bprMode=vanilla. Same frame, same
   *     unreachable top, as the Easy Apply capture lost on 3 Aug.
   *  2. It cannot SHOW the popover. mount() appends to the frame's own
   *     documentElement, so even when getJob() does succeed the box is rendered
   *     inside a frame nobody is looking at — and on the external path that box
   *     is the only copy of the capture, so it is not a lost tag, it is a lost
   *     application. showResult() already relays the receipt to frame 0 for
   *     precisely this reason; the ask-first path never got the same treatment.
   *
   * Frame 0 has the real page in front of the user, so it has both. Gated to
   * the immediate-apply path: Easy Apply's deferred submit fires from inside
   * the modal's iframe legitimately, and its answers only exist there. */
  function relayApply(detail) {
    if (window === window.top) return Promise.resolve(false);
    return tell({ type: "tracker-relay-apply", detail }).then((r) => !!(r && r.ok));
  }

  /* Which job a stash belongs to. Reuses answerFormKey() — it already has to
   * answer "same job or a different one?" for the Q&A store, and it already
   * resolves on the apply pages as well as the listing. */
  const jobKey = () => {
    try {
      return (adapter.answerFormKey && adapter.answerFormKey()) || location.href;
    } catch (e) { return location.href; }
  };

  function stashJob() {
    let job = null;
    try { job = adapter.getJob(); } catch (e) { job = null; }
    if (!job) return;
    // Promise form, not the callback: this click navigates immediately, and
    // the open port is what keeps the worker alive long enough to finish the
    // write. The page dying before it resolves is fine — the port is the
    // browser's, not the page's.
    try {
      chrome.runtime.sendMessage({ type: "tracker-stash-job", key: jobKey(), job })
        .catch(() => {});
    } catch (e) { /* worker unreachable — the submit page still has id and title */ }
  }

  // Fields the later pages of an apply flow no longer show. Verified on
  // JobStreet's review page: id and title survive, company does not.
  const CARRIED = ["company", "title", "jd_text", "url", "platform_job_id",
                   "location", "posted_label", "reposted",
                   // Printed on the listing and gone by the review page, the
                   // same way company is — if it isn't carried, it's lost.
                   "salary_raw", "work_type", "salary_match"];

  function withStashedJob(job, completed) {
    if (!completed) return Promise.resolve(job);
    try {
      return chrome.runtime.sendMessage({ type: "tracker-take-job", key: jobKey() })
        .then((r) => {
          const was = r && r.job;
          // The page in front of us wins; the stash only fills the gaps it
          // left behind. A stale snapshot must never overwrite what the submit
          // page can actually see.
          //
          // That rule has a sharp edge worth watching, which is why the
          // comparison below is recorded. "Gaps only" tests !job[k] — EMPTY,
          // not WRONG. A submit-page read that comes back non-empty and wrong
          // therefore beats a correct stashed value and the stash is silently
          // discarded. capture()'s own note names the mechanism that could
          // produce one: Easy Apply swaps the top card for an "application
          // sent" confirmation moments after the submit click. Two real
          // captures (Southridge APAC, Adatum) came out with a title matching no
          // job on the page while keeping the right company — the shape this
          // edge predicts, though never reproduced. If it recurs, `disagreed`
          // below names the field and shows both candidate values.
          const pageSaw = { title: job.title || null, company: job.company || null };
          if (was) for (const k of CARRIED) if (!job[k] && was[k]) job[k] = was[k];
          if (was) {
            const disagreed = ["title", "company"].filter(
              (k) => pageSaw[k] && was[k] && pageSaw[k] !== was[k]);
            tell({
              type: "tracker-provenance",
              detail: {
                at: Date.now(), url: location.href, id: job.platform_job_id || null,
                source: (job._prov && job._prov.title_source) || null,
                layout: (job._prov && job._prov.layout) || null,
                stashed: !!was,
                disagreed,
                page: { title: pageSaw.title, company: pageSaw.company },
                stash: { title: was.title || null, company: was.company || null },
              },
            });
          }
          return job;
        })
        .catch(() => job);
    } catch (e) { return Promise.resolve(job); }
  }

  function capture(trigger, external, ats, completed) {
    // Snapshot the job DOM NOW, not later: platforms routinely swap the page's
    // content out from under us — e.g. LinkedIn's Easy Apply replaces the top
    // card (title/company/location) with an "application sent" confirmation
    // within moments of the real submit click.
    // A completion fires on a page deep in the apply flow, which may show far
    // less than the listing did — the stash is expected to fill it in, so an
    // empty read there is not yet a failure.
    const job = adapter.getJob() || (completed ? {} : null);
    if (!job || (!job.title && !job.jd_text && !completed)) {
      console.warn("[tracker] capture failed — adapter found no job on this page",
                   location.href);
      tell({
        type: "tracker-capture-failure",
        detail: { platform: adapter.platform, url: location.href, at: Date.now() },
      });
      if (external) notCapturedPopover();
      return;
    }
    // Same breadcrumb for the path that never consults a stash (an immediate
    // apply), so "where did this title come from" is answerable for EVERY
    // capture rather than only deferred ones. withStashedJob emits the richer
    // page-vs-stash version when a snapshot exists.
    if (!completed) {
      tell({
        type: "tracker-provenance",
        detail: {
          at: Date.now(), url: location.href, id: job.platform_job_id || null,
          source: (job._prov && job._prov.title_source) || null,
          layout: (job._prov && job._prov.layout) || null,
          stashed: false, disagreed: [],
          page: { title: job.title || null, company: job.company || null },
        },
      });
    }
    // getRecruiter is optional — most platforms don't surface a named
    // contact on the job page, and adapters that don't implement it just
    // omit the method rather than returning null every time.
    const recruiter = adapter.getRecruiter ? adapter.getRecruiter() : null;
    // Same reason as the job snapshot above, only more so: the apply form is
    // torn out of the DOM the instant the submit lands. shared/answers.js has
    // been accumulating it step by step; take() sweeps the final step and
    // hands over everything, now, while it still exists.
    // diagnostics() BEFORE take(), which clears the store. This is the only
    // window in which "what did the sweep see, and what did it skip" still
    // exists — the form is about to be gone, and a missing answer leaves no
    // error behind to find later. Fire-and-forget into the popup's ring buffer.
    if (window.__trackerAnswers && window.__trackerAnswers.diagnostics) {
      try {
        chrome.runtime.sendMessage(
          { type: "tracker-sweep", url: location.href,
            detail: window.__trackerAnswers.diagnostics() },
          () => void chrome.runtime.lastError);
      } catch (e) { /* worker asleep or context invalidated — never block the save */ }
    }
    const answers = window.__trackerAnswers ? window.__trackerAnswers.take() : null;

    // Every DOM read above is synchronous and already done; only now is it
    // safe to wait on the worker. Merging first would risk the submit's own
    // navigation tearing the page down before the form had been read.
    const merged = withStashedJob(job, completed);

    if (external) {
      // Ask first, then write — see confirmPopover.
      confirmPopover((tags, report) => {
        merged
          .then((j) => send(buildPayload(trigger, external, ats, j, recruiter,
                                         answers, tags, completed)))
          .then((res) => {
            if (res && res.ok) {
              const qa = res.answers ? ` ${res.answers} form answers kept.` : "";
              report((res.enriched ? "Saved — enriched an existing record." : "Saved.") + qa,
                     true);
            } else {
              report(`Save failed: ${(res && res.error) || "no response"}. Check options.`,
                     false);
            }
          });
      });
      return;
    }

    // Everything else — Easy Apply's final submit, an explicit capture from
    // the popup — is unambiguous, so write it NOW with focused unset (NULL
    // means "not said", not "generic") and let the tag follow. Holding the
    // whole capture until a human answered a question is how real applications
    // used to get lost: 45 seconds of inattention, or closing the modal, and
    // it was gone with nothing recorded anywhere.
    merged.then((j) => {
      const payload = buildPayload(trigger, external, ats, j, recruiter, answers,
                                   { focused: null, note: null }, completed);
      send(payload).then((res) => showResult(payload, res));
    });
  }

  /* --------------------------------------------- apply detection (delegated) */

  document.addEventListener("click", (ev) => {
    const sel = (adapter.applySelectors || []).join(",");
    const hit = applyTarget(ev, sel);
    if (hit) {
      const external = adapter.isExternal ? adapter.isExternal(hit) : false;
      // Only external applies have a resolvable ATS destination — Easy
      // Apply / native quick-apply never leaves the platform, so there's
      // nothing to detect from.
      const ats = external
        ? detectAts(adapter.resolveExternalUrl ? adapter.resolveExternalUrl(hit) : hit.href)
        : null;
      if (external || !adapter.deferInternalApply) {
        // The top frame captures inline and SYNCHRONOUSLY — the DOM must be
        // read in this tick, before the page's own handler runs and swaps it
        // out. Only a subframe, which cannot do this correctly at all, pays the
        // round trip; if frame 0 can't help either, it says so and we fall back
        // to the local attempt, which at least records the failure.
        if (window === window.top) {
          capture("apply", external, ats);
        } else {
          relayApply({ trigger: "apply", external, ats })
            .then((relayed) => { if (!relayed) capture("apply", external, ats); });
        }
        return;
      }
      // deferInternalApply: this click only opened the apply flow — the
      // applicant can still cancel or discard partway through, so wait for the
      // real completion signal below instead of recording an application that
      // may never happen. Nothing is POSTed here.
      //
      // But DO remember the job, because on a platform whose flow is its own
      // page this is the last screen that shows the company name. Costs
      // nothing if the flow is abandoned: the stash expires unsent.
      stashJob();
      return;
    }
    // The real completion. On LinkedIn this IS the capture (deferInternalApply
    // suppressed the opening click). On JobStreet the record already exists
    // from that opening click, and `completed` tells the server to move its
    // applied time here — to when the application was actually sent, not when
    // the form was opened. Harmless in the first case: with no event on record
    // yet, there is nothing to correct.
    const submitHit = textMatchTarget(ev) || completionTarget(ev);
    if (submitHit) capture("apply", false, null, true);
  }, true);

  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    /* Manual capture from the popup ("interested", no apply). */
    if (msg && msg.type === "tracker-capture-manual") {
      capture("manual", false, null);
      respond({ ok: true });
    }
    /* A subframe's capture, relayed here so the receipt outlives that frame.
     * Only ever delivered to frameId 0 — the background does the targeting,
     * and page scripts cannot send extension messages at all, so this can't
     * be spoofed from the page. */
    if (msg && msg.type === "tracker-receipt") {
      receiptPopover(msg.detail);
      respond({ ok: true });
    }
    /* A subframe's apply, carried out here instead — see relayApply. Answering
     * `false` when this frame can't see a job either hands the attempt back, so
     * the failure is still recorded rather than swallowed by the handoff. */
    if (msg && msg.type === "tracker-apply") {
      let job = null;
      try { job = adapter.getJob(); } catch (e) { job = null; }
      if (!job || (!job.title && !job.jd_text)) { respond({ ok: false }); return false; }
      const d = msg.detail || {};
      capture(d.trigger || "apply", d.external, d.ats);
      respond({ ok: true });
    }
    return false;
  });

  /* Is this tab owed a receipt from a page that navigated before it could show
   * one? Top frame only — a subframe rendering it would put the box inside
   * whatever iframe happened to load, and the whole point is to outlive that.
   * See background.js:stashReceipt. */
  if (window === window.top) {
    try {
      chrome.runtime.sendMessage({ type: "tracker-claim-receipt" })
        .then((r) => { if (r && r.detail) receiptPopover(r.detail); })
        .catch(() => {});
    } catch (e) { /* worker asleep on a cold start — nothing owed, then */ }
  }
})();
