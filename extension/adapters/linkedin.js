/* LinkedIn adapter. Selectors are layered fallbacks — LinkedIn re-skins often.
 * When capture fails, fix these first; everything else lives in shared code. */
window.__trackerAdapter = {
  platform: "linkedin",
  applySelectors: [
    ".jobs-apply-button",
    "button[aria-label*='Easy Apply']",
    "button[aria-label^='Apply']",
    "button[data-live-test-job-apply-button]",
  ],
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
    const idFromUrl =
      new URLSearchParams(location.search).get("currentJobId") ||
      (location.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1] || null;
    const title = q([
      ".job-details-jobs-unified-top-card__job-title",
      ".jobs-unified-top-card__job-title",
      "h1",
    ]);
    const company = q([
      ".job-details-jobs-unified-top-card__company-name a",
      ".job-details-jobs-unified-top-card__company-name",
      ".jobs-unified-top-card__company-name",
    ]);
    const jdEl =
      document.querySelector("#job-details") ||
      document.querySelector(".jobs-description__content") ||
      document.querySelector(".jobs-box__html-content");
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
