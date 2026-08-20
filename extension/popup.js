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
    // A "no job found" entry used to say only which frame's url failed, which
    // made five different sessions render as five identical lines. When the
    // frame that failed is NOT the tab's top frame and the tab was on a real
    // job page, that is the whole diagnosis in one sentence — say it.
    if (!f.error && f.topFrame === false) {
      const w = document.createElement("span");
      w.className = "warn";
      w.textContent = ` — ran in a subframe; tab was on ${f.tabUrl || "an unknown page"}` +
                      (f.read ? ` (read: ${f.read}${f.docSource ? `, ${f.docSource}` : ""})` : "");
      li.appendChild(w);
    }
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
    // An apply flow that OPENED but could not be read — nothing was stashed, so
    // a later submit has nothing to fall back on. Distinct from a capture line:
    // there is no title to print because that is the whole problem.
    if (p.stage === "open") {
      const w = document.createElement("span");
      w.className = "warn";
      w.textContent = "apply opened, but the job could not be read — nothing "
        + "stashed for the submit to use";
      li.append(w, ` (${p.url || "?"})`);
      ul.appendChild(li);
      continue;
    }
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
    // The frame that submits can be one that cannot read the job at all (an
    // Easy Apply modal inside linkedin.com/preload/). Say so, and say whether
    // asking frame 0 rescued it — a capture with no stash AND nothing recovered
    // is the one that saves as "unknown company", and it used to print no line
    // here whatsoever.
    if (p.askedTop) {
      const s = document.createElement("span");
      const got = [...(p.fromTop || []), ...(p.fromUrl || [])];
      if (got.length) {
        s.textContent = ` — ${p.stashed ? "stash short" : "no stash"}; recovered `
          + `${got.join(", ")} from ${p.fromTop && p.fromTop.length ? "the job page" : "the tab URL"}`;
      } else {
        s.className = "warn";
        s.textContent = ` — ${p.stashed ? "stash short" : "no stash"}, `
          + (p.topFrame ? "no top frame to ask (this IS frame 0)"
                        : "frame 0 had nothing to add")
          + `; identity missing. tab was ${p.tabUrl || "unknown"}`;
      }
      li.append(s);
    }
    // A same-tab stash is a GUESS about which job was meant, used only for what
    // a real read left empty. Worth seeing every time it supplies anything.
    if (p.fromGuess && p.fromGuess.length) {
      const g = document.createElement("span");
      g.className = "warn";
      g.textContent = ` — ${p.fromGuess.join(", ")} came from a same-tab guess, `
        + `not this job's own stash; check the record`;
      li.append(g);
    }
    ul.appendChild(li);
  }
});
