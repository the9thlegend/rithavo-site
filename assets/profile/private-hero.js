/* Portrait-led hero for the signed-in private profile (/profile/). It reuses the member's own public-profile payload, so it shows the
   confirmed photograph, name, headline and capabilities exactly as saved. The links to the owner panel are only drawn when the
   management API answers 200; while the feature is off, or on any error, the page stays exactly as it was. Text is inserted with textContent. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var ICONS = {
    layers: "M12 3l9 5-9 5-9-5 9-5z M3 13l9 5 9-5 M3 17.5l9 5 9-5",
    target: "M12 21a9 9 0 100-18 9 9 0 000 18z M12 16.5a4.5 4.5 0 100-9 4.5 4.5 0 000 9z M12 12.8a.8.8 0 100-1.6.8.8 0 000 1.6z",
    users: "M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7z M2.5 20c0-3.3 2.9-6 6.5-6s6.5 2.7 6.5 6 M16.5 4.4a3.5 3.5 0 010 6.6 M18 14.3c2 .8 3.5 2.7 3.5 5.7",
    chart: "M4 20V10 M10 20V4 M16 20v-8 M22 20H2",
    pin: "M12 21s7-6.2 7-11.5A7 7 0 005 9.5C5 14.8 12 21 12 21z M12 12a2.5 2.5 0 100-5 2.5 2.5 0 000 5z",
    check: "M5 12.5l4.5 4.5L19 7.5"
  };
  var CHIP_ICONS = ["layers", "target", "users", "chart"];
  var main = document.querySelector(".app-main");
  if (!main || !window.fetch) { return; }

  function svg(name) {
    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.8"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(NS, "path"); p.setAttribute("d", ICONS[name] || ICONS.check); s.appendChild(p);
    return s;
  }
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === "text") { n.textContent = attrs[k]; } else { n.setAttribute(k, attrs[k]); } });
    (kids || []).forEach(function (c) { if (c) { n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); } });
    return n;
  }
  function getJson(url) {
    return fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } }).then(function (r) { return r.status === 200 ? r.json() : null; });
  }

  getJson("/api/my-public-profile").then(function (mine) {
    if (!mine || !mine.slug) { return null; }
    return getJson("/api/public-profile/" + encodeURIComponent(mine.slug)).then(function (d) { return d ? { d: d, slug: mine.slug } : null; });
  }).then(function (got) {
    if (!got) { return; }
    var d = got.d, s = d.sections || {};
    var hasPhoto = !!(d.photo && d.photo.present), hasPhotoNow = hasPhoto;
    var photoUrl = "/api/public-profile/" + encodeURIComponent(got.slug) + "/photo";
    var names = (s.capabilities && s.capabilities.length ? s.capabilities : (s.skills || [])).slice(0, 4);
    var chips = names.map(function (n, i) { return el("div", { "class": "ph-chip ph-chip--" + (i + 1) }, [svg(CHIP_ICONS[i % 4]), el("span", { text: n })]); });

    function build(alpha, src) {
      var stage;
      if (hasPhoto) {
        var img = el("img", { "class": "ph-photo", src: src, alt: "Your confirmed photograph" });
        if (alpha) { stage = el("div", { "class": "ph-stage" }, [el("div", { "class": "ph-blob", "aria-hidden": "true" }), el("div", { "class": "ph-ring", "aria-hidden": "true" }), img].concat(chips)); }
        else {
          stage = el("div", { "class": "ph-stage ph-stage--framed" }, [el("div", { "class": "ph-blob", "aria-hidden": "true" }), el("div", { "class": "ph-frame" }, [img])].concat(chips));
          var f = d.photo.focus;
          if (f) { stage.style.setProperty("--pf-fx", f.x + "%"); stage.style.setProperty("--pf-fy", f.y + "%"); }
        }
      } else {
        stage = el("div", { "class": "ph-stage ph-stage--initial" }, [el("div", { "class": "ph-initial", "aria-hidden": "true", text: (d.name || "R").trim().charAt(0).toUpperCase() })].concat(chips));
      }
      var meta = [];
      if (s.location) { meta.push(el("span", {}, [svg("pin"), s.location])); }
      if ((s.professional_intent || []).length) { meta.push(el("span", {}, [el("i", { "class": "ph-dot", "aria-hidden": "true" }), "Open to " + s.professional_intent.join(", ")])); }
      var badges = [];
      if (d.trust && d.trust.label && d.trust.label !== "Unverified") { badges.push(el("span", { "class": "ph-badge" }, [svg("check"), d.trust.label])); }
      var headline = d.headline || s.current_role || "";
      var hero = el("section", { "class": "ph-hero", "aria-labelledby": "ph-name" }, [
        el("div", { "class": "ph-hero__copy" }, [
          el("p", { "class": "ph-eyebrow", text: "Hello, I'm" }),
          el("h1", { "class": "ph-name", id: "ph-name", text: d.name || "Your profile" }),
          headline ? el("p", { "class": "ph-headline ph-gradtext", text: headline }) : null,
          badges.length ? el("div", { "class": "ph-badges" }, badges) : null,
          el("div", { "class": "ph-actions" }, [
            el("a", { "class": "btn btn-primary", href: "/profile/edit", text: "Edit your profile" }),
            el("a", { "class": "btn btn-ghost", href: "/c/" + got.slug, text: "View public profile" }),
            el("a", { "class": "btn btn-ghost", href: "/c/manage", text: "Public profile settings" })
          ]),
          meta.length ? el("div", { "class": "ph-meta" }, meta) : null
        ]),
        el("div", { "class": "ph-hero__portrait" }, [stage])
      ]);
      var anchor = main.querySelector("p.label");
      main.insertBefore(hero, anchor || main.firstChild);
      document.documentElement.classList.add("ph-on");
    }

    if (!hasPhoto) { hasPhoto = false; build(false, null); return; }
    // one request: the same response gives the transparency flag and the image. A cut-out is used only when the owner's image
    // really has transparency; otherwise it is framed.
    fetch(photoUrl, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (r.status !== 200) { hasPhoto = false; build(false, null); return; }
      var alpha = r.headers.get("X-Rithavo-Photo-Alpha") === "1";
      build(alpha, "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==");  // draw the hero now; the photograph fills in as it arrives
      return r.blob().then(function (b) { var img = main.querySelector(".ph-photo"); if (img) { img.src = URL.createObjectURL(b); } });
    }).catch(function () { hasPhoto = false; build(false, null); });
  }).catch(function () { /* the page works exactly as before */ });
})();
