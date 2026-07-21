/* Service worker: owns the API call (host_permissions exempt it from CORS,
 * and it works against http://localhost where a content-script fetch from an
 * https page would be blocked as mixed content). Also keeps a small ring
 * buffer of capture failures the popup surfaces. */
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

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg && msg.type === "tracker-capture") {
    (async () => {
      const { apiBase, token } = await settings();
      if (!token) return respond({ ok: false, error: "no API token set in options" });
      try {
        const r = await fetch(`${apiBase.replace(/\/$/, "")}/captures`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${token}`,
          },
          body: JSON.stringify(msg.payload),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) {
          await recordFailure({ url: msg.payload.url, at: Date.now(),
                                error: `HTTP ${r.status}` });
          return respond({ ok: false, error: body.detail || `HTTP ${r.status}` });
        }
        chrome.storage.local.set({ last: { at: Date.now(), ...body } });
        respond({ ok: true, ...body });
      } catch (e) {
        await recordFailure({ url: msg.payload.url, at: Date.now(),
                              error: String(e) });
        respond({ ok: false, error: "server unreachable — is `serve` running?" });
      }
    })();
    return true;                       // async respond
  }
  if (msg && msg.type === "tracker-capture-failure") {
    recordFailure(msg.detail);
    return false;
  }
  return false;
});
