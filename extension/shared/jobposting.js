/* A job read off any page that publishes one — employer career sites and
 * applicant tracking systems (docs/career-sites.md §5).
 *
 * Loaded BEFORE an adapter and shared/capture.js. Defines
 * window.__trackerJobPosting and touches nothing else at load, so
 * tests/test_extension.js can run it against a fake document:
 *
 *   read(doc, loc)   the getJob() shape, or null when the page names no job
 *   idFrom(href)     {platform_job_id, url, by} | null — the posting's identity
 *   pageId(doc, loc, hints)  the same, from the page when its address lost the id
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
    // SAP's newer SuccessFactors data centres serve the same career pages
    // from sapsf.com / sapsf.eu (career44.sapsf.com, 3 Oct 2026: the classic
    // form at /portalcareer?_s.crb=…, which no script reached).
    ["sapsf.com", "successfactors"], ["sapsf.eu", "successfactors"],
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
    // Each employer is its own tenant, <tenant>.darwinbox.com, which serves
    // its HR system too; the candidate portal is /ms/candidate… (8 Oct 2026).
    ["darwinbox.com", "darwinbox"],
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
  //     opaque 8-40 character code of letters AND digits (Workable, JazzHR).
  //     "Last" within the JOB's part of the path: an apply flow is the job's
  //     address plus `/apply` or `/application` plus the flow's own state, and
  //     that state can look like an id. Oracle's form is `…/job/2087/apply/
  //     section/1`, which read as job 1, so its answers keyed per section and
  //     the handoff bound to 2087 was refused at the submit (28 Sep 2026). The
  //     tail is searched only when the part before it has no id, which is
  //     where JazzHR keeps its own (`/apply/<id>/<slug>`);
  //  3. otherwise a parameter whose NAME says it is the job's id (JOB_PARAM,
  //     below), and failing that the whole path PLUS the non-tracking query. The query is not
  //     decoration there: a generic page carrying its job in a parameter this
  //     list does not know (an embed's ?for=…&token=…) would otherwise give
  //     every job on it ONE id, and a capture would silently update another
  //     job's posting. A tracking parameter at worst makes a duplicate, which
  //     is visible and mergeable — the failure invariant #3 prefers.
  //     On an apply flow's address, a parameter naming the wizard's STEP is
  //     left out, for rule 2's reason: it is the flow's state. Phenom's own
  //     apply (2 Oct 2026) is `…/apply?jobSeqNo=<job>&step=N&stepname=<s>`,
  //     so every "Next" changed this id, which is also the answers store's
  //     key, and answers.js emptied the store at each step as if the job had
  //     changed: of a six-step form, the capture kept the last step's one
  //     answer. Only the step is dropped, never the rest of the query, so a
  //     job in an unknown parameter still keys apart; a step parameter by
  //     another name only keeps today's per-step key.
  // Lower-cased throughout, so a URL's case variants (Workday changes it
  // between its own links) keep one identity.
  const ID_PARAMS = ["gh_jid", "jobid", "job_id", "job", "jid", "pid", "reqid",
                     "req_id", "requisitionid", "career_job_req_id", "jk", "id"];
  // A parameter whose NAME says it is the job's identifier, past the list
  // above: a job word, then optionally what kind of number, then an id word
  // — Phenom's `jobSeqNo`, MyGreenhouse's `job_post_id`, a `requisitionNumber`.
  // Phenom's apply (3 Oct 2026) is `…/apply?jobSeqNo=<job>&source=…`, so until
  // then it fell to rule 3 and the form's id was the whole query, which
  // pageId reads as "no id": the handoff could never check the submit against
  // the job (handoffFits). The job page, `/job/<same token>/<slug>`, already
  // gave that token by rule 2, so the two ids now agree. Anchored at both
  // ends: `jobApplicationId` (an application's id) and `filter_reqid` (a
  // search's) do not match, nor `jobTitle`, `jobFamilyGroup`, `jobSource`.
  // The value must hold a digit and no spaces. Replayed before it was
  // written: of 19 stored addresses it changes 1 (that one), and of 662
  // visited job-site addresses 28, all the job's own id.
  const JOB_PARAM = /^(job|req|requisition|posting|vacancy|opening)[_-]?(seq|post|posting|req)?[_-]?(id|no|num|number|code|ref)$/;
  const JOB_PARAM_VALUE = /^(?=[a-z0-9_-]*\d)[a-z0-9_-]{4,64}$/i;
  const SEGMENT_IDS = [
    /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/,
    /-([0-9a-f]{32})$/,
    // Workday's `…_<requisition>`: short letter groups, then digits
    // (R120291, JR012345, PT-JR012345, R-07654321, REQ-2024-001). Each group
    // capped at five letters, so a slug word (`_engineer-2024`) is not one.
    // Until 2 Oct 2026 one group only: `_PT-JR012345` gave a fallback id.
    /_((?:[a-z]{1,5}-)*[a-z]{0,5}-?\d{3,}(?:-\d+)*)$/,
    /^(\d{4,})-/,
    /^(\d+)$/,
    /^((?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{8,40})$/,
  ];

  // The path segment where a job's address turns into its apply flow
  // (Lever's …/apply, Ashby's …/application, Workday's …/apply/…). ONE list:
  // idFrom stops looking for the id there, and generic.js's apply-flow test
  // is built from it.
  const APPLY_SEGMENTS = ["apply", "application"];
  // A query parameter that says which step of an apply flow the page is
  // (rule 3): `step`, `stepname`, `currentStep`, …
  const FLOW_STEP_PARAM = /step/;

  function decodeSegment(s) {
    try { return decodeURIComponent(s); } catch (e) { return s; }
  }

  // A hiring system that serves many employers from ONE host names the
  // employer in a parameter, and each employer numbers its requisitions on
  // its own: career2.successfactors.eu serves both Litware Bank and Relecloud,
  // whose "51234"s are different jobs. So the tenant goes into the token,
  // `<host>/<tenant>/<id>` (docs/career-sites.md §16.5, P2). Vendors whose
  // tenant is in the host (Workday's contoso.wd3…) need nothing.
  const TENANT_PARAM = { successfactors: "company" };
  const TENANT_SHAPE = /^[a-z0-9_-]{1,40}$/i;

  // The tenant an address names, for a vendor that names it in a parameter.
  function tenantOf(href) {
    let u;
    try { u = new URL(href); } catch (e) { return null; }
    const name = TENANT_PARAM[vendorOfHost(u.hostname.toLowerCase().replace(/^www\./, ""))];
    if (!name) return null;
    const hit = [...u.searchParams].find(([k, v]) => k.toLowerCase() === name && TENANT_SHAPE.test(v));
    return hit ? hit[1].toLowerCase() : null;
  }

  function idFrom(href) {
    let u;
    try { u = new URL(href); } catch (e) { return null; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    const host = u.hostname.toLowerCase().replace(/^www\./, "");
    const params = [...u.searchParams].map(([k, v]) => [k.toLowerCase(), v]);
    // `by` says which rule gave the token — "param", "segment", or "path"
    // for the fallback, which pageId() reads as "this address has no id".
    let token = null, by = null;
    for (const name of ID_PARAMS) {
      const hit = params.find(([k, v]) => k === name && v && v.length <= 64);
      if (hit) { token = hit[1].toLowerCase(); by = "param"; break; }
    }
    const segs = u.pathname.split("/").map(decodeSegment)
      .map((s) => s.toLowerCase()).filter(Boolean);
    // The job's part of the path, newest segment first, then the apply
    // flow's tail (rule 2 above).
    const cut = segs.findIndex((s) => APPLY_SEGMENTS.includes(s));
    const head = cut < 0 ? segs : segs.slice(0, cut);
    const tail = cut < 0 ? [] : segs.slice(cut + 1);
    // Copied before reversing: `head` may BE `segs`, which the fallback joins.
    for (const seg of [...head].reverse().concat([...tail].reverse())) {
      if (token) break;
      for (const rx of SEGMENT_IDS) {
        const m = rx.exec(seg);
        if (m) { token = m[1]; by = "segment"; break; }
      }
    }
    // Rule 3's first resort: a parameter NAMED for the job, holding an
    // id-shaped value (JOB_PARAM). Only where rules 1-2 found nothing, so it
    // changes no id they give.
    if (!token) {
      const hit = params.find(([k, v]) => JOB_PARAM.test(k) && JOB_PARAM_VALUE.test(v));
      if (hit) { token = hit[1].toLowerCase(); by = "param"; }
    }
    if (!token) {
      if (!segs.length) return null;          // a bare site is not a job
      const query = params.filter(([k]) => !k.startsWith("utm_") &&
                                           !(cut >= 0 && FLOW_STEP_PARAM.test(k)))
        .map(([k, v]) => [k, v.toLowerCase()])
        .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0))
        .map(([k, v]) => `${k}=${v}`).join("&");
      token = segs.join("/") + (query ? `?${query}` : "");
      by = "path";
    }
    const tenant = by !== "path" ? tenantOf(href) : null;
    if (tenant) token = `${tenant}/${token}`;
    return {
      platform_job_id: `${host}/${token}`.slice(0, 300),
      url: u.origin + u.pathname + u.search,
      by,
    };
  }

  /* The posting's identity on the page in front of the user: idFrom() of its
   * address, unless that address has lost the job's id and the page prints
   * it. SuccessFactors' form, after any postback, sits at
   * /portalcareer?_s.crb=<a session crumb>: idFrom falls back to path plus
   * query, an id that changes at every sign-in (28 Sep 2026: the session
   * timed out mid-form), while the page's <h1> and tab title end in the
   * requisition, "(51234)" — on three tenants of three
   * (docs/career-sites.md §16). The same number is what the form's first
   * address carries (career_job_req_id), so the two ids agree.
   * Only on an ATS's own host and only when the address gave no id, so a
   * title's "(2027)" anywhere else is left alone. The stored address drops
   * the query: a crumb is session state, not where the job is.
   * The page rule has no Python mirror — manual entry has only a URL.
   * `hints.tenant`: the employer an EARLIER page of this tab's visit named
   * (generic.js keeps it in the hiring system's own sessionStorage), since
   * the postback address has lost the tenant too. Without it the id is
   * `<host>/<id>`, which still keys the form but cannot tell two employers
   * on one host apart.
   * The page may also STATE the requisition in a field named for one, and
   * that is read first: SuccessFactors' candidate experience (29 Sep 2026)
   * prints no "(51234)" anywhere, its title being "Career Opportunities: <job>
   * (Singapore)", but carries <meta name="jobRequisitionId"> and a hidden
   * <input name="career_job_req_id">. By NAME only, and only names that say
   * requisition: ID_PARAMS' bare "id" or "job" names too many unrelated
   * hidden fields to trust on a page. Two such fields that disagree give
   * nothing. */
  const PRINTED_REQ = /\((\d{3,9})\)\s*$/;
  const REQ_FIELD = /^(?:career_)?(?:job_?)?req(?:uisition)?_?id$/i;
  const REQ_VALUE = /^(?=[^\d]*\d)[a-z0-9_-]{3,40}$/i;

  function namedReq(doc) {
    const seen = new Set();
    for (const el of doc.querySelectorAll("meta[name], input[name]")) {
      if (!REQ_FIELD.test(el.getAttribute("name") || "")) continue;
      const v = String((el.tagName === "META" ? el.getAttribute("content") : el.value) || "").trim();
      if (REQ_VALUE.test(v)) seen.add(v.toLowerCase());
    }
    return seen.size === 1 ? [...seen][0] : null;
  }

  function pageId(doc, loc, hints) {
    const id = idFrom(loc.href);
    if (!id || id.by !== "path" || !atsOfUrl(loc.href)) return id;
    const h1 = doc.querySelector("h1");
    const printed = [h1 && h1.textContent, doc.title]
      .map((t) => PRINTED_REQ.exec(str(t) || "")).find(Boolean);
    const req = namedReq(doc) || (printed && printed[1]);
    if (!req) return id;
    const u = new URL(loc.href);
    const hinted = hints && hints.tenant && TENANT_SHAPE.test(hints.tenant) ? hints.tenant.toLowerCase() : null;
    const tenant = tenantOf(loc.href) || hinted;
    return {
      platform_job_id: `${u.hostname.toLowerCase().replace(/^www\./, "")}/` +
                       (tenant ? `${tenant}/` : "") + req,
      url: u.origin + u.pathname,
      by: "page",
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
      // A canonical that downgrades the page it sits on is kept at the page's
      // own https: Greenhouse's job boards print `http://` (29 Sep 2026).
      if (u.protocol === "http:" && /^https:/i.test(loc.href || "")) u.protocol = "https:";
      // Only this site's own canonical: some point at a different host (an
      // aggregator's copy), and a url must describe the page actually read.
      return u.hostname.replace(/^www\./, "") === (loc.hostname || "").replace(/^www\./, "")
        ? u.origin + u.pathname + u.search : null;
    } catch (e) { return null; }
  }

  function read(doc, loc, hints) {
    const ld = jsonLdPostings(doc);
    const md = ld.length ? [] : microdataPostings(doc);
    const p = pick(ld.length ? ld : md, loc);
    const id = pageId(doc, loc, hints);
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
    // "This page publishes a JobPosting": what makes it a LISTING, whatever
    // its title was read from. A Career Site Builder page (28 Sep 2026) holds
    // only the description inside its JobPosting and the title outside it, so
    // title_source says "og" on a page that is every bit a listing.
    job._prov.structured = !!p;
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
    // A hiring system's page with no JobPosting still lays the job out: its
    // own description and location, where the vendor table knows where.
    const dom = listingDom(doc, job.ats);
    if (!job.jd_text && dom.jd_text) job.jd_text = dom.jd_text;
    if (!job.location && dom.location) job.location = dom.location;
    if (!job.company) {
      job.company = meta(doc, 'meta[property="og:site_name"]');
      if (job.company) job._prov.weak.push("company");
    }
    if (!job.company) {
      job.company = titleEmployer(doc.title, job.title);
      if (job.company) job._prov.weak.push("company");
    }
    // Last, on an employer's OWN site only: the tab title's "… | <site>" is
    // the site's owner, and there that is the employer ("Principal AI Engineer
    // Job Details | Litware Bank", a listing with no hiringOrganization and no
    // og:site_name, 28 Sep 2026). On a hiring system's host the owner is the
    // vendor or an arbitrary tenant brand, so it is never read there.
    if (!job.company && !atsOfUrl(loc.href)) {
      job.company = siteOwner(doc.title, job.title);
      // ...unless it is the page's own hiring-system tenant, a CODE: a
      // Career Site Builder listing titled "… Job Details | <TENANTPRD>"
      // whose inline config hands over to that same tenant (8 Oct 2026, a
      // listing with no hiringOrganization and no og:site_name). The receipt
      // then asks for the employer instead of a code being filed as one.
      const hand = job.company ? atsHandoff(doc) : null;
      if (hand && hand.tenant && hand.tenant.toLowerCase() === job.company.toLowerCase()) job.company = null;
      if (job.company) job._prov.weak.push("company");
    }
    const bare = stripRequisition(job.title, id);
    if (bare !== job.title) { job._prov.title_stripped = job.title; job.title = bare; }
    if (!job.title && !job.jd_text) return null;
    return job;
  }

  /* An ATS form's title with its own requisition number appended —
   * SuccessFactors' <h1> reads "AVP, Software Engineer (1234)" (measured live
   * 24 Sep 2026) — loses the suffix, but ONLY when that number is the page's
   * own id: in its address (`career_job_req_id=1234`, i.e. idFrom's token),
   * or, where the address lost it, the one pageId() took from the page.
   * Stripping every trailing parenthesis would merge "Engineer (Backend)" with
   * "Engineer (Web)"; a number the page itself proves is its id is no part of
   * the job's name. The suffix also defeats exact-title email matching
   * (.claude/rules/matching.md). */
  function stripRequisition(title, id) {
    if (!title || !id || id.by === "path") return title;
    // The id's LAST segment: a SuccessFactors id carries its tenant before it
    // (`<host>/<tenant>/<id>`).
    const token = id.platform_job_id.slice(id.platform_job_id.lastIndexOf("/") + 1);
    if (!token) return title;
    const esc = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const m = new RegExp(`^(.*?)\\s*[([]\\s*(?:req\\w*\\s*)?#?\\s*${esc}\\s*[)\\]]\\s*$`, "i").exec(title);
    return m && m[1].trim() ? m[1].trim() : title;
  }

  /* The owner of a site, from its tab title's last "|" segment, as a weak
   * company (read() above uses it on employers' own sites only). Not the job's
   * own title, not a word that names the page rather than the owner, and a
   * trailing "Careers"/"Jobs" is the site's name, not the employer's. */
  const NOT_OWNER = /^(careers?|jobs?|job details|job search|search jobs|home|apply|opportunities)$/i;

  /* The employer a tab title names right AFTER the job's own title: "Job
   * Application for <title> at <company>", every page of Greenhouse's job
   * boards (read live 29 Sep 2026), which publish no JobPosting and no
   * og:site_name, so this was the one place such a page named its employer
   * as text. Anchored on the job's title, so an " at " inside a title
   * ("Engineer at Scale") is never taken for one, and read on any site: it
   * names the employer outright, where siteOwner() below guesses from a
   * site's name and is kept off hiring systems. Weak, like siteOwner. */
  function titleEmployer(tabTitle, jobTitle) {
    const t = str(tabTitle), j = str(jobTitle);
    if (!t || !j) return null;
    const i = t.toLowerCase().indexOf(j.toLowerCase());
    if (i < 0) return null;
    const m = /^\s+at\s+(.+)$/i.exec(t.slice(i + j.length));
    const who = m ? str(m[1].split(/\s+[|–—-]\s+/)[0]) : null;
    if (!who || who.length > 60 || !/\p{L}/u.test(who) || NOT_OWNER.test(who)) return null;
    return who;
  }

  /* Where a hiring system lays out a job on its OWN page, for the pages that
   * publish no JobPosting: the description and the location, by selector,
   * per vendor, in the one vendor table's spirit (SUBMIT_HOOKS in generic.js
   * is the same kind of entry). Measured, not guessed: Greenhouse's
   * job-boards page, 29 Sep 2026 (`.job__description`, 4.5k chars;
   * `.job__location`). A selector that finds nothing reads nothing. */
  const LISTING_DOM = {
    greenhouse: { jd_text: ".job__description", location: ".job__location" },
  };

  function listingDom(doc, vendor) {
    const sel = LISTING_DOM[vendor];
    if (!sel) return {};
    const grab = (s, asText) => {
      const el = doc.querySelector(s);
      if (!el) return null;
      // innerHTML keeps the paragraphs htmlToText turns into lines; a fake
      // DOM in the tests has only textContent.
      const raw = el.innerHTML != null ? el.innerHTML : el.textContent;
      return asText ? (htmlToText(raw || "") || null) : str(raw);
    };
    return { jd_text: grab(sel.jd_text, true), location: grab(sel.location, false) };
  }

  function siteOwner(tabTitle, jobTitle) {
    const parts = (tabTitle || "").split(/\s+\|\s+/);
    if (parts.length < 2) return null;
    const owner = (str(parts[parts.length - 1]) || "")
      .replace(/\s+(careers?|jobs?|recruitment|talent)$/i, "").trim();
    if (!owner || owner.length > 60 || !/\p{L}/u.test(owner) || NOT_OWNER.test(owner)) return null;
    if (jobTitle && owner.toLowerCase() === jobTitle.toLowerCase()) return null;
    return owner;
  }

  /* Where a Career Site Builder listing hands its applicant over: the
   * SuccessFactors data centre and the tenant, from the site's own inline
   * configuration (`"ssoUrl" : 'https://career2.successfactors.eu'`,
   * `"ssoCompanyId" : 'litwarebk'`; read on the 28 Sep 2026 page). An
   * isolated-world content script cannot read the page's globals but can read
   * its inline script TEXT. The listing's own job number is deliberately not
   * taken: on one site of two it is not the requisition (§16.2). Null when
   * the page says nothing of the kind. */
  function atsHandoff(doc) {
    let atsHost = null, tenant = null;
    for (const s of doc.querySelectorAll("script")) {
      if (s.getAttribute("src")) continue;
      const t = s.textContent || "";
      if (!atsHost) {
        const m = /["']?ssoUrl["']?\s*[:=]\s*["']https?:\/\/([a-z0-9.-]+)/i.exec(t);
        if (m && vendorOfHost(m[1].toLowerCase())) atsHost = m[1].toLowerCase();
      }
      if (!tenant) {
        const m = /["']?(?:ssoCompanyId|companyId)["']?\s*[:=]\s*["']([a-z0-9_-]{1,40})["']/i.exec(t);
        if (m) tenant = m[1].toLowerCase();
      }
      if (atsHost && tenant) break;
    }
    return atsHost || tenant ? { atsHost, tenant } : null;
  }

  /* The ids a listing BELIEVES its hiring system holds for its job (P4): its
   * handover's data centre and tenant (atsHandoff) and its own job number,
   * `career2.successfactors.eu/litwarebk/51234`. On one Career Site Builder
   * site that number is the requisition, on another it is not (§16.2), so
   * this is a CANDIDATE: the server only looks for a job holding it and never
   * stores it (ingest.upsert_record), and a wrong guess matches nothing.
   * [] without both halves of the handover or an id-shaped number. */
  function atsCandidates(job, handoff) {
    if (!job || !job.platform_job_id || !handoff || !handoff.atsHost || !handoff.tenant) return [];
    const token = job.platform_job_id.slice(job.platform_job_id.lastIndexOf("/") + 1);
    if (!/^[a-z0-9_-]{1,40}$/i.test(token) || !/\d/.test(token)) return [];
    return [`${handoff.atsHost}/${handoff.tenant}/${token.toLowerCase()}`];
  }

  /* Does this page publish a JobPosting? A page that does is a LISTING, and
   * one of its own "Apply" controls leaves for the application rather than
   * sending it (adapters/generic.js). */
  const hasPosting = (doc) => jsonLdPostings(doc).length > 0 || microdataPostings(doc).length > 0;

  /* A hiring system whose JOB page can send the application itself, with no
   * form: SuccessFactors' quick apply, for a signed-in candidate with a
   * complete profile. The job page's own "Apply" sent Relecloud's application
   * on 25 Sep 2026, and the tab landed three seconds later on
   * `/portalcareer?…&isRedirectToAppSent=true&…`, the confirmation email
   * arriving the same minute (seen once). `quickApplies` says the vendor can;
   * `quickApplySent` says an address is that landing. generic.js ties the two
   * together with a note left by the click, so the landing alone never files
   * anything (docs/career-sites.md §16.3 item 6). */
  const QUICK_APPLY_SENT = { successfactors: /[?&]isRedirectToAppSent=true(?:&|$)/i };

  function quickApplyRule(href) {
    try { return QUICK_APPLY_SENT[vendorOfHost(new URL(href).hostname.toLowerCase())] || null; }
    catch (e) { return null; }
  }
  const quickApplies = (href) => !!quickApplyRule(href);

  function quickApplySent(href) {
    const rx = quickApplyRule(href);
    try { return !!rx && rx.test(new URL(href).search); } catch (e) { return false; }
  }

  /* A company to SUGGEST when a capture found none — shown on the receipt for
   * the user to confirm or correct, never stored on its own say-so. Two
   * sources, in order:
   *  1. the page address's own `company` parameter — how SuccessFactors names
   *     its tenant (`?company=Contoso`, measured); dropped when it is not a name
   *     (digits, or longer than a name);
   *  2. the site the user came FROM, when it is a different site: an
   *     employer's careers.<brand>.com hands over to its hiring system, so the
   *     referrer's host minus the careers/jobs/www labels names the brand.
   * The first survives only until a postback (SuccessFactors then drops it),
   * the second only when no sign-in page came between — which is why this is
   * a suggestion, and why phase C's listing stash is the real fix. */
  // Where a referrer names the SOURCE of a click, not the employer: the job
  // boards this extension serves, and search.
  const NOT_EMPLOYERS = ["linkedin.com", "jobstreet.com", "jobstreet.com.sg", "jobstreet.com.my",
                         "jobstreet.co.id", "seek.com.au", "indeed.com", "google.com", "bing.com"];

  // Returns { name, site }: `name` the suggestion (or null), `site` the
  // employer's own site the user came from (or null) — the one to turn on in
  // the popup so the next application there needs no question at all.
  function suggestCompany(loc, referrer) {
    let site = null;
    try {
      const from = new URL(referrer).hostname.toLowerCase();
      const here = (loc.hostname || "").toLowerCase();
      // A vendor's or a job board's host names them, not the employer.
      if (from && from !== here && !vendorOfHost(from) &&
          !NOT_EMPLOYERS.some((s) => from === s || from.endsWith("." + s))) site = from;
    } catch (e) { /* no referrer */ }
    let name = null;
    try {
      const tenant = (new URL(loc.href).searchParams.get("company") || "").trim();
      // A name, not a tenant CODE ("C0001234567P"): starts with a letter,
      // no run of four digits.
      if (tenant && /^[\p{L}][\p{L}\p{N} &.'-]{0,39}$/u.test(tenant) && !/\d{4}/.test(tenant)) {
        name = tenant;
      }
    } catch (e) { /* no usable address */ }
    if (!name && site) {
      const labels = site.split(".").filter((l) => !/^(www\d*|careers?|jobs?|apply|recruit\w*|talent)$/.test(l));
      name = labels[0] || null;
    }
    return { name, site };
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

  /* THE HANDOFF (docs/career-sites.md §16.3 item 2). When a hiring system's
   * page loads in a tab, which listing did the applicant just leave? The
   * last one the tab showed on ANOTHER site: its Apply is what navigated
   * here. Or, in a tab another opened (a job board's "Apply on company
   * website"), the opener's last one, which that click stashed moments ago.
   * Decided here, seconds after the click, rather than at the submit, which
   * comes after a sign-in, an account and (28 Sep 2026) a session timeout.
   *
   * `page` is {host, vendor, tenant, site}. Only the MOST RECENT entry of each list
   * is considered: an older one is a guess about which job was meant. It must
   * not contradict the page: a listing that names where it hands over
   * (atsHandoff: data centre, tenant) must name THIS host and tenant, and a
   * known vendor must be this page's. The opener's entry must be fresh, since
   * the click that opened this tab stashed it; a same-tab listing may have
   * been read for a while. Returns {entry, via} or null. */
  const OPENER_WINDOW_MS = 15 * 60 * 1000;

  // `openerKept`: the opener came from keepOpener's record, not from Chrome,
  // so the opener's entry must also have DEPARTED to this host (departsTo).
  function pickDeparture(openerEntries, ownEntries, page, now, openerKept) {
    const fits = (e) => {
      if (!e || !e.job) return false;
      const h = e.job.handoff || {};
      if (h.atsHost && h.atsHost !== page.host) return false;
      if (h.tenant && page.tenant && h.tenant !== page.tenant) return false;
      if (e.job.ats && page.vendor && e.job.ats !== page.vendor) return false;
      return true;
    };
    const opener = (openerEntries || [])[0];
    if (opener && now - opener.at <= OPENER_WINDOW_MS && fits(opener) &&
        (!openerKept || departsTo(opener, page.host))) {
      return { entry: opener, via: "opener" };
    }
    // A site the user enabled (an employer's own domain; Eightfold's
    // candidate site runs on one, 2 Oct 2026) binds from the opener only.
    // A same-tab listing elsewhere is evidence about a hiring system's own
    // host, where its vendor can disagree; here none need be known.
    if (page.site) return null;
    // Same-tab: never a listing on this very host (a hiring system's own job
    // page before its own form, which the keyed stash already links).
    const own = (ownEntries || []).find((e) => e && e.job && hostOf(e.job.url) !== page.host);
    if (own && fits(own)) return { entry: own, via: "tab" };
    return null;
  }

  /* THE OPENER, KEPT (3 Oct 2026). Every link from a job board's Apply to the
   * employer's tab rests on `sender.tab.openerTabId`, and Chrome forgets it
   * far more readily than the design assumed. Chromium's TabStripModel
   * (read in its source that day) forgets EVERY tab's opener in the window
   * when any tab navigates other than by a link (typed, bookmark, keyword),
   * when the user switches to a tab that is neither this one's opener nor
   * opened by it, and when another tab is opened from a link in the
   * foreground. A real apply on an employer's Phenom site lost it within 37 s: the
   * site was enabled only once its form was open, so the first claim came
   * late, found no opener, and the submit filed a second record beside
   * LinkedIn's. The 30 Sep SmartRecruiters apply had lost it by its submit.
   *
   * So the worker writes each tab's opener down when Chrome creates the tab
   * (tabs.onCreated carries openerTabId, no "tabs" permission needed) and
   * uses that once Chrome's own is gone. What Chrome's forgetting protects
   * against is real: after a TYPED address, the tab is a new task, and a
   * kept opener would file that task's application onto the job board's job.
   * So a kept opener is trusted only on the host its job's Apply LEFT FOR
   * (`dest`, the destination capture.js resolves at the click: LinkedIn's
   * safety/go unwrapped), or a subdomain of it. A redirector (grnh.se), or a
   * career site handing over to its ATS on another host, therefore links
   * nothing through a kept opener: a duplicate, as before, never a wrong
   * merge. Chrome's live opener, when it still stands, is used as it always
   * was, with no host check. */
  const KEPT_OPENERS_MAX = 200;

  // The record after `tab` was created: {[tabId]: {opener, at}}, capped to
  // the newest. Null when there is nothing to keep (a tab nobody opened).
  function keepOpener(kept, tab, now) {
    if (!tab || tab.id == null || tab.openerTabId == null) return null;
    const next = { ...(kept || {}), [tab.id]: { opener: tab.openerTabId, at: now } };
    const ids = Object.keys(next);
    if (ids.length > KEPT_OPENERS_MAX) {
      ids.sort((a, b) => next[a].at - next[b].at)
        .slice(0, ids.length - KEPT_OPENERS_MAX).forEach((k) => delete next[k]);
    }
    return next;
  }

  // The tab that opened `tab`: Chrome's, while it stands, else the kept one.
  // {id, kept}; id null when neither knows.
  function openerOf(kept, tab) {
    if (!tab || tab.id == null) return { id: null, kept: false };
    if (tab.openerTabId != null) return { id: tab.openerTabId, kept: false };
    const k = kept && kept[tab.id];
    return k && k.opener != null ? { id: k.opener, kept: true } : { id: null, kept: false };
  }

  // Did this stashed departure leave for `host`? Its destination's host, or a
  // subdomain of it. An entry with no destination (stashed before 0.26.0, or
  // by an Apply whose link could not be read) never qualifies.
  function departsTo(entry, host) {
    const d = entry && entry.dest;
    return !!d && !!host && (host === d || host.endsWith("." + d));
  }

  /* Does a submit belong to its tab's handoff? The same hiring system's host,
   * and, when both sides know it, the same job id on it: a tab that went on
   * to another job's form must not file that job onto the listing. An id the
   * binding LEARNED (learnsAlias, below) fits only when the submit's page
   * title is the bound job's too, since that id came from where the page was
   * reached from, not from the listing itself. A binding made on a site the
   * user enabled (`site`) whose id is not known on both sides fits only on
   * the title as well: its first page may be the job's own, with no form and
   * so no id, and a second job applied to in the same tab must not be filed
   * onto the first. */
  const knowsId = (b, id) => !!id && (b.atsJobId === id || (b.aliases || []).includes(id));

  function handoffFits(binding, page) {
    if (!binding || !binding.job || binding.host !== page.host) return false;
    const titled = !!page.title && sameJob(binding.job.title, page.title);
    if (!binding.atsJobId || !page.atsJobId) return !binding.site || titled;
    if (binding.atsJobId === page.atsJobId) return true;
    return (binding.aliases || []).includes(page.atsJobId) && titled;
  }

  /* One job, two ids on one host. SmartRecruiters' listing is
   * /<Co>/<number>-<slug> and its form /oneclick-ui/…/publication/<UUID>
   * (docs/career-sites.md §4.2), so the binding the listing made held the
   * number and the form's submit sent the UUID. handoffFits read that as
   * another job's form, and a real LinkedIn → SmartRecruiters apply was filed
   * twice (30 Sep 2026). The form page's referrer names the listing (measured
   * live: its full address, no referrer policy on the site), so a page on the
   * binding's host, reached from a page whose id the binding knows, that is
   * NOT itself a listing, is the same job under its second id. A listing is
   * excluded because a similar job's listing, reached the same way, is
   * another job. */
  function learnsAlias(b, page) {
    return !!(b && b.job && page && b.host === page.host && b.atsJobId && page.atsJobId &&
      !page.listing && !knowsId(b, page.atsJobId) && knowsId(b, page.fromId));
  }

  /* The tab's binding after one of its pages says where it is
   * (background.js:claimHandoff), given what pickDeparture chose: the new
   * binding, or null to leave the tab's as it is. Pure, so the cases that
   * would file one job onto another are tested, not argued. */
  const jobKeyOf = (j) => (j && (j.platform_job_id || j.url)) || null;

  function rebind(cur, pick, page, now) {
    if (pick) {
      const same = !!cur && jobKeyOf(cur.job) === jobKeyOf(pick.entry.job);
      const here = same && cur.host === page.host;
      // The same listing still leads this tab's list, but the hiring system
      // now shows ANOTHER job's id: the tab went on to a second job without a
      // new listing. Its binding stays the first job's, and handoffFits keeps
      // the second job's submit off it.
      if (here && cur.atsJobId && page.atsJobId && !knowsId(cur, page.atsJobId)) return null;
      // An id is kept only on its own host: a career site's listing number (a
      // site binding, 2 Oct 2026) is not the id its hiring system's form on
      // another host will send.
      return { at: now, job: pick.entry.job, via: pick.via, host: page.host,
               atsJobId: (here && cur.atsJobId) || page.atsJobId || null,
               ...(same && cur.aliases ? { aliases: cur.aliases } : {}),
               // Made on a site the user enabled, not a hiring system's own
               // host: handoffFits then asks for the title too.
               ...(page.site ? { site: true } : {}) };
    }
    // A later page of the visit shows the job's id.
    if (cur && cur.host === page.host && page.atsJobId && !cur.atsJobId) {
      return { ...cur, atsJobId: page.atsJobId };
    }
    return null;
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

  /* A Chrome match pattern ("*://*.successfactors.com/portalcareer*") as a
   * regular expression over a whole URL — one translation, used where the
   * extension must say "does this page get the capture scripts?" without the
   * browser to ask: the toolbar icon's rule (background.js, which hands it to
   * declarativeContent, i.e. RE2 — so only constructs RE2 and JavaScript read
   * alike) and the popup's covered-page check. Match-pattern semantics: `*` as
   * the scheme means http or https; `*.` before a host means the host or any
   * subdomain of it, never a mere suffix (`evil-linkedin.com` is not
   * linkedin.com); a pattern names no port, so any port matches; `*` in the
   * path is any run of characters. */
  function matchPatternRegex(pattern) {
    const m = /^(\*|https?):\/\/([^/]+)(\/.*)$/.exec(pattern || "");
    if (!m) return null;
    const esc = (s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
    const scheme = m[1] === "*" ? "https?" : m[1];
    let host;
    if (m[2] === "*") host = "[^/:]+";
    else if (m[2].startsWith("*.")) host = `([^/:]*\\.)?${esc(m[2].slice(2))}`;
    else host = esc(m[2]);
    const path = m[3].split("*").map(esc).join(".*");
    return `^${scheme}://${host}(:[0-9]+)?${path}`;
  }

  /* Does any of these match patterns reach `host`, on any path? The same
   * host semantics as matchPatternRegex. Asked by background.js:syncSites of
   * a site the user enabled: one the manifest already covers must not get a
   * second, registered copy of the capture scripts (Darwinbox, 8 Oct 2026,
   * enabled for one tenant the day before the vendor joined the manifest). */
  function hostCovered(patterns, host) {
    const want = String(host || "").toLowerCase();
    return !!want && (patterns || []).some((p) => {
      const m = /^(\*|https?):\/\/([^/]+)\//.exec(p || "");
      if (!m) return false;
      const h = m[2].toLowerCase();
      if (h === "*") return true;
      return h.startsWith("*.") ? want === h.slice(2) || want.endsWith(h.slice(1)) : want === h;
    });
  }

  // `self` in the service worker, which imports this file for sameJob so the
  // rule exists once; `window` in a page, where the two are the same object.
  (typeof window !== "undefined" ? window : self).__trackerJobPosting =
    { read, idFrom, pageId, tenantOf, atsHandoff, atsCandidates, hasPosting, siteOwner, pickDeparture,
      handoffFits, learnsAlias, rebind, knowsId, keepOpener, openerOf, departsTo, quickApplies, quickApplySent, atsOfUrl, vendorOf, htmlToText, sameJob,
      pickListed, siteOf, APPLY_SEGMENTS, VENDORS,
      matchPatternRegex, hostCovered, stripRequisition, suggestCompany };
})();
