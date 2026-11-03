/* Ritanya on rithavo.com: the guide launcher and chat panel.

   What this is: a text guide that answers from Rithavo's reviewed product information and points people to real pages on
   rithavo.com. It is NOT live AI, it cannot see an account, and it gives no personal career advice. The server decides
   all of that (app/ritanya_chat.py); this file only shows what the server returns.

   Rules this file keeps (each one is checked by tests/test_ritanya_integration.py):
   - It talks only to /api/ritanya/status and /api/ritanya/chat on this origin, with credentials omitted. No cookie is read
     or set. The conversation lives in this tab's sessionStorage and is cleared by "New conversation" or by closing the tab.
   - Server text is shown with textContent only, never as HTML.
   - Every link is a same-origin relative path, validated here again before it is used.
   - With the assistant switched off on the server, /status does not exist and nothing is shown at all.
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

  // Set to a same-origin path such as "/assets/ritanya/portrait/avatar.webp" ONLY after the portrait's provenance and
  // usage rights are confirmed by the founder. Until then the Rithavo mark is used.
  var AVATAR_SRC = null;

  var doc = document;
  var memory = {};                       // used only if sessionStorage is unavailable

  // Inside the installed app the Rithavo Card is not offered (it is not an experience on rithavo.com), so the guide does not
  // offer it as a question either.
  function inApp() {
    return doc.documentElement.classList.contains("is-pwa") || window.navigator.standalone === true ||
      !!(window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
  }
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
  function boot() {
    if (hiddenHere()) return;
    var cached = load(KEY_STATUS);
    if (cached === "off") return;
    if (cached) {
      try { var cfg = JSON.parse(cached); if (cfg && cfg.enabled === true) { start(cfg); return; } } catch (e) { /* ask again */ }
    }
    fetchJson(STATUS_URL, { method: "GET" }, 5000).then(function (r) {
      if (r.status === 200 && r.data && r.data.enabled === true) { store(KEY_STATUS, JSON.stringify(r.data)); start(r.data); }
      else store(KEY_STATUS, "off");
    }).catch(function () { /* offline or blocked: show nothing, try again on the next page */ });
  }

  /* ------------------------------------------------------------------ the guide */
  function start(config) {
    // Load the stylesheet first so the launcher never flashes unstyled. If it fails to load, show nothing.
    var sheet = doc.createElement("link");
    sheet.rel = "stylesheet"; sheet.href = "/assets/ritanya/ritanya.css";
    sheet.onload = function () { build(config); };
    doc.head.appendChild(sheet);
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
    el("div", "rtn-spacer", doc.body).setAttribute("aria-hidden", "true");   // keeps the page's last lines clear of the launcher

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
            if (typeof s !== "string" || s.length > 80 || (inApp() && /\bcard\b/i.test(s))) return;
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
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot); else boot();
})();
