const DEFAULTS = { apiBase: "http://127.0.0.1:8000", token: "" };
chrome.storage.sync.get(DEFAULTS, (s) => {
  document.getElementById("apiBase").value = s.apiBase;
  document.getElementById("token").value = s.token;
});
document.getElementById("save").addEventListener("click", () => {
  const apiBase = document.getElementById("apiBase").value.trim() || DEFAULTS.apiBase;
  // Remote servers need a runtime origin grant (localhost is pre-granted).
  try {
    const origin = new URL(apiBase).origin + "/*";
    if (!origin.startsWith("http://127.0.0.1") && !origin.startsWith("http://localhost")) {
      chrome.permissions.request({ origins: [origin] });
    }
  } catch (e) { /* invalid URL — the fetch will surface it */ }
  chrome.storage.sync.set({
    apiBase,
    token: document.getElementById("token").value.trim(),
  }, () => {
    const el = document.getElementById("status");
    el.textContent = "Saved";
    setTimeout(() => (el.textContent = ""), 1800);
  });
});
