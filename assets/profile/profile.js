/* Individual public profile page (/c/{slug}). Renders ONLY what GET /public-profile/{slug} returns; every field is optional and a
   section with nothing to show is simply not drawn. Text is inserted with textContent. */
(function () {
  "use strict";
  var PF = window.PF, el = PF.el, svg = PF.svg;
  var parts = window.location.pathname.split("/").filter(Boolean);
  var slug = parts[0] === "c" ? parts[1] : "";
  var root = document.getElementById("pf-root");
  var main = document.getElementById("pf-main");
  var state = { data: null, photo: null, busy: false, showShare: false };

  function setBusy(flag) { main.setAttribute("aria-busy", flag ? "true" : "false"); }

  function stateView(title, text, actions) {
    PF.clear(root);
    root.appendChild(el("section", { class: "pf-state" }, [
      el("h1", { text: title }), el("p", { text: text }),
      actions ? el("div", { class: "pf-actions" }, actions) : null
    ]));
    document.title = title + " | Rithavo";
    setBusy(false);
    main.focus({ preventScroll: true });
  }
  function homeLink() { return el("a", { class: "pf-btn pf-btn--ghost", href: "/home/", text: "Go to Rithavo" }); }
  function unavailable() {
    stateView("This profile isn't available", "It may have been hidden, or the link may be wrong. The page never says which.", [homeLink()]);
  }

  function who() {
    var d = state.data;
    return d.kind === "business" ? d.name : firstName(d.name);
  }

  function firstName(name) { return (name || "").trim().split(/\s+/)[0] || "this member"; }

  /* ---------- actions ---------- */
  function say(message, kind) { PF.toast(message, kind); }

  function profileUrl() { return window.location.origin + "/c/" + slug; }

  function copyLink() {
    PF.copyText(profileUrl()).then(function (ok) {
      if (ok) { say("Link copied"); return; }
      state.showShare = "link";
      renderAll();
      var input = document.getElementById("pf-link-input");
      if (input) { input.focus(); input.select(); }
      say("Copying is blocked. Select the link and copy it yourself.", "error");
    });
  }

  function follow(on) {
    if (state.busy) { return; }
    state.busy = true; renderActions();
    PF.api("POST", "/api/public-profile/" + slug + (on ? "/follow" : "/unfollow")).then(function (res) {
      if (res.status === 429) { say("You're going a little fast. Please wait a minute and try again.", "error"); }
      else if (res.status === 404) { unavailable(); state.busy = false; return; }
      else if (res.status !== 200) { say("We couldn't update that. Please try again.", "error"); }
      else { say(on ? "You now follow " + who() : "You no longer follow " + who()); }
      return refresh();
    }).then(function () { state.busy = false; renderActions(); });
  }

  function shareCard(email, phone) {
    PF.api("POST", "/api/public-profile/" + slug + "/share", { share_email: !!email, share_phone: !!phone }).then(function (res) {
      var code = res.data && res.data.code;
      if (res.status === 200) { say("Card shared with " + who()); state.showShare = false; return refresh().then(renderAll); }
      var map = { no_email: "No email is set on your Card. Add one in Public profile.", no_phone: "No phone is set on your Card. Add one in Public profile.",
        already_shared: "You already shared your Card with this member. You can only share once.", not_allowed: "Sharing needs a mutual follow and a confirmed photograph." };
      say(map[code] || "We couldn't share your Card. Please try again.", "error");
    });
  }

  function relationButtons(rel, opts) {
    var out = [];
    if (rel.is_self) { return out; }
    var on = !!rel.following;
    var b = el("button", { type: "button", class: "pf-btn pf-btn--primary " + (opts.sm ? "pf-btn--sm" : ""), "aria-pressed": on ? "true" : "false", "aria-busy": state.busy ? "true" : false, disabled: state.busy },
      on ? [svg("usercheck"), state.busy ? "Updating" : "Following"] : [svg("userplus"), state.busy ? "Updating" : "Follow"]);
    b.addEventListener("click", function () { follow(!on); });
    out.push(b);
    return out;
  }

  function shareButton(label, sm, variant) {
    var b = el("button", { type: "button", class: "pf-btn " + (variant || "pf-btn--ghost") + (sm ? " pf-btn--sm" : "") }, [svg("share"), label || "Share profile"]);
    b.addEventListener("click", copyLink);
    return b;
  }

  function cardShareControl(rel) {
    if (rel.is_self || !rel.mutual) { return null; }
    if (rel.shared) { return el("p", { class: "pf-direction", text: "You both follow each other. You shared your Card with " + who() + "." }); }
    var b = el("button", { type: "button", class: "pf-btn pf-btn--ghost", disabled: !rel.can_share, "aria-disabled": !rel.can_share ? "true" : false }, [svg("card"), "Share Card"]);
    b.addEventListener("click", function () { state.showShare = state.showShare === "card" ? false : "card"; renderAll(); });
    var wrap = el("div", { class: "pf-actions" }, [b]);
    if (!rel.can_share) { wrap.appendChild(el("p", { class: "pf-direction", text: "You can share your Card once you follow each other and you have a confirmed photograph." })); }
    return wrap;
  }

  function sharePanel() {
    if (state.showShare === "link") {
      return el("div", { class: "pf-panel" }, [
        el("h3", { text: "Copy the link" }),
        el("input", { id: "pf-link-input", class: "pf-input", readonly: true, value: profileUrl(), "aria-label": "Profile link", type: "text" })
      ]);
    }
    if (state.showShare !== "card") { return null; }
    var email = el("input", { type: "checkbox", id: "pf-sh-email", checked: true });
    var phone = el("input", { type: "checkbox", id: "pf-sh-phone" });
    var go = el("button", { type: "button", class: "pf-btn pf-btn--primary", }, [svg("card"), "Share Card"]);
    go.addEventListener("click", function () { shareCard(email.checked, phone.checked); });
    return el("div", { class: "pf-panel" }, [
      el("h3", { text: "Share your Card with " + who() }),
      el("label", { class: "pf-check", for: "pf-sh-email" }, [email, "My contact email"]),
      el("label", { class: "pf-check", for: "pf-sh-phone" }, [phone, "My contact phone"]),
      el("p", { class: "pf-direction", text: "Nothing is shared by default. You can only share once." }), go
    ]);
  }

  /* ---------- composition ---------- */
  function present(sections, key) { var v = sections[key]; return Array.isArray(v) ? v.length > 0 : !!v; }

  function build() {
    var d = state.data, s = d.sections || {}, rel = d.relationship || {};
    var nodes = [];
    var order = (d.section_order || []).filter(function (k) { return present(s, k); });

    // hero
    var chips = (s.capabilities || []).slice(0, 4).map(function (name, i) {
      return el("div", { class: "pf-chip pf-chip--" + (i + 1) }, [svg(PF.CHIP_ICONS[i % PF.CHIP_ICONS.length]), el("span", { text: name })]);
    });
    var stage;
    var alpha = state.photo && state.photo.alpha;
    if (state.photo) {
      var img = el("img", { class: "pf-photo", src: state.photo.url, alt: "Photograph of " + (d.name || "this member") });
      if (alpha) { stage = el("div", { class: "pf-stage" }, [el("div", { class: "pf-blob", "aria-hidden": "true" }), el("div", { class: "pf-ring", "aria-hidden": "true" }), img].concat(chips)); }
      else {
        stage = el("div", { class: "pf-stage pf-stage--framed" }, [el("div", { class: "pf-blob", "aria-hidden": "true" }), el("div", { class: "pf-frame" }, [img])].concat(chips));
        var focus = d.photo && d.photo.focus;
        if (focus) { stage.style.setProperty("--pf-fx", focus.x + "%"); stage.style.setProperty("--pf-fy", focus.y + "%"); }
      }
    } else {
      stage = el("div", { class: "pf-stage pf-stage--initial" }, [el("div", { class: "pf-initial", "aria-hidden": "true", text: (d.name || "R").trim().charAt(0).toUpperCase() })].concat(chips));
    }

    var headline = d.headline || s.current_role || "";
    var meta = [];
    if (s.location) { meta.push(el("span", {}, [svg("pin"), s.location])); }
    if ((s.professional_intent || []).length) { meta.push(el("span", {}, [el("i", { class: "pf-dot", "aria-hidden": "true" }), "Open to " + s.professional_intent.join(", ")])); }
    var badges = [];
    if (d.trust && d.trust.label && d.trust.label !== "Unverified") { badges.push(el("span", { class: "pf-badge" }, [svg("check"), d.trust.label])); }
    if (d.is_verified_mentor) { badges.push(el("span", { class: "pf-badge" }, [svg("check"), "Mentor"])); }

    var actions = el("div", { class: "pf-actions", id: "pf-actions", "data-action-surface": "" });
    var hero = el("section", { class: "pf-hero", id: "pf-overview", "aria-labelledby": "pf-name" }, [
      el("div", { class: "pf-hero__copy" }, [
        el("p", { class: "pf-eyebrow", text: "Hello, I'm" }),
        el("h1", { class: "pf-name", id: "pf-name", text: d.name || "Rithavo member" }),
        headline ? el("p", { class: "pf-headline pf-gradtext", text: headline }) : null,
        badges.length ? el("div", { class: "pf-badges" }, badges) : null,
        actions,
        meta.length ? el("div", { class: "pf-meta" }, meta) : null,
        rel.is_self ? el("div", { class: "pf-owner-note" }, [
          el("span", { text: d.visible_to_members ? "This is how signed-in members see your profile." : "Only you can see this profile right now." }),
          el("a", { class: "pf-btn pf-btn--ghost pf-btn--sm", href: "/c/manage", text: "Edit public profile" })]) : null,
        cardShareControl(rel), sharePanel()
      ]),
      el("div", { class: "pf-hero__portrait" }, [stage])
    ]);
    nodes.push(hero);

    // fact tiles, in the owner's order
    var tileDefs = { current_role: ["briefcase", "Current role", function () { return s.current_role; }],
      industries: ["layers", "Industries", function () { return (s.industries || []).join(", "); }],
      career_direction: ["compass", "Career direction", function () { return s.career_direction; }] };
    var tiles = [];
    order.forEach(function (k) {
      var def = tileDefs[k];
      if (!def) { return; }
      tiles.push(el("div", { class: "pf-tile" }, [el("span", { class: "pf-tile__icon" }, [svg(def[0])]), el("div", {}, [el("p", { class: "pf-tile__label", text: def[1] }), el("p", { class: "pf-tile__value", text: def[2]() })])]));
    });
    if (tiles.length) { nodes.push(el("section", { class: "pf-facts", "aria-label": "Highlights" }, tiles)); }

    // sections
    var secs = [], links = [["pf-overview", "Overview"]];
    order.forEach(function (k) {
      if (k === "capabilities") {
        secs.push(el("section", { class: "pf-section", id: "pf-expertise", "aria-labelledby": "pf-h-exp" }, [
          el("div", { class: "pf-section__head" }, [el("h2", { class: "pf-section__title", id: "pf-h-exp", text: "What I work on" })]),
          el("ul", { class: "pf-pills" }, s.capabilities.map(function (c, i) { return el("li", { class: "pf-pill" }, [svg(PF.CHIP_ICONS[i % 4]), c]); }))]));
        links.push(["pf-expertise", "Expertise"]);
      } else if (k === "professional_intent") {
        secs.push(el("section", { class: "pf-section", id: "pf-open", "aria-labelledby": "pf-h-open" }, [
          el("div", { class: "pf-section__head" }, [el("h2", { class: "pf-section__title", id: "pf-h-open", text: "Open to" })]),
          el("ul", { class: "pf-pills" }, s.professional_intent.map(function (c) { return el("li", { class: "pf-pill pf-pill--warm" }, [svg("spark"), c]); }))]));
        links.push(["pf-open", "Open to"]);
      }
    });
    if (secs.length) { nodes.push(el("div", { class: "pf-sections" }, secs)); }

    // closing band
    var cta = el("section", { class: "pf-cta", "aria-label": "Follow", "data-action-surface": "" }, [
      el("div", {}, [el("h2", { text: rel.is_self ? "Your profile, as members see it" : "Let's stay in touch" }),
        el("p", { text: rel.is_self ? "Only signed-in Rithavo members can open this page, and only while your profile is visible." : "Follow " + firstName(d.name) + " to keep up with their profile. Only signed-in Rithavo members can see it." })]),
      el("div", { class: "pf-actions", id: "pf-cta-actions" })]);
    nodes.push(cta);
    return { nodes: nodes, links: links };
  }

  /* ---------- business composition (release R2): the same components, existing business fields only ---------- */
  function splitList(text) {
    var items = (text || "").split(/[,;\n]+/).map(function (x) { return x.trim(); }).filter(function (x) { return x && x.length <= 48; });
    return items.length >= 2 ? items.slice(0, 12) : [];
  }

  function buildBusiness() {
    var d = state.data, rel = d.relationship || {};
    var nodes = [], links = [["pf-overview", "Overview"]];
    var expertise = splitList(d.areas_of_expertise);
    var chips = expertise.slice(0, 4).map(function (name, i) {
      return el("div", { class: "pf-chip pf-chip--" + (i + 1) }, [svg(PF.CHIP_ICONS[i % PF.CHIP_ICONS.length]), el("span", { text: name })]);
    });
    var face = state.photo
      ? el("img", { class: "pf-logo-card__img", src: state.photo.url, alt: "Logo of " + (d.name || "this business") })
      : el("span", { class: "pf-logo-card__initial", "aria-hidden": "true", text: (d.name || "B").trim().charAt(0).toUpperCase() });
    var stage = el("div", { class: "pf-stage pf-stage--biz" }, [el("div", { class: "pf-blob", "aria-hidden": "true" }), el("div", { class: "pf-logo-card" }, [face])].concat(chips));

    var meta = [];
    if (d.location) { meta.push(el("span", {}, [svg("pin"), d.location])); }
    var actions = el("div", { class: "pf-actions", id: "pf-actions", "data-action-surface": "" });
    nodes.push(el("section", { class: "pf-hero", id: "pf-overview", "aria-labelledby": "pf-name" }, [
      el("div", { class: "pf-hero__copy" }, [
        el("p", { class: "pf-eyebrow", text: "Business on Rithavo" }),
        el("h1", { class: "pf-name", id: "pf-name", text: d.name || "Business" }),
        d.industry ? el("p", { class: "pf-headline pf-gradtext", text: d.industry }) : null,
        d.summary ? el("p", { class: "pf-lede", text: d.summary }) : null,
        actions,
        meta.length ? el("div", { class: "pf-meta" }, meta) : null,
        rel.is_self ? el("div", { class: "pf-owner-note" }, [
          el("span", { text: d.visible_to_members ? "This is how signed-in members see your business." : "Only you can see this business right now." }),
          el("a", { class: "pf-btn pf-btn--ghost pf-btn--sm", href: "/c/manage?view=business", text: "Edit business" })]) : null,
        cardShareControl(rel), sharePanel()
      ]),
      el("div", { class: "pf-hero__portrait" }, [stage])
    ]));

    var tiles = [];
    if (d.industry) { tiles.push(el("div", { class: "pf-tile" }, [el("span", { class: "pf-tile__icon" }, [svg("layers")]), el("div", {}, [el("p", { class: "pf-tile__label", text: "Industry" }), el("p", { class: "pf-tile__value", text: d.industry })])])); }
    if (d.location) { tiles.push(el("div", { class: "pf-tile" }, [el("span", { class: "pf-tile__icon" }, [svg("pin")]), el("div", {}, [el("p", { class: "pf-tile__label", text: "Location" }), el("p", { class: "pf-tile__value", text: d.location })])])); }
    if (tiles.length) { nodes.push(el("section", { class: "pf-facts", "aria-label": "Highlights" }, tiles)); }

    var secs = [];
    if (d.products_services) {
      secs.push(el("section", { class: "pf-section", id: "pf-offer", "aria-labelledby": "pf-h-offer" }, [
        el("div", { class: "pf-section__head" }, [el("h2", { class: "pf-section__title", id: "pf-h-offer", text: "Products and services" })]),
        el("div", { class: "pf-card pf-prose" }, [el("p", { text: d.products_services })])]));
      links.push(["pf-offer", "What they offer"]);
    }
    if (d.areas_of_expertise) {
      var body = expertise.length
        ? el("ul", { class: "pf-pills" }, expertise.map(function (c, i) { return el("li", { class: "pf-pill" }, [svg(PF.CHIP_ICONS[i % 4]), c]); }))
        : el("div", { class: "pf-card pf-prose" }, [el("p", { text: d.areas_of_expertise })]);
      secs.push(el("section", { class: "pf-section", id: "pf-expertise", "aria-labelledby": "pf-h-exp" }, [
        el("div", { class: "pf-section__head" }, [el("h2", { class: "pf-section__title", id: "pf-h-exp", text: "Areas of expertise" })]), body]));
      links.push(["pf-expertise", "Expertise"]);
    }
    if (secs.length) { nodes.push(el("div", { class: "pf-sections" }, secs)); }

    nodes.push(el("section", { class: "pf-cta", "aria-label": "Follow", "data-action-surface": "" }, [
      el("div", {}, [el("h2", { text: rel.is_self ? "Your business, as members see it" : "Let's stay in touch" }),
        el("p", { text: rel.is_self ? "Only signed-in Rithavo members can open this page, and only while your business is visible."
          : "Follow " + (d.name || "this business") + " to keep up with its profile. Only signed-in Rithavo members can see it." })]),
      el("div", { class: "pf-actions", id: "pf-cta-actions" })]));
    return { nodes: nodes, links: links };
  }

  function renderActions() {
    var d = state.data; if (!d) { return; }
    var rel = d.relationship || {};
    var targets = [["pf-actions", false, "pf-btn--ghost"], ["pf-cta-actions", false, "pf-btn--ghost"]];
    targets.forEach(function (t) {
      var host = document.getElementById(t[0]); if (!host) { return; }
      PF.clear(host);
      relationButtons(rel, { sm: false }).forEach(function (b) { host.appendChild(b); });
      host.appendChild(shareButton("Share profile", false));
    });
    // header and bar
    var navFollow = document.getElementById("pf-nav-actions"); if (navFollow) { PF.clear(navFollow); relationButtons(rel, { sm: true }).forEach(function (b) { navFollow.appendChild(b); }); navFollow.insertBefore(shareButton("Share", true), navFollow.firstChild); }
    var bar = document.getElementById("pf-bar"); if (bar) { PF.clear(bar); relationButtons(rel, { sm: false }).forEach(function (b) { bar.appendChild(b); }); bar.appendChild(shareButton("Share", false)); }
  }

  function renderAll() {
    var d = state.data;
    var built = d.kind === "business" ? buildBusiness() : build();
    PF.clear(root);
    built.nodes.forEach(function (n) { root.appendChild(n); });
    var links = document.getElementById("pf-links"), menu = document.getElementById("pf-menu-links");
    [links, menu].forEach(function (host) {
      if (!host) { return; }
      PF.clear(host);
      built.links.forEach(function (l, i) { host.appendChild(el("a", { href: "#" + l[0], "aria-current": host === links && i === 0 ? "location" : false, text: l[1] })); });
    });
    renderActions();
    document.title = (d.name || "Profile") + " | Rithavo";
    document.body.classList.add("has-bar");
    setBusy(false);
    PF.watchActionBar();
    spy();
  }

  /* current-section marker in the header links */
  var spied = false;
  function spy() {
    if (spied || !("IntersectionObserver" in window)) { return; }
    spied = true;
    var links = document.querySelectorAll("#pf-links a");
    var map = {}; links.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) { return; }
        links.forEach(function (a) { a.removeAttribute("aria-current"); });
        if (map[e.target.id]) { map[e.target.id].setAttribute("aria-current", "location"); }
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    Object.keys(map).forEach(function (id) { var t = document.getElementById(id); if (t) { io.observe(t); } });
  }

  function loadPhoto(d) {
    if (state.photo) { URL.revokeObjectURL(state.photo.url); state.photo = null; }
    var has = d.kind === "business" ? (d.logo && d.logo.present) : (d.photo && d.photo.present);
    if (!has) { return Promise.resolve(); }
    return fetch("/api/public-profile/" + slug + "/photo", { credentials: "same-origin", cache: "no-store" }).then(function (res) {
      if (!res.ok) { return; }
      var alpha = res.headers.get("X-Rithavo-Photo-Alpha") === "1";
      return res.blob().then(function (blob) { state.photo = { url: URL.createObjectURL(blob), alpha: alpha }; });
    }).catch(function () { /* the initial tile is used */ });
  }

  function refresh() {
    return PF.api("GET", "/api/public-profile/" + slug).then(function (res) {
      if (res.status === 200 && res.data) { state.data = res.data; }
      return res;
    });
  }

  function start() {
    if (!slug) { unavailable(); return; }
    PF.api("GET", "/api/public-profile/" + slug).then(function (res) {
      if (res.status === 401) { window.location.replace("/sign-in/?next=/c/" + encodeURIComponent(slug)); return; }
      if (res.status === 404) { unavailable(); return; }
      if (res.status === 429) { stateView("Too many requests", "You're going a little fast. Please wait a minute and try again.", [homeLink()]); return; }
      if (res.status !== 200 || !res.data) {
        var retry = el("button", { type: "button", class: "pf-btn pf-btn--primary", text: "Try again" });
        retry.addEventListener("click", function () { window.location.reload(); });
        stateView("We couldn't load this profile", "Check your connection and try again.", [retry]);
        return;
      }
      state.data = res.data;
      loadPhoto(res.data).then(renderAll);
    });
  }
  start();
})();
