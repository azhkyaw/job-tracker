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

  function buildPayload(trigger, external, ats, job, recruiter, tags) {
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
      recruiter_name: (recruiter && recruiter.name) || null,
      recruiter_url: (recruiter && recruiter.url) || null,
      recruiter_role: (recruiter && recruiter.role) || null,
      location: job.location || null,
      posted_label: job.posted_label || null,
      reposted: job.reposted ?? null,
      ats: ats || null,
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

  function capture(trigger, external, ats) {
    // Snapshot the job DOM NOW, not inside the popover callback below: the
    // popover waits on a human, and platforms routinely swap the page's
    // content out from under it in the meantime — e.g. LinkedIn's Easy Apply
    // replaces the top card (title/company/location) with an "application
    // sent" confirmation within moments of the real submit click, well before
    // anyone gets around to tagging Focused/Generic. Read while the DOM still
    // reflects the job being applied to; tag whenever the human gets to it.
    const job = adapter.getJob();
    if (!job || (!job.title && !job.jd_text)) {
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
    popover((tags, report) => {
      const payload = buildPayload(trigger, external, ats, job, recruiter, tags);
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
      // deferInternalApply: this click only opened an in-page wizard (e.g.
      // LinkedIn Easy Apply) — the applicant can still cancel or discard
      // partway through, so wait for the real completion signal below
      // instead of recording an application that may never happen.
    }
    const submitHit = textMatchTarget(ev);
    if (submitHit) capture("apply", false, null);
  }, true);

  /* Manual capture from the popup ("interested", no apply). */
  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg && msg.type === "tracker-capture-manual") {
      capture("manual", false, null);
      respond({ ok: true });
    }
    return false;
  });
})();
