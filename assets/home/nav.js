/* Header dropdowns on the public pages: hover (mouse) or click opens one, any other closes, Escape and an outside click close all. */
(function () {
  "use strict";
  var all = Array.prototype.slice.call(document.querySelectorAll(".navlinks .nav-dropdown details"));
  if (!all.length) { return; }
  var fine = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  var timers = new WeakMap();
  function closeOthers(keep) { all.forEach(function (d) { if (d !== keep && d.open) { d.open = false; } }); }
  all.forEach(function (d) {
    d.addEventListener("toggle", function () { if (d.open) { closeOthers(d); } });
    if (fine) {
      var li = d.parentNode;
      li.addEventListener("pointerenter", function () { window.clearTimeout(timers.get(d)); timers.set(d, window.setTimeout(function () { closeOthers(d); d.open = true; }, 70)); });
      li.addEventListener("pointerleave", function () { window.clearTimeout(timers.get(d)); timers.set(d, window.setTimeout(function () { d.open = false; }, 180)); });
    }
    d.addEventListener("keydown", function (e) { if (e.key === "Escape") { d.open = false; var s = d.querySelector("summary"); if (s) { s.focus(); } } });
  });
  document.addEventListener("click", function (e) { if (!e.target.closest || !e.target.closest(".navlinks .nav-dropdown")) { closeOthers(null); } });
})();
