/* Front pages v2: scroll-driven tone changes, line reveals, mini-nav and full-screen menu, one-at-a-time hover menus, starfield,
   drifting words, timelines, tilt and magnetic buttons. Everything degrades to a readable static page: without JS nothing is hidden,
   and with prefers-reduced-motion nothing moves. */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var fine = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  root.classList.add("x-js");
  function $all(sel, ctx) { return Array.prototype.slice.call((ctx || doc).querySelectorAll(sel)); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  /* ---------- reveals: lines and blocks rise when their group enters ---------- */
  $all("[data-x]").forEach(function (g) {
    $all(".x-ln", g).forEach(function (l, i) { l.style.setProperty("--d", i); });
    $all("[data-x-reveal]", g).forEach(function (l, i) { l.style.setProperty("--d", i); });
  });
  // a reveal element with no [data-x] ancestor is its own group, so nothing can be left hidden
  var groups = $all("[data-x]").filter(function (n) { return !n.classList.contains("x-mark"); }).concat($all("[data-x-reveal]").filter(function (n) { return !n.closest("[data-x]") || n.hasAttribute("data-x"); }));
  groups = groups.filter(function (n, i) { return groups.indexOf(n) === i; });
  if (reduce || !("IntersectionObserver" in window)) { groups.forEach(function (g) { g.classList.add("x-in"); }); }
  else {
    var rio = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("x-in"); rio.unobserve(e.target); } }); }, { threshold: 0.04, rootMargin: "0px 0px 12% 0px" });
    groups.forEach(function (g) { rio.observe(g); });
  }

  /* ---------- tone: the whole background changes colour as sections arrive ---------- */
  var tone = "void";
  function setTone(t) { if (t && root.getAttribute("data-tone") !== t) { root.setAttribute("data-tone", t); } }
  if ("IntersectionObserver" in window) {
    var tio = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { tone = e.target.getAttribute("data-tone"); setTone(tone); } }); }, { rootMargin: "-46% 0px -46% 0px" });
    $all("[data-tone]").forEach(function (s) { tio.observe(s); });
  }
  $all("[data-tone-hover]").forEach(function (el) {
    el.addEventListener("pointerenter", function () { setTone(el.getAttribute("data-tone-hover")); });
    el.addEventListener("pointerleave", function () { setTone(tone); });
    el.addEventListener("focus", function () { setTone(el.getAttribute("data-tone-hover")); });
    el.addEventListener("blur", function () { setTone(tone); });
  });

  /* ---------- scroll-linked values: page progress, timelines, drifting words, footer wordmark, mini-nav ---------- */
  var progress = doc.querySelector(".x-progress"), dock = doc.querySelector(".x-dock");
  var lines = $all(".x-line");
  var ticking = false;
  function frac(el) { var r = el.getBoundingClientRect(), h = window.innerHeight; return clamp((h - r.top) / (h + r.height), 0, 1); }
  function onScroll() {
    ticking = false;
    var max = Math.max(1, root.scrollHeight - window.innerHeight), y = window.scrollY || 0;
    if (progress) { progress.style.setProperty("--p", (clamp(y / max, 0, 1) * 100).toFixed(2) + "%"); }
    if (dock) { dock.classList.toggle("is-on", y > window.innerHeight * 0.75); }
    if (reduce) { return; }
    lines.forEach(function (l) {
      var r = l.getBoundingClientRect(), h = window.innerHeight, p = clamp((h * 0.86 - r.top) / (r.height + h * 0.1), 0, 1);
      l.style.setProperty("--p", p.toFixed(3));
      $all(".x-stop", l).forEach(function (s, i, all) { s.classList.toggle("on", p >= (i + 0.4) / all.length); });
    });
  }
  function req() { if (!ticking) { ticking = true; window.requestAnimationFrame(onScroll); } }
  window.addEventListener("scroll", req, { passive: true }); window.addEventListener("resize", req); onScroll();
  if (reduce) { lines.forEach(function (l) { l.style.setProperty("--p", 1); $all(".x-stop", l).forEach(function (s) { s.classList.add("on"); }); }); }

  /* ---------- header menus: hover or click, one open at a time, never overlapping ---------- */
  var dds = $all(".x-dd");
  function closeDd(except) { dds.forEach(function (d) { if (d !== except) { d.classList.remove("is-open"); var b = d.querySelector(".x-dd-btn"); if (b) { b.setAttribute("aria-expanded", "false"); } } }); }
  dds.forEach(function (d) {
    var btn = d.querySelector(".x-dd-btn");
    d.addEventListener("pointerenter", function () { closeDd(d); });
    btn.addEventListener("click", function () { var open = !d.classList.contains("is-open"); closeDd(d); d.classList.toggle("is-open", open); btn.setAttribute("aria-expanded", open ? "true" : "false"); });
    d.addEventListener("keydown", function (e) { if (e.key === "Escape") { d.classList.remove("is-open"); btn.setAttribute("aria-expanded", "false"); btn.focus(); } });
  });
  doc.addEventListener("click", function (e) { if (!e.target.closest || !e.target.closest(".x-dd")) { closeDd(null); } });

  /* ---------- full-screen menu ---------- */
  var menu = doc.querySelector(".x-menu"), lastFocus = null;
  function setMenu(open) {
    if (!menu) { return; }
    menu.classList.toggle("is-open", open); menu.setAttribute("aria-hidden", open ? "false" : "true"); root.style.overflow = open ? "hidden" : "";
    $all("[data-x-open-menu]").forEach(function (b) { b.setAttribute("aria-expanded", open ? "true" : "false"); });
    if (open) { lastFocus = doc.activeElement; window.setTimeout(function () { var c = menu.querySelector(".x-menu-close"); if (c) { c.focus(); } }, 120); }
    else if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
  }
  $all("[data-x-open-menu]").forEach(function (b) { b.addEventListener("click", function () { setMenu(true); }); });
  $all("[data-x-close-menu]").forEach(function (b) { b.addEventListener("click", function () { setMenu(false); }); });
  if (menu) {
    $all("a", menu).forEach(function (a) { a.addEventListener("click", function () { setMenu(false); }); });
    menu.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { setMenu(false); return; }
      if (e.key !== "Tab") { return; }
      var f = $all("a[href], button:not([disabled])", menu).filter(function (n) { return n.offsetParent !== null; });
      if (!f.length) { return; }
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    $all(".x-menu-main a", menu).forEach(function (a, i) { a.style.setProperty("--i", i); });
  }

  /* ---------- pointer effects (mouse only) ---------- */
  if (fine && !reduce) {
    $all(".x-door, .x-card").forEach(function (c) {
      var tilt = c.classList.contains("x-door");
      c.addEventListener("pointermove", function (e) {
        var r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        c.style.setProperty("--x-mx", (x * 100).toFixed(1) + "%"); c.style.setProperty("--x-my", (y * 100).toFixed(1) + "%");
        if (tilt) { c.style.setProperty("--x-rx", ((x - 0.5) * 6).toFixed(2) + "deg"); c.style.setProperty("--x-ry", ((0.5 - y) * 4).toFixed(2) + "deg"); }
      });
      c.addEventListener("pointerleave", function () { c.style.setProperty("--x-rx", "0deg"); c.style.setProperty("--x-ry", "0deg"); });
    });
    $all(".x-btn").forEach(function (b) {
      b.addEventListener("pointermove", function (e) { var r = b.getBoundingClientRect(); b.style.transform = "translate(" + ((e.clientX - r.left - r.width / 2) * 0.14).toFixed(1) + "px," + ((e.clientY - r.top - r.height / 2) * 0.22).toFixed(1) + "px)"; });
      b.addEventListener("pointerleave", function () { b.style.transform = ""; });
    });
    var stack = doc.querySelector(".x-stack");
    if (stack) { stack.addEventListener("pointermove", function (e) { var r = stack.getBoundingClientRect(); stack.style.setProperty("--x-sx", (((e.clientX - r.left) / r.width - 0.5) * 22).toFixed(1) + "deg"); }); stack.addEventListener("pointerleave", function () { stack.style.setProperty("--x-sx", "0deg"); }); }
  }
  var stackEl = doc.querySelector(".x-stack");
  if (stackEl && "IntersectionObserver" in window && !reduce) { var sio = new IntersectionObserver(function (es) { es.forEach(function (e) { stackEl.classList.toggle("is-wide", e.isIntersecting); }); }, { threshold: 0.5 }); sio.observe(stackEl); }

  /* ---------- the four moves cycle on their own ---------- */
  $all(".x-moves").forEach(function (m) {
    var items = $all(".x-move", m), i = 0, timer = null;
    function show(k) { items.forEach(function (n, j) { n.classList.toggle("on", j === k); }); }
    function run() { if (timer || reduce) { return; } timer = window.setInterval(function () { i = (i + 1) % items.length; show(i); }, 2400); }
    function stop() { if (timer) { window.clearInterval(timer); timer = null; } }
    show(0);
    items.forEach(function (n, j) { n.addEventListener("pointerenter", function () { stop(); i = j; show(j); }); });
    m.addEventListener("pointerleave", run);
    if ("IntersectionObserver" in window) { new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { run(); } else { stop(); } }); }, { threshold: 0.4 }).observe(m); } else { run(); }
  });

  /* ---------- tabs ---------- */
  $all("[data-x-tabs]").forEach(function (rootEl) {
    var tabs = $all('[role="tab"]', rootEl), panels = tabs.map(function (t) { return doc.getElementById(t.getAttribute("aria-controls")); });
    function select(i, focus) { tabs.forEach(function (t, k) { var on = k === i; t.setAttribute("aria-selected", on ? "true" : "false"); t.tabIndex = on ? 0 : -1; if (panels[k]) { panels[k].hidden = !on; } }); if (focus) { tabs[i].focus(); } }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(i, false); });
      t.addEventListener("keydown", function (e) { var n = tabs.length, to = -1; if (e.key === "ArrowRight") { to = (i + 1) % n; } else if (e.key === "ArrowLeft") { to = (i - 1 + n) % n; } else if (e.key === "Home") { to = 0; } else if (e.key === "End") { to = n - 1; } if (to >= 0) { e.preventDefault(); select(to, true); } });
    });
    select(0, false);
  });

  /* ---------- footer wordmark: plays every time it comes into view, resets once it has left ---------- */
  var markIn = doc.querySelector(".x-mark");
  if (markIn) {
    if (reduce || !("IntersectionObserver" in window)) { markIn.classList.add("x-in"); }
    else { new IntersectionObserver(function (es) { es.forEach(function (e) {
      if (e.intersectionRatio >= 0.3) { markIn.classList.add("x-in"); } else if (e.intersectionRatio === 0) { markIn.classList.remove("x-in"); }
    }); }, { threshold: [0, 0.3] }).observe(markIn); }
  }

  /* ---------- Ritanya hero: the scene leans gently toward the pointer ---------- */
  var orbitEl = doc.querySelector(".x-orbit");
  if (orbitEl && fine && !reduce) {
    var scn = orbitEl.querySelector(".x-orbit-scene");
    orbitEl.addEventListener("pointermove", function (e) { var r = orbitEl.getBoundingClientRect(); scn.style.setProperty("--px", (((e.clientX - r.left) / r.width) * 2 - 1).toFixed(3)); scn.style.setProperty("--py", (((e.clientY - r.top) / r.height) * 2 - 1).toFixed(3)); });
    orbitEl.addEventListener("pointerleave", function () { scn.style.setProperty("--px", "0"); scn.style.setProperty("--py", "0"); });
  }

  /* ---------- About: words light up as you read, progress rails fill, panels glow under the pointer ---------- */
  var abWords = $all("[data-x-words]"), abProg = $all("[data-x-prog]");
  if (abProg.length) {
    abWords.forEach(function (p) {
      p._w = p.textContent.split(/\s+/).filter(Boolean).map(function (t) { var s = doc.createElement("span"); s.className = "w"; s.textContent = t + " "; return s; });
      p.textContent = ""; p._w.forEach(function (s) { p.appendChild(s); });
    });
    var abOn = !reduce;
    if (abOn) { doc.documentElement.classList.add("ab-js"); }
    var abTick = function () {
      var vh = window.innerHeight || 800;
      abProg.forEach(function (el) {
        var r = el.getBoundingClientRect();
        var pr = Math.max(0, Math.min(1, (vh * 0.82 - r.top) / (r.height * 0.7 + vh * 0.22)));
        if (!abOn) { pr = 1; }
        el.style.setProperty("--p", pr.toFixed(3));
        if (el._w) { var lit = Math.round(pr * el._w.length); el._w.forEach(function (s, i) { s.classList.toggle("lit", i < lit); }); }
        if (el.classList.contains("ab-rail")) { var li = el.children; for (var k = 0; k < li.length; k++) { li[k].classList.toggle("on", pr >= (k + 0.6) / (li.length + 0.4)); } }
      });
    };
    var abBusy = false, abReq = function () { if (!abBusy) { abBusy = true; window.requestAnimationFrame(function () { abBusy = false; abTick(); }); } };
    window.addEventListener("scroll", abReq, { passive: true }); window.addEventListener("resize", abReq); abTick();
  }
  if (fine && !reduce) {
    $all(".ab-panel").forEach(function (a) {
      a.addEventListener("pointermove", function (e) { var r = a.getBoundingClientRect(); a.style.setProperty("--mx", (e.clientX - r.left).toFixed(0) + "px"); a.style.setProperty("--my", (e.clientY - r.top).toFixed(0) + "px"); });
    });
  }

  /* ---------- footer wordmark: plays every time it comes into view, resets once it has left ---------- */
  var markIn = doc.querySelector(".x-mark");
  if (markIn) {
    if (reduce || !("IntersectionObserver" in window)) { markIn.classList.add("x-in"); }
    else { new IntersectionObserver(function (es) { es.forEach(function (e) {
      if (e.intersectionRatio >= 0.3) { markIn.classList.add("x-in"); } else if (e.intersectionRatio === 0) { markIn.classList.remove("x-in"); }
    }); }, { threshold: [0, 0.3] }).observe(markIn); }
  }

  /* ---------- Ritanya hero: the scene leans gently toward the pointer ---------- */
  var orbitEl = doc.querySelector(".x-orbit");
  if (orbitEl && fine && !reduce) {
    var scn = orbitEl.querySelector(".x-orbit-scene");
    orbitEl.addEventListener("pointermove", function (e) { var r = orbitEl.getBoundingClientRect(); scn.style.setProperty("--px", (((e.clientX - r.left) / r.width) * 2 - 1).toFixed(3)); scn.style.setProperty("--py", (((e.clientY - r.top) / r.height) * 2 - 1).toFixed(3)); });
    orbitEl.addEventListener("pointerleave", function () { scn.style.setProperty("--px", "0"); scn.style.setProperty("--py", "0"); });
  }

  /* ---------- About imagery: each picture leans gently toward the pointer ---------- */
  if (fine && !reduce) {
    $all(".ab-art").forEach(function (a) {
      a.addEventListener("pointermove", function (e) { var r = a.getBoundingClientRect(); a.style.setProperty("--tx", (((e.clientX - r.left) / r.width) * 2 - 1).toFixed(3)); a.style.setProperty("--ty", (((e.clientY - r.top) / r.height) * 2 - 1).toFixed(3)); });
      a.addEventListener("pointerleave", function () { a.style.setProperty("--tx", "0"); a.style.setProperty("--ty", "0"); });
    });
  }

  /* ---------- footer wordmark: the cursor lights the letters it passes over ---------- */
  var markEl = doc.querySelector(".x-mark");
  if (markEl && fine && !reduce) {
    var letters = $all(".x-mark-word i", markEl);
    markEl.addEventListener("pointermove", function (e) { letters.forEach(function (l) { var r = l.getBoundingClientRect(); l.style.setProperty("--lx", (e.clientX - r.left).toFixed(0) + "px"); l.style.setProperty("--ly", (e.clientY - r.top).toFixed(0) + "px"); }); });
    markEl.addEventListener("pointerleave", function () { letters.forEach(function (l) { l.style.removeProperty("--lx"); l.style.removeProperty("--ly"); }); });
  }

  /* ---------- legal pages: the contents list follows the section being read ---------- */
  var toc = $all(".x-toc a");
  if (toc.length && "IntersectionObserver" in window) {
    var byId = {}; toc.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var tio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting && byId[e.target.id]) { toc.forEach(function (a) { a.classList.remove("is-on"); }); byId[e.target.id].classList.add("is-on"); } });
    }, { rootMargin: "-18% 0px -70% 0px" });
    $all(".x-doc section[id]").forEach(function (s) { tio.observe(s); });
  }

  /* ---------- starfield ---------- */
  var cv = doc.querySelector(".x-stars");
  if (cv && cv.getContext) {
    var ctx = cv.getContext("2d"), stars = [], W = 0, H = 0, mx = 0, my = 0, dpr = Math.min(window.devicePixelRatio || 1, 2), raf = 0;
    function seed() { W = cv.width = Math.floor(window.innerWidth * dpr); H = cv.height = Math.floor(window.innerHeight * dpr); var n = Math.round(Math.min(220, W * H / 11000)); stars = [];
      for (var i = 0; i < n; i++) { stars.push({ x: Math.random() * W, y: Math.random() * H, z: 0.2 + Math.random() * 0.8, r: (0.4 + Math.random() * 1.3) * dpr, a: 0.25 + Math.random() * 0.65, t: Math.random() * 6.28 }); } }
    function draw(t) {
      ctx.clearRect(0, 0, W, H); var sy = (window.scrollY || 0) * dpr;
      for (var i = 0; i < stars.length; i++) { var s = stars[i], tw = reduce ? 1 : 0.65 + 0.35 * Math.sin(t / 900 + s.t);
        var x = (s.x + mx * 22 * s.z * dpr) % W, y = ((s.y - sy * 0.12 * s.z) % H + H) % H;
        ctx.globalAlpha = s.a * tw; ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x, y, s.r * s.z, 0, 6.2832); ctx.fill(); }
      if (!reduce && !doc.hidden) { raf = window.requestAnimationFrame(draw); }
    }
    seed(); draw(0);
    window.addEventListener("resize", function () { seed(); if (reduce) { draw(0); } });
    if (fine && !reduce) { window.addEventListener("pointermove", function (e) { mx = e.clientX / window.innerWidth - 0.5; my = e.clientY / window.innerHeight - 0.5; }, { passive: true }); }
    doc.addEventListener("visibilitychange", function () { if (!doc.hidden && !reduce) { window.cancelAnimationFrame(raf); raf = window.requestAnimationFrame(draw); } });
  }
})();
