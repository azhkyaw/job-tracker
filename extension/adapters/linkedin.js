/* LinkedIn adapter. Selectors are layered fallbacks — LinkedIn re-skins often.
 * When capture fails, fix these first; everything else lives in shared code. */
window.__trackerAdapter = {
  platform: "linkedin",
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
  answerFormRoot() {
    if (window !== window.top) return document.querySelector("form") || document.body;
    return document.querySelector(
      ".jobs-easy-apply-modal, .jobs-easy-apply-content, " +
      "[role='dialog'] form, [data-test-modal] form");
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
  getJob() {
    // Easy Apply's "Submit application" click happens inside a same-origin
    // iframe (the modal) whose own document only has the contact-form/resume
    // fields — the job title/company/JD live in the outer page. Since the
    // script now runs in every frame (manifest all_frames), always read job
    // identity from the top frame's document/location; when getJob() is
    // itself called from the top frame (e.g. an external-apply click), top
    // *is* window, so this is a no-op there.
    let topWin = window;
    try {
      if (window.top && window.top.document) topWin = window.top;
    } catch (e) { /* cross-origin top somehow — fall back to this frame */ }
    const topDoc = topWin.document;
    const topLoc = topWin.location;

    const q = (sels) => {
      for (const s of sels) {
        const el = topDoc.querySelector(s);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return null;
    };
    const idFromUrl =
      new URLSearchParams(topLoc.search).get("currentJobId") ||
      (topLoc.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1] || null;

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
      topDoc.querySelector("#job-details") ||
      topDoc.querySelector(".jobs-description__content") ||
      topDoc.querySelector(".jobs-box__html-content") ||
      topDoc.querySelector("[id^='JobDetails_AboutTheJob_']");

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
    const classContainer = topDoc.querySelector(
      ".job-details-jobs-unified-top-card__tertiary-description-container");
    if (classContainer && classContainer.children[0]) {
      tertiaryHost = classContainer.children[0];
    } else {
      for (let node = jdEl || topDoc.body, hops = 0;
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
    const docTitleTracksSelectedJob = !/\/jobs\/collections\//.test(topLoc.pathname);
    const titleParts = topDoc.title.split(" | ");
    const titleFromDocTitle =
      docTitleTracksSelectedJob && titleParts.length === 3 && titleParts[2] === "LinkedIn"
        ? titleParts[0].trim() : null;
    const companyFromDocTitle =
      docTitleTracksSelectedJob && titleParts.length === 3 && titleParts[2] === "LinkedIn"
        ? titleParts[1].trim() : null;

    const title = classTitle || structTitle || titleFromDocTitle;
    const company = classCompany || structCompany || companyFromDocTitle;
    if (!title && !jdEl) return null;

    return {
      platform_job_id: idFromUrl,
      url: idFromUrl ? `https://www.linkedin.com/jobs/view/${idFromUrl}/` : topLoc.href,
      company,
      title,
      jd_text: jdEl ? jdEl.innerText.trim() : null,
      location,
      posted_label,
      reposted,
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
    // The card nests a name-only <a> (its only content is the text "Ray Tan")
    // inside a wrapping card-sized <a> with the same href whose textContent
    // also picks up the role and the "Job poster" badge — same nested-anchor
    // shape LinkedIn uses for the Easy Apply shadow-DOM controls. Own direct
    // text (excluding descendant elements) isolates just the name; fall back
    // to full textContent if that ever comes back empty for every candidate.
    const ownText = (el) => [...el.childNodes]
      .filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
    let best = null;
    for (const l of links) {
      const text = ownText(l) || l.textContent.trim();
      if (text && (!best || text.length < best.text.length)) best = { text, href: l.href };
    }
    if (!best) return null;
    // The role/headline text ("Talent Acquisition | Hiring top Tech talents
    // across Migoo and Product") lives in a <span> inside the OTHER link — the
    // outer, card-sized one, not the inner name-only one — as that span's own
    // direct text. Everything else under the outer link (avatar, name, "•
    // 2nd" connection degree, "Job poster" badge) wraps its text in further
    // child elements instead, so this stays the only match.
    const outer = links.reduce((a, b) => (a.textContent.length >= b.textContent.length ? a : b));
    const roleSpan = [...outer.querySelectorAll("span")].find((s) => ownText(s));
    const role = roleSpan ? ownText(roleSpan) : null;
    return { name: best.text, url: best.href.split("?")[0], role };
  },
};
