/* Motion and tabs for the front pages. Everything degrades to a static page: without JS the tab panels are all visible (the first is
   the only one hidden by the script), and with prefers-reduced-motion nothing animates. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.documentElement.classList.add("hx-js");
  var fine = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  // stagger index for children of .hx-stagger
  document.querySelectorAll(".hx-stagger").forEach(function (group) {
    Array.prototype.forEach.call(group.children, function (child, i) { child.style.setProperty("--i", i); });
  });

  // scroll-triggered reveals
  var targets = document.querySelectorAll(".hx-stagger, .hx-bars, .hx-flow, .hx-product");
  function show(el) { el.classList.add("hx-visible"); if (el.classList.contains("hx-flow")) { el.style.setProperty("--hx-p", 1); } }
  if (reduce || !("IntersectionObserver" in window)) { targets.forEach(show); }
  else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { show(e.target); io.unobserve(e.target); } });
    }, { threshold: 0.18, rootMargin: "0px 0px -6% 0px" });
    targets.forEach(function (t) { io.observe(t); });
  }

  // pointer glow on cards and items; gentle 3D tilt on the two doors (mouse only)
  if (fine && !reduce) {
    document.querySelectorAll(".hx-card, .hx-item").forEach(function (card) {
      var tilt = card.classList.contains("hx-card");
      card.addEventListener("pointermove", function (ev) {
        var r = card.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width, y = (ev.clientY - r.top) / r.height;
        card.style.setProperty("--hx-mx", (x * 100).toFixed(1) + "%");
        card.style.setProperty("--hx-my", (y * 100).toFixed(1) + "%");
        if (tilt) { card.style.setProperty("--hx-rx", ((x - 0.5) * 7).toFixed(2) + "deg"); card.style.setProperty("--hx-ry", ((0.5 - y) * 5).toFixed(2) + "deg"); }
      });
      card.addEventListener("pointerleave", function () { card.style.setProperty("--hx-rx", "0deg"); card.style.setProperty("--hx-ry", "0deg"); });
    });
  }

  // tabs (WAI-ARIA tablist pattern, arrow keys, Home/End)
  document.querySelectorAll("[data-hx-tabs]").forEach(function (root) {
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (t) { return document.getElementById(t.getAttribute("aria-controls")); });
    function select(i, focus) {
      tabs.forEach(function (t, k) {
        var on = k === i;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        if (panels[k]) { panels[k].hidden = !on; }
      });
      if (focus) { tabs[i].focus(); }
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(i, false); });
      t.addEventListener("keydown", function (ev) {
        var n = tabs.length, to = -1;
        if (ev.key === "ArrowRight") { to = (i + 1) % n; } else if (ev.key === "ArrowLeft") { to = (i - 1 + n) % n; }
        else if (ev.key === "Home") { to = 0; } else if (ev.key === "End") { to = n - 1; }
        if (to >= 0) { ev.preventDefault(); select(to, true); }
      });
    });
    select(0, false);
  });
})();
