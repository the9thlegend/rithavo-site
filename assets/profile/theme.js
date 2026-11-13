/* Light / Dark / System theme preference for the profile pages (decisions D-G and U3).
   - "system" (the default) removes data-theme, so the Bearing tokens follow prefers-color-scheme.
   - The choice is remembered on this device only (localStorage). It is never sent anywhere and never stored on a server.
   - Storage can be blocked or throw (private windows, blocked site data): every access is guarded and the page then simply
     follows the system setting.
   Load this as the first script in <head> so the theme is applied before first paint. */
(function () {
  "use strict";
  var KEY = "rithavo-theme";
  var root = document.documentElement;

  function read() {
    try {
      var v = window.localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : "system";
    } catch (e) {
      return "system";
    }
  }

  function syncMeta() {
    try {
      var canvas = getComputedStyle(root).getPropertyValue("--c-canvas").trim();
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta && canvas) { meta.setAttribute("content", canvas); }
    } catch (e) { /* cosmetic only */ }
  }

  function apply(choice) {
    if (choice === "light" || choice === "dark") { root.setAttribute("data-theme", choice); }
    else { root.removeAttribute("data-theme"); }
    syncMeta();
    var buttons = document.querySelectorAll("[data-theme-choice]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].getAttribute("data-theme-choice") === choice ? "true" : "false");
    }
  }

  function set(choice) {
    var value = choice === "light" || choice === "dark" ? choice : "system";
    try {
      if (value === "system") { window.localStorage.removeItem(KEY); } else { window.localStorage.setItem(KEY, value); }
    } catch (e) { /* the choice still applies for this page view */ }
    apply(value);
  }

  window.RithavoTheme = { get: read, set: set, apply: apply };
  apply(read());

  document.addEventListener("DOMContentLoaded", function () {
    apply(read());
    var buttons = document.querySelectorAll("[data-theme-choice]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function (event) { set(event.currentTarget.getAttribute("data-theme-choice")); });
    }
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () { if (read() === "system") { syncMeta(); } };
      if (mq.addEventListener) { mq.addEventListener("change", onChange); }
    }
  });
})();
