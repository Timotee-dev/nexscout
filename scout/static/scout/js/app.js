// NexScout — small progressive enhancements. Everything works without JS too.
(function () {
  "use strict";
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // Toasts: dismiss + auto-hide success messages
  $$(".toast").forEach((t) => {
    t.querySelector(".x")?.addEventListener("click", () => t.remove());
    if (t.classList.contains("success")) setTimeout(() => t.remove(), 6000);
  });

  // Confirm before destructive actions
  $$("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); })
  );

  // Busy state for slow (Claude / network) actions: status line across the top
  $$("form[data-busy]").forEach((f) =>
    f.addEventListener("submit", (e) => {
      if (e.defaultPrevented) return;
      (e.submitter || f.querySelector("button"))?.classList.add("busy");
      if (!document.querySelector(".busy-bar")) {
        const bar = document.createElement("div"); bar.className = "busy-bar"; document.body.appendChild(bar);
        const note = document.createElement("div"); note.className = "busy-note"; note.setAttribute("role", "status");
        note.textContent = f.dataset.busy; document.body.appendChild(note);
      }
    })
  );

  // Copy buttons
  $$("[data-copy]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const el = document.querySelector(btn.dataset.copy);
      if (!el) return;
      const text = el.value ?? el.textContent;
      try { await navigator.clipboard.writeText(text); }
      catch { el.select?.(); document.execCommand("copy"); }
      const old = btn.innerHTML; btn.textContent = "Copied";
      setTimeout(() => (btn.innerHTML = old), 1400);
    })
  );

  // Word counts
  $$(".wc[data-for]").forEach((wc) => {
    const ta = document.getElementById(wc.dataset.for);
    if (!ta) return;
    const limit = parseInt(ta.dataset.wordcount || "0", 10);
    const update = () => {
      const n = (ta.value.match(/\b[\w'’-]+\b/g) || []).length;
      wc.textContent = limit ? `${n} / ${limit} words` : `${n} words`;
      wc.classList.toggle("over", limit && n >= limit);
    };
    ta.addEventListener("input", update); update();
  });

  // Save drafts without leaving the page
  $$("form[data-ajax-save]").forEach((f) =>
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const out = f.querySelector(".saved");
      try {
        const res = await fetch(f.action, { method: "POST", body: new FormData(f), headers: { "X-Requested-With": "fetch" } });
        const data = await res.json();
        if (!data.ok) throw new Error("save failed");
        if (out) { out.textContent = data.warnings?.length ? `Saved · ${data.warnings.length} thing${data.warnings.length > 1 ? "s" : ""} to check` : "Saved"; out.classList.add("show"); }
      } catch { f.submit(); }
    })
  );

  // Auto-submit filters
  $$("form[data-autosubmit]").forEach((f) => {
    let t;
    f.addEventListener("change", () => f.submit());
    f.querySelector("input[type=search]")?.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => f.submit(), 450); });
  });
  $$("select[data-autosubmit-select]").forEach((s) => s.addEventListener("change", () => s.form.submit()));

  // Jobs split view: on small screens a row opens the full job page
  $$(".jrow[data-detail]").forEach((row) =>
    row.addEventListener("click", (e) => {
      if (window.matchMedia("(max-width: 900px)").matches && !e.metaKey && !e.ctrlKey) {
        e.preventDefault(); location.href = row.dataset.detail;
      }
    })
  );
  document.querySelector(".split.explicit .jrow.sel")?.scrollIntoView({ block: "nearest" });

  // Keep search caret at end after auto-submit reloads
  const search = document.querySelector(".filters input[type=search]");
  if (search && search.value) { search.focus(); const v = search.value; search.value = ""; search.value = v; }
})();

// ------------------------------------------------------------ Swipe deck
(function () {
  "use strict";
  const deck = document.getElementById("deck");
  if (!deck) return;
  const csrf = deck.dataset.csrf;
  const toast = document.getElementById("swipe-toast");
  const controls = document.querySelector(".swipe-controls");
  let busy = false, toastTimer;

  const cards = () => Array.from(deck.querySelectorAll(".scard:not(.gone)"));
  const top = () => cards()[0];
  const restack = () => cards().forEach((c, i) => c.style.setProperty("--i", i));

  function say(msg, bad) {
    toast.textContent = msg; toast.classList.toggle("bad", !!bad); toast.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), 4200);
  }

  function fly(card, decision) {
    const dx = decision === "apply" ? 1 : decision === "skip" ? -1 : 0;
    const dy = decision === "save" ? -1 : 0;
    card.style.transition = "transform .35s ease-in, opacity .35s";
    card.style.transform = `translate(${dx * 140}%, ${dy * 120}%) rotate(${dx * 22}deg)`;
    card.classList.add("gone");
    setTimeout(() => { card.remove(); restack(); if (!top()) { deck.querySelector(".deck-empty").classList.remove("hidden"); controls.hidden = true; } }, 360);
  }

  async function decide(decision) {
    const card = top();
    if (!card || busy) return;
    if (decision === "apply" && card.dataset.scam && !confirm("This listing has scam warnings. Apply anyway?")) {
      card.style.transform = ""; return;
    }
    busy = true;
    fly(card, decision);
    const body = new FormData(); body.append("decision", decision); body.append("csrfmiddlewaretoken", csrf);
    try {
      const res = await fetch(card.dataset.url, { method: "POST", body, headers: { "X-Requested-With": "fetch" } });
      const data = await res.json();
      say(data.message || "Done", !data.ok);
    } catch { say("Couldn't save that decision. Check NexScout is running.", true); }
    finally { busy = false; }
  }

  // Drag / swipe gestures
  let start = null;
  deck.addEventListener("pointerdown", (e) => {
    const card = top();
    if (!card || !card.contains(e.target) || e.target.closest("a, button")) return;
    start = { x: e.clientX, y: e.clientY, card };
    card.classList.add("dragging"); card.setPointerCapture(e.pointerId);
  });
  deck.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    start.card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 18}deg)`;
    const s = (sel, v) => (start.card.querySelector(sel).style.opacity = Math.max(0, Math.min(1, v)));
    s(".stamp-apply", dx / 110); s(".stamp-skip", -dx / 110); s(".stamp-save", -dy / 110 - Math.abs(dx) / 200);
  });
  const end = (e) => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y, card = start.card;
    card.classList.remove("dragging"); start = null;
    card.querySelectorAll(".stamp").forEach((st) => (st.style.opacity = 0));
    if (dx > 120) decide("apply");
    else if (dx < -120) decide("skip");
    else if (dy < -120) decide("save");
    else card.style.transform = "";
  };
  deck.addEventListener("pointerup", end);
  deck.addEventListener("pointercancel", end);

  document.querySelectorAll(".sbtn[data-decision]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.decision)));
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select")) return;
    if (e.key === "ArrowRight") decide("apply");
    else if (e.key === "ArrowLeft") decide("skip");
    else if (e.key === "ArrowUp") { e.preventDefault(); decide("save"); }
  });
})();
