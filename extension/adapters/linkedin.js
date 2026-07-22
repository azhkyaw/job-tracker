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
  getJob() {
    const q = (sels) => {
      for (const s of sels) {
        const el = document.querySelector(s);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return null;
    };
    // LinkedIn's "AI-powered search" beta variant (/jobs/search-results/) ships
    // hashed atomic CSS classes (e.g. "f6cb7395") instead of the stable
    // .job-details-jobs-unified-top-card__* classes below — those classes are
    // regenerated per build, so no fixed selector will survive across
    // deploys. document.title ("<title> | <company> | LinkedIn") is set by
    // LinkedIn's own tab-title code and is a steadier fallback than chasing
    // hashes.
    const titleParts = document.title.split(" | ");
    const titleFromDocTitle =
      titleParts.length >= 2 && titleParts[titleParts.length - 1] === "LinkedIn"
        ? titleParts[0].trim() : null;
    const companyFromDocTitle =
      titleParts.length >= 3 && titleParts[titleParts.length - 1] === "LinkedIn"
        ? titleParts[titleParts.length - 2].trim() : null;

    const idFromUrl =
      new URLSearchParams(location.search).get("currentJobId") ||
      (location.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1] || null;
    const title = q([
      ".job-details-jobs-unified-top-card__job-title",
      ".jobs-unified-top-card__job-title",
      "h1",
    ]) || titleFromDocTitle;
    const company = q([
      ".job-details-jobs-unified-top-card__company-name a",
      ".job-details-jobs-unified-top-card__company-name",
      ".jobs-unified-top-card__company-name",
    ]) || companyFromDocTitle;
    const jdEl =
      document.querySelector("#job-details") ||
      document.querySelector(".jobs-description__content") ||
      document.querySelector(".jobs-box__html-content") ||
      document.querySelector("[id^='JobDetails_AboutTheJob_']");
    if (!title && !jdEl) return null;
    return {
      platform_job_id: idFromUrl,
      url: idFromUrl ? `https://www.linkedin.com/jobs/view/${idFromUrl}/` : location.href,
      company,
      title,
      jd_text: jdEl ? jdEl.innerText.trim() : null,
    };
  },
};
