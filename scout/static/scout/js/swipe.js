// NexScout Swipe: drag a card, use the buttons, or press arrow keys.
// Without JavaScript the buttons still work (they post the form for the top card).
(function () {
  "use strict";
  const deck = document.getElementById("deck");
  const form = document.getElementById("swipe-controls");
  if (!deck) return;

  const csrf = deck.dataset.csrf;
  const toast = document.getElementById("swipe-toast");
  const empty = deck.querySelector(".deck-empty");
  const THRESHOLD = 90;          // px to count as a swipe
  let busy = false, toastTimer = null, drag = null;

  const cards = () => Array.from(deck.querySelectorAll(".scard:not(.gone)"));
  const topCard = () => cards()[0] || null;

  function restack() {
    cards().forEach((c, i) => c.style.setProperty("--i", i));
    const t = topCard();
    if (form && t) form.action = t.dataset.url;
    if (!t) {
      if (empty) empty.classList.remove("hidden");
      if (form) form.hidden = true;
    }
  }

  function say(msg, bad) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.toggle("bad", !!bad);
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 4500);
  }

  function setStamps(card, dx, dy) {
    const op = (sel, v) => { const el = card.querySelector(sel); if (el) el.style.opacity = Math.max(0, Math.min(1, v)); };
    op(".stamp-apply", dx / THRESHOLD);
    op(".stamp-skip", -dx / THRESHOLD);
    op(".stamp-save", (-dy / THRESHOLD) - Math.abs(dx) / (THRESHOLD * 2));
  }

  function flyAway(card, decision) {
    const dx = decision === "apply" ? 1 : decision === "skip" ? -1 : 0;
    const dy = decision === "save" ? -1 : 0;
    card.classList.remove("dragging");
    card.classList.add("gone");
    card.style.transition = "transform .32s ease-in, opacity .32s ease-in";
    card.style.transform = `translate(${dx * 130}%, ${dy * 110}%) rotate(${dx * 20}deg)`;
    card.style.opacity = "0";
    setTimeout(() => { card.remove(); restack(); }, 330);
  }

  function snapBack(card) {
    card.classList.remove("dragging");
    card.style.transition = "";
    card.style.transform = "";
    setStamps(card, 0, 0);
  }

  async function decide(decision) {
    const card = topCard();
    if (!card || busy) return;
    if (decision === "apply" && card.dataset.scam &&
        !window.confirm("This listing has scam warnings. Apply anyway?")) { snapBack(card); return; }
    busy = true;
    flyAway(card, decision);
    restack();
    const body = new FormData();
    body.append("decision", decision);
    body.append("csrfmiddlewaretoken", csrf);
    try {
      const res = await fetch(card.dataset.url, {
        method: "POST", body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" },
      });
      let data = null;
      try { data = await res.json(); } catch (_) { /* not JSON */ }
      if (!res.ok || !data) {
        say(res.status === 403 ? "Your session expired. Refresh the page and try again."
                                : `Couldn't save that (error ${res.status}). Refresh and try again.`, true);
      } else {
        say(data.message || "Done", !data.ok);
      }
    } catch (_) {
      say("Couldn't reach NexScout. Is it still running?", true);
    } finally {
      busy = false;
    }
  }

  // ---- dragging (mouse, pen and touch via Pointer Events)
  deck.addEventListener("pointerdown", (e) => {
    const card = topCard();
    if (!card || busy || !card.contains(e.target) || e.target.closest("a, button, input, textarea")) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: 0, dy: 0, card, moved: false };
    card.classList.add("dragging");
    try { card.setPointerCapture(e.pointerId); } catch (_) {}
  });

  deck.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag.dx = e.clientX - drag.x;
    drag.dy = e.clientY - drag.y;
    if (Math.abs(drag.dx) > 4 || Math.abs(drag.dy) > 4) drag.moved = true;
    drag.card.style.transform = `translate(${drag.dx}px, ${drag.dy}px) rotate(${drag.dx / 16}deg)`;
    setStamps(drag.card, drag.dx, drag.dy);
    if (drag.moved) e.preventDefault();
  });

  function endDrag(e) {
    if (!drag || (e && e.pointerId !== drag.id)) return;
    const { card, dx, dy } = drag;
    drag = null;
    if (dx > THRESHOLD) decide("apply");
    else if (dx < -THRESHOLD) decide("skip");
    else if (dy < -THRESHOLD && Math.abs(dx) < THRESHOLD) decide("save");
    else snapBack(card);
  }
  deck.addEventListener("pointerup", endDrag);
  deck.addEventListener("pointercancel", (e) => { if (drag) { snapBack(drag.card); drag = null; } });
  deck.addEventListener("lostpointercapture", (e) => { if (drag && e.pointerId === drag.id) endDrag(e); });

  // ---- buttons (the form also works without JavaScript)
  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const btn = e.submitter || document.activeElement;
      const decision = btn && btn.value;
      if (decision) decide(decision);
    });
  }

  // ---- keyboard
  document.addEventListener("keydown", (e) => {
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); decide("apply"); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); decide("skip"); }
    else if (e.key === "ArrowUp") { e.preventDefault(); decide("save"); }
  });

  restack();
  deck.classList.add("ready");
})();
