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

/* What the Q&A sweep saw on each of the last few captures. The point is the
 * skip counts: a field the sweep couldn't label is an answer that silently
 * never reaches the tracker, and without this there is nothing to look at —
 * the record simply comes out short. */
chrome.storage.local.get({ sweeps: [] }, ({ sweeps }) => {
  if (!sweeps.length) return;
  const ul = document.getElementById("sweeps");
  ul.innerHTML = "";
  for (const s of sweeps.slice(0, 5)) {
    const li = document.createElement("li");
    const when = new Date(s.at).toLocaleString();
    const last = s.last;
    if (!last) {
      li.append(`${when} — `);
      const w = document.createElement("span");
      w.className = "warn";
      let msg = s.noRoot ? `no apply form found (${s.noRoot} attempts)` : "form never swept";
      // dialogPresent/controlsOnPage: was something dialog-like actually open
      // with fields on the page, or did the sweep just never see a form at
      // all? The first shape means the root selector needs a look; the second
      // usually just means the click wasn't inside an apply flow.
      if (s.noRootHint) {
        msg += s.noRootHint.dialogPresent
          ? ` — a dialog was open with ${s.noRootHint.controlsOnPage} field(s) on the page, but the root selector didn't match it`
          : ` — no dialog was open (${s.noRootHint.controlsOnPage} field(s) elsewhere on the page)`;
      }
      w.textContent = msg;
      li.append(w);
      // Real form-field elements that appeared on the page during the miss,
      // caught as they were inserted (not guessed from position afterwards —
      // position-based guessing was tried and confirmed wrong twice the same
      // day, see answers.js). Each line is a genuine candidate for "this is
      // what the real modal looks like".
      const inserts = s.noRootHint && s.noRootHint.recentInserts;
      if (inserts && inserts.length) {
        for (const ins of inserts) {
          const insEl = document.createElement("div");
          insEl.style.fontFamily = "monospace";
          insEl.style.fontSize = ".8em";
          insEl.style.opacity = "0.8";
          insEl.style.marginTop = "2px";
          insEl.textContent =
            `+${ins.tag}${ins.role ? " role=" + ins.role : ""} ${ins.cls} (depth ${ins.depthFromBody})`;
          li.append(insEl);
        }
      }
      ul.appendChild(li);
      continue;
    }
    li.append(`${when} — `);
    const kept = document.createElement("b");
    kept.textContent = `${s.kept} kept`;
    li.append(kept, ` of ${last.controls} controls, ${s.sweeps} sweeps`);
    // Each of these is a specific, actionable shape of loss.
    const gaps = [];
    if (s.noLabelMax) gaps.push(`${s.noLabelMax} unlabelled`);
    if (last.noValue) gaps.push(`${last.noValue} empty`);
    if (last.disabled) gaps.push(`${last.disabled} disabled`);
    gaps.push(`${s.textareaSeen} textarea${s.textareaSeen === 1 ? "" : "s"} seen`);
    // Only "unlabelled" is a defect: those are answers the user gave that the
    // sweep threw away. Empty and disabled are the form being a form, and a
    // textarea count of zero is normal on most of them — it's reported plainly
    // because it's the one number that says whether a missing long answer was
    // never seen or merely never labelled.
    const g = document.createElement("span");
    g.className = s.noLabelMax ? "warn" : "";
    g.textContent = ` — ${gaps.join(", ")}`;
    li.append(g);
    ul.appendChild(li);
  }
});

/* Where each recent capture's title came from, and whether the submit page
 * and the stashed snapshot agreed about it.
 *
 * Exists because two real captures (Southridge APAC, Adatum — early Aug 2026)
 * stored a title belonging to no job on the page, kept the right company, and
 * could not be reproduced afterwards on any of the three LinkedIn layouts.
 * Nothing recorded which branch had produced the string, so the trail ended
 * there. A `disagreed` line is the thing to read: it means the page and the
 * stash both had a value and they differed, so "gaps only" kept the page's —
 * which is correct when the page is right and is exactly the failure when it
 * isn't. Boring grey lines are healthy. */
chrome.storage.local.get({ provenance: [] }, ({ provenance }) => {
  if (!provenance.length) return;
  const ul = document.getElementById("prov");
  ul.innerHTML = "";
  for (const p of provenance.slice(0, 6)) {
    const li = document.createElement("li");
    li.append(`${new Date(p.at).toLocaleString()} — `);
    const b = document.createElement("b");
    b.textContent = (p.page && p.page.title) || "(no title)";
    li.append(b, ` via ${p.source || "?"}`);
    if (p.layout) li.append(` on ${p.layout}`);
    if (p.disagreed && p.disagreed.length) {
      const w = document.createElement("span");
      w.className = "warn";
      w.textContent = ` — page and stash disagreed on ${p.disagreed.join(", ")}; `
        + p.disagreed.map((k) => `page "${(p.page && p.page[k]) || ""}" beat stash `
        + `"${(p.stash && p.stash[k]) || ""}"`).join("; ");
      li.append(w);
    } else if (p.stashed) {
      li.append(" — agreed with stash");
    }
    ul.appendChild(li);
  }
});
