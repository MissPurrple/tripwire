// Loading states for the whole site: a striped bar across the top while a page
// loads, and "Working..." on a button while its form is being sent.
(function () {
  function busy() { document.body.classList.add("is-loading"); }
  window.addEventListener("pageshow", function () {  // coming back with the Back button: not loading any more
    document.body.classList.remove("is-loading");
    document.querySelectorAll("button.is-busy").forEach(function (b) {
      b.classList.remove("is-busy"); b.disabled = false;
      if (b.dataset.label) b.textContent = b.dataset.label;
    });
  });
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || form.hasAttribute("data-no-loading")) return;
    var button = event.submitter || form.querySelector("button");
    busy();
    if (button && !button.classList.contains("link")) {
      button.dataset.label = button.textContent;
      button.classList.add("is-busy");
      button.textContent = "Working…";
      setTimeout(function () { button.disabled = true; }, 0);  // after the form has been sent
    }
  });
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[href]");
    if (!link || link.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey) return;
    var href = link.getAttribute("href");
    if (href.charAt(0) === "#" || link.origin !== location.origin) return;
    busy();
  });
})();

// Typewriter: green-screen text appears one letter at a time, to slow things down
// a little. Mark a container with data-typewriter; any element inside can set
// data-speed (milliseconds per letter). Links stay clickable while typing.
window.Tripwire = window.Tripwire || {};
(function (T) {
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function speedFor(node, fallback) {
    var el = node.parentElement && node.parentElement.closest("[data-speed]");
    return el ? +el.dataset.speed : fallback;
  }

  T.type = function (root, fallbackSpeed) {
    fallbackSpeed = fallbackSpeed || 28;
    return new Promise(function (resolve) {
      if (reduced) { resolve(); return; }
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          if (!n.nodeValue.trim() && n.nodeValue.indexOf(" ") === -1) return NodeFilter.FILTER_REJECT;
          return n.parentElement.closest(".cursor, [data-no-type], script, style") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        }
      });
      var nodes = [], n;
      while ((n = walker.nextNode())) nodes.push({ node: n, text: n.nodeValue, speed: speedFor(n, fallbackSpeed) });
      root.style.minHeight = root.offsetHeight + "px";   // keep the layout still while letters arrive
      root.setAttribute("aria-busy", "true");
      root.classList.add("typing");
      nodes.forEach(function (item) { item.node.nodeValue = ""; });
      var i = 0, pos = 0;
      (function tick() {
        if (i >= nodes.length) {
          root.removeAttribute("aria-busy"); root.classList.remove("typing"); root.style.minHeight = "";
          resolve(); return;
        }
        var item = nodes[i];
        var step = item.speed < 8 ? 3 : 1;                 // very fast lines type a few letters per tick
        pos = Math.min(item.text.length, pos + step);
        item.node.nodeValue = item.text.slice(0, pos);
        if (pos >= item.text.length) { i++; pos = 0; setTimeout(tick, Math.min(item.speed * 4, 120)); return; }
        setTimeout(tick, item.speed);
      })();
    });
  };

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-typewriter]").forEach(function (el) {
      if (!el.closest("[data-typewriter-manual]")) T.type(el, +el.dataset.typewriter || 28);
    });
  });
})(window.Tripwire);
