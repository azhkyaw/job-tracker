/* Service worker: owns the API call (host_permissions exempt it from CORS,
 * and it works against http://localhost where a content-script fetch from an
 * https page would be blocked as mixed content). Also keeps a small ring
 * buffer of capture failures the popup surfaces, and relays a subframe's
 * receipt to the tab's top frame. */
const DEFAULTS = { apiBase: "http://127.0.0.1:8000", token: "" };

async function settings() {
  return new Promise((res) => chrome.storage.sync.get(DEFAULTS, res));
}

async function recordFailure(detail) {
  const { failures = [] } = await new Promise((res) =>
    chrome.storage.local.get({ failures: [] }, res));
  failures.unshift(detail);
  chrome.storage.local.set({ failures: failures.slice(0, 10) });
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
      chrome.storage.local.set({ last: { at: Date.now(), ...r.body } });
      // apiBase travels back so the receipt can link straight to the record —
      // only the worker knows it (it lives in chrome.storage.sync).
      respond({ ok: true, apiBase: r.base, ...r.body });
    })();
    return true;                       // async respond
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

  if (msg && msg.type === "tracker-capture-failure") {
    recordFailure(msg.detail);
    return false;
  }
  return false;
});
