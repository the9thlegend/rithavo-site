/* Rithavo PWA layer. One small vanilla script, loaded on every page. Same origin only: it contacts no other host.

   - registers /sw.js and shows an "update available" prompt (never swaps a page under someone without being asked)
   - marks the document .is-pwa when the site runs as an installed app
   - shows an offline notice and a reconnect notice
   - the install control (Android/desktop prompt, or Add to Home Screen steps on iOS)
   - the mobile bottom navigation, organised by Rithavo's three pillars
   - RithavoPWA.clearPrivateState(), called on sign-out so no cache outlives the session

   Nothing here reads, stores or sends any account data. */
(function () {
  "use strict";

  var doc = document, root = doc.documentElement;

  function isStandalone() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || window.navigator.standalone === true;
  }
  function isIos() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }
  if (isStandalone()) root.classList.add("is-pwa");

  function el(tag, attrs, children) {
    var n = doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "html") n.innerHTML = attrs[k];            // only ever used with the static strings in this file
      else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }

  /* ----------------------------------------------------------- toast and offline notice */
  var toastHost = null;
  function host() {
    if (!toastHost) {
      toastHost = el("div", { "class": "pwa-toasts", "aria-live": "polite", role: "status" });
      doc.body.appendChild(toastHost);
    }
    return toastHost;
  }
  function toast(message, action) {
    var t = el("div", { "class": "pwa-toast" }, [el("span", { text: message })]);
    if (action) {
      var b = el("button", { type: "button", "class": "pwa-toast-btn", text: action.label });
      b.addEventListener("click", function () { action.run(); t.remove(); });
      t.appendChild(b);
    }
    var x = el("button", { type: "button", "class": "pwa-toast-close", "aria-label": "Dismiss", text: "×" });
    x.addEventListener("click", function () { t.remove(); });
    t.appendChild(x);
    host().appendChild(t);
    return t;
  }

  var offlineBar = null;
  function setOffline(offline) {
    if (offline && !offlineBar) {
      offlineBar = el("div", { "class": "pwa-offline-bar", role: "status", text: "You're offline. Some things won't load or save until you reconnect." });
      doc.body.appendChild(offlineBar);
    } else if (!offline && offlineBar) {
      offlineBar.remove(); offlineBar = null;
      var t = toast("Back online.");
      setTimeout(function () { t.remove(); }, 2500);
    }
  }
  window.addEventListener("offline", function () { setOffline(true); });
  window.addEventListener("online", function () { setOffline(false); });

  /* ----------------------------------------------------------- service worker */
  var reloadingForUpdate = false;
  function promptUpdate(worker) {
    toast("A new version of Rithavo is ready.", {
      label: "Refresh",
      run: function () { reloadingForUpdate = true; worker.postMessage({ type: "SKIP_WAITING" }); }
    });
  }
  function registerWorker() {
    if (!("serviceWorker" in navigator)) return;
    if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function (reg) {
      if (reg.waiting && navigator.serviceWorker.controller) promptUpdate(reg.waiting);
      reg.addEventListener("updatefound", function () {
        var w = reg.installing;
        if (!w) return;
        w.addEventListener("statechange", function () {
          if (w.state === "installed" && navigator.serviceWorker.controller) promptUpdate(w);
        });
      });
    }).catch(function () { /* the site works without a worker */ });
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (reloadingForUpdate) window.location.reload();
    });
  }
  window.addEventListener("load", registerWorker);

  /* ----------------------------------------------------------- sign-out hygiene */
  function clearPrivateState() {
    var jobs = [];
    if (window.caches && caches.keys) {
      jobs.push(caches.keys().then(function (names) {
        // the shell cache is public and identical for everyone, so it stays; pages and assets visited signed in are removed
        return Promise.all(names.filter(function (n) { return n.indexOf("rithavo-") === 0 && n.indexOf("rithavo-shell-") !== 0; }).map(function (n) { return caches.delete(n); }));
      }));
    }
    if ("serviceWorker" in navigator && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({ type: "CLEAR_CACHES" });
    }
    // Never block sign-out for long: the session cookie is already cleared by the server.
    return Promise.race([Promise.all(jobs), new Promise(function (r) { setTimeout(r, 1500); })]).catch(function () {});
  }

  /* ----------------------------------------------------------- install control */
  var deferredPrompt = null;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    mountInstall();
  });
  window.addEventListener("appinstalled", function () { deferredPrompt = null; mountInstall(); });

  function mountInstall() {
    var slots = doc.querySelectorAll("[data-pwa-install]");
    slots.forEach(function (slot) {
      slot.innerHTML = "";
      if (isStandalone()) { slot.hidden = true; return; }
      var title = el("p", { "class": "label", text: "Rithavo app" });
      var body = null;
      if (deferredPrompt) {
        var btn = el("button", { type: "button", "class": "btn btn-primary", text: "Install the Rithavo app" });
        btn.addEventListener("click", function () {
          var p = deferredPrompt; deferredPrompt = null;
          p.prompt();
          p.userChoice.finally(mountInstall);
        });
        body = el("div", {}, [el("p", { "class": "body-md", text: "Add Rithavo to your home screen to open it like an app." }), btn]);
      } else if (isIos()) {
        body = el("p", { "class": "body-md", text: "To install on iPhone or iPad: open this page in Safari, tap the Share button, then choose Add to Home Screen." });
      } else {
        body = el("p", { "class": "body-md", text: "To install, use your browser's menu and choose Install app or Add to Home screen." });
      }
      slot.appendChild(title); slot.appendChild(body); slot.hidden = false;
    });
  }

  /* ----------------------------------------------------------- bottom navigation (three pillars) */
  var ICONS = {
    home: '<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4v-5h-6v5H5a1 1 0 0 1-1-1z"/>',
    career: '<path d="M5 20V10M12 20V4M19 20v-7"/>',
    systems: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    growth: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    account: '<circle cx="12" cy="8" r="3.6"/><path d="M5 20c1-4 4-6 7-6s6 2 7 6"/>'
  };
  function svg(name) {
    return '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + ICONS[name] + "</svg>";
  }

  var CAREER_LINKS = [
    ["Profile", "/profile/"],
    ["Career Intelligence", "/career-intelligence/"],
    ["Application Diagnosis", "/application-diagnosis/"],
    ["Resumes", "/resumes/"],
    ["Explore", "/home/"],
    ["Mentorship", "/mentorship/"],
    ["History", "/history/"]
  ];
  var CAREER_PATHS = ["/profile", "/career-intelligence", "/application-diagnosis", "/resumes", "/mentorship", "/history", "/career"];
  var PUBLIC_PILLAR_PATHS = ["/systems", "/growth", "/career"];

  function activeKey() {
    var p = location.pathname.replace(/\/+$/, "") || "/";
    if (p === "/home") return "home";
    if (p === "/systems") return "systems";
    if (p === "/growth") return "growth";
    if (p === "/account") return "account";
    for (var i = 0; i < CAREER_PATHS.length; i++) if (p === CAREER_PATHS[i] || p.indexOf(CAREER_PATHS[i] + "/") === 0) return "career";
    return "";
  }

  function wantsBottomNav() {
    var p = location.pathname.replace(/\/+$/, "") || "/";
    var onAppPage = !!doc.getElementById("app-topbar");
    var onPillarPage = PUBLIC_PILLAR_PATHS.some(function (x) { return p === x || p.indexOf(x + "/") === 0; });
    return (onAppPage && window.innerWidth <= 720) || (root.classList.contains("is-pwa") && onPillarPage && window.innerWidth <= 960);
  }

  var nav = null, sheet = null, lastFocus = null;

  function link(key, label, href, sub) {
    var a = el("a", { href: href, "data-key": key, html: svg(key) });
    a.appendChild(el("span", { "class": "pwa-nav-label", text: label }));
    if (sub) a.appendChild(el("small", { text: sub }));
    return a;
  }

  function buildNav() {
    nav = el("nav", { "class": "pwa-bottomnav", "aria-label": "Primary" });
    nav.appendChild(link("home", "Home", "/home/"));
    var careerBtn = el("button", { type: "button", "data-key": "career", "aria-haspopup": "dialog", "aria-expanded": "false", html: svg("career") });
    careerBtn.appendChild(el("span", { "class": "pwa-nav-label", text: "Career" }));
    careerBtn.addEventListener("click", function () { openSheet(careerBtn); });
    nav.appendChild(careerBtn);
    nav.appendChild(link("systems", "Systems", "/systems/", "In development"));
    nav.appendChild(link("growth", "Growth", "/growth/", "In development"));
    nav.appendChild(link("account", "Account", "/account/"));
    var key = activeKey();
    Array.prototype.forEach.call(nav.querySelectorAll("[data-key]"), function (n) {
      if (n.getAttribute("data-key") === key) n.setAttribute("aria-current", "page");
    });

    sheet = el("div", { "class": "pwa-sheet", hidden: "" });
    var backdrop = el("div", { "class": "pwa-sheet-backdrop" });
    var panel = el("div", { "class": "pwa-sheet-panel", role: "dialog", "aria-modal": "true", "aria-labelledby": "pwa-sheet-title", tabindex: "-1" });
    panel.appendChild(el("h2", { id: "pwa-sheet-title", "class": "pwa-sheet-title", text: "Rithavo Career" }));
    var list = el("ul", { "class": "pwa-sheet-list" });
    CAREER_LINKS.forEach(function (item) {
      var a = el("a", { href: item[1], text: item[0] });
      if (location.pathname === item[1] && item[0] !== "Explore") a.setAttribute("aria-current", "page");
      list.appendChild(el("li", {}, [a]));
    });
    panel.appendChild(list);
    var close = el("button", { type: "button", "class": "btn btn-ghost pwa-sheet-close", text: "Close" });
    panel.appendChild(close);
    sheet.appendChild(backdrop); sheet.appendChild(panel);
    backdrop.addEventListener("click", closeSheet);
    close.addEventListener("click", closeSheet);
    panel.addEventListener("keydown", trapFocus);
    doc.body.appendChild(nav); doc.body.appendChild(sheet);
  }

  function focusables() { return sheet.querySelectorAll("a[href], button:not([disabled])"); }
  function trapFocus(e) {
    if (e.key === "Escape") { closeSheet(); return; }
    if (e.key !== "Tab") return;
    var f = focusables(); if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  function openSheet(trigger) {
    lastFocus = trigger; trigger.setAttribute("aria-expanded", "true");
    sheet.hidden = false; root.classList.add("pwa-sheet-open");
    var first = sheet.querySelector("a[href]"); if (first) first.focus();
  }
  function closeSheet() {
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true; root.classList.remove("pwa-sheet-open");
    if (lastFocus) { lastFocus.setAttribute("aria-expanded", "false"); lastFocus.focus(); }
  }

  function syncNav() {
    var want = wantsBottomNav();
    if (want && !nav) buildNav();
    if (nav) {
      nav.hidden = !want;
      if (!want) closeSheet();
      doc.body.classList.toggle("has-bottom-nav", want);
      root.style.setProperty("--rv-bottomnav-h", want ? "calc(64px + env(safe-area-inset-bottom, 0px))" : "0px");
    }
  }

  function ready(fn) { if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", fn); else fn(); }
  ready(function () {
    syncNav(); mountInstall();
    if (!navigator.onLine) setOffline(true);
    window.addEventListener("resize", syncNav);
    // pages build #app-topbar asynchronously after the session check; re-evaluate once it exists
    window.addEventListener("load", syncNav);
  });

  window.RithavoPWA = { clearPrivateState: clearPrivateState, isStandalone: isStandalone };
})();
