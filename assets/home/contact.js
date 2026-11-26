/* Rithavo contact forms.
   Rithavo has no form backend on purpose: a form here only prepares an email to hello@rithavo.com, and nothing is stored by the site.
   Because a mailto: link silently does nothing when the visitor has no mail app set up, every form also shows the finished draft with
   three ways out: open the mail app, open Gmail, or copy the text. Nothing on any form is required. */
(function () {
  "use strict";
  var TO = "hello@rithavo.com";
  var doc = document;
  function $all(sel, ctx) { return Array.prototype.slice.call((ctx || doc).querySelectorAll(sel)); }
  function val(form, name) { var el = form.elements[name]; return el && el.value ? el.value.replace(/\s+$/g, "").replace(/^\s+/g, "") : ""; }
  function checked(form, name) { var el = form.querySelector('input[name="' + name + '"]:checked'); return el ? el.value : ""; }

  function copyText(text, done) {
    function fallback() {
      var t = doc.createElement("textarea"); t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0"; doc.body.appendChild(t); t.select();
      var ok = false; try { ok = doc.execCommand("copy"); } catch (e) { ok = false; } doc.body.removeChild(t); done(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(function () { done(true); }, fallback); } else { fallback(); }
  }
  function flash(btnOrWrap, ok) {
    var wrap = btnOrWrap.closest("[data-x-draft], .x-mailbox, .x-foot-cta, body") || doc.body;
    var note = wrap.querySelector(".x-copied");
    if (!note) { return; }
    note.textContent = ok ? "Copied" : "Copy failed. Select the text and copy it by hand.";
    note.hidden = false; window.clearTimeout(note._t); note._t = window.setTimeout(function () { note.hidden = true; }, 2600);
  }

  /* ---------- the draft each form builds ---------- */
  var BUILDERS = {
    business: function (f) {
      var biz = val(f, "business"), lines = [];
      function add(label, v) { if (v) { lines.push(label + ": " + v); } }
      lines.push("Hello Rithavo,", "", "I would like to talk about a business opportunity.", "");
      add("Business name", biz); add("Nature of business", val(f, "nature")); add("Size of business", checked(f, "size")); add("Interested in", checked(f, "interest"));
      add("Collaboration pace we have in mind", checked(f, "pace")); lines.push("");
      add("My name", val(f, "name")); add("Email", val(f, "email")); add("Phone", val(f, "phone")); add("Website", val(f, "site"));
      var msg = val(f, "message");
      if (msg) { lines.push("", "What we have in mind:", msg); }
      return { subject: "Business enquiry" + (biz ? " - " + biz : ""), body: lines.join("\n") };
    },
    help: function (f) {
      var kind = checked(f, "kind") || "Question", lines = [];
      function add(label, v) { if (v) { lines.push(label + ": " + v); } }
      lines.push("Hello Rithavo,", "", "Type: " + kind, "");
      var msg = val(f, "message"); if (msg) { lines.push(msg, ""); }
      add("Where it happened (page or product)", val(f, "where")); add("Device and browser", val(f, "device"));
      add("My name", val(f, "name")); add("Reply to", val(f, "email"));
      return { subject: kind + " - Rithavo", body: lines.join("\n") };
    },
    enterprise: function (f) {
      var org = val(f, "org"), lines = [];
      function add(label, v) { if (v) { lines.push(label + ": " + v); } }
      lines.push("Hello Rithavo,", "", "I would like to talk about enterprise solutions.", "");
      add("Organisation", org); add("Where we operate", val(f, "region")); add("Looking at", checked(f, "interest")); add("Timeline", checked(f, "timeline")); lines.push("");
      add("My name", val(f, "name")); add("Role", val(f, "role")); add("Email", val(f, "email")); add("Phone", val(f, "phone"));
      var msg = val(f, "message");
      if (msg) { lines.push("", "What we would like to achieve:", msg); }
      return { subject: "Enterprise enquiry" + (org ? " - " + org : ""), body: lines.join("\n") };
    },
    careers: function (f) {
      var lines = [], role = val(f, "area");
      function add(label, v) { if (v) { lines.push(label + ": " + v); } }
      lines.push("Hello Rithavo,", "", "Please keep me in mind when positions open.", "");
      add("Area I would work in", role); add("My name", val(f, "name")); add("Email", val(f, "email")); add("Profile or portfolio link", val(f, "link"));
      var msg = val(f, "message"); if (msg) { lines.push("", msg); }
      return { subject: "Interest in future positions" + (role ? " - " + role : ""), body: lines.join("\n") };
    }
  };

  function mailto(d) { return "mailto:" + TO + "?subject=" + encodeURIComponent(d.subject) + "&body=" + encodeURIComponent(d.body); }
  function gmail(d) { return "https://mail.google.com/mail/?view=cm&fs=1&to=" + encodeURIComponent(TO) + "&su=" + encodeURIComponent(d.subject) + "&body=" + encodeURIComponent(d.body); }

  /* ---------- required fields: inline messages, never an alert ---------- */
  function problem(el) {
    var v = (el.value || "").replace(/^\s+|\s+$/g, "");
    if (!v) { return "This is needed so we can reply."; }
    if (el.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)) { return "Enter an email address like name@company.com."; }
    if (el.type === "tel" && v.replace(/\D/g, "").length < 7) { return "Enter a number we can reach you on, with the country code."; }
    return "";
  }
  function setError(el, msg) {
    var wrap = el.closest(".x-field"), note = wrap.querySelector(".x-err");
    if (!msg) { wrap.classList.remove("has-error"); el.removeAttribute("aria-invalid"); el.removeAttribute("aria-describedby"); if (note) { note.remove(); } return; }
    wrap.classList.add("has-error"); el.setAttribute("aria-invalid", "true");
    if (!note) { note = doc.createElement("p"); note.className = "x-err"; note.id = el.id + "-err"; wrap.appendChild(note); }
    note.textContent = msg; el.setAttribute("aria-describedby", note.id);
  }
  function validate(form) {
    var bad = [], banner = form.querySelector(".x-formerr");
    $all("[required]", form).forEach(function (el) { var m = problem(el); setError(el, m); if (m) { bad.push(el); } });
    if (!banner) { banner = doc.createElement("p"); banner.className = "x-formerr"; banner.setAttribute("role", "alert"); form.insertBefore(banner, form.querySelector(".x-form-foot")); }
    banner.textContent = bad.length ? "Please complete the " + bad.length + " highlighted " + (bad.length === 1 ? "field" : "fields") + " marked with *." : "";
    banner.classList.toggle("is-on", bad.length > 0);
    if (bad.length) { bad[0].focus(); }
    return bad.length === 0;
  }
  $all("form[data-x-compose] [required]").forEach(function (el) {
    el.addEventListener("input", function () { var wrap = el.closest(".x-field"); if (wrap.classList.contains("has-error") && !problem(el)) { setError(el, ""); } });
    el.addEventListener("blur", function () { if ((el.value || "").length) { setError(el, problem(el)); } });
  });

  $all("form[data-x-compose]").forEach(function (form) {
    var kind = form.getAttribute("data-x-compose"), build = BUILDERS[kind];
    var tray = form.parentNode.querySelector("[data-x-draft]");
    if (!build || !tray) { return; }
    var pre = tray.querySelector("pre"), openMail = tray.querySelector("[data-x-mail]"), openGmail = tray.querySelector("[data-x-gmail]"), copy = tray.querySelector("[data-x-copy-draft]");
    var current = null;
    function prepare() {
      current = build(form);
      pre.textContent = "To: " + TO + "\nSubject: " + current.subject + "\n\n" + current.body;
      openMail.setAttribute("href", mailto(current)); openGmail.setAttribute("href", gmail(current));
      tray.classList.add("is-on");
      return current;
    }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!validate(form)) { tray.classList.remove("is-on"); return; }
      var d = prepare();
      tray.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
      window.location.href = mailto(d);
    });
    copy.addEventListener("click", function () { copyText(pre.textContent, function (ok) { flash(copy, ok); }); });
  });

  /* ---------- the plain address: copy it, or open the mail app ---------- */
  $all("[data-x-copy]").forEach(function (b) {
    b.addEventListener("click", function () { copyText(b.getAttribute("data-x-copy"), function (ok) { flash(b, ok); }); });
  });
})();
