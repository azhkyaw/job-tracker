/* Service worker: owns the API call (host_permissions exempt it from CORS,
 * and it works against http://localhost where a content-script fetch from an
 * https page would be blocked as mixed content). Also keeps a small ring
 * buffer of capture failures the popup surfaces, and relays a subframe's
 * receipt to the tab's top frame. */
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
 * focused = NULL and no popover was ever seen.)
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
      // apiBase travels back so the receipt can link straight to the record —
      // only the worker knows it (it lives in chrome.storage.sync).
      respond({ ok: true, apiBase: r.base, ...r.body });
    })();
    return true;                       // async respond
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

  /* Tagging an already-saved capture: focused, or a note. Separate call per
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
