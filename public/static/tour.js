// The first-time tour (templates/_tour.html): a few short steps that light up the menu.
// Shown once per device, after the opening and the read-aloud question; "Take the tour"
// (any [data-tour-start] link) shows it again. Skip with the button or Escape.
window.Tripwire = window.Tripwire || {};
(function (T) {
  var KEY = "tripwire-tour", tour, card, spot, steps = [], at = 0, lastFocus = null;

  function seen() { try { return localStorage.getItem(KEY) === "done"; } catch (e) { return true; } }
  function remember() { try { localStorage.setItem(KEY, "done"); } catch (e) {} }
  function visible(el) { var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; }
  function target(step) {
    var list = step.target ? document.querySelectorAll(step.target) : [];
    for (var i = 0; i < list.length; i++) if (visible(list[i])) return list[i];
    return null;
  }

  function place() {
    if (tour.hidden) return;
    var el = target(steps[at]), pad = 6;
    card.classList.remove("below", "above");
    if (!el) { spot.hidden = true; tour.classList.add("no-target"); return; }
    tour.classList.remove("no-target");
    var r = el.getBoundingClientRect();
    spot.hidden = false;
    spot.style.left = (r.left - pad) + "px"; spot.style.top = (r.top - pad) + "px";
    spot.style.width = (r.width + pad * 2) + "px"; spot.style.height = (r.height + pad * 2) + "px";
    if (innerWidth < 640) return;                     // phones: the card sits at the bottom of the screen
    var below = r.bottom + 18, width = card.offsetWidth;
    card.classList.add(below + card.offsetHeight < innerHeight ? "below" : "above");
    card.style.top = (below + card.offsetHeight < innerHeight ? below : Math.max(12, r.top - 18 - card.offsetHeight)) + "px";
    card.style.left = Math.min(Math.max(12, r.left + r.width / 2 - width / 2), innerWidth - width - 12) + "px";
  }

  function show(n) {
    at = Math.max(0, Math.min(n, steps.length - 1));
    var step = steps[at], last = at === steps.length - 1;
    tour.querySelector("[data-tour-count]").textContent = "Step " + (at + 1) + " of " + steps.length;
    tour.querySelector("[data-tour-title]").textContent = step.title;
    var el = target(step), inMenu = el && el.matches(".nav-narrow > summary");
    tour.querySelector("[data-tour-text]").textContent = step.text + (inMenu ? " Find it in the Menu, top right." : "");
    tour.querySelector("[data-tour-end]").hidden = !last;
    tour.querySelector("[data-tour-next]").hidden = last;
    tour.querySelector("[data-tour-back]").hidden = at === 0;
    place();
    (last ? tour.querySelector("[data-tour-finish] button") : tour.querySelector("[data-tour-next]")).focus();
    if (document.documentElement.classList.contains("a11y-read") && T.speak) T.speak(step.title + ". " + step.text);
  }

  function close() {
    remember();
    tour.hidden = true;
    document.documentElement.classList.remove("tour-open");
    if (T.stopSpeaking) T.stopSpeaking();
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  T.startTour = function () {
    if (!tour || !steps.length) return;
    lastFocus = document.activeElement;
    tour.hidden = false;
    document.documentElement.classList.add("tour-open");
    show(0);
  };

  document.addEventListener("DOMContentLoaded", function () {
    tour = document.getElementById("tour");
    if (!tour) return;
    card = tour.querySelector(".tour-card"); spot = tour.querySelector("[data-tour-spot]");
    var template = tour.querySelector("[data-tour-steps]");
    steps = Array.prototype.map.call(template.content.querySelectorAll("section"), function (s) {
      return { target: s.dataset.target, title: s.dataset.title, text: s.textContent.replace(/\s+/g, " ").trim() };
    });
    tour.querySelector("[data-tour-next]").addEventListener("click", function () { show(at + 1); });
    tour.querySelector("[data-tour-back]").addEventListener("click", function () { show(at - 1); });
    tour.querySelector("[data-tour-skip]").addEventListener("click", close);
    tour.querySelector("[data-tour-done]").addEventListener("click", close);
    tour.querySelector("[data-tour-finish]").addEventListener("submit", remember);
    tour.querySelector("[data-tour-shade]").addEventListener("click", function () {});   // clicks outside do nothing
    document.addEventListener("keydown", function (event) {
      if (tour.hidden) return;
      if (event.key === "Escape") { event.preventDefault(); close(); }
      else if (event.key === "ArrowRight" && at < steps.length - 1) show(at + 1);
      else if (event.key === "ArrowLeft" && at > 0) show(at - 1);
      else if (event.key === "Tab") {                    // keep keyboard focus inside the tour
        var items = Array.prototype.filter.call(card.querySelectorAll("button"), function (b) { return visible(b); });
        var first = items[0], lastItem = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); lastItem.focus(); }
        else if (!event.shiftKey && document.activeElement === lastItem) { event.preventDefault(); first.focus(); }
      }
    });
    addEventListener("resize", place);
    addEventListener("scroll", place, { passive: true });
    document.querySelectorAll("[data-tour-start]").forEach(function (link) {
      link.addEventListener("click", function (event) { event.preventDefault(); scrollTo(0, 0); T.startTour(); });
    });
    // First visit: after the opening and the read-aloud question (app.js says when those are done).
    if (!seen()) document.addEventListener("tripwire:welcome-done", function () { setTimeout(T.startTour, 300); }, { once: true });
  });
})(window.Tripwire);
