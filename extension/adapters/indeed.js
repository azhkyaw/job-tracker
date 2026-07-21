/* Indeed adapter. Job id rides in the ?jk= or ?vjk= param; description in
 * #jobDescriptionText. Apply buttons vary between Indeed Apply and external. */
window.__trackerAdapter = {
  platform: "indeed",
  applySelectors: [
    "#indeedApplyButton",
    "button[id^='indeedApplyButton']",
    ".jobsearch-IndeedApplyButton-newDesign",
    "#applyButtonLinkContainer a",
    "button[aria-label*='Apply now']",
    "a[aria-label*='Apply now']",
  ],
  isExternal(el) {
    return el.tagName === "A" ||
           (el.textContent || "").toLowerCase().includes("company site");
  },
  getJob() {
    const q = (sels) => {
      for (const s of sels) {
        const el = document.querySelector(s);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return null;
    };
    const params = new URLSearchParams(location.search);
    const idFromUrl = params.get("jk") || params.get("vjk") || null;
    const title = q([
      "h1[data-testid='jobsearch-JobInfoHeader-title']",
      "h2[data-testid='jobsearch-JobInfoHeader-title']",
      ".jobsearch-JobInfoHeader-title",
      "h1",
    ]);
    const company = q([
      "[data-testid='inlineHeader-companyName']",
      "[data-company-name]",
      ".jobsearch-CompanyInfoContainer a",
    ]);
    const jdEl = document.querySelector("#jobDescriptionText");
    if (!title && !jdEl) return null;
    return {
      platform_job_id: idFromUrl,
      url: idFromUrl ? `${location.origin}/viewjob?jk=${idFromUrl}` : location.href,
      company,
      title,
      jd_text: jdEl ? jdEl.innerText.trim() : null,
    };
  },
};
