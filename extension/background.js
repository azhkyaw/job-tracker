/* Service worker: owns the API call (host_permissions exempt it from CORS,
 * and it works against http://localhost where a content-script fetch from an
 * https page would be blocked as mixed content). Also keeps a small ring
 * buffer of capture failures the popup surfaces, and relays a subframe's
 * receipt to the tab's top frame. */
// The job reader's title rule (sameJob), so the link decision below and the
// tests share ONE definition of "the same job's title".
importScripts("shared/jobposting.js");

const DEFAULTS = { apiBase: "http://127.0.0.1:8000", token: "" };

async function settings() {
  return new Promise((res) => chrome.storage.sync.get(DEFAULTS, res));
}

/* Every local write goes through here so it can be AWAITED. An unawaited
 * chrome.storage.local.set is a promise nobody holds: the caller's .then()
 * fires before the data has landed, the message port closes, and an MV3
 * service worker with no open port is free to be terminated mid-write. That is
 * how a stashed job snapshot disappeared between one page and the next. */
async function setLocal(obj) {
  return new Promise((res) => chrome.storage.local.set(obj, res));
}

async function recordFailure(detail) {
  const { failures = [] } = await new Promise((res) =>
    chrome.storage.local.get({ failures: [] }, res));
  failures.unshift(detail);
  await setLocal({ failures: failures.slice(0, 10) });
}

/* Form-sweep diagnostics, one entry per capture. Separate buffer from
 * `failures` because these are not failures — a capture that saved fine can
 * still have quietly skipped half the form, and that is precisely the case
 * this exists to make visible. */
async function recordSweep(detail) {
  const { sweeps = [] } = await new Promise((res) =>
    chrome.storage.local.get({ sweeps: [] }, res));
  sweeps.unshift(detail);
  await setLocal({ sweeps: sweeps.slice(0, 10) });
}

/* Where a capture's title and company came from, one entry per capture.
 *
 * Third buffer, again separate: like `sweeps` these are not failures — the
 * capture saved — but unlike sweeps they answer "is the value RIGHT" rather
 * than "did we see the whole form". Kept deeper than the other two (25) for a
 * specific reason: the two bad titles this exists to catch were noticed over a
 * day after the fact, and the Lamna loss of 4 Aug 2026 was never
 * root-caused precisely because the ring buffers had rolled over before anyone
 * read them. chrome.storage.local survives a browser restart, so depth is the
 * only thing standing between a rare bug and another unexplained record. */
async function recordProvenance(detail) {
  const { provenance = [] } = await new Promise((res) =>
    chrome.storage.local.get({ provenance: [] }, res));
  provenance.unshift(detail);
  await setLocal({ provenance: provenance.slice(0, 25) });
}

/* A receipt whose page never got to show it.
 *
 * An apply control that is a real <a> — JobStreet's is — navigates the instant
 * it's clicked. The content script is torn down before the POST resolves, so
 * the `.then()` that renders the receipt never runs, while the record saves
 * perfectly well because THIS worker owns the fetch. The visible result is an
 * application in the tracker that was never offered a tailored/generic tag.
 * (Confirmed on a real JobStreet apply, 29 Jul 2026: the record existed with
 * no tag and no popover was ever seen.)
 *
 * So the receipt is held here, per tab, and the next content script to load in
 * that tab claims it. Nothing is lost if the claim never comes — a receipt is
 * optional by design; the application is what matters and it is already saved.
 */
const RECEIPT_TTL_MS = 5 * 60 * 1000;

async function _receipts() {
  const { receipts = {} } = await new Promise((res) =>
    chrome.storage.local.get({ receipts: {} }, res));
  return receipts;
}

async function stashReceipt(tabId, detail) {
  if (tabId == null) return;
  const receipts = await _receipts();
  receipts[tabId] = { at: Date.now(), detail };
  await setLocal({ receipts });
}

async function claimReceipt(tabId) {
  const receipts = await _receipts();
  const mine = tabId == null ? null : receipts[tabId];
  delete receipts[tabId];
  // Sweep everyone else's stale entries while we're in here. Tab ids get
  // reused, and a receipt surfacing an hour later on an unrelated page is
  // worse than no receipt at all.
  for (const [k, v] of Object.entries(receipts)) {
    if (Date.now() - v.at > RECEIPT_TTL_MS) delete receipts[k];
  }
  await setLocal({ receipts });
  return mine && Date.now() - mine.at <= RECEIPT_TTL_MS ? mine.detail : null;
}

async function dropReceipt(tabId) {
  if (tabId == null) return;
  const receipts = await _receipts();
  if (!(tabId in receipts)) return;
  delete receipts[tabId];
  await setLocal({ receipts });
}

/* The job, remembered from the page that could still see it.
 *
 * A platform whose apply flow is its own page (JobStreet: /job/<id> ->
 * /apply -> /apply/profile -> /apply/review) shows the company name on the
 * LISTING and never again. Verified on the real review page 29 Jul 2026:
 * getJob() there yields the id and the title from an <h1>, but company comes
 * back null and there is no data-automation hook to reach it by. So capturing
 * only at submit — which is what recording the true submit time requires —
 * would file "unknown company · AI Platform Engineer".
 *
 * The opening click therefore stashes the full snapshot here (it survives the
 * navigation that destroys the content script) and the submit merges it in.
 * Nothing is POSTed until the submit: an abandoned flow leaves a stash that
 * simply expires, never a record. */
const PENDING_JOB_TTL_MS = 2 * 60 * 60 * 1000;

/* The key can DEGRADE between the stash and the take, and then the exact-match
 * lookup finds nothing even though the right snapshot is sitting right there.
 *
 * Both sides call the adapter's answerFormKey(), which reads the job id out of
 * the TOP frame's URL. A frame that cannot reach its top — a detached one
 * returns itself for window.top, no error raised — falls through to its own
 * href instead, so the submit asks for a key the opening click never wrote.
 * A real Easy Apply (Proseware, 3 Aug 2026) landed exactly here: the submit
 * fired from a frame whose own URL was linkedin.com/preload/?_bprMode=vanilla,
 * getJob() came back null against that same unreachable top, and the record
 * saved with no company, no title and no job id — while the correct snapshot,
 * stashed minutes earlier under the real currentJobId, expired untouched.
 *
 * So a miss falls back to the most recent stash from the SAME TAB. That is a
 * guess where the keyed hit is a fact, and it gets its own much shorter window
 * to say so: long enough for a wizard someone is actually filling in, short
 * enough that a stash from a job browsed earlier in the same tab is not still
 * eligible to be pasted onto a different application. Note the fallback wins
 * outright in practice rather than merely filling gaps — the caller merges
 * gaps-only, but a frame this broken supplies no fields to lose to. */
const PENDING_JOB_FALLBACK_MS = 30 * 60 * 1000;

async function _pendingJobs() {
  const { pendingJobs = {} } = await new Promise((res) =>
    chrome.storage.local.get({ pendingJobs: {} }, res));
  return pendingJobs;
}

async function stashPendingJob(key, job, tabId) {
  if (!key) return;
  const jobs = await _pendingJobs();
  jobs[key] = { at: Date.now(), job, tabId: tabId == null ? null : tabId };
  for (const [k, v] of Object.entries(jobs)) {
    if (Date.now() - v.at > PENDING_JOB_TTL_MS) delete jobs[k];
  }
  await setLocal({ pendingJobs: jobs });
}

/* Returns { job, exact } — `exact` distinguishes a KEYED hit, which is a fact
 * about this job, from the same-tab fallback, which is a guess. The caller
 * ranks them differently and it matters: see capture.js:withStashedJob. */
async function takePendingJob(key, tabId) {
  if (!key) return { job: null, exact: false };
  const jobs = await _pendingJobs();
  // Expiry is enforced on READ as well as on write, because writes are exactly
  // what stops happening when the opening click can't read the job — and the
  // prune used to live only in stashPendingJob. Measured 18 Aug 2026: SEVEN
  // stashes from 11 Aug were still in storage, 6.6 days into a 2-hour TTL,
  // because nothing had been stashed since to trigger a prune. Only the
  // 30-minute fallback window stood between them and being pasted onto an
  // unrelated application.
  let pruned = false;
  for (const [k, v] of Object.entries(jobs)) {
    if (Date.now() - v.at > PENDING_JOB_TTL_MS) { delete jobs[k]; pruned = true; }
  }
  let hitKey = key in jobs ? key : null;
  const exact = hitKey !== null;
  if (hitKey === null && tabId != null) {
    // Newest eligible stash belonging to this tab. Tab ids are reused, which
    // is what the tighter window guards against as much as the wrong-job case.
    for (const [k, v] of Object.entries(jobs)) {
      if (v.tabId !== tabId) continue;
      if (Date.now() - v.at > PENDING_JOB_FALLBACK_MS) continue;
      if (hitKey === null || v.at > jobs[hitKey].at) hitKey = k;
    }
  }
  if (hitKey === null) {
    if (pruned) await setLocal({ pendingJobs: jobs });
    return { job: null, exact: false };
  }
  const rec = jobs[hitKey];
  delete jobs[hitKey];
  await setLocal({ pendingJobs: jobs });
  return { job: rec ? rec.job : null, exact };
}

/* The job an EXTERNAL apply left for, remembered under the tab that clicked
 * it — a job board's "Apply on company website" (docs/career-sites.md §8).
 *
 * That click opens the employer's site in a new tab; a submit there asks for
 * the job ITS tab was opened for, by `sender.tab.openerTabId` — the browser's
 * own record of which tab opened which, readable without the `tabs`
 * permission (it gates only url, pendingUrl, title and favIconUrl). So the
 * link is a fact about the tabs, not a guess about the job, which is why it
 * may wait as long as the keyed job stash does: a sign-in plus a new
 * candidate account can take most of an hour.
 *
 * One board tab can open several employer sites before any is submitted, so
 * the opener's entries are a short list and the submit names its own title:
 *  - a title that matches exactly one entry (jobposting.js sameJob) links it;
 *  - no title and exactly one entry links it (the tab relationship alone);
 *  - anything else links nothing, and the submit files its own record. A
 *    duplicate is visible and mergeable; a submit filed onto a DIFFERENT
 *    job's record is neither — the preference invariant #3 states. That is
 *    also the case of browsing on from the opened page to another job at the
 *    same employer: one entry, a title that disagrees, no link. */
const EXTERNAL_TTL_MS = 2 * 60 * 60 * 1000;
const EXTERNAL_PER_TAB = 5;

async function _externalJobs() {
  const { externalJobs = {} } = await new Promise((res) =>
    chrome.storage.local.get({ externalJobs: {} }, res));
  const now = Date.now();
  for (const [tab, list] of Object.entries(externalJobs)) {
    const fresh = list.filter((e) => now - e.at <= EXTERNAL_TTL_MS);
    if (fresh.length) externalJobs[tab] = fresh; else delete externalJobs[tab];
  }
  return externalJobs;
}

async function stashExternal(tabId, job) {
  if (tabId == null || !job) return;
  const all = await _externalJobs();
  const list = (all[tabId] || []).filter((e) =>
    !(job.platform_job_id && e.job.platform_job_id === job.platform_job_id));
  list.unshift({ at: Date.now(), job });
  all[tabId] = list.slice(0, EXTERNAL_PER_TAB);
  await setLocal({ externalJobs: all });
}

/* Returns { job, via, candidates }.
 *
 * Two lists are consulted, in order, each by jobposting.js:pickListed:
 *  1. the OPENER tab's — a job board's external apply (phase B). It wins: the
 *     record the board started is the one this application completes;
 *  2. the submitting tab's OWN — the listing the tab showed before it moved
 *     on to the hiring system (phase C). An employer's career site sends the
 *     candidate to its ATS in the same tab (a SuccessFactors site: listing on
 *     careers.<employer>, form on career{N}.successfactors.com), and only the
 *     listing names the company and carries the JD; the form shows neither.
 * `via` is "opener" / "tab", with "+title" when a title picked it;
 * `candidates` counts what was there, for the provenance line when nothing
 * was chosen. */
async function takeExternal(openerTabId, ownTabId, title) {
  const all = await _externalJobs();
  const J = self.__trackerJobPosting;
  const lists = [["opener", openerTabId], ["tab", ownTabId]]
    .filter(([, id]) => id != null && all[id] && all[id].length);
  const candidates = lists.reduce((n, [, id]) => n + all[id].length, 0);
  for (const [via, id] of lists) {
    const pick = J.pickListed(all[id], title);
    if (!pick) continue;
    all[id] = all[id].filter((e) => e !== pick.entry);
    if (!all[id].length) delete all[id];
    await setLocal({ externalJobs: all });
    return { job: pick.entry.job, via: pick.byTitle ? `${via}+title` : via, candidates };
  }
  await setLocal({ externalJobs: all });        // persist the prune
  return { job: null, candidates };
}

/* ------------------------------------------------ sites enabled by the user
 *
 * "Always capture on this site" (popup): an employer's own career domain,
 * which no manifest can list in advance. The popup asks Chrome for that ONE
 * host; this worker then registers the generic capture scripts for it.
 *
 * A site runs them only while BOTH hold: it is in `enabledSites` AND Chrome's
 * permission for it stands. Permission alone is not enough — options.js also
 * asks for an origin, a remote tracker server's, and that server's pages must
 * never get job-capture scripts. Registration is derived from the two by
 * syncSites(), run on install, on startup and on every permission change, so
 * a site revoked in chrome://extensions stops running them too. */
const SITE_SCRIPTS = ["shared/jobposting.js", "adapters/generic.js",
                      "shared/answers.js", "shared/capture.js"];
const siteScriptId = (host) => `site-${host}`;

async function _enabledSites() {
  const { enabledSites = [] } = await new Promise((res) =>
    chrome.storage.local.get({ enabledSites: [] }, res));
  return enabledSites;
}

async function syncSites() {
  const sites = await _enabledSites();
  const { origins = [] } = await chrome.permissions.getAll();
  const granted = new Set(origins);
  const want = sites.filter((h) => granted.has(`*://${h}/*`));
  const have = (await chrome.scripting.getRegisteredContentScripts())
    .map((s) => s.id).filter((id) => id.startsWith("site-"));
  const drop = have.filter((id) => !want.some((h) => siteScriptId(h) === id));
  if (drop.length) await chrome.scripting.unregisterContentScripts({ ids: drop });
  const add = want.filter((h) => !have.includes(siteScriptId(h)));
  if (add.length) {
    await chrome.scripting.registerContentScripts(add.map((h) => ({
      id: siteScriptId(h), matches: [`*://${h}/*`], js: SITE_SCRIPTS,
      runAt: "document_idle", allFrames: true, persistAcrossSessions: true,
    })));
  }
  await syncIconRule(want).catch(() => {});
}

/* ------------------------------------------------------------ toolbar icon
 *
 * The icon says whether the extension captures on the page in front of you:
 * grey with a hollow dot anywhere else (manifest action.default_icon), blue
 * with an amber dot on a page it captures on (scripts/make_icons.py has the
 * design). The switch is a declarativeContent RULE, evaluated by Chrome on
 * every navigation and undone the moment a page stops matching — so the
 * worker is never woken per page load, and no content script races it. The
 * alternative, chrome.action.setIcon per tab from a content script's hello,
 * does not survive: a tab's own icon "automatically resets when the tab is
 * closed" (Chrome docs) — NOT when it navigates — so it would stay blue on
 * every page browsed to after a supported one.
 *
 * The rule's pages are exactly the capture scripts' pages: the manifest's
 * content_scripts patterns and the enabled sites, one list, translated by
 * jobposting.js:matchPatternRegex. declarativeContent wants image DATA, not
 * paths, so the PNGs are decoded here. */
async function _iconData(state) {
  const out = {};
  for (const size of [16, 32]) {
    const blob = await (await fetch(chrome.runtime.getURL(`icons/${state}-${size}.png`))).blob();
    const bitmap = await createImageBitmap(blob);
    const ctx = new OffscreenCanvas(size, size).getContext("2d");
    ctx.drawImage(bitmap, 0, 0);
    out[size] = ctx.getImageData(0, 0, size, size);
  }
  return out;
}

async function syncIconRule(enabledHosts) {
  const J = self.__trackerJobPosting;
  const patterns = [
    ...chrome.runtime.getManifest().content_scripts.flatMap((cs) => cs.matches),
    ...enabledHosts.map((h) => `*://${h}/*`),
  ];
  const DC = chrome.declarativeContent;
  const conditions = patterns.map((p) => J.matchPatternRegex(p)).filter(Boolean)
    .map((re) => new DC.PageStateMatcher({ pageUrl: { urlMatches: re } }));
  const imageData = await _iconData("on");
  await new Promise((res) => DC.onPageChanged.removeRules(undefined, res));
  await new Promise((res) => DC.onPageChanged.addRules(
    [{ conditions, actions: [new DC.SetIcon({ imageData })] }], res));
}

/* Enable one host, and start on the page the user is looking at: the listing
 * is what needs remembering, and waiting for a reload would miss it. A page
 * that already has the scripts (the popup's own capture injects them) is
 * left alone — a second copy would capture every submit twice. */
async function enableSite(host, tabId) {
  const sites = await _enabledSites();
  if (!sites.includes(host)) await setLocal({ enabledSites: [...sites, host] });
  await syncSites();
  if (tabId == null) return;
  const live = await chrome.tabs.sendMessage(tabId, { type: "tracker-ping" }, { frameId: 0 })
    .then((r) => !!(r && r.ok)).catch(() => false);
  if (!live) {
    await chrome.scripting.executeScript({ target: { tabId }, files: SITE_SCRIPTS })
      .catch(() => {});
  }
}

async function disableSite(host) {
  const sites = await _enabledSites();
  await setLocal({ enabledSites: sites.filter((h) => h !== host) });
  await chrome.permissions.remove({ origins: [`*://${host}/*`] }).catch(() => false);
  await syncSites();
}

chrome.runtime.onInstalled.addListener(() => { syncSites().catch(() => {}); });
chrome.runtime.onStartup.addListener(() => { syncSites().catch(() => {}); });
chrome.permissions.onRemoved.addListener(() => { syncSites().catch(() => {}); });
// The permission prompt can close the popup before its own code runs on, so
// the grant is completed HERE: the popup notes which host it asked for, and a
// matching grant within two minutes enables it.
chrome.permissions.onAdded.addListener((perms) => {
  (async () => {
    const { pendingSite } = await new Promise((res) =>
      chrome.storage.local.get({ pendingSite: null }, res));
    if (!pendingSite || Date.now() - pendingSite.at > 2 * 60 * 1000) return;
    if (!(perms.origins || []).includes(`*://${pendingSite.host}/*`)) return;
    await setLocal({ pendingSite: null });
    await enableSite(pendingSite.host, pendingSite.tabId);
  })().catch(() => {});
});

async function api(path, body) {
  const { apiBase, token } = await settings();
  if (!token) return { ok: false, error: "no API token set in options" };
  const base = apiBase.replace(/\/$/, "");
  try {
    const r = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    const parsed = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: parsed.detail || `HTTP ${r.status}`, base };
    return { ok: true, body: parsed, base };
  } catch (e) {
    return { ok: false, error: "server unreachable — is `serve` running?", base };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (msg && msg.type === "tracker-capture") {
    (async () => {
      const r = await api("/captures", msg.payload);
      if (!r.ok) {
        await recordFailure({ url: msg.payload.url, at: Date.now(), error: r.error });
        return respond({ ok: false, error: r.error });
      }
      await setLocal({ last: { at: Date.now(), ...r.body } });
      // Stashed BEFORE responding, because responding may be pointless: if the
      // click navigated the tab, the frame waiting on this reply is already
      // gone. Shape matches what showResult() builds in the content script —
      // the one place these two must agree.
      await stashReceipt(sender.tab && sender.tab.id, {
        ok: true, id: r.body.application_id, label: r.body.label,
        answers: r.body.answers, enriched: r.body.enriched, apiBase: r.base,
      });
      // An ATS submit that completed the record a job board's tab started:
      // tell that tab, so a box still asking "Capture this application?"
      // turns into the receipt (capture.js). Needs host permission for the
      // opener's site — linkedin.com has it; elsewhere this quietly fails and
      // the box simply stays until dismissed.
      const opener = sender.tab && sender.tab.openerTabId;
      if (msg.notifyOpener && opener != null) {
        chrome.tabs.sendMessage(opener, {
          type: "tracker-external-completed",
          detail: { ok: true, id: r.body.application_id, label: r.body.label,
                    answers: r.body.answers, enriched: r.body.enriched, apiBase: r.base },
        }, { frameId: 0 }).catch(() => {});
      }
      // apiBase travels back so the receipt can link straight to the record —
      // only the worker knows it (it lives in chrome.storage.sync).
      respond({ ok: true, apiBase: r.base, ...r.body });
    })();
    return true;                       // async respond
  }

  // Same worker-lifetime rule as the job stash below: sent by the click that
  // opens the employer's site, the worst moment to be racing a shutdown.
  if (msg && msg.type === "tracker-stash-external") {
    stashExternal(sender.tab && sender.tab.id, msg.job).then(() => respond({ ok: true }));
    return true;
  }

  if (msg && msg.type === "tracker-take-external") {
    takeExternal(sender.tab && sender.tab.openerTabId, sender.tab && sender.tab.id,
                 msg.title || null)
      .then((r) => respond(r));
    return true;
  }

  // From the popup, which has no tab of its own: the host and the tab travel
  // in the message. The permission itself is Chrome's, asked for by the popup.
  if (msg && msg.type === "tracker-enable-site") {
    enableSite(msg.host, msg.tabId).then(() => respond({ ok: true }))
      .catch((e) => respond({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (msg && msg.type === "tracker-disable-site") {
    disableSite(msg.host).then(() => respond({ ok: true }))
      .catch((e) => respond({ ok: false, error: String(e && e.message || e) }));
    return true;
  }

  // `return true` + respond() is NOT decoration here: it holds the message
  // port open, and an open port is what keeps an MV3 service worker alive.
  // Returning false tells Chrome the handler is finished, so the worker may be
  // killed between stashPendingJob's storage read and its write — and this
  // particular message is sent by a click that navigates the page
  // immediately, which is the worst possible moment to be racing a shutdown.
  // A real JobStreet apply on 29 Jul 2026 came out as "unknown company"
  // exactly here: deferral worked, the submit captured, the stash was gone.
  // sender.tab.id is the TAB, shared by every frame in it — which is the whole
  // point: the opening click stashes from the top frame and the submit takes
  // from the modal's subframe, so the tab is the one identifier both ends of a
  // single apply agree on even when the key itself has degraded.
  if (msg && msg.type === "tracker-stash-job") {
    stashPendingJob(msg.key, msg.job, sender.tab && sender.tab.id)
      .then(() => respond({ ok: true }));
    return true;
  }

  if (msg && msg.type === "tracker-take-job") {
    takePendingJob(msg.key, sender.tab && sender.tab.id)
      .then((r) => respond({ job: r.job, exact: r.exact }));
    return true;
  }

  /* The content script asks, on load, whether this tab is owed a receipt. */
  if (msg && msg.type === "tracker-claim-receipt") {
    claimReceipt(sender.tab && sender.tab.id).then((detail) => respond({ detail }));
    return true;
  }

  /* A receipt rendered in the page it belonged to — the usual case, and the
   * stash is now a duplicate waiting to fire on the next navigation. */
  if (msg && msg.type === "tracker-receipt-shown") {
    dropReceipt(sender.tab && sender.tab.id).then(() => respond({ ok: true }));
    return true;
  }

  // Same worker-lifetime reason as tracker-stash-job below: this is also sent
  // from a click that is about to navigate, and a diagnostic that vanishes
  // under a race is worse than none.
  if (msg && msg.type === "tracker-sweep") {
    recordSweep({ at: Date.now(), url: msg.url, ...(msg.detail || {}) })
      .then(() => respond({ ok: true }));
    return true;
  }

  // Title/company provenance. Same worker-lifetime reasoning as the sweep
  // above — it rides an apply click that is about to navigate.
  if (msg && msg.type === "tracker-provenance") {
    recordProvenance(msg.detail || {}).then(() => respond({ ok: true }));
    return true;
  }

  /* Annotating an already-saved capture with a note. Separate call per
   * field, so one failing doesn't take the other with it. */
  if (msg && msg.type === "tracker-tag") {
    (async () => {
      const r = await api(`/captures/${encodeURIComponent(msg.id)}/tag`, msg.body || {});
      respond(r.ok ? { ok: true, ...r.body } : { ok: false, error: r.error });
    })();
    return true;
  }

  /* Draw a subframe's receipt in the tab's TOP frame instead — the Easy Apply
   * modal's iframe is destroyed when the modal closes. Requires host
   * permission for the tab; without it sendMessage rejects and the caller
   * falls back to rendering in its own frame. The target is derived from the
   * real sender, never from the message, and page scripts can't reach this
   * listener at all. */
  if (msg && msg.type === "tracker-relay-receipt") {
    const tabId = sender.tab && sender.tab.id;
    if (tabId == null || sender.frameId === 0) { respond({ ok: false }); return false; }
    chrome.tabs.sendMessage(tabId, { type: "tracker-receipt", detail: msg.detail },
                            { frameId: 0 })
      .then((r) => respond({ ok: !!(r && r.ok) }))
      .catch(() => respond({ ok: false }));
    return true;
  }

  /* A subframe saw an apply it cannot handle — have the top frame do it. Same
   * targeting rules and same host-permission requirement as the receipt relay
   * above: the tab comes from the real sender, never from the message, and
   * frame 0 is never allowed to relay to itself. See capture.js:relayApply. */
  if (msg && msg.type === "tracker-relay-apply") {
    const tabId = sender.tab && sender.tab.id;
    if (tabId == null || sender.frameId === 0) { respond({ ok: false }); return false; }
    chrome.tabs.sendMessage(tabId, { type: "tracker-apply", detail: msg.detail },
                            { frameId: 0 })
      .then((r) => respond({ ok: !!(r && r.ok) }))
      .catch(() => respond({ ok: false }));
    return true;
  }

  /* "Which frame am I?" — asked once per injection, because a content script
   * cannot answer it reliably for itself: a frame that cannot reach its top
   * gets ITSELF back from window.top with no error, so `window === window.top`
   * reads true inside a subframe. sender.frameId is the browser's own record,
   * and every relay below already trusts it over anything the message says. */
  if (msg && msg.type === "tracker-whoami") {
    respond({ frameId: sender.frameId, top: sender.frameId === 0 });
    return false;
  }

  /* A subframe's submit asking frame 0 who the job is. Same targeting rules
   * again — tab from the real sender, frame 0 never relays to itself.
   *
   * This is the rescue that does NOT depend on having remembered anything: the
   * stash needs the opening click to have been seen and its key to have
   * survived, and when either fails the record saves with no company, title or
   * job id (18 Aug 2026, url linkedin.com/preload/?_bprMode=vanilla). Frame 0
   * is still sitting on the job page throughout, so it can simply be asked. */
  if (msg && msg.type === "tracker-relay-getjob") {
    const tabId = sender.tab && sender.tab.id;
    // `tabUrl` is read HERE, at capture time, from the browser's own record of
    // the tab — not cached at injection, because LinkedIn is an SPA and rewrites
    // its URL by pushState without ever re-injecting the content script, so a
    // cached copy could name a different job entirely. It travels back even when
    // there is no job to report: it is both the last-resort identity source
    // (adapter.jobFromUrl) and the fact that says whether the tab is on a job
    // page at all, which is the question the 20 Aug 2026 recurrence turns on.
    const tabUrl = (sender.tab && sender.tab.url) || null;
    if (tabId == null || sender.frameId === 0) { respond({ job: null, tabUrl }); return false; }
    chrome.tabs.sendMessage(tabId, { type: "tracker-getjob" }, { frameId: 0 })
      .then((r) => respond({ job: (r && r.job) || null, tabUrl }))
      .catch(() => respond({ job: null, tabUrl }));
    return true;
  }

  if (msg && msg.type === "tracker-capture-failure") {
    // Same race as the others, and the one where losing the write hurts most:
    // this is the record that a capture failed at all.
    recordFailure(msg.detail).then(() => respond({ ok: true }));
    return true;
  }
  return false;
});
