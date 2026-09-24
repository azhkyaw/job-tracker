/* Any other job page — an employer's career site or its applicant tracking
 * system (docs/career-sites.md). Loaded after shared/jobposting.js, which does
 * all the reading; this file only states the adapter contract for it.
 *
 * Phase A (24 Sep 2026): injected by the toolbar popup into the page in front
 * of the user, on their click, and only there — activeTab + scripting, no
 * standing access to any site. So there are no apply selectors yet: the
 * capture IS the click on "Capture this job as applied". Per-vendor submit
 * hooks come with phase B, on the ATS hosts the manifest will list.
 */
window.__trackerAdapter = {
  platform: "other",
  applySelectors: [],
  getJob() {
    return window.__trackerJobPosting.read(document, location);
  },
  // Identity from a URL alone — capture.js's last resort when the page read
  // comes back empty. Same id read() would have derived from the same URL.
  jobFromUrl(href) {
    const id = window.__trackerJobPosting.idFrom(href);
    return id ? { platform_job_id: id.platform_job_id, url: id.url } : null;
  },
};
