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
