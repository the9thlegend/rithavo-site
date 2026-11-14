/* Owner panel for the individual public profile (/c/manage). Talks only to /my-public-profile, /my-photos, /my-photo and the
   member's own public page. Never uploads a photograph: it only chooses among photographs the member has already confirmed. */
(function () {
  "use strict";
  var PF = window.PF, el = PF.el, svg = PF.svg;
  var root = document.getElementById("pf-root");
  var main = document.getElementById("pf-main");
  var S = { card: null, photos: [], preview: null, previewPhoto: null, dirty: false };
  var SHOW_LABELS = [["current_role", "Current role"], ["location", "Location"], ["industries", "Industries"], ["capabilities", "Capabilities"], ["career_direction", "Career direction"], ["professional_intent", "Open to"]];

  function stateView(title, text, actions) {
    PF.clear(root);
    root.appendChild(el("section", { class: "pf-state" }, [el("h1", { text: title }), el("p", { text: text }), actions ? el("div", { class: "pf-actions" }, actions) : null]));
    main.setAttribute("aria-busy", "false");
  }

  function field(id, label, control, help) {
    return el("div", { class: "pf-field" }, [el("label", { for: id, class: "pf-field__label", text: label }), control, help ? el("p", { class: "pf-help", id: id + "-help", text: help }) : null]);
  }
  function card(title, children, note) {
    return el("section", { class: "pf-card" }, [el("h2", { class: "pf-card__title", text: title })].concat(children, note ? [el("p", { class: "pf-help", text: note })] : []));
  }

  function callout(text, kind) {
    return el("div", { class: "pf-callout", "data-kind": kind || "warn", role: kind === "error" ? "alert" : false }, [svg("shield"), el("span", { text: text })]);
  }

  /* ---------- controls bound to a working copy ---------- */
  var work = null;
  function copyFromServer() {
    var c = S.card;
    work = { is_discoverable: c.is_discoverable, headline: c.headline_override || "", show: Object.assign({}, c.show), email: c.contact_email || "", phone: c.contact_phone || "",
      fx: c.photo_focus.x, fy: c.photo_focus.y, order: c.section_order.slice(), caps: c.capabilities.filter(function (x) { return x.selected; }).map(function (x) { return x.id; }) };
    S.dirty = false;
  }
  function touch() { S.dirty = true; var save = document.getElementById("pf-save"); if (save) { save.removeAttribute("disabled"); } }

  function checkbox(id, label, checked, disabled, onChange) {
    var input = el("input", { type: "checkbox", id: id, checked: checked, disabled: disabled });
    input.addEventListener("change", function () { onChange(input.checked); touch(); renderPreviewNote(); });
    return el("label", { class: "pf-check", for: id, "aria-disabled": disabled ? "true" : false }, [input, label]);
  }

  function visibilityCard() {
    var c = S.card, blocked = !c.can_make_discoverable;
    var kids = [checkbox("pf-vis", "Show my profile to signed-in Rithavo members", work.is_discoverable && !blocked, blocked, function (v) { work.is_discoverable = v; })];
    if (blocked) { kids.push(callout("This needs a confirmed profile photograph. Confirm one in your private profile, then come back.")); }
    return card("Who can see your profile", kids, "While this is on, signed-in members can open your profile. Switch it off and your profile, photograph and Follow button disappear for everyone but you.");
  }

  function headlineCard() {
    var input = el("input", { type: "text", id: "pf-headline", class: "pf-input", maxlength: 120, value: work.headline, placeholder: S.card.profile_headline || "Your headline", autocomplete: "off" });
    var count = el("span", { class: "pf-counter", id: "pf-headline-count", text: work.headline.length + " / 120" });
    input.addEventListener("input", function () { work.headline = input.value; count.textContent = input.value.length + " / 120"; touch(); });
    return card("Headline", [field("pf-headline", "Headline on your public profile", input), count], "This replaces the headline on your public profile only. Leave it empty to use the headline from your private profile.");
  }

  function showCard() {
    return card("What members can see", SHOW_LABELS.map(function (p) {
      return el("div", {}, [checkbox("pf-show-" + p[0], p[1], !!work.show[p[0]], false, function (v) { work.show[p[0]] = v; })]);
    }), "Each switch controls one block. Experience, education and skills are not shown on this page.");
  }

  function capsCard() {
    var caps = S.card.capabilities;
    if (!caps.length) { return card("Capabilities to show", [el("p", { class: "pf-help", text: "No capabilities to show. Capabilities come from your private profile; none are available to choose yet." }), el("a", { class: "pf-link", href: "/profile/", text: "Open your private profile" })]); }
    return card("Capabilities to show", caps.map(function (cap) {
      return el("div", {}, [checkbox("pf-cap-" + cap.id, cap.name, work.caps.indexOf(cap.id) >= 0, false, function (v) {
        work.caps = work.caps.filter(function (x) { return x !== cap.id; }); if (v) { work.caps.push(cap.id); } })]);
    }), "Only the capabilities you tick appear. The others stay private.");
  }

  function orderCard() {
    var list = el("ol", { class: "pf-order", id: "pf-order" });
    function draw() {
      PF.clear(list);
      work.order.forEach(function (key, i) {
        var up = el("button", { type: "button", class: "pf-iconbtn", "aria-label": "Move " + S.card.section_labels[key] + " up", disabled: i === 0 }, [svg("chart")]);
        var down = el("button", { type: "button", class: "pf-iconbtn", "aria-label": "Move " + S.card.section_labels[key] + " down", disabled: i === work.order.length - 1 }, [svg("chart")]);
        up.textContent = "↑"; down.textContent = "↓";
        function swap(a, b) { var t = work.order[a]; work.order[a] = work.order[b]; work.order[b] = t; touch(); draw(); var focusEl = list.querySelectorAll("button")[(b * 2) + (b > a ? 1 : 0)]; if (focusEl && !focusEl.disabled) { focusEl.focus(); } }
        up.addEventListener("click", function () { swap(i, i - 1); });
        down.addEventListener("click", function () { swap(i, i + 1); });
        list.appendChild(el("li", { class: "pf-order__row" }, [el("span", { text: S.card.section_labels[key] }), el("span", { class: "pf-order__btns" }, [up, down])]));
      });
    }
    draw();
    return card("Order of blocks", [list], "Buttons, not dragging, so the order can be changed with a keyboard or a screen reader.");
  }

  function contactCard() {
    var email = el("input", { type: "email", id: "pf-email", class: "pf-input", value: work.email, autocomplete: "off", maxlength: 254 });
    var phone = el("input", { type: "tel", id: "pf-phone", class: "pf-input", value: work.phone, autocomplete: "off", maxlength: 32 });
    email.addEventListener("input", function () { work.email = email.value; touch(); });
    phone.addEventListener("input", function () { work.phone = phone.value; touch(); });
    return card("Contact details", [field("pf-email", "Contact email", email), field("pf-phone", "Contact phone", phone)], "Never shown on your profile. Shared only when you choose Share Card with someone who follows you back.");
  }

  function photoCard() {
    var grid = el("div", { class: "pf-photos", role: "radiogroup", "aria-label": "Profile photograph" });
    S.photos.forEach(function (p, i) {
      var label = (p.selectable ? "Photograph " + (i + 1) : "Photograph " + (i + 1) + ", not confirmed");
      var b = el("button", { type: "button", class: "pf-photo-opt", role: "radio", "aria-checked": p.selected ? "true" : "false", "aria-label": label + (p.selected ? ", selected" : ""), disabled: !p.selectable, "data-selected": p.selected ? "1" : false }, [
        p.selectable ? el("img", { src: "/my-photos/" + p.id + "/image", alt: "" }) : el("span", { class: "pf-photo-ph", "aria-hidden": "true" }), el("span", { text: p.selectable ? (p.selected ? "Selected" : "Use this") : "Not confirmed" })]);
      b.addEventListener("click", function () {
        PF.api("POST", "/my-photo", { photo_id: p.id }).then(function (res) {
          if (res.status === 200 && res.data) { S.photos = res.data.photos; PF.toast("Photograph selected"); S.card.has_selected_photo = true; refreshPreview().then(redraw); }
          else { PF.toast("We couldn't select that photograph.", "error"); }
        });
      });
      grid.appendChild(b);
    });
    var kids = S.photos.length ? [grid] : [el("p", { class: "pf-help", text: "No photographs yet. Add and confirm one in your private profile, then choose it here." }), el("a", { class: "pf-link", href: "/profile/", text: "Confirm a photograph" })];
    function slider(id, label, key) {
      var input = el("input", { type: "range", id: id, min: 0, max: 100, value: work[key], class: "pf-range" });
      input.addEventListener("input", function () { work[key] = parseInt(input.value, 10); touch(); });
      return field(id, label, input);
    }
    if (S.photos.length) { kids.push(slider("pf-fx", "Focus: left to right", "fx"), slider("pf-fy", "Focus: top to bottom", "fy")); }
    return card("Photograph", kids, "You can choose only photographs you have already confirmed. Uploading a new one is not available here yet. Focus only changes how a photo without a transparent background is cropped.");
  }

  /* ---------- preview ---------- */
  function previewCard() {
    var d = S.preview, host = el("aside", { class: "pf-card pf-preview", "aria-label": "What members see" }, [el("p", { class: "pf-eyebrow", text: "What members see" })]);
    if (d) {
      var photo = S.previewPhoto ? el("img", { class: "pf-preview__img", src: S.previewPhoto, alt: "" }) : el("div", { class: "pf-preview__img pf-preview__img--initial", "aria-hidden": "true", text: (d.name || "R").charAt(0).toUpperCase() });
      host.appendChild(el("div", { class: "pf-preview__who" }, [photo, el("div", {}, [el("p", { class: "pf-preview__name", text: d.name || "Rithavo member" }), el("p", { class: "pf-help", text: d.headline || (d.sections && d.sections.current_role) || "" })])]));
      var caps = (d.sections && d.sections.capabilities) || [];
      if (caps.length) { host.appendChild(el("ul", { class: "pf-pills pf-pills--sm" }, caps.slice(0, 4).map(function (c) { return el("li", { class: "pf-pill" }, [c]); }))); }
      host.appendChild(el("p", { class: "pf-help", id: "pf-preview-note", text: S.dirty ? "Showing your last saved version. Save to update it." : "This is your saved profile." }));
    } else { host.appendChild(el("p", { class: "pf-help", text: "The preview appears once your profile can be shown." })); }
    host.appendChild(el("a", { class: "pf-link", href: "/c/" + S.card.slug, text: "Open the full preview" }));
    return host;
  }
  function renderPreviewNote() { var n = document.getElementById("pf-preview-note"); if (n) { n.textContent = "Showing your last saved version. Save to update it."; } }

  function linkCard() {
    var url = window.location.origin + "/c/" + S.card.slug;
    var input = el("input", { type: "text", class: "pf-input", id: "pf-link", readonly: true, value: url, "aria-label": "Your profile link" });
    var copy = el("button", { type: "button", class: "pf-btn pf-btn--ghost", text: "Copy link" });
    copy.addEventListener("click", function () { PF.copyText(url).then(function (ok) { if (ok) { PF.toast("Link copied"); } else { input.focus(); input.select(); PF.toast("Select the link and copy it yourself.", "error"); } }); });
    return el("section", { class: "pf-card" }, [el("p", { class: "pf-eyebrow", text: "Your link" }), input, copy, el("p", { class: "pf-help", text: "Only signed-in Rithavo members can open it, and only while your profile is visible." })]);
  }

  /* ---------- save ---------- */
  function save() {
    var body = { is_discoverable: work.is_discoverable, headline_override: work.headline, contact_email: work.email, contact_phone: work.phone, photo_focus_x: work.fx, photo_focus_y: work.fy, section_order: work.order, capability_ids: work.caps };
    SHOW_LABELS.forEach(function (p) { body["show_" + p[0]] = !!work.show[p[0]]; });
    var btn = document.getElementById("pf-save"); btn.setAttribute("disabled", ""); btn.setAttribute("aria-busy", "true");
    PF.api("POST", "/my-public-profile", body).then(function (res) {
      btn.removeAttribute("aria-busy");
      var msg = document.getElementById("pf-save-msg");
      if (res.status === 200 && res.data) { S.card = res.data; copyFromServer(); PF.toast("Saved"); msg.textContent = ""; refreshPreview().then(redraw); return; }
      btn.removeAttribute("disabled");
      var text = (res.data && res.data.detail) || (res.status === 429 ? "You're going a little fast. Please wait a minute and try again." : "We couldn't save. Please try again.");
      msg.textContent = text; PF.toast(text, "error");
    });
  }

  function refreshPreview() {
    if (!S.card.is_discoverable && !S.card.has_selected_photo) { S.preview = null; return Promise.resolve(); }
    return PF.api("GET", "/public-profile/" + S.card.slug).then(function (res) {
      S.preview = res.status === 200 ? res.data : null;
      if (S.previewPhoto) { URL.revokeObjectURL(S.previewPhoto); S.previewPhoto = null; }
      if (!S.preview || !S.preview.photo || !S.preview.photo.present) { return; }
      return fetch("/public-profile/" + S.card.slug + "/photo", { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) { if (b) { S.previewPhoto = URL.createObjectURL(b); } }).catch(function () {});
    });
  }

  function redraw() {
    PF.clear(root);
    var form = el("div", { class: "pf-manage__form" }, [visibilityCard(), headlineCard(), showCard(), capsCard(), orderCard(), contactCard(), photoCard()]);
    var saveBtn = el("button", { type: "button", id: "pf-save", class: "pf-btn pf-btn--primary", disabled: !S.dirty }, [svg("check"), "Save changes"]);
    saveBtn.addEventListener("click", save);
    var discard = el("button", { type: "button", class: "pf-btn pf-btn--ghost", text: "Discard" });
    discard.addEventListener("click", function () { copyFromServer(); redraw(); PF.toast("Changes discarded"); });
    form.appendChild(el("div", { class: "pf-savebar" }, [saveBtn, discard, el("p", { id: "pf-save-msg", class: "pf-help", role: "alert" })]));
    root.appendChild(el("div", { class: "pf-manage" }, [
      el("header", { class: "pf-manage__head" }, [el("p", { class: "pf-eyebrow", text: "Your profile" }), el("h1", { class: "pf-name pf-name--md", text: "Public profile" }),
        el("p", { class: "pf-lede", text: "Choose what signed-in Rithavo members see when they open your profile." })]),
      el("div", { class: "pf-manage__grid" }, [form, el("div", { class: "pf-manage__side" }, [previewCard(), linkCard()])])
    ]));
    document.title = "Public profile | Rithavo";
    main.setAttribute("aria-busy", "false");
  }

  function start() {
    Promise.all([PF.api("GET", "/my-public-profile"), PF.api("GET", "/my-photos")]).then(function (r) {
      var c = r[0], p = r[1];
      if (c.status === 401) { window.location.replace("/sign-in/?next=/c/manage"); return; }
      if (c.status !== 200 || !c.data) {
        var retry = el("button", { type: "button", class: "pf-btn pf-btn--primary", text: "Try again" });
        retry.addEventListener("click", function () { window.location.reload(); });
        stateView(c.status === 404 ? "This page isn't available" : "We couldn't load your settings", c.status === 404 ? "Complete your private profile first, or check the link." : "Check your connection and try again.", c.status === 404 ? null : [retry]);
        return;
      }
      S.card = c.data; S.photos = (p.status === 200 && p.data && p.data.photos) || [];
      copyFromServer();
      refreshPreview().then(redraw);
    });
  }
  window.addEventListener("beforeunload", function (e) { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
  start();
})();
