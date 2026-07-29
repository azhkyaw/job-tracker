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

  const send = (payload) =>
    chrome.runtime.sendMessage({ type: "tracker-capture", payload });

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
  function mount(html) {
    if (host) host.remove();
    host = document.createElement("div");
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `<style>${CSS}</style>${html}`;
    document.documentElement.appendChild(host);
    const close = () => { if (host) { host.remove(); host = null; } };
    const box = root.querySelector(".box");
    let timer = null;
    // Auto-dismiss is a convenience, never a deadline: engaging with the box
    // at all cancels it, because the one thing this must not do is vanish
    // mid-sentence while someone is typing a note.
    const fade = (ms) => { clearTimeout(timer); timer = setTimeout(close, ms); };
    const hold = () => clearTimeout(timer);
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
      chrome.runtime.sendMessage({ type: "tracker-tag", id: d.id, body })
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
    ui.fade(45000);
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
    return chrome.runtime.sendMessage({ type: "tracker-relay-receipt", detail })
      .then((r) => !!(r && r.ok)).catch(() => false);
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
          if (was) for (const k of CARRIED) if (!job[k] && was[k]) job[k] = was[k];
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
      chrome.runtime.sendMessage({
        type: "tracker-capture-failure",
        detail: { platform: adapter.platform, url: location.href, at: Date.now() },
      });
      return;
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
    const hit = sel ? ev.target.closest(sel) : null;
    if (hit) {
      const external = adapter.isExternal ? adapter.isExternal(hit) : false;
      // Only external applies have a resolvable ATS destination — Easy
      // Apply / native quick-apply never leaves the platform, so there's
      // nothing to detect from.
      const ats = external
        ? detectAts(adapter.resolveExternalUrl ? adapter.resolveExternalUrl(hit) : hit.href)
        : null;
      if (external || !adapter.deferInternalApply) {
        capture("apply", external, ats);
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
