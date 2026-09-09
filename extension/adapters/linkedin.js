/* LinkedIn adapter. Selectors are layered fallbacks — LinkedIn re-skins often.
 * When capture fails, fix these first; everything else lives in shared code. */

// document.querySelector never descends into a shadow root, open or closed —
// confirmed live (3 Aug 2026) as the actual reason answerFormRoot() below was
// missing Easy Apply modals opened from the standalone /jobs/view/ page: the
// same .jobs-easy-apply-modal markup exists, just inside an open shadow host,
// invisible to a plain selector. Closed roots stay genuinely unreachable —
// same limit shared/answers.js's collect() already lives with.
function deepQuerySelector(root, selector) {
  const hit = root.querySelector(selector);
  if (hit) return hit;
  for (const el of root.querySelectorAll("*")) {
    if (el.shadowRoot) {
      const found = deepQuerySelector(el.shadowRoot, selector);
      if (found) return found;
    }
  }
  return null;
}

// "Did this document actually have the job on it?" — the same test capture.js
// applies before it will save (a bare id is not enough to identify a record by
// itself), so the frame that answers here is the frame that would have been
// allowed to save. Kept as one named predicate rather than repeated inline:
// when the bar moves it has to move for both.
function usableJob(job) {
  return !!(job && (job.title || job.jd_text));
}

window.__trackerAdapter = {
  platform: "linkedin",
  // Opt-in for shared/answers.js's noRoot diagnostic (a MutationObserver that
  // watches for real form-field insertions when the known modal selectors
  // fail to match — see the gotcha there). LinkedIn-specific because this is
  // the only platform where that miss has actually happened; JobStreet/Indeed
  // don't pay for an observer aimed at a failure mode they haven't hit.
  trackFormInserts: true,
  // Easy Apply is an in-page multi-step wizard (resume -> questions -> review
  // -> submit) — clicking its opener only starts the flow, and LinkedIn's own
  // "Save this application?" prompt lets the applicant cancel or discard
  // partway through. Capturing on that opening click records applications
  // that may never actually happen; only the final submit click should count.
  deferInternalApply: true,
  applySelectors: [
    ".jobs-apply-button",
    "button[aria-label*='Easy Apply']",
    "button[aria-label^='Apply']",
    "button[data-live-test-job-apply-button]",
    // External applies ("Apply on company website") render as an <a>, not a
    // <button> — none of the selectors above are tag-agnostic, so this class
    // of apply was never catchable at all, on either page layout. The Easy
    // Apply opener is *also* an <a> with "Apply" in its aria-label ("Easy
    // Apply to this job") and matches this selector too — isExternal() below
    // is what tells them apart, tag-agnostically, by checking for "Easy
    // Apply" in the label; deferInternalApply then suppresses the internal
    // one until the real submit.
    "a[aria-label*='Apply']",
  ],
  // The wizard's final button has no stable selector (same hashed atomic CSS
  // as everything else on this layout) — matched by exact visible text
  // instead, checked directly against click targets in shared/capture.js.
  applyTextMatches: ["Submit application"],
  isExternal(el) {
    const label = (el.getAttribute("aria-label") || el.textContent || "");
    return !/easy apply/i.test(label);       // plain "Apply" leaves the site
  },
  // External applies don't link straight to the employer/ATS — LinkedIn
  // routes them through an outbound-link safety interstitial
  // (linkedin.com/safety/go/?url=<real destination>&urlhash=...), verified
  // live 2026-07-23. Unwrap it so ATS detection (shared/capture.js) sees the
  // real host instead of linkedin.com.
  resolveExternalUrl(el) {
    const href = el.href || el.getAttribute("href");
    if (!href) return null;
    try {
      const u = new URL(href, location.href);
      if (/(^|\.)linkedin\.com$/i.test(u.hostname) && u.pathname.includes("/safety/go")) {
        return u.searchParams.get("url") || href;
      }
      return href;
    } catch (e) {
      return href;
    }
  },
  // Screening-question capture (shared/answers.js). Easy Apply renders in a
  // same-origin iframe whose whole document IS the wizard — see getJob() — so
  // in a subframe the form element (or failing that, the body) is the right
  // root. In the TOP frame there is no Easy Apply form at all, and returning
  // document there would scrape LinkedIn's own search/filter inputs as if they
  // were application answers; the modal selectors are checked instead so that
  // a future non-iframe layout still works, and anything else yields null.
  //
  // ROOT CAUSE FOUND (3 Aug 2026), after two wrong guesses (a widened
  // selector requiring no <form>, then several diagnostic-only attempts —
  // see the .claude/rules/extension.md gotcha for the full trail). Two real misses (Bellows &
  // Munson, Relecloud) both traced back to the SAME cause, confirmed live: Easy
  // Apply opened from the standalone `/jobs/view/<id>/` page renders the
  // ENTIRE modal inside an OPEN shadow root (a `<div class="theme--dark">`
  // host) — the exact same `.jobs-easy-apply-modal` / `role="dialog"`
  // markup that matches fine when Easy Apply is opened from the split-pane
  // search results view, which has no shadow root at all for the same
  // company/job. `document.querySelector` never pierces a shadow boundary,
  // open or closed, so every previous version of this selector was
  // correctly failing to find something it fundamentally could not see —
  // not a wrong selector, a wrong search space. `deepQuerySelector` recurses
  // into open shadow roots the same way `answers.js`'s `collect()` already
  // does for gathering fields once a root is found; closed shadow roots
  // remain genuinely unreachable, same as everywhere else in this file.
  //
  // SECOND ROOT CAUSE (2 Sep 2026), and this time it is the layout, not the
  // search space. LinkedIn rebuilt Easy Apply around 18 Aug 2026: the wizard
  // is now a native `<dialog open data-testid="dialog"
  // aria-labelledby="dialog-header">` mounted directly under `#root` in the
  // TOP document — no `role` attribute, no shadow root, no `<form>` inside,
  // hashed atomic classes throughout (measured live, 2 Sep). A `[role='dialog']`
  // selector does not match a `<dialog>` element: the implicit role is not an
  // attribute. So every sweep on that layout came back noRoot — the capture
  // that surfaced this swept 30 times, found no root 30 times, and reported
  // `dialogPresent:false` with 30 controls on the page. Both layouts ran side
  // by side for at least three days (classic-modal captures kept their answers
  // on 20-21 Aug while `<dialog>` ones lost them), and the only answers that
  // survived on the new layout came through answers.js's change-listener
  // backstop, i.e. fields the applicant TYPED into — every prefilled field,
  // every radio and the resume choice were lost, silently, for two weeks.
  // `dialog[open]` goes first because it is the shape that is live today.
  answerFormRoot() {
    if (window !== window.top) return document.querySelector("form") || document.body;
    return deepQuerySelector(document,
      "dialog[open], .jobs-easy-apply-modal, .jobs-easy-apply-content, " +
      "[role='dialog'], [data-test-modal]");
  },
  // The job the form belongs to, so answers can't survive into the next one.
  // Read from the top frame's URL (cheap — this runs on every field edit)
  // rather than via getJob(), which walks the DOM.
  answerFormKey() {
    let loc = location;
    try { if (window.top && window.top.location) loc = window.top.location; } catch (e) {}
    return new URLSearchParams(loc.search).get("currentJobId") ||
           (loc.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1] || loc.href;
  },
  // Identity from a URL alone, for a capture where every DOM read has failed —
  // the background supplies the tab's real URL, which survives a frame that
  // cannot see the page it belongs to. No company and no title (they are not in
  // the URL), but a job id is what makes a record findable and repairable.
  jobFromUrl(href) {
    if (!href) return null;
    try {
      const u = new URL(href);
      if (!/(^|\.)linkedin\.com$/i.test(u.hostname)) return null;
      const id = u.searchParams.get("currentJobId") ||
                 (u.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1] || null;
      if (!id) return null;
      return { platform_job_id: id, url: `https://www.linkedin.com/jobs/view/${id}/` };
    } catch (e) { return null; }
  },
  // The job identity, read from whichever document actually has it.
  //
  // "The outer page" was an assumption, and it is wrong on the layout that has
  // been eating captures since 3 Aug 2026. Easy Apply's submit fires inside the
  // modal's same-origin iframe, whose own document holds only the
  // contact-form/resume fields, so reading the TOP frame is right there and has
  // always been the reason this walks up. But LinkedIn also has a render mode
  // in which the WHOLE PAGE — nav, job card, JD, apply button — is rendered
  // inside a full-viewport iframe at linkedin.com/preload/?_bprMode=vanilla
  // (confirmed live 21 Aug 2026: 2133x1050, same-origin, `render-mode-VANILLA`
  // on its <html>, window.top reachable and pointing at the outer shell). In
  // that mode the job is in THIS frame and the top is the leftover shell —
  // walking up is walking away from the only document that has an answer.
  //
  // Every blind capture on record has this signature: the frame's own url is
  // the preload page, window.top is readable (the recorded `layout` is
  // "collections"/"view", read off the top's own pathname), and the top read
  // comes back with no title, no company and no JD. It was diagnosed twice as
  // an UNREACHABLE top and mitigated twice on that theory — the keyed stash,
  // then asking frame 0 — and both mitigations ask the same shell that has
  // nothing to say. Five "no job found" failures in the extension's own ring
  // buffer share it (3 Aug x2, 4 Aug, 7 Aug, 21 Aug — the 10/11 Aug entries in
  // that buffer carry an `error` and are save failures, a different thing),
  // plus the three blind records repaired by hand (Coho, Trey, Lucerne
  // Consultants) and one application lost outright (Woodgrove Finance, 21 Aug).
  //
  // So: try the top, and when it yields nothing usable, try this frame. Top
  // still WINS every field it can answer, which is what keeps the Easy Apply
  // modal case correct — that document has no job card, so it loses by having
  // nothing, not by being distrusted.
  getJob() {
    let topWin = window;
    try {
      if (window.top && window.top.document) topWin = window.top;
    } catch (e) { /* cross-origin top somehow — fall back to this frame */ }
    const top = this.readJob(topWin.document, topWin.location);
    if (topWin === window) return top;
    if (usableJob(top)) return top;

    const own = this.readJob(document, location);
    if (!usableJob(own)) return top || own;
    // The id and the url it was built from move together — own.url falls back
    // to this frame's own href, which is truthy and WRONG (that is how a
    // capture came to record linkedin.com/preload/?_bprMode=vanilla as the job
    // url), so a gaps-only merge would keep it and drop the top's real id.
    if (!own.platform_job_id && top && top.platform_job_id) {
      own.platform_job_id = top.platform_job_id;
      own.url = top.url;
    }
    for (const k of ["company", "title", "jd_text", "location", "posted_label"]) {
      if (!own[k] && top && top[k]) own[k] = top[k];
    }
    if (own.reposted == null && top && top.reposted != null) own.reposted = top.reposted;
    // Which document answered, and which page the tab was on — the two facts
    // that make the next occurrence of this readable at a glance instead of
    // costing another session.
    own._prov.doc_source = "self";
    if (top && top._prov) own._prov.layout = top._prov.layout;
    return own;
  },
  readJob(doc, loc) {
    const q = (sels) => {
      for (const s of sels) {
        const el = doc.querySelector(s);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return null;
    };
    const idFromUrl =
      new URLSearchParams(loc.search).get("currentJobId") ||
      (loc.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1] || null;

    // Try the classic, human-named classes first — still real on the
    // /jobs/collections/recommended/ layout (verified live 2026-07-23).
    const classTitle = q([
      ".job-details-jobs-unified-top-card__job-title",
      ".jobs-unified-top-card__job-title",
    ]);
    const classCompany = q([
      ".job-details-jobs-unified-top-card__company-name a",
      ".job-details-jobs-unified-top-card__company-name",
      ".jobs-unified-top-card__company-name",
    ]);
    const jdEl =
      doc.querySelector("#job-details") ||
      doc.querySelector(".jobs-description__content") ||
      doc.querySelector(".jobs-box__html-content") ||
      doc.querySelector("[id^='JobDetails_AboutTheJob_']");

    // Location / posted-time / repost line ("Singapore, Singapore · Reposted
    // 3 weeks ago · Over 100 people clicked apply"). Three layouts verified
    // live 2026-07-23:
    //  1. /jobs/collections/recommended/ — has a real, stable class
    //     (.job-details-jobs-unified-top-card__tertiary-description-container)
    //     wrapping ONE child <span> whose own children are the segments,
    //     plus a trailing <p> ("Promoted by hirer...") sharing the container.
    //  2. /jobs/view/ and /jobs/search-results/ — hashed atomic CSS, no
    //     class survives a build. Found structurally instead, scoped to an
    //     ancestor of jdEl (NOT the whole document — the split search-results
    //     view duplicates the job title as plain text in the left-hand
    //     results list, so an unscoped search can anchor on that instead of
    //     the real top card). Within that scope, the tertiary line is
    //     identified by SHAPE — a <p> with >=2 SPAN children and a "·" in
    //     its text — rather than by matching already-known title text: a
    //     same-shaped exact-text match breaks when the job's own title (or a
    //     headhunter's company name) itself contains " | ", which also
    //     corrupts document.title-based guessing below. Once found, its
    //     preceding sibling <p>s ARE the title and company — this doubles as
    //     a title/company source that's immune to that corruption.
    let location = null, posted_label = null, reposted = null;
    let tertiaryHost = null, structTitle = null, structCompany = null;
    const classContainer = doc.querySelector(
      ".job-details-jobs-unified-top-card__tertiary-description-container");
    if (classContainer && classContainer.children[0]) {
      tertiaryHost = classContainer.children[0];
    } else {
      for (let node = jdEl || doc.body, hops = 0;
           hops < 12 && node && !tertiaryHost; hops++, node = node.parentElement) {
        const ps = [...node.querySelectorAll("p")];
        const idx = ps.findIndex((p) => {
          const spanCount = [...p.children].filter((c) => c.tagName === "SPAN").length;
          return spanCount >= 2 && p.textContent.includes("·");
        });
        if (idx > 0) {
          tertiaryHost = ps[idx];
          structTitle = ps[idx - 1].textContent.trim();
          structCompany = idx >= 2 ? ps[idx - 2].textContent.trim() : null;
        }
      }
    }
    if (tertiaryHost) {
      const segments = [...tertiaryHost.children]
        .filter((c) => c.tagName === "SPAN")
        .map((s) => s.textContent.trim())
        .filter((t) => t && t !== "·");
      location = segments[0] || null;
      const postedRaw = segments[1] || null;
      if (postedRaw) {
        reposted = /^Reposted\b/i.test(postedRaw);
        posted_label = postedRaw.replace(/^Reposted\s+/i, "");
      }
    }

    // Last resort: document.title ("<title> | <company> | LinkedIn") set by
    // LinkedIn's own tab-title code. Only trustworthy when it has exactly one
    // job title and one company segment — a job/company name containing its
    // own " | " (verified live 2026-07-23 on a headhunter posting whose title
    // read "... | APPLICABLE FOR WORK VISA") desyncs the positional guess,
    // which the structural path above isn't fooled by since it doesn't parse
    // this string at all. Also unusable on /jobs/collections/recommended/,
    // where the tab title is the page's own ("Top job picks for you |
    // LinkedIn") regardless of which job is selected — verified live same day.
    const docTitleTracksSelectedJob = !/\/jobs\/collections\//.test(loc.pathname);
    const titleParts = doc.title.split(" | ");
    const titleFromDocTitle =
      docTitleTracksSelectedJob && titleParts.length === 3 && titleParts[2] === "LinkedIn"
        ? titleParts[0].trim() : null;
    const companyFromDocTitle =
      docTitleTracksSelectedJob && titleParts.length === 3 && titleParts[2] === "LinkedIn"
        ? titleParts[1].trim() : null;

    let title = classTitle || structTitle || titleFromDocTitle;
    let company = classCompany || structCompany || companyFromDocTitle;
    let jd_text = jdEl ? jdEl.innerText.trim() : null;
    let title_source = classTitle ? "class" : structTitle ? "struct"
                     : titleFromDocTitle ? "doctitle" : null;
    let company_source = classCompany ? "class" : structCompany ? "struct"
                       : companyFromDocTitle ? "doctitle" : null;

    // SPLIT-LAYOUT STALE PANE (9 Sep 2026). On /jobs/search and
    // /jobs/collections the job card, the JD and the top card all live in a
    // detail PANE, while the id comes from the URL's currentJobId — two sources
    // that can desync. The pane keeps rendering a previously-viewed job (a
    // promoted listing that loaded into it first is the usual culprit) while
    // currentJobId has already advanced to the job being applied to, so
    // identity is right and the CONTENT is a different job's. Three real
    // applications filed this way on 8 Sep 2026 — Northwind Labs, Fabrikam and
    // Tailspin, every one stamped with Contoso Markets's company, title and JD, Contoso Markets
    // being the promoted card sitting at the top of an "AI Engineer" search.
    // The pane carries no job id of its own (measured live), but the results
    // LIST does: the card whose data-*-job-id equals currentJobId holds that
    // job's own title and company. When the card disagrees with what the pane
    // rendered, the pane is stale — re-source title/company from the card and
    // DROP the JD (the card has none, and the pane's JD belongs to the wrong
    // job). currentJobId, the fact, is kept. On /jobs/view/ there is no results
    // card, so `card` is null and this is a no-op — the guard is the card's
    // existence, not the pathname, so a future split layout is covered too.
    let stale_pane = null;
    if (idFromUrl && /^\d+$/.test(idFromUrl) && title) {
      const norm = (s) => s.toLowerCase().replace(/\s+/g, " ").trim();
      // Card title/company text is doubled (visible span + a visually-hidden
      // copy carrying the accessible name — the same a11y pattern answers.js
      // strips); the two halves are identical, so take the first.
      const undouble = (s) => {
        const c = (s || "").replace(/\s+/g, " ").trim();
        const h = c.length / 2;
        return c.length % 2 === 0 && h > 0 && c.slice(0, h) === c.slice(h)
          ? c.slice(0, h) : c;
      };
      const card =
        doc.querySelector(`[data-occludable-job-id="${idFromUrl}"]`) ||
        doc.querySelector(`[data-job-id="${idFromUrl}"]`);
      const link = card && card.querySelector(
        "a.job-card-container__link, a.job-card-list__title--link");
      const cardTitle = link ? undouble(link.textContent) : null;
      if (cardTitle && norm(cardTitle) !== norm(title)) {
        const sub = card.querySelector(".artdeco-entity-lockup__subtitle");
        stale_pane = { url: idFromUrl, shown: title, card: cardTitle };
        title = cardTitle;
        company = sub ? undouble(sub.textContent) : null;
        title_source = company_source = "card";
        jd_text = null;
        location = null;
        posted_label = null;
        reposted = null;
      }
    }
    // A job id read out of the URL is a FACT, and discarding it because the DOM
    // has gone missing is how a capture ends up unidentifiable. LinkedIn swaps
    // the top card for an "application sent" confirmation moments after an Easy
    // Apply submit — which is exactly when frame 0 gets asked who the job is
    // (capture.js:askTopJob) — so returning null there would hand back nothing
    // at the one moment the surviving field is the one that identifies the job.
    // Every caller guards on title/jd_text rather than on null (capture()'s
    // empty-job check, the tracker-apply responder), so an id-only read still
    // fails the checks meant to fail; it just stops being invisible.
    if (!title && !jdEl && !idFromUrl) return null;

    // Which of the three sources actually won, recorded so a wrong title is
    // diagnosable AFTER the fact instead of costing a live session.
    //
    // Two real captures stored a title belonging to no job on the page —
    // Southridge APAC 3 Aug 2026 ("Senior AI/ML Engineer" for what LinkedIn calls
    // "Data Scientist – Platform AI Squad") and Adatum 2 Aug ("AI Fullstack
    // Engineer" for "Fullstack Software Engineer"). Both were Easy Apply, both
    // kept the CORRECT company, and neither was reproducible: replaying all
    // three layouts live on 4 Aug returned the right title every time, and the
    // id/title pair was never observed out of sync across a selection change.
    // Nothing in the record said which branch had produced the string, so
    // there was no way to narrow it further. This field is that missing fact.
    const _prov = {
      // Which DOCUMENT this read came from, alongside which SELECTOR won.
      // getJob() overwrites it with "self" when the top frame had nothing and
      // this frame did — the one distinction that separates a healthy capture
      // from a rescued one, and the field to read first when the next blind
      // record turns up.
      doc_source: "top",
      title_source,
      company_source,
      layout: loc.pathname.startsWith("/jobs/collections/") ? "collections"
            : loc.pathname.startsWith("/jobs/view/") ? "view"
            : loc.pathname.startsWith("/jobs/search") ? "search" : "other",
      // Present only when the split-layout guard above caught a stale detail
      // pane and re-sourced identity from the results card — the field to read
      // first if a capture's company/title ever looks like a neighbour's again.
      ...(stale_pane && { stale_pane }),
    };

    return {
      platform_job_id: idFromUrl,
      url: idFromUrl ? `https://www.linkedin.com/jobs/view/${idFromUrl}/` : loc.href,
      company,
      title,
      jd_text,
      location,
      posted_label,
      reposted,
      // Diagnostic only. buildPayload() whitelists the fields it sends, so
      // this never reaches the server — it exists for the popup buffer.
      _prov,
    };
  },
  // "Meet the hiring team" job-poster card — only present when the poster
  // opted to show it, so absence is normal, not a selector failure. Deliberately
  // NOT the separate "People you can reach out to" card LinkedIn also shows:
  // that's just a 1st-degree connection who happens to work there, not a
  // hiring contact. Same top-frame reasoning as getJob(): Easy Apply's submit
  // click fires inside the modal iframe, which doesn't have this section in
  // its own document.
  //
  // Verified live against the "AI-powered search" beta (/jobs/search-results/)
  // on 2026-07-23: this layout has NO stable classes at all on the card
  // (hashed atomic CSS on every element, e.g. "_85e1283b e218918b..." —
  // regenerated per build, same issue getJob() works around via document.title).
  // "Meet the hiring team" is plain visible copy though, so anchor on that text
  // instead of any class. Not verified against the classic /jobs/view/ layout.
  //
  // REWRITTEN 3 Aug 2026 against the real card, after the old version stored
  //   name = "Jane Recruiter • 2ndSenior Consultant | Software and Data
  //           Engineering @ Northwind RecruitingJob poster"
  //   role = "Jane Recruiter"
  // on a live capture — the whole blob as the name, and the name as the role.
  // Both of its structural assumptions were simply wrong for this layout:
  //
  //  1. It expected a name-only <a> NESTED inside a card-sized <a> with the
  //     same href, and picked the shorter one. There is exactly ONE such <a>.
  //     With no nested pair, ownText() on it is empty (its text all lives in
  //     descendants), so the fallback handed back the entire card blob.
  //  2. It took the FIRST <span> carrying its own direct text as the role,
  //     on the theory that everything else wraps its text further down. The
  //     name is such a span too, and it comes first — hence role = the name.
  //
  // What is actually there, confirmed live: one <a href="/in/…">, and inside
  // it the name and the headline are the first two <span>s with their own
  // direct text, in that order. The connection degree ("• 2nd") and the "Job
  // poster" badge did NOT surface as own-text spans on this card, but they are
  // filtered by name anyway — they are the two things most likely to appear as
  // a stray span on a layout this file hasn't met yet, and a wrong role is
  // harder to notice than a missing one.
  getRecruiter() {
    let topWin = window;
    try {
      if (window.top && window.top.document) topWin = window.top;
    } catch (e) { /* cross-origin top somehow — fall back to this frame */ }
    const topDoc = topWin.document;
    const heading = [...topDoc.querySelectorAll("*")].find(
      (el) => el.children.length === 0 && el.textContent.trim() === "Meet the hiring team");
    const card = heading && heading.parentElement;
    if (!card) return null;
    const links = [...card.querySelectorAll("a[href*='/in/']")];
    if (!links.length) return null;

    const ownText = (el) => [...el.childNodes]
      .filter((n) => n.nodeType === 3).map((n) => n.textContent).join("")
      .replace(/\s+/g, " ").trim();
    const NOISE = /^(?:•\s*)?(?:1st|2nd|3rd)\s*\+?$|^job poster$|^ask about job$/i;

    const parts = [];
    for (const l of links) {
      for (const s of l.querySelectorAll("span")) {
        const t = ownText(s);
        if (t && !NOISE.test(t) && !parts.includes(t)) parts.push(t);
      }
    }
    let name = parts[0] || null;
    let role = parts[1] || null;
    // No usable spans at all — a layout neither shape fits. Fall back to the
    // shortest link text, which is what the previous version reduced to, and
    // claim no role rather than inventing one out of the blob.
    if (!name) {
      for (const l of links) {
        const t = ownText(l) || l.textContent.replace(/\s+/g, " ").trim();
        if (t && (!name || t.length < name.length)) name = t;
      }
      role = null;
    }
    if (!name) return null;
    return { name, url: links[0].href.split("?")[0], role };
  },
};
