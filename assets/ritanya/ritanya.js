/* Ritanya on rithavo.com: the guide launcher and chat panel.

   What this is: a text guide that answers from Rithavo's reviewed product information and points people to real pages on
   rithavo.com. It is NOT live AI, it cannot see an account, and it gives no personal career advice. The server decides
   all of that (app/ritanya_chat.py); this file only shows what the server returns.

   Rules this file keeps (each one is checked by tests/test_ritanya_integration.py):
   - It talks only to /api/ritanya/status and /api/ritanya/chat on this origin, with credentials omitted. No cookie is read
     or set. The conversation lives in this tab's sessionStorage and is cleared by "New conversation" or by closing the tab.
   - Server text is shown with textContent only, never as HTML.
   - Every link is a same-origin relative path, validated here again before it is used.
   - With the assistant switched off on the server, /status answers {"enabled": false} (or does not exist) and nothing is shown.
   - Kill switch: the page re-asks /status every RECHECK_MS. "off" or "paused" removes the guide at once; if the server cannot be
     asked, the last known "on" is used for at most GRACE_MS, then the guide is removed and the conversation cleared.
   - A browser engine that cannot be loaded or run is never mounted (no launcher, no half-working panel).
   - The portrait stays off (AVATAR_SRC = null) until its rights are cleared; the Rithavo mark is the avatar. */
(function () {
  "use strict";

  var STATUS_URL = "/api/ritanya/status";
  var CHAT_URL = "/api/ritanya/chat";
  var KEY_STATUS = "rv_ritanya_status";
  var KEY_CONVERSATION = "rv_ritanya_conversation";
  var TIMEOUT_MS = 10000;
  var MAX_CHARS = 400;
  var MAX_SAVED_MESSAGES = 60;
  var HIDDEN_ON = ["/sign-in", "/sign-up", "/reset-password", "/onboarding", "/admin", "/offline"];
  var ENGINE_URL = "/assets/ritanya/engine.js";
  // How long an "on" or "off" answer from the server is trusted before the page asks again. This is the kill switch for the
  // browser engine: once the server says the guide is off (or paused), every open page removes it within this long.
  var RECHECK_MS = 120000;
  // How long the LAST successful "on" may keep the guide working while the server cannot be asked at all (offline, a hiccup, a
  // blocked request). A brief interruption is tolerated; once this has passed with no successful answer, the guide is removed and
  // the conversation cleared. A successful "off" or "paused" answer does not wait for this: it removes the guide at once.
  var GRACE_MS = 300000;

  // Set to a same-origin path such as "/assets/ritanya/portrait/avatar.webp" ONLY after the portrait's provenance and
  // usage rights are confirmed by the founder. Until then the Rithavo mark is used.
  var AVATAR_SRC = null;

  var doc = document;
  var memory = {};                       // used only if sessionStorage is unavailable

  function pathKey() { return window.location.pathname.replace(/\/+$/, "").toLowerCase(); }
  function hiddenHere() {
    var p = pathKey();
    return HIDDEN_ON.some(function (x) { return p === x || p.indexOf(x + "/") === 0; });
  }

  function store(key, value) {
    try { if (value === null) window.sessionStorage.removeItem(key); else window.sessionStorage.setItem(key, value); }
    catch (e) { if (value === null) delete memory[key]; else memory[key] = value; }
  }
  function load(key) {
    try { var v = window.sessionStorage.getItem(key); if (v !== null) return v; } catch (e) { /* fall through */ }
    return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null;
  }

  function el(tag, cls, parent, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function svg(markup, cls) {
    // The markup below is a fixed string defined in this file, never server or user text.
    var holder = doc.createElement("span");
    holder.className = cls || "rtn-icon";
    holder.setAttribute("aria-hidden", "true");
    var parsed = new DOMParser().parseFromString('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" focusable="false">' + markup + "</svg>", "image/svg+xml");
    holder.appendChild(doc.importNode(parsed.documentElement, true));
    return holder;
  }
  var ICON = {
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    refresh: '<path d="M19.5 11A7.5 7.5 0 106 16.5M19.5 5v6h-6"/>',
    send: '<path d="M4 12l16-8-6 16-3-7z"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    mark: '<path d="M4 5l8.5 7L4 19" opacity="0.65"/><path d="M11 4l10 8-10 8" stroke-width="2.4"/>'
  };

  /* A path is usable only if it is a single-slash relative path on this site. */
  function safePath(p) { return typeof p === "string" && /^\/(?![\/\\])[^\s]*$/.test(p) && p.length < 200; }

  function fetchJson(url, options, ms) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = setTimeout(function () { if (controller) controller.abort(); }, ms || TIMEOUT_MS);
    var init = Object.assign({ credentials: "omit", cache: "no-store" }, options || {});
    if (controller) init.signal = controller.signal;
    return fetch(url, init).then(function (resp) {
      clearTimeout(timer);
      return resp.json().catch(function () { return null; }).then(function (data) { return { status: resp.status, data: data }; });
    }, function (err) { clearTimeout(timer); throw err; });
  }

  /* ------------------------------------------------------------------ boot: show nothing unless the server says it is on */
  function readStatus() {
    var raw = load(KEY_STATUS);
    if (!raw) return null;
    try {
      var v = JSON.parse(raw);
      if (v && typeof v.t === "number") {
        var age = Date.now() - v.t;
        if (!(age >= 0)) age = Infinity;               // a clock that moved backwards cannot extend the grace period
        // t is the time of the last SUCCESSFUL answer (on or off); a failed attempt never refreshes it
        return { age: age, fresh: age < RECHECK_MS, cfg: v.cfg && v.cfg.enabled === true ? v.cfg : null };
      }
    } catch (e) { /* an older format or garbage: ask again */ }
    return null;
  }
  function writeStatus(cfg) { store(KEY_STATUS, JSON.stringify({ t: Date.now(), cfg: cfg || null })); }
  var expiryTimer = null;
  function cancelExpiry() { if (expiryTimer) { clearTimeout(expiryTimer); expiryTimer = null; } }
  /* done(cfg): the config when on, null when the server says off or paused (or the route is gone), undefined when it could not be
     asked or answered unusably (offline, a blocked request, a server error, a non-JSON page such as a sign-in portal) */
  function askStatus(done) {
    fetchJson(STATUS_URL, { method: "GET" }, 5000).then(function (r) {
      if (r.status === 200 && r.data && r.data.enabled === true) { writeStatus(r.data); cancelExpiry(); done(r.data); }
      else if ((r.status === 200 && r.data && r.data.enabled === false) || r.status === 404) { writeStatus(null); cancelExpiry(); done(null); }
      else done(undefined);
    }).catch(function () { done(undefined); });
  }
  function boot() {
    if (hiddenHere()) return;
    var saved = readStatus();
    if (saved && saved.fresh) { if (saved.cfg) start(saved.cfg); return; }
    askStatus(function (cfg) {
      if (cfg) { start(cfg); return; }
      // Could not be asked: a brief interruption may use the last known "on"; after the grace period nothing is shown.
      if (cfg === undefined && saved && saved.cfg && saved.age <= GRACE_MS) start(saved.cfg);
    });
  }

  /* After the guide was removed because the server could not be reached for the whole grace period, it comes back (fresh, with an
     empty conversation) the first time the server can be asked again and says it is on. It never returns after "off" or "paused". */
  var recovery = null;
  function watchForRecovery() {
    if (recovery) return;
    var asking = false;
    function again() {
      if (doc.hidden || asking) return;
      asking = true;
      askStatus(function (cfg) {
        asking = false;
        if (cfg === undefined) return;               // still unreachable: wait for the next online / visible event
        stop();
        if (cfg) start(cfg);
      });
    }
    function stop() { window.removeEventListener("online", again); doc.removeEventListener("visibilitychange", again); recovery = null; }
    window.addEventListener("online", again);
    doc.addEventListener("visibilitychange", again);
    recovery = stop;
  }

  /* ------------------------------------------------------------------ the guide */
  var mounted = false;                                // true from start() until the guide is removed or found unusable
  function start(config) {
    if (mounted) return;
    mounted = true;
    var after = function () {
      loadEngine(config, function (usable) {
        // A guide whose engine cannot run is never mounted at all: no launcher, no half-working panel. The site's own pages stay.
        if (usable) build(config); else mounted = false;
      });
    };
    // Load the stylesheet first so the launcher never flashes unstyled. If it fails to load, show nothing.
    var existing = doc.querySelector('link[href="/assets/ritanya/ritanya.css"]');
    if (existing) { after(); return; }                // already loaded earlier on this page (the guide came back after an outage)
    var sheet = doc.createElement("link");
    sheet.rel = "stylesheet"; sheet.href = "/assets/ritanya/ritanya.css";
    sheet.onload = after;
    sheet.onerror = function () { mounted = false; };
    doc.head.appendChild(sheet);
  }
  /* The browser engine is one static file from this site (no request is made for a message). It is requested with its version, so a
     new release is fetched even if an older copy is cached. next(true) when the guide may be built; next(false) when the engine
     could not be loaded or parsed (an old browser, a blocked file, a damaged copy), checked by asking it one fixed question. */
  function engineUsable() {
    try {
      var r = window.RitanyaEngine && window.RitanyaEngine.respond("What is Rithavo?", null, 0);
      return !!(r && r.ok && r.result && typeof r.result.reply === "string");
    } catch (e) { return false; }
  }
  function loadEngine(config, next) {
    if (config.engine !== "browser") { next(true); return; }
    if (engineUsable()) { next(true); return; }
    var settled = false;
    function settle() { if (settled) return; settled = true; clearTimeout(timer); next(engineUsable()); }
    var timer = setTimeout(settle, TIMEOUT_MS);
    var version = typeof config.engine_version === "string" && /^[0-9a-f]{1,16}$/.test(config.engine_version) ? config.engine_version : "0";
    var s = doc.createElement("script");
    s.src = ENGINE_URL + "?v=" + version; s.async = true;
    s.onload = settle; s.onerror = settle;
    doc.head.appendChild(s);
  }

  function build(config) {
    var state = { messages: [], lastTopic: null, unknownStreak: 0, pending: false };
    var restored = false;
    try {
      var saved = JSON.parse(load(KEY_CONVERSATION) || "null");
      if (saved && Array.isArray(saved.messages)) {
        state.messages = saved.messages.slice(-MAX_SAVED_MESSAGES).filter(validSaved);
        state.lastTopic = typeof saved.lastTopic === "string" ? saved.lastTopic : null;
        state.unknownStreak = Number.isInteger(saved.unknownStreak) && saved.unknownStreak >= 0 && saved.unknownStreak <= 10 ? saved.unknownStreak : 0;
        restored = state.messages.length > 0;
      }
    } catch (e) { /* start fresh */ }

    function validSaved(m) {
      return m && (m.role === "user" || m.role === "ritanya") && typeof m.text === "string" && m.text.length <= 2000;
    }
    function persist() {
      store(KEY_CONVERSATION, JSON.stringify({ messages: state.messages.slice(-MAX_SAVED_MESSAGES), lastTopic: state.lastTopic, unknownStreak: state.unknownStreak }));
    }

    /* ---- launcher ---- */
    var launcher = el("button", "rtn-launcher", doc.body);
    launcher.type = "button";
    launcher.setAttribute("aria-haspopup", "dialog");
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-label", "Ask Ritanya, Rithavo's guide");
    avatar(launcher, "rtn-avatar-launcher");
    el("span", "rtn-launcher-label", launcher, "Ask Ritanya");
    var spacer = el("div", "rtn-spacer", doc.body);
    spacer.setAttribute("aria-hidden", "true");   // keeps the page's last lines clear of the launcher

    /* ---- panel ---- */
    var root = el("div", "rtn-root", doc.body);
    root.hidden = true;
    var backdrop = el("div", "rtn-backdrop", root);
    var panel = el("div", "rtn-panel", root);   // a div: the site stylesheet gives every <section> 120px of padding
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Ritanya, Rithavo's guide");
    panel.tabIndex = -1;

    var head = el("header", "rtn-head", panel);
    avatar(head, "rtn-avatar-head");
    var titles = el("div", "rtn-titles", head);
    el("h2", "rtn-title", titles, "Ritanya");
    el("p", "rtn-subtitle", titles, "Rithavo's guide");
    var resetBtn = iconButton(head, "refresh", "New conversation");
    var closeBtn = iconButton(head, "close", "Close Ritanya");

    el("p", "rtn-disclosure", panel, config.disclosure);
    var log = el("div", "rtn-log", panel);
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");
    log.setAttribute("aria-relevant", "additions");
    var status = el("p", "rtn-status", panel);
    status.setAttribute("aria-live", "polite");

    var form = el("form", "rtn-form", panel);
    form.noValidate = true;
    var input = el("input", "rtn-input", form);
    input.type = "text"; input.name = "message"; input.maxLength = MAX_CHARS; input.autocomplete = "off";
    input.setAttribute("aria-label", "Message Ritanya");
    input.setAttribute("enterkeyhint", "send");
    input.placeholder = "Ask about Rithavo…";
    var sendBtn = el("button", "rtn-send", form);
    sendBtn.type = "submit";
    sendBtn.setAttribute("aria-label", "Send");
    sendBtn.appendChild(svg(ICON.send));

    function avatar(parent, cls) {
      var wrap = el("span", "rtn-avatar " + cls, parent);
      wrap.setAttribute("aria-hidden", "true");
      if (AVATAR_SRC && safePath(AVATAR_SRC)) { var img = el("img", "rtn-avatar-img", wrap); img.src = AVATAR_SRC; img.alt = ""; img.width = 40; img.height = 40; }
      else wrap.appendChild(svg(ICON.mark, "rtn-mark"));
      return wrap;
    }
    function iconButton(parent, icon, label) {
      var b = el("button", "rtn-iconbtn", parent);
      b.type = "button"; b.setAttribute("aria-label", label); b.title = label;
      b.appendChild(svg(ICON[icon]));
      return b;
    }

    /* ---- rendering ---- */
    function suggestionsFor() {
      var list = (config.starters && config.starters[pathKey() + "/"]) || (pathKey() === "" && config.starters && config.starters["/"]) || config.default_starters || [];
      return Array.isArray(list) ? list : [];
    }
    function render(message, animate) {
      var row = el("div", "rtn-msg rtn-" + message.role + (animate ? " rtn-in" : ""), log);
      el("p", "rtn-bubble", row, message.text);
      if (message.role === "ritanya") {
        if (message.actions && message.actions.length) {
          var actions = el("div", "rtn-actions", row);
          message.actions.forEach(function (a) {
            if (!a || !safePath(a.href) || typeof a.label !== "string") return;
            var link = el("a", "rtn-action", actions);
            link.href = a.href;
            link.appendChild(doc.createTextNode(a.label));
            if (a.needs_sign_in) el("span", "rtn-action-note", link, "Sign-in required");
            link.appendChild(svg(ICON.arrow));
          });
        }
        if (message.suggestions && message.suggestions.length) {
          var chips = el("div", "rtn-chips", row);
          message.suggestions.slice(0, 4).forEach(function (s) {
            if (typeof s !== "string" || s.length > 80) return;
            var chip = el("button", "rtn-chip", chips, s);
            chip.type = "button";
            chip.addEventListener("click", function () { ask(s); });
          });
        }
      }
    }
    function scrollDown() { log.scrollTop = log.scrollHeight; }
    function clearChips() { Array.prototype.forEach.call(log.querySelectorAll(".rtn-chips"), function (n) { n.remove(); }); }
    function setStatus(text) { status.textContent = text || ""; }
    function setPending(on) { state.pending = on; sendBtn.disabled = on; input.setAttribute("aria-busy", on ? "true" : "false"); setStatus(on ? "Ritanya is replying…" : ""); }

    function greet() {
      state.messages = [{ role: "ritanya", text: config.greeting, actions: [], suggestions: suggestionsFor() }];
      state.lastTopic = null; state.unknownStreak = 0;
    }
    function repaint() {
      log.textContent = "";
      state.messages.forEach(function (m, i) {
        // suggestions are only live on the newest Ritanya message
        var copy = Object.assign({}, m);
        if (i !== state.messages.length - 1) copy.suggestions = [];
        render(copy, false);
      });
      scrollDown();
    }
    if (!restored) greet();
    repaint();

    /* ---- sending ---- */
    function fallbackReply(text) {
      return { role: "ritanya", text: text, actions: (config.fallback_actions || []).map(function (a) { return { label: a.label, href: a.href, needs_sign_in: false }; }), suggestions: [] };
    }
    function ask(text) {
      text = String(text || "").replace(/\s+/g, " ").trim();
      if (!text || state.pending) return;
      if (text.length > MAX_CHARS) { setStatus("Please keep it under " + MAX_CHARS + " characters."); return; }
      clearChips();
      var mine = { role: "user", text: text };
      state.messages.push(mine); render(mine, true); scrollDown();
      input.value = "";
      setPending(true);
      if (config.engine === "browser") { answerLocally(text); return; }
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        finishWith(fallbackReply("You're offline, so I can't answer right now. Your conversation is kept. These pages work once you reconnect."));
        return;
      }
      var body = { message: text };
      if (state.lastTopic) body.last_topic = state.lastTopic;
      if (state.unknownStreak) body.unknown_streak = state.unknownStreak;
      fetchJson(CHAT_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(function (r) {
        var d = r.data || {};
        if (r.status === 200 && typeof d.reply === "string") {
          if (typeof d.topic === "string") state.lastTopic = d.topic;
          state.unknownStreak = d.unknown ? Math.min(10, state.unknownStreak + 1) : 0;
          finishWith({ role: "ritanya", text: d.reply, actions: Array.isArray(d.actions) ? d.actions : [], suggestions: Array.isArray(d.suggestions) ? d.suggestions : [] });
        } else if (r.status === 429) {
          finishWith(fallbackReply(typeof d.message === "string" ? d.message : (config.messages && config.messages.busy) || "I'm getting a lot of questions right now. Please try again in a minute."));
        } else {
          finishWith(fallbackReply((config.messages && config.messages.unavailable) || "I can't answer right now. Your conversation is kept. The links below still work."));
        }
      }).catch(function () {
        finishWith(fallbackReply((config.messages && config.messages.unavailable) || "I can't answer right now. Your conversation is kept. The links below still work."));
      });
    }
    function finishWith(reply) {
      state.messages.push(reply); render(reply, true); persist(); setPending(false); scrollDown();
    }
    /* Browser engine: the answer is computed here from the reviewed information; no request is made and nothing leaves the page. */
    function answerLocally(text) {
      recheck(function (on) {
        if (!on) return;                                           // switched off while open: the guide is already gone
        var engine = window.RitanyaEngine, out = null;
        try {
          out = engine && engine.respond(text, state.lastTopic, state.unknownStreak);
          if (engine && out && !out.ok) out = engine.respond(text, null, 0);   // a damaged saved topic must not block every message
        } catch (e) { out = null; }
        if (!out || !out.ok || !out.result || typeof out.result.reply !== "string") {
          finishWith(fallbackReply((config.messages && config.messages.unavailable) || "I can't answer right now. Your conversation is kept. The links below still work."));
          return;
        }
        var d = out.result;
        if (typeof d.topic === "string") state.lastTopic = d.topic;
        state.unknownStreak = d.unknown ? Math.min(10, state.unknownStreak + 1) : 0;
        finishWith({ role: "ritanya", text: d.reply, actions: Array.isArray(d.actions) ? d.actions : [], suggestions: Array.isArray(d.suggestions) ? d.suggestions : [] });
      });
    }
    /* The kill switch. A recent "on" is trusted; otherwise the server is asked again (edge-cached, so cheap).
       - "off" or "paused": the whole guide is removed at once and what the visitor typed is cleared.
       - "on": carry on (and the grace clock restarts, because the answer is stored with its time).
       - could not be asked: the last known "on" keeps the guide working for GRACE_MS after that answer. When that has run out
         without one successful answer, the guide is removed and the conversation cleared, exactly as for "off". */
    var dead = false;
    function recheck(done) {
      if (dead) { done(false); return; }
      var saved = readStatus();
      if (saved && saved.fresh && saved.cfg) { done(true); return; }
      askStatus(function (cfg) {
        if (dead) { done(false); return; }
        if (cfg === null) { teardown(); done(false); return; }
        if (cfg) { done(true); return; }
        var last = readStatus();
        if (last && last.cfg && last.age <= GRACE_MS) { armExpiry(last.age); done(true); return; }
        expire(); done(false);
      });
    }
    // So an idle page whose server stays unreachable is also removed when the grace period ends, not only when someone touches it.
    function armExpiry(age) {
      cancelExpiry();
      expiryTimer = setTimeout(function () { expiryTimer = null; recheck(function () {}); }, Math.max(1000, GRACE_MS - age) + 1000);
    }
    function expire() { teardown(); store(KEY_STATUS, null); watchForRecovery(); }
    function onVisible() { if (!doc.hidden) recheck(function () {}); }
    function teardown() {
      dead = true; mounted = false;
      cancelExpiry();
      doc.removeEventListener("visibilitychange", onVisible);
      store(KEY_CONVERSATION, null);
      [launcher, root, spacer].forEach(function (n) { if (n && n.parentNode) n.parentNode.removeChild(n); });
      doc.documentElement.classList.remove("rtn-lock");
      doc.body.classList.remove("rtn-on");
    }
    form.addEventListener("submit", function (e) { e.preventDefault(); ask(input.value); });

    resetBtn.addEventListener("click", function () {
      greet(); store(KEY_CONVERSATION, null); repaint(); setStatus("Started a new conversation."); input.focus();
    });

    /* ---- open / close, focus, keyboard ---- */
    var lastFocus = null;
    function isSheet() { return window.matchMedia && window.matchMedia("(max-width: 640px)").matches; }
    function open() {
      lastFocus = doc.activeElement && doc.activeElement !== doc.body ? doc.activeElement : launcher;
      root.hidden = false;
      launcher.setAttribute("aria-expanded", "true");
      panel.setAttribute("aria-modal", isSheet() ? "true" : "false");
      doc.documentElement.classList.toggle("rtn-lock", isSheet());
      launcher.classList.add("rtn-launcher-hidden");
      setTimeout(function () { input.focus(); }, 30);
      recheck(function () { /* if it is off now, teardown() has already removed everything */ });
    }
    function close() {
      if (root.hidden) return;
      root.hidden = true;
      launcher.setAttribute("aria-expanded", "false");
      doc.documentElement.classList.remove("rtn-lock");
      launcher.classList.remove("rtn-launcher-hidden");
      (lastFocus && doc.contains(lastFocus) ? lastFocus : launcher).focus();
    }
    launcher.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    backdrop.addEventListener("click", close);
    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.stopPropagation(); close(); return; }
      if (e.key !== "Tab") return;
      var f = panel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled])');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    // Following a link inside the app ends the conversation view, not the conversation itself (it is kept for this tab).
    log.addEventListener("click", function (e) { var a = e.target.closest && e.target.closest("a.rtn-action"); if (a) { persist(); } });
    doc.body.classList.add("rtn-on");
    doc.addEventListener("visibilitychange", onVisible);
    var now = readStatus();                          // started from a stale "on" during an interruption: the grace clock is already running
    if (now && now.cfg && !now.fresh) armExpiry(now.age);
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot); else boot();
})();
