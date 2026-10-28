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
