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
  // Same contract as LinkedIn: the opening click records NOTHING, and the
  // application is written when it is actually sent — so the applied time is
  // the submit, not the five minutes of form-filling before it.
  //
  // SEEK's flow is its own page (/job/<id> -> /apply -> /apply/profile ->
  // /apply/review), so the opening click also destroys this script. The job
  // snapshot is stashed through the service worker on that click and merged
  // back at submit; without it the record would lose the company name, which
  // the review page does not show anywhere reachable (verified 29 Jul 2026 —
  // it's a bare <span>, no data-automation on it or any ancestor).
  deferInternalApply: true,
  // VERIFIED against the real review page 29 Jul 2026: a <button type="submit">
  // whose text is "⁠Submit application" — a WORD JOINER glued to the
  // front, which String.trim() does not remove. shared/capture.js compares
  // normalised visible text for exactly this reason. There is no
  // data-automation attribute on that button, so text is the only hook; a bare
  // "Submit" is deliberately not accepted, since it would match unrelated
  // buttons and capture whatever job that page happened to describe.
  applyTextMatches: ["Submit application"],
  isExternal(el) {
    return (el.textContent || "").toLowerCase().includes("on employer site") ||
           (el.getAttribute("data-automation") || "").includes("external");
  },
  // Screening Q&A, same contract as LinkedIn's. Gated on the apply path rather
  // than looking for a form anywhere: a job listing page carries SEEK's own
  // search form, and sweeping that would file the text in the search box as an
  // answer to a question nobody asked.
  answerFormRoot() {
    if (!/\/apply(\/|$)/.test(location.pathname)) return null;
    return document.querySelector(
      "[data-automation='applicationForm'], form[data-automation*='apply'], form");
  },
  answerFormKey() {
    return (location.pathname.match(/\/job\/(\d+)/) || [])[1] ||
           new URLSearchParams(location.search).get("jobId") || location.href;
  },
  getJob() {
    const q = (sels) => {
      for (const s of sels) {
        const el = document.querySelector(s);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return null;
    };
    // Canonical page is /job/<id>; the search split-view instead carries the
    // same id as ?jobId=<id> on a /<slug>-jobs search URL — fall back to that
    // or the id would silently go missing and url would point at the search
    // page instead of the posting.
    const idFromUrl = (location.pathname.match(/\/job\/(\d+)/) || [])[1] ||
      new URLSearchParams(location.search).get("jobId") || null;
    const title = q(["[data-automation='job-detail-title']", "h1"]);
    const company = q([
      "[data-automation='advertiser-name']",
      "[data-automation='job-detail-company-name']",
    ]);
    // The detail pane's own hooks are `job-detail-*` and are document-UNIQUE
    // even in the split view, where the results list beside them carries 30
    // `jobLocation` and 18 `jobSalary` nodes of its own (verified 29 Jul 2026).
    // Never reach for the unprefixed card names: querySelector would return
    // the first CARD's value, i.e. a different job's location entirely.
    // NOT named `location`: that shadows window.location, and the id/url reads
    // above it would then hit the temporal dead zone and throw on every page.
    const jobLocation = q(["[data-automation='job-detail-location']"]);
    // Salary is printed BESIDE the ad, never inside jd_text — so the JD
    // extractor has never been able to see it. Sent as the displayed string;
    // pipeline/salary.py turns it into numbers, currency and period.
    const salaryRaw = q(["[data-automation='job-detail-salary']"]);
    const workType = q(["[data-automation='job-detail-work-type']"]);
    // Present only when SEEK judges the range to match the profile salary; its
    // absence means "nothing said", which is why this is a tri-state and not a
    // plain boolean defaulted to false.
    const matchEl = document.querySelector("[data-automation='job-detail-salary-match']");
    // "Posted 1d ago" has no data-automation of its own, so it's matched by
    // shape within the detail block — and only ever from a node that says
    // nothing else, so a paragraph merely mentioning a date can't win.
    let postedLabel = null;
    for (const el of document.querySelectorAll("span,div,time")) {
      if (el.children.length) continue;
      const m = (el.textContent || "").trim().match(/^Posted\s+(.+?\s+ago)$/i);
      if (m) { postedLabel = m[1]; break; }
    }
    const jdEl = document.querySelector("[data-automation='jobAdDetails']");
    if (!title && !jdEl) return null;
    return {
      platform_job_id: idFromUrl,
      url: idFromUrl ? `${location.origin}/job/${idFromUrl}` : location.href.split("?")[0],
      company,
      title,
      location: jobLocation,
      salary_raw: salaryRaw,
      work_type: workType,
      salary_match: matchEl ? true : null,
      posted_label: postedLabel,
      jd_text: jdEl ? jdEl.innerText.trim() : null,
    };
  },
};
