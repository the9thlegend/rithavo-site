/* Shared helpers for the member profile pages. No inline script, no eval, and no HTML-string insertion: every value from
   the server is inserted with textContent, so profile text can never become markup. */
(function () {
  "use strict";
  var SVG_NS = "http://www.w3.org/2000/svg";

  var ICONS = {
    spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M18 16l.8 2.2L21 19l-2.2.8L18 22l-.8-2.2L15 19l2.2-.8z",
    layers: "M12 3l9 5-9 5-9-5 9-5z M3 13l9 5 9-5 M3 17.5l9 5 9-5",
    target: "M12 21a9 9 0 100-18 9 9 0 000 18z M12 16.5a4.5 4.5 0 100-9 4.5 4.5 0 000 9z M12 12.8a.8.8 0 100-1.6.8.8 0 000 1.6z",
    users: "M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7z M2.5 20c0-3.3 2.9-6 6.5-6s6.5 2.7 6.5 6 M16.5 4.4a3.5 3.5 0 010 6.6 M18 14.3c2 .8 3.5 2.7 3.5 5.7",
    chart: "M4 20V10 M10 20V4 M16 20v-8 M22 20H2",
    pin: "M12 21s7-6.2 7-11.5A7 7 0 005 9.5C5 14.8 12 21 12 21z M12 12a2.5 2.5 0 100-5 2.5 2.5 0 000 5z",
    briefcase: "M3 8h18v11H3z M8 8V5.5A1.5 1.5 0 019.5 4h5A1.5 1.5 0 0116 5.5V8 M3 13h18",
    compass: "M12 21a9 9 0 100-18 9 9 0 000 18z M15.8 8.2l-2 5.6-5.6 2 2-5.6z",
    check: "M5 12.5l4.5 4.5L19 7.5",
    share: "M18 8a3 3 0 100-6 3 3 0 000 6z M6 15a3 3 0 100-6 3 3 0 000 6z M18 22a3 3 0 100-6 3 3 0 000 6z M8.6 13.5l6.8 4 M15.4 6.5l-6.8 4",
    userplus: "M10 11a4 4 0 100-8 4 4 0 000 8z M3 21c0-3.9 3.1-7 7-7 1.4 0 2.7.4 3.8 1.1 M19 8v6 M16 11h6",
    usercheck: "M10 11a4 4 0 100-8 4 4 0 000 8z M3 21c0-3.9 3.1-7 7-7 1.4 0 2.7.4 3.8 1.1 M16 17.5l2.2 2.2L22 15.5",
    link: "M10 14a4.5 4.5 0 006.4 0l3-3a4.5 4.5 0 00-6.4-6.4l-1 1 M14 10a4.5 4.5 0 00-6.4 0l-3 3a4.5 4.5 0 006.4 6.4l1-1",
    edit: "M4 20h4L19 9l-4-4L4 16z M13.5 6.5l4 4",
    card: "M3 6h18v12H3z M3 10h18 M7 15h4",
    shield: "M12 3l8 3v6c0 4.6-3.3 8.3-8 9-4.7-.7-8-4.4-8-9V6z M8.5 12l2.5 2.5 4.5-5"
  };
  var CHIP_ICONS = ["layers", "target", "users", "chart"];

  function svg(name) {
    var s = document.createElementNS(SVG_NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.8");
    s.setAttribute("stroke-linecap", "round");
    s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("focusable", "false");
    var p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", ICONS[name] || ICONS.spark);
    s.appendChild(p);
    return s;
  }

  /* el("div", {class: "x", "aria-label": "y"}, [child, "text"]) */
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === false || value === null || value === undefined) { return; }
        if (key === "text") { node.textContent = value; }
        else { node.setAttribute(key, value === true ? "" : String(value)); }
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) { return; }
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

  /* JSON request to this same origin. Resolves { status, data, headers }; never rejects (a network failure is status 0). */
  function api(method, url, body) {
    var init = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" }, cache: "no-store" };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    return fetch(url, init).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        return { status: res.status, data: data, headers: res.headers };
      });
    }).catch(function () { return { status: 0, data: null, headers: null }; });
  }

  var toastTimer = null;
  function toast(message, kind) {
    var host = document.getElementById("pf-toast");
    if (!host) { return; }
    host.textContent = message;
    host.setAttribute("data-kind", kind || "ok");
    host.setAttribute("data-show", "1");
    if (toastTimer) { window.clearTimeout(toastTimer); }
    toastTimer = window.setTimeout(function () { host.removeAttribute("data-show"); }, 4200);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
    }
    return Promise.resolve(false);
  }

  /* Show the action bar only while no inline action surface is on screen (one action surface at a time). */
  function watchActionBar() {
    var surfaces = document.querySelectorAll("[data-action-surface]");
    if (!surfaces.length || !("IntersectionObserver" in window)) { return; }
    var visible = new Set();
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { visible.add(e.target); } else { visible.delete(e.target); } });
      document.body.classList.toggle("bar-on", visible.size === 0);
    }, { rootMargin: "0px 0px -84px 0px", threshold: 0 });
    surfaces.forEach(function (s) { io.observe(s); });
  }

  window.PF = { ICONS: ICONS, CHIP_ICONS: CHIP_ICONS, svg: svg, el: el, clear: clear, api: api, toast: toast, copyText: copyText, watchActionBar: watchActionBar };
})();
