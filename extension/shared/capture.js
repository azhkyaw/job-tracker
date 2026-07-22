/* Shared capture core (design doc §6.1).
 *
 * The site adapter (loaded before this file) sets window.__trackerAdapter:
 *   platform        "linkedin" | "jobstreet" | "indeed"
 *   applySelectors  CSS selectors whose click means "the user is applying"
 *   isExternal(el)  optional: true when the button leaves for the employer site
 *   getJob()        {platform_job_id, url, company, title, jd_text} | null
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

  function buildPayload(trigger, external, tags) {
    const job = adapter.getJob();
    if (!job || (!job.title && !job.jd_text)) {
      console.warn("[tracker] capture failed — adapter found no job on this page",
                   location.href);
      chrome.runtime.sendMessage({
        type: "tracker-capture-failure",
        detail: { platform: adapter.platform, url: location.href, at: Date.now() },
      });
      return null;
    }
    return {
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
      recruiter_name: null,
      recruiter_url: null,
    };
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
      if (isButtonish && adapter.applyTextMatches.includes((el.textContent || "").trim())) {
        return el;
      }
    }
    return null;
  }

  /* ---------------------------------------------------------- popover */

  let host = null;
  function popover(onDone) {
    if (host) host.remove();
    host = document.createElement("div");
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        .box{position:fixed;right:18px;bottom:18px;z-index:2147483647;
             background:#fff;color:#1B2A33;border:1px solid #DAE3E7;
             border-left:3px solid #0E6E66;border-radius:8px;
             box-shadow:0 6px 24px rgba(27,42,51,.18);
             font:13px/1.4 system-ui,sans-serif;padding:12px 14px;width:280px}
        .t{font-weight:600;margin-bottom:8px}
        .row{display:flex;gap:6px;margin-bottom:8px}
        button{font:inherit;cursor:pointer;border:1px solid #DAE3E7;
               background:#fff;border-radius:6px;padding:4px 10px}
        button.primary{background:#0E6E66;border-color:#0E6E66;color:#fff}
        input{font:inherit;width:100%;box-sizing:border-box;
              border:1px solid #DAE3E7;border-radius:6px;padding:4px 8px}
        .s{color:#5E7079;margin-top:6px;min-height:1.2em}
      </style>
      <div class="box">
        <div class="t">Capture this application?</div>
        <div class="row">
          <button class="primary" data-f="true">Focused</button>
          <button data-f="false">Generic</button>
          <button data-f="skip">Skip tag</button>
        </div>
        <input type="text" placeholder="optional note" aria-label="Note">
        <div class="s"></div>
      </div>`;
    const status = root.querySelector(".s");
    root.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        const f = b.dataset.f;
        const focused = f === "skip" ? null : f === "true";
        status.textContent = "Saving…";
        onDone({ focused, note: root.querySelector("input").value.trim() },
               (msg, ok) => {
                 status.textContent = msg;
                 if (ok) setTimeout(() => host && host.remove(), 2200);
               });
      }));
    document.documentElement.appendChild(host);
    setTimeout(() => host && host.remove(), 45000);
  }

  function capture(trigger, external) {
    popover((tags, report) => {
      const payload = buildPayload(trigger, external, tags);
      if (!payload) return report("Couldn't read this page — selectors may need updating.", false);
      send(payload).then((res) => {
        if (res && res.ok) {
          report(res.enriched ? "Saved — enriched an existing record." : "Saved.", true);
        } else {
          report(`Save failed: ${(res && res.error) || "no response"}. Check options.`, false);
        }
      });
    });
  }

  /* --------------------------------------------- apply detection (delegated) */

  document.addEventListener("click", (ev) => {
    const sel = (adapter.applySelectors || []).join(",");
    const hit = sel ? ev.target.closest(sel) : null;
    if (hit) {
      const external = adapter.isExternal ? adapter.isExternal(hit) : false;
      if (external || !adapter.deferInternalApply) {
        capture("apply", external);
        return;
      }
      // deferInternalApply: this click only opened an in-page wizard (e.g.
      // LinkedIn Easy Apply) — the applicant can still cancel or discard
      // partway through, so wait for the real completion signal below
      // instead of recording an application that may never happen.
    }
    const submitHit = textMatchTarget(ev);
    if (submitHit) capture("apply", false);
  }, true);

  /* Manual capture from the popup ("interested", no apply). */
  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg && msg.type === "tracker-capture-manual") {
      capture("manual", false);
      respond({ ok: true });
    }
    return false;
  });
})();
