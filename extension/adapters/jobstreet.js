/* JobStreet (SEEK platform) adapter — data-automation attributes are the most
 * stable hooks SEEK exposes; they cover the SG/MY/ID variants and seek.com.au. */
window.__trackerAdapter = {
  platform: "jobstreet",
  applySelectors: [
    "[data-automation='job-detail-apply']",
    "[data-automation='jobDetailsApplyButton']",
    "a[data-automation*='apply']",
    "button[data-automation*='apply']",
  ],
  isExternal(el) {
    return (el.textContent || "").toLowerCase().includes("on employer site") ||
           (el.getAttribute("data-automation") || "").includes("external");
  },
  getJob() {
    const q = (sels) => {
      for (const s of sels) {
        const el = document.querySelector(s);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return null;
    };
    const idFromUrl = (location.pathname.match(/\/job\/(\d+)/) || [])[1] || null;
    const title = q(["[data-automation='job-detail-title']", "h1"]);
    const company = q([
      "[data-automation='advertiser-name']",
      "[data-automation='job-detail-company-name']",
    ]);
    const jdEl = document.querySelector("[data-automation='jobAdDetails']");
    if (!title && !jdEl) return null;
    return {
      platform_job_id: idFromUrl,
      url: location.href.split("?")[0],
      company,
      title,
      jd_text: jdEl ? jdEl.innerText.trim() : null,
    };
  },
};
