document.getElementById("cap").addEventListener("click", async () => {
  const out = document.getElementById("out");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  chrome.tabs.sendMessage(tab.id, { type: "tracker-capture-manual" }, () => {
    out.textContent = chrome.runtime.lastError
      ? "This page isn't a supported job site."
      : "Check the page for the capture popover.";
  });
});
chrome.storage.local.get({ failures: [] }, ({ failures }) => {
  if (!failures.length) return;
  const ul = document.getElementById("fails");
  ul.innerHTML = "";
  for (const f of failures.slice(0, 5)) {
    const li = document.createElement("li");
    li.textContent = `${new Date(f.at).toLocaleString()} — ${f.error || "no job found"} — ${f.url || ""}`;
    ul.appendChild(li);
  }
});
