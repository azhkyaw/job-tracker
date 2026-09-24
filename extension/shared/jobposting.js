/* A job read off any page that publishes one — employer career sites and
 * applicant tracking systems (docs/career-sites.md §5).
 *
 * Loaded BEFORE an adapter and shared/capture.js. Defines
 * window.__trackerJobPosting and touches nothing else at load, so
 * tests/test_extension.js can run it against a fake document:
 *
 *   read(doc, loc)   the getJob() shape, or null when the page names no job
 *   idFrom(href)     {platform_job_id, url} | null — the posting's identity
 *   atsOfUrl(href)   ATS vendor of a URL's host, or null
 *   vendorOf(doc, loc)  the same, falling back to the hosts the page loads
 *
 * Why schema.org JobPosting: Google's job search requires it, so most ATS
 * vendors publish one on the public job page — as JSON-LD or as microdata.
 * The 24 Sep 2026 survey of 18 vendors (docs/career-sites.md §4) found it on
 * most, absent on three, and never in one consistent shape: dates in five
 * formats, descriptions as HTML, as entity-ENCODED HTML and as flattened
 * text, an organisation as an object, a bare string or a nested itemscope.
 * Everything below tolerates exactly those shapes. When a page has no
 * JobPosting at all (Greenhouse, Recruitee and Avature publish none), read()
 * falls back to <h1> / og:title / the tab title and says so in
 * _prov.title_source.
 */
(() => {
  /* ------------------------------------------------------------ vendors */

  // Host suffix -> ATS vendor. ONE table, for the page's own host, for an
  // external-apply destination (capture.js:detectAts) and for the resource
  // hosts a page loads — which is the only way to recognise a vendor behind an
  // employer's own domain: a SuccessFactors career site on careers.<employer>
  // says "successfactors" nowhere but in where its scripts come from.
  const VENDORS = [
    ["greenhouse.io", "greenhouse"], ["lever.co", "lever"],
    ["myworkdayjobs.com", "workday"], ["myworkday.com", "workday"],
    ["myworkdaycdn.com", "workday"],
    ["ashbyhq.com", "ashby"], ["ashbyprd.com", "ashby"],
    ["icims.com", "icims"], ["smartrecruiters.com", "smartrecruiters"],
    ["jobvite.com", "jobvite"], ["bamboohr.com", "bamboohr"],
    ["taleo.net", "taleo"], ["oraclecloud.com", "oracle"],
    ["successfactors.com", "successfactors"], ["successfactors.eu", "successfactors"],
    ["workable.com", "workable"], ["breezy.hr", "breezy"],
    ["personio.com", "personio"], ["personio.de", "personio"],
    ["recruitee.com", "recruitee"], ["recruiteecdn.com", "recruitee"],
    ["teamtailor.com", "teamtailor"], ["teamtailor-cdn.com", "teamtailor"],
    ["jazzhr.com", "jazzhr"],
    // JazzHR serves customer job boards from applytojob.com, not jazzhr.com —
    // a real external apply resolved to <tenant>.applytojob.com (4 Aug 2026)
    // and came back with no ATS despite being a textbook JazzHR board.
    ["applytojob.com", "jazzhr"],
    ["paylocity.com", "paylocity"],
    ["phenompeople.com", "phenom"],
    ["avature.net", "avature"], ["avacdn.net", "avature"],
    ["vscdn.net", "eightfold"], ["eightfold.ai", "eightfold"],
  ];

  function hostOf(href, base) {
    try { return new URL(href, base).hostname.toLowerCase(); } catch (e) { return ""; }
  }

  function vendorOfHost(host) {
    if (!host) return null;
    for (const [suffix, vendor] of VENDORS) {
      if (host === suffix || host.endsWith("." + suffix)) return vendor;
    }
    return null;
  }

  const atsOfUrl = (href) => vendorOfHost(hostOf(href));

  function vendorOf(doc, loc) {
    const own = atsOfUrl(loc && loc.href);
    if (own) return own;
    // The src/href PROPERTIES are already absolute in a real page; the
    // attribute may be protocol-relative ("//rmkcdn…") or relative, so it is
    // resolved against the page rather than read as it stands.
    for (const el of doc.querySelectorAll("script[src], link[href], iframe[src]")) {
      const raw = el.src || el.href || el.getAttribute("src") || el.getAttribute("href") || "";
      const v = vendorOfHost(hostOf(raw, loc && loc.href));
      if (v) return v;
    }
    return null;
  }

  /* ----------------------------------------------------------- identity */

  // A posting's identity from its URL alone, the same way on every site:
  // `<host>/<token>`, host first so two employers' "12345" can never collide
  // under platform 'other' (postings_platform_job_uidx is user+platform+id).
  // pipeline/joburl.py:generic_id is this function in Python, and
  // tests/job_urls.json holds the two to one list — a manual entry pasted from
  // the same URL must derive the same id, or it never converges with a capture.
  //
  // The token, in order:
  //  1. an explicit id parameter in the query — Taleo's ?job=, an embedded
  //     Greenhouse board's ?gh_jid=, Eightfold's ?pid= …;
  //  2. the LAST id-shaped path segment: a UUID (Lever, Ashby), a slug ending
  //     in 32 hex (MyCareersFuture), a slug ending _REQID (Workday), digits
  //     then a slug (SmartRecruiters, Teamtailor), a bare number (most), or an
  //     opaque 8-40 character code of letters AND digits (Workable, JazzHR);
  //  3. otherwise the whole path PLUS the non-tracking query. The query is not
  //     decoration there: a generic page carrying its job in a parameter this
  //     list does not know (an embed's ?for=…&token=…) would otherwise give
  //     every job on it ONE id, and a capture would silently update another
  //     job's posting. A tracking parameter at worst makes a duplicate, which
  //     is visible and mergeable — the failure invariant #3 prefers.
  // Lower-cased throughout, so a URL's case variants (Workday changes it
  // between its own links) keep one identity.
  const ID_PARAMS = ["gh_jid", "jobid", "job_id", "job", "jid", "pid", "reqid",
                     "req_id", "requisitionid", "career_job_req_id", "jk", "id"];
  const SEGMENT_IDS = [
    /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/,
    /-([0-9a-f]{32})$/,
    /_([a-z]{0,5}\d{3,}(?:-\d+)?)$/,
    /^(\d{4,})-/,
    /^(\d+)$/,
    /^((?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{8,40})$/,
  ];

  function decodeSegment(s) {
    try { return decodeURIComponent(s); } catch (e) { return s; }
  }

  function idFrom(href) {
    let u;
    try { u = new URL(href); } catch (e) { return null; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    const host = u.hostname.toLowerCase().replace(/^www\./, "");
    const params = [...u.searchParams].map(([k, v]) => [k.toLowerCase(), v]);
    let token = null;
    for (const name of ID_PARAMS) {
      const hit = params.find(([k, v]) => k === name && v && v.length <= 64);
      if (hit) { token = hit[1].toLowerCase(); break; }
    }
    const segs = u.pathname.split("/").map(decodeSegment)
      .map((s) => s.toLowerCase()).filter(Boolean);
    for (let i = segs.length - 1; i >= 0 && !token; i--) {
      for (const rx of SEGMENT_IDS) {
        const m = rx.exec(segs[i]);
        if (m) { token = m[1]; break; }
      }
    }
    if (!token) {
      if (!segs.length) return null;          // a bare site is not a job
      const query = params.filter(([k]) => !k.startsWith("utm_"))
        .map(([k, v]) => [k, v.toLowerCase()])
        .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0))
        .map(([k, v]) => `${k}=${v}`).join("&");
      token = segs.join("/") + (query ? `?${query}` : "");
    }
    return {
      platform_job_id: `${host}/${token}`.slice(0, 300),
      url: u.origin + u.pathname + u.search,
    };
  }

  /* ---------------------------------------------------------------- text */

  const NAMED = {
    amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", ndash: "–",
    mdash: "—", hellip: "…", rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“",
    bull: "•", middot: "·", copy: "©", reg: "®", trade: "™", deg: "°",
  };

  function decodeEntities(s) {
    return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (all, e) => {
      if (e[0] === "#") {
        const n = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        try { return String.fromCodePoint(n); } catch (err) { return all; }
      }
      const v = NAMED[e.toLowerCase()];
      return v === undefined ? all : v;
    });
  }

  function stripTags(s) {
    return s
      .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, "")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<li\b[^>]*>/gi, "\n• ")
      .replace(/<\/li>/gi, "")
      .replace(/<\/?(p|div|ul|ol|h[1-6]|tr|table|section|article|blockquote)\b[^>]*>/gi, "\n")
      .replace(/<[^>]+>/g, "");
  }

  // HTML, entity-encoded HTML, or text with entities — all three were live on
  // 24 Sep 2026 (most vendors; Teamtailor and Phenom; Workday). Two passes of
  // strip-then-decode reach text from any of them: encoded HTML only shows
  // its tags after the first decode.
  function htmlToText(value) {
    let s = String(value == null ? "" : value);
    for (let pass = 0; pass < 2; pass++) {
      if (/<\/?[a-z][^>]*>/i.test(s)) s = stripTags(s);
      s = decodeEntities(s);
    }
    return s.replace(/\r\n?/g, "\n").replace(/[ \t ]+/g, " ")
      .replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  const str = (v) => {
    if (v == null || typeof v === "object") return null;
    const s = htmlToText(v).replace(/\s+/g, " ").trim();
    return s || null;
  };

  /* ------------------------------------------------------ the JobPosting */

  const isJobPostingType = (t) => [].concat(t || []).some(
    (x) => String(x).replace(/^.*[/:#]/, "") === "JobPosting");

  function jsonLdPostings(doc) {
    const out = [];
    const visit = (x) => {
      if (!x || typeof x !== "object") return;
      if (Array.isArray(x)) { x.forEach(visit); return; }
      if (x["@graph"]) visit(x["@graph"]);
      if (isJobPostingType(x["@type"])) out.push(x);
    };
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      const raw = s.textContent || "";
      let parsed = null;
      try { parsed = JSON.parse(raw); } catch (e) {
        // A raw newline or tab inside a string is illegal JSON and common in
        // hand-templated blocks; retry with control characters flattened.
        try { parsed = JSON.parse(raw.replace(/[\u0000-\u001f]+/g, " ")); } catch (e2) {}
      }
      visit(parsed);
    }
    return out;
  }

  // The microdata value of one itemprop element, per the HTML spec's rules.
  function itemValue(el) {
    if (el.hasAttribute("itemscope")) return itemObject(el);
    const tag = el.tagName;
    if (tag === "META") return el.getAttribute("content");
    if (tag === "A" || tag === "LINK" || tag === "AREA") return el.getAttribute("href");
    if (tag === "IMG" || tag === "SOURCE" || tag === "IFRAME") return el.getAttribute("src");
    if (tag === "TIME") return el.getAttribute("datetime") || (el.textContent || "").trim();
    if (tag === "DATA" || tag === "METER") return el.getAttribute("value");
    return el.innerText || el.textContent || "";
  }

  // An item's properties: every [itemprop] whose nearest enclosing scope is
  // THIS one. A nested Organization's "name" belongs to the Organization, not
  // to the JobPosting — that is how SmartRecruiters writes hiringOrganization.
  // [itemtype] counts as a scope too, so a page that forgot itemscope on the
  // JobPosting element itself still reads.
  function itemObject(scope) {
    const obj = {};
    for (const el of scope.querySelectorAll("[itemprop]")) {
      const owner = el.parentElement && el.parentElement.closest("[itemscope], [itemtype]");
      if (owner !== scope) continue;
      const v = itemValue(el);
      if (v == null || v === "") continue;
      for (const name of (el.getAttribute("itemprop") || "").split(/\s+/)) {
        if (name && !(name in obj)) obj[name] = v;
      }
    }
    return obj;
  }

  function microdataPostings(doc) {
    return [...doc.querySelectorAll("[itemtype]")]
      .filter((el) => isJobPostingType(el.getAttribute("itemtype")))
      .map(itemObject);
  }

  const first = (v) => (Array.isArray(v) ? v.find((x) => x != null && x !== "") : v);

  function orgName(v) {
    v = first(v);
    if (v && typeof v === "object") return str(v.name) || str(v.legalName);
    return str(v);
  }

  function place(v) {
    const all = Array.isArray(v) ? v : [v];
    for (const p of all) {
      if (!p) continue;
      if (typeof p !== "object") { const s = str(p); if (s) return s; continue; }
      const a = first(p.address);
      if (a && typeof a === "object") {
        const country = a.addressCountry && typeof a.addressCountry === "object"
          ? a.addressCountry.name : a.addressCountry;
        const parts = [];
        for (const x of [a.addressLocality, a.addressRegion, country]) {
          const s = str(x);
          if (s && !parts.some((q) => q.toLowerCase() === s.toLowerCase())) parts.push(s);
        }
        // A survey site put every place in streetAddress alone ("Singapore,
        // SG"); a street line is only the answer when nothing else is.
        if (parts.length) return parts.join(", ");
        const street = str(a.streetAddress);
        if (street) return street;
      }
      const s = str(a) || str(p.name);
      if (s) return s;
    }
    return null;
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // The date as "24 Sep 2026" — a label, like the platforms' "3 weeks ago",
  // since that is what postings.posted_label holds and the detail page prints.
  // Taken from the text's own Y-M-D, never shifted through a timezone: the
  // date the page printed is the date. Java's Date.toString() form
  // ("Thu Sep 24 00:00:00 UTC 2026", a SuccessFactors site) is read by shape.
  function dateLabel(v) {
    const s = str(first(v));
    if (!s) return null;
    let m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (m && +m[2] >= 1 && +m[2] <= 12) return `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}`;
    m = /^[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) [\d:]+ [A-Za-z+\-\d]+ (\d{4})$/.exec(s);
    if (m) {
      const mon = MONTHS.find((x) => x.toLowerCase() === m[1].toLowerCase());
      if (mon) return `${+m[2]} ${mon} ${m[3]}`;
    }
    return null;
  }

  const UNITS = { HOUR: "hour", DAY: "day", WEEK: "week", MONTH: "month", YEAR: "year" };
  const money = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

  // baseSalary rendered as the kind of line pipeline/salary.py already reads
  // ("SGD 134,400 – 176,400 per year"). The period rides along or the figure
  // means nothing — SEA quotes monthly, most of the world annually.
  function salaryLine(v) {
    v = first(v);
    if (!v || typeof v !== "object") return null;
    const q = v.value && typeof v.value === "object" ? v.value : v;
    const num = (x) => { const n = Number(String(x == null ? "" : x).replace(/,/g, "")); return x !== "" && x != null && isFinite(n) && n > 0 ? n : null; };
    const lo = num(q.minValue), hi = num(q.maxValue);
    const one = num(q.value !== undefined && typeof q.value !== "object" ? q.value : null);
    const figure = lo && hi ? (lo === hi ? money(lo) : `${money(lo)} – ${money(hi)}`)
      : (lo || hi || one) ? money(lo || hi || one) : null;
    if (!figure) return null;
    const cur = str(v.currency || q.currency);
    const unit = UNITS[String(q.unitText || v.unitText || "").toUpperCase()];
    return [cur, figure, unit ? `per ${unit}` : null].filter(Boolean).join(" ");
  }

  const WORK_TYPES = {
    FULL_TIME: "Full time", FULLTIME: "Full time", PART_TIME: "Part time",
    PARTTIME: "Part time", CONTRACTOR: "Contract/Temp", CONTRACT: "Contract/Temp",
    TEMPORARY: "Contract/Temp", INTERN: "Internship", INTERNSHIP: "Internship",
  };

  function workType(v) {
    const s = str(first(v));
    if (!s) return null;
    const key = s.toUpperCase().replace(/[\s-]+/g, "_");
    if (key === "OTHER") return null;
    return WORK_TYPES[key] || s.slice(0, 40);
  }

  function description(p) {
    // SmartRecruiters splits one ad over four itemprops.
    return ["description", "responsibilities", "qualifications", "incentives"]
      .map((k) => htmlToText(first(p[k]) || ""))
      .filter(Boolean).join("\n\n") || null;
  }

  function pick(postings, loc) {
    if (postings.length <= 1) return postings[0] || null;
    // A page listing several (a search page) — the one whose url is this page.
    const here = (loc.pathname || "").replace(/\/$/, "");
    return postings.find((p) => {
      try { return new URL(String(first(p.url) || ""), loc.href).pathname.replace(/\/$/, "") === here; }
      catch (e) { return false; }
    }) || postings[0];
  }

  function meta(doc, sel) {
    const el = doc.querySelector(sel);
    return el ? str(el.getAttribute("content")) : null;
  }

  function canonicalUrl(doc, loc) {
    const el = doc.querySelector('link[rel="canonical"]');
    const href = el && el.getAttribute("href");
    if (!href) return null;
    try {
      const u = new URL(href, loc.href);
      // Only this site's own canonical: some point at a different host (an
      // aggregator's copy), and a url must describe the page actually read.
      return u.hostname.replace(/^www\./, "") === (loc.hostname || "").replace(/^www\./, "")
        ? u.origin + u.pathname + u.search : null;
    } catch (e) { return null; }
  }

  function read(doc, loc) {
    const ld = jsonLdPostings(doc);
    const md = ld.length ? [] : microdataPostings(doc);
    const p = pick(ld.length ? ld : md, loc);
    const id = idFrom(loc.href);
    const job = {
      platform_job_id: id ? id.platform_job_id : null,
      url: canonicalUrl(doc, loc) || (id ? id.url : loc.href),
      company: null, title: null, jd_text: null, location: null,
      posted_label: null, salary_raw: null, work_type: null,
      ats: vendorOf(doc, loc),
      _prov: { title_source: null, doc_source: "top", layout: null },
    };
    job._prov.layout = job.ats || "generic";
    if (p) {
      job.title = str(first(p.title)) || str(first(p.name));
      job.company = orgName(p.hiringOrganization);
      job.jd_text = description(p);
      job.location = place(p.jobLocation) ||
        ([].concat(p.jobLocationType || []).includes("TELECOMMUTE") ? "Remote" : null);
      job.posted_label = dateLabel(p.datePosted);
      job.salary_raw = salaryLine(p.baseSalary);
      job.work_type = workType(p.employmentType);
      job._prov.title_source = ld.length ? "jsonld" : "microdata";
    }
    // Fields filled by a fallback are WEAK, and capture.js lets a keyed stash
    // of this same job replace them. Gaps-only merging assumes the page in
    // front of the user reads best, which is false exactly here: an apply
    // page that dropped the JobPosting (Lever, Workable, Personio — measured
    // 24 Sep 2026) yields its tab title, "Contoso - Senior Engineer", while
    // the listing it came from published the clean title a minute earlier.
    job._prov.weak = [];
    if (!job.title) {
      // <h1> first: on a job page it is the job's title, where og:title and
      // the tab title tend to wrap it ("Job Application for X at Y").
      const h1 = doc.querySelector("h1");
      const h1t = h1 ? str(h1.textContent) : null;
      const og = meta(doc, 'meta[property="og:title"]');
      job.title = h1t || og || str(doc.title);
      job._prov.title_source = h1t ? "h1" : og ? "og" : job.title ? "doctitle" : null;
      if (job.title) job._prov.weak.push("title");
    }
    if (!job.company) {
      job.company = meta(doc, 'meta[property="og:site_name"]');
      if (job.company) job._prov.weak.push("company");
    }
    if (!job.title && !job.jd_text) return null;
    return job;
  }

  /* Are two titles the same job's? For linking a submit on an ATS to the
   * external apply that opened it, where the ATS and the job board word one
   * role differently: "Software Engineer (Real-time Collaborative Platform –
   * Full Stack)" on the form, "Software Engineer" on the board. So one title's
   * words CONTAINED in the other's counts — but only from two words up, since
   * a bare "Engineer" is inside every title — and otherwise the word sets must
   * overlap well past half: "Senior AI Engineer" against "Agentic AI Engineer"
   * shares exactly half and is two different roles
   * (.claude/rules/matching.md). */
  const words = (s) => new Set((str(s) || "").normalize("NFKC").toLowerCase()
    .split(/[^\p{L}\p{N}]+/u).filter(Boolean));

  function sameJob(a, b) {
    const A = words(a), B = words(b);
    if (!A.size || !B.size) return false;
    const shared = [...A].filter((w) => B.has(w)).length;
    const small = Math.min(A.size, B.size);
    if (small >= 2 && shared === small) return true;
    return shared / (A.size + B.size - shared) >= 0.6;
  }

  /* Which remembered job a submit belongs to, from one tab's short list
   * (background.js:takeExternal asks this of the opener tab's list, then of
   * the submitting tab's own). With the submit's title: the ONE entry whose
   * title is the same job's, or nothing. Without a title: the only entry, or
   * nothing. Anything ambiguous links nothing, and the submit files its own
   * record — a duplicate is visible and mergeable, a submit filed onto the
   * wrong job is neither (invariant #3). */
  function pickListed(entries, title) {
    if (!entries || !entries.length) return null;
    if (title) {
      const hits = entries.filter((e) => e && e.job && sameJob(e.job.title, title));
      return hits.length === 1 ? { entry: hits[0], byTitle: true } : null;
    }
    return entries.length === 1 ? { entry: entries[0], byTitle: false } : null;
  }

  /* The site an "Always capture on this site" click enables: one host, both
   * schemes — `*://careers.contoso.com/*` — and nothing wider. Only web pages
   * qualify; chrome:// and file:// cannot be granted. */
  function siteOf(href) {
    let u;
    try { u = new URL(href); } catch (e) { return null; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    const host = u.hostname.toLowerCase();
    return host ? { host, pattern: `*://${host}/*` } : null;
  }

  // `self` in the service worker, which imports this file for sameJob so the
  // rule exists once; `window` in a page, where the two are the same object.
  (typeof window !== "undefined" ? window : self).__trackerJobPosting =
    { read, idFrom, atsOfUrl, vendorOf, htmlToText, sameJob, pickListed, siteOf };
})();
