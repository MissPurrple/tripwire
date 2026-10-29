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

  T.calm = function () { return reduced || document.documentElement.classList.contains("a11y-calm"); };

  T.type = function (root, fallbackSpeed) {
    fallbackSpeed = fallbackSpeed || 28;
    return new Promise(function (resolve) {
      if (T.calm()) { resolve(); return; }
      // Screen readers get the whole text at once, from a hidden copy, while the letters animate.
      var copy = document.createElement("div");
      copy.className = "sr-only";
      copy.textContent = root.innerText;
      ["role", "aria-live"].forEach(function (a) { if (root.hasAttribute(a)) copy.setAttribute(a, root.getAttribute(a)); });
      root.parentNode.insertBefore(copy, root);
      root.setAttribute("aria-hidden", "true");
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
          root.removeAttribute("aria-hidden"); copy.remove();
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


// Access: read aloud, calm mode, bigger text, strong contrast. Settings live in this
// browser only (localStorage). Read aloud uses the device's own voice.
(function (T) {
  var html = document.documentElement, KEY = "tripwire-access";
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } }
  function save(settings) { try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch (e) {} }
  function apply(settings) {
    ["calm", "big", "contrast", "read"].forEach(function (k) { html.classList.toggle("a11y-" + k, !!settings[k]); });
    var readPage = document.getElementById("read-page");
    if (readPage) readPage.hidden = !settings.read;
  }

  // ---- speaking ----
  var queue = [], speaking = false;
  var LANGS = { en: "en-GB", hi: "hi-IN", st: "st-ZA" };
  T.stopSpeaking = function () {
    queue = []; speaking = false;
    if (window.speechSynthesis) speechSynthesis.cancel();
  };
  function deviceVoice(text, done) {
    if (!window.speechSynthesis) { done(); return; }
    var u = new SpeechSynthesisUtterance(text), lang = LANGS[html.lang] || html.lang;
    u.lang = lang;
    var voice = speechSynthesis.getVoices().filter(function (v) { return v.lang && v.lang.indexOf(lang.slice(0, 2)) === 0; })[0];
    if (voice) u.voice = voice;
    u.onend = u.onerror = done;
    speechSynthesis.speak(u);
  }
  function next() {
    if (!queue.length) { speaking = false; return; }
    speaking = true;
    var text = queue.shift();
    deviceVoice(text, next);
  }
  function chunks(text) {  // pieces of up to ~600 characters, split at sentence ends
    var out = [], piece = "";
    (text.match(/[^.!?]+[.!?]*\s*/g) || [text]).forEach(function (s) {
      if ((piece + s).length > 600 && piece) { out.push(piece.trim()); piece = ""; }
      piece += s;
    });
    if (piece.trim()) out.push(piece.trim());
    return out;
  }
  T.speak = function (text) {
    T.stopSpeaking();
    text = (text || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    queue = chunks(text);
    next();
  };
  T.readPage = function () {
    var main = document.getElementById("main"), parts = [];
    if (!main) return;
    main.querySelectorAll("h1, h2, h3, p, li, dt, dd, td, th, blockquote, figcaption, label").forEach(function (el) {
      if (el.closest("[aria-hidden=true], .sr-only, script, style, dialog") || !el.offsetParent) return;
      if (el.parentElement && el.parentElement.closest("p, li, dd, td, th, blockquote, label")) return;  // read once
      var text = el.innerText.trim();
      if (text) parts.push(text);
    });
    T.speak(parts.join(". "));
  };

  document.addEventListener("DOMContentLoaded", function () {
    var settings = load(), panel = document.getElementById("access-panel"), welcome = document.getElementById("access-welcome");
    apply(settings);
    document.querySelectorAll("[data-setting]").forEach(function (box) {
      box.checked = !!settings[box.dataset.setting];
      box.addEventListener("change", function () {
        settings[box.dataset.setting] = box.checked; save(settings); apply(settings);
        if (box.dataset.setting === "read") { box.checked ? T.speak("Read aloud is on. Click any text to hear it.") : T.stopSpeaking(); }
      });
    });
    var button = document.getElementById("access-button");
    if (button && panel) button.addEventListener("click", function () { panel.showModal(); });
    var readPage = document.getElementById("read-page");
    if (readPage) readPage.addEventListener("click", function () {
      if (speaking) { T.stopSpeaking(); return; }
      T.readPage();
    });
    // The first visit: ask once whether to read aloud.
    if (welcome && !settings.asked && !document.querySelector("dialog[open]")) {
      welcome.showModal();
      welcome.querySelectorAll("[data-welcome]").forEach(function (b) {
        b.addEventListener("click", function () {
          var choice = b.dataset.welcome;
          settings.asked = true;
          if (choice === "read") settings.read = true;
          if (choice === "calm") settings.calm = true;
          save(settings); apply(settings); welcome.close();
          if (choice === "read") T.speak(document.title + ". Read aloud is on. Click any text to hear it, or press Read this page aloud.");
        });
      });
    }
    // Read aloud: click any text to hear it; links and buttons are read when they get keyboard focus.
    document.addEventListener("click", function (event) {
      if (!html.classList.contains("a11y-read")) return;
      if (event.target.closest("a, button, input, select, textarea, summary, dialog")) return;
      var block = event.target.closest("h1, h2, h3, p, li, dt, dd, td, th, blockquote, figcaption, label, .screen-line, .stat-tile");
      if (block) T.speak(block.innerText);
    });
    document.addEventListener("focusin", function (event) {
      if (!html.classList.contains("a11y-read")) return;
      var el = event.target.closest("a, button, input, select, textarea");
      if (!el || el.id === "read-page") return;
      var label = el.getAttribute("aria-label") || (el.labels && el.labels[0] && el.labels[0].innerText) || el.innerText || el.placeholder || "";
      if (label.trim()) T.speak(label);
    });
    // Try to read the page as it opens (browsers may wait for a click before allowing sound).
    if (settings.read && settings.asked && document.querySelector("#main h1")) {
      setTimeout(function () { T.speak(document.querySelector("#main h1").innerText); }, 400);
    }
  });
})(window.Tripwire);
