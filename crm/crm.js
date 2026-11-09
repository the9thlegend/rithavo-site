/* CRM Lite front end. Vanilla JS. Every record shown comes from /api/crm/*, which decides visibility
   server-side; nothing here filters by owner and nothing trusts a client-side id. All data is escaped. */
(() => {
  const API = "/api/crm";
  const main = document.getElementById("crm-main");
  const nav = document.getElementById("crm-nav");
  let ME = null;
  let badges = {};

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const label = (m, k) => (m && m[k]) || String(k || "").replace(/_/g, " ");
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—");
  const dtm = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
  const money = (n) => (n == null ? "—" : "₹" + Number(n).toLocaleString("en-IN"));

  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    const resp = await fetch(API + path, init);
    let data = null;
    try { data = await resp.json(); } catch (e) { /* empty */ }
    return { ok: resp.ok, status: resp.status, data };
  }
  function errText(r) {
    const d = r.data && r.data.detail;
    if (!d) return `Request failed (${r.status}).`;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => x.msg).join(" ");
    return d.message || d.detail || `Request failed (${r.status}).`;
  }
  const banner = (kind, text) => `<div class="app-banner app-banner-${kind}" role="${kind === "error" ? "alert" : "status"}">${esc(text)}</div>`;
  const pill = (stage) => {
    const c = stage === "Won" ? "s-won" : stage === "Lost" ? "s-lost" : stage === "On Hold" ? "s-hold" : stage === "Founder Review" ? "s-review" : "";
    return `<span class="crm-pill ${c}">${esc(stage)}</span>`;
  };
  const isFounder = () => ME && ME.role === "founder";

  /* ---------- sign-in gate and MFA ---------- */
  async function boot() {
    const r = await api("/me");
    if (r.status === 401) { location.href = "/sign-in/?next=" + encodeURIComponent("/crm/"); return; }
    if (r.status === 404) return gateMsg("The CRM is not available on this deployment.");
    if (r.status === 403) return gateMsg("CRM access has not been activated for your account. Ask the Founder to activate it.");
    if (!r.ok) return gateMsg(errText(r));
    ME = r.data;
    document.getElementById("crm-who").innerHTML = `<span>${esc(ME.email)}</span><span class="crm-pill">${esc(ME.role)}</span>`;
    if (ME.mfa === "enrol") return renderEnrol();
    if (ME.mfa === "verify") return renderVerify();
    if (ME.mfa === "locked_soft") return gateMsg("Too many incorrect codes. Try again in a few minutes.");
    if (ME.mfa === "locked_hard") return gateMsg("Your account is locked. Ask the Founder to unlock it.");
    return startApp();
  }
  function gateMsg(t) { main.innerHTML = `<div class="crm-narrow"><h1>CRM</h1>${banner("info", t)}<p><a href="/home/">Back to Rithavo</a></p></div>`; }

  async function renderEnrol(notice) {
    main.innerHTML = `<div class="crm-narrow"><h1>Set up your authenticator</h1>${notice ? banner("error", notice) : ""}
      <p class="crm-sub">The CRM needs a second step every time you sign in. Add this key to an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, Authy), then enter the 6-digit code it shows.</p>
      <div id="enrol-body" class="app-skeleton">Preparing…</div></div>`;
    const r = await api("/mfa/enrol/start", { method: "POST" });
    const body = document.getElementById("enrol-body");
    body.className = "";
    if (!r.ok) { body.innerHTML = banner("error", errText(r)); return; }
    body.innerHTML = `
      <p class="crm-h">Setup key</p><code class="crm-code" id="enrol-key">${esc(r.data.secret)}</code>
      <p class="hint" style="font-size:.8rem;color:var(--ink-faint)">Choose "enter a setup key" in your app. Time-based, 6 digits. Or open <a href="${esc(r.data.otpauth_uri)}">this link</a> on the device that has the app.</p>
      <form id="enrol-form" style="margin-top:18px"><div class="app-field"><label for="enrol-code">6-digit code</label>
      <input id="enrol-code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{6,7}" required></div>
      <button class="crm-btn primary" type="submit">Confirm and continue</button></form><div id="enrol-msg"></div>`;
    document.getElementById("enrol-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const c = await api("/mfa/enrol/confirm", { method: "POST", body: { code: document.getElementById("enrol-code").value } });
      if (c.status === 409 && errText(c).startsWith("Start MFA setup first")) {
        // The server that answered has no pending setup for you (it is a different instance from the one that issued the key).
        // Issue a fresh key rather than leave a dead end; the earlier key will never work, so remove it from your app.
        return renderEnrol("The setup you started was not found, so a new key has been issued. Delete the earlier entry from your authenticator app and add this one.");
      }
      if (!c.ok) { document.getElementById("enrol-msg").innerHTML = banner("error", errText(c)); return; }
      main.innerHTML = `<div class="crm-narrow"><h1>Save your recovery codes</h1>
        <p class="crm-sub">Each code works once if you lose your authenticator. They are shown only now. Store them in a password manager, not in the CRM or an email.</p>
        <ul class="crm-codes">${c.data.recovery_codes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <button class="crm-btn primary" id="codes-done" type="button">I have saved them</button></div>`;
      document.getElementById("codes-done").addEventListener("click", boot);
    });
  }

  function renderVerify(msg) {
    main.innerHTML = `<div class="crm-narrow"><h1>Enter your code</h1>
      <p class="crm-sub">Open your authenticator app and enter the current 6-digit code.</p>
      <form id="vf"><div class="app-field"><label for="vcode">6-digit code</label>
      <input id="vcode" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{6,7}" required autofocus></div>
      <button class="crm-btn primary" type="submit">Continue</button></form><div id="vmsg">${msg ? banner("error", msg) : ""}</div>
      <p class="crm-h">Lost your authenticator?</p>
      <form id="rf"><div class="app-field"><label for="rcode">Recovery code</label><input id="rcode" autocomplete="off" placeholder="XXXX-XXXX-XXXX"></div>
      <button class="crm-btn" type="submit">Use a recovery code</button></form></div>`;
    document.getElementById("vf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const r = await api("/mfa/verify", { method: "POST", body: { code: document.getElementById("vcode").value } });
      if (r.ok) return boot();
      document.getElementById("vmsg").innerHTML = banner("error", errText(r));
    });
    document.getElementById("rf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const r = await api("/mfa/recover", { method: "POST", body: { code: document.getElementById("rcode").value } });
      if (r.ok) return boot();
      document.getElementById("vmsg").innerHTML = banner("error", errText(r));
    });
  }

  /* ---------- app shell ---------- */
  const NAV = [
    ["overview", "Overview"], ["pipeline", "Pipeline"], ["leads", "Leads"], ["followups", "Follow-ups"],
    ["companies", "Companies"], ["contacts", "Contacts"], ["reports", "Reports"],
  ];
  const NAV_FOUNDER = [["reviews", "Founder review"], ["staff", "Staff and security"], ["retention", "Retention"], ["audit", "Audit log"]];

  function startApp() {
    nav.hidden = false;
    document.getElementById("crm-search").hidden = false;
    document.getElementById("crm-search").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = document.getElementById("crm-q").value.trim();
      if (q.length >= 2) location.hash = "#/search/" + encodeURIComponent(q);
    });
    window.addEventListener("hashchange", route);
    route();
  }
  function drawNav(active) {
    const item = ([k, t]) => `<a href="#/${k}" ${active === k ? 'aria-current="page"' : ""}>${esc(t)}${badges[k] ? `<span class="crm-badge">${badges[k]}</span>` : ""}</a>`;
    nav.innerHTML = NAV.map(item).join("") + (isFounder() ? `<div class="sep">Founder</div>` + NAV_FOUNDER.map(item).join("") : "") +
      `<div class="sep">Session</div><a href="#/signout" id="nav-signout">Lock CRM</a>`;
    document.getElementById("nav-signout").addEventListener("click", async (e) => {
      e.preventDefault(); await api("/mfa/signout", { method: "POST" }); location.hash = ""; location.reload();
    });
  }

  async function guard(r) {
    // The server remains the authority: a lapsed MFA stamp (idle/absolute limit) returns to the code screen.
    if (r.status === 401 && r.data && r.data.detail && r.data.detail.code) { ME.mfa = r.data.detail.code.includes("enrol") ? "enrol" : "verify"; ME.mfa === "enrol" ? renderEnrol() : renderVerify("Your session timed out. Enter a new code."); nav.hidden = true; return false; }
    if (r.status === 401) { location.href = "/sign-in/?next=" + encodeURIComponent("/crm/"); return false; }
    return true;
  }

  async function route() {
    const parts = (location.hash.replace(/^#\/?/, "") || "overview").split("/");
    const [page, arg] = parts;
    drawNav(page);
    main.innerHTML = `<div class="app-skeleton">Loading…</div>`;
    main.focus({ preventScroll: true });
    const pages = { overview, pipeline, leads, followups, companies, company, contacts, opportunity, reviews, reports, staff, retention, audit, search };
    try { await (pages[page] || overview)(decodeURIComponent(arg || "")); } catch (e) { main.innerHTML = banner("error", "Something went wrong loading this page."); }
    refreshBadges();
  }
  async function refreshBadges() {
    const r = await api("/overview");
    if (!r.ok) return;
    badges = { followups: (r.data.followups_overdue || 0) + (r.data.followups_due_today || 0), reviews: r.data.reviews_pending || 0 };
    const page = (location.hash.replace(/^#\/?/, "") || "overview").split("/")[0];
    drawNav(page);
  }

  const table = (heads, rows, empty) => rows.length
    ? `<div class="crm-tablewrap"><table class="crm-table"><thead><tr>${heads.map((h) => `<th scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`
    : `<div class="crm-empty">${esc(empty)}</div>`;
  const oppLink = (o) => `<a href="#/opportunity/${o.id}">${esc(o.ref)}</a>`;

  /* ---------- pages ---------- */
  async function overview() {
    const r = await api("/overview"); if (!(await guard(r))) return;
    const o = r.data;
    const open = Object.entries(o.by_stage).filter(([k]) => !["Won", "Lost"].includes(k)).reduce((a, [, n]) => a + n, 0);
    const stat = (n, t, warn) => `<div class="crm-stat ${warn && n ? "warn" : ""}"><b>${n}</b><span>${esc(t)}</span></div>`;
    main.innerHTML = `<h1>Overview</h1><p class="crm-sub">${isFounder() ? "All records across the team." : "Your records only."}</p>
      <div class="crm-grid">${stat(open, "Open opportunities")}${stat(o.followups_due_today, "Follow-ups due today")}
      ${stat(o.followups_overdue, "Follow-ups overdue", true)}${stat(o.on_hold_review_due, "On hold, review due", true)}
      ${stat(o.no_contact_90d, "No meaningful contact, 90+ days", true)}
      ${isFounder() ? stat(o.reviews_pending, "Awaiting your review", true) + stat(o.needs_owner, "Companies needing an owner", true) + stat(o.retention_due, "Retention flags", true) : ""}</div>
      <p class="crm-h">By stage</p>${table(["Stage", "Opportunities"], Object.entries(o.by_stage).map(([k, n]) => `<tr><td>${pill(k)}</td><td>${n}</td></tr>`), "Nothing here yet. Capture your first lead.")}
      <div id="nc"></div>`;
    if (o.no_contact_90d) {
      const q = await api("/review/no-contact");
      document.getElementById("nc").innerHTML = `<p class="crm-h">90-day review: no meaningful contact yet</p><p class="crm-sub">Log a two-way contact, mark the opportunity Lost with a reason, or ask the Founder to archive it.</p>` +
        table(["Ref", "Company", "Stage", "Created"], q.data.map((x) => `<tr><td>${oppLink(x)}</td><td>${esc(x.company_name)}</td><td>${pill(x.stage)}</td><td>${day(x.created_at)}</td></tr>`), "");
    }
  }

  async function pipeline() {
    const r = await api("/opportunities"); if (!(await guard(r))) return;
    const stages = ME.stages.filter((s) => !["Won", "Lost", "On Hold"].includes(s));
    const by = {}; r.data.forEach((o) => (by[o.stage] = by[o.stage] || []).push(o));
    const card = (o) => `<a class="crm-opp" href="#/opportunity/${o.id}"><b>${esc(o.title)}</b><small>${esc(o.ref)} · ${esc(o.company_name)}${isFounder() ? " · " + esc(o.owner_label) : ""}</small></a>`;
    const col = (s) => `<section class="crm-col" aria-label="${esc(s)}"><h3>${esc(s)} <span>${(by[s] || []).length}</span></h3>${(by[s] || []).map(card).join("") || ""}</section>`;
    main.innerHTML = `<h1>Pipeline</h1><p class="crm-sub">Open opportunities by stage. Moving a stage happens on the opportunity, where the checks are shown.</p>
      <div class="crm-board">${stages.map(col).join("")}</div>
      <p class="crm-h">Closed and paused</p><div class="crm-board">${["On Hold", "Lost", "Won"].map(col).join("")}</div>`;
  }

  async function leads(arg, prefill) {
    const r = await api("/opportunities"); if (!(await guard(r))) return;
    const rows = r.data.filter((o) => ["New Lead", "Contacted"].includes(o.stage));
    const opt = (arr, map) => arr.map((k) => `<option value="${esc(k)}">${esc(label(map, k))}</option>`).join("");
    const p = prefill || {};
    main.innerHTML = `<h1>Leads</h1><p class="crm-sub">A lead is an opportunity in New Lead. Capturing one creates the company, the contact and the opportunity together, and you own all three.</p>
      <div class="crm-card"><form id="lead-form" class="crm-form" novalidate>
        ${p.company_id ? `<input type="hidden" name="company_id" value="${esc(p.company_id)}">` : `
        <div class="app-field"><label for="f-company">Company</label><input id="f-company" name="company_name" required maxlength="160"></div>
        <div class="app-field"><label for="f-industry">Industry (optional)</label><input id="f-industry" name="industry" maxlength="100"></div>`}
        <div class="app-field"><label for="f-contact">Contact name</label><input id="f-contact" name="contact_name" required maxlength="120"></div>
        <div class="app-field"><label for="f-email">Contact email</label><input id="f-email" name="contact_email" type="email" maxlength="200"></div>
        <div class="app-field full"><label for="f-title">Opportunity</label><input id="f-title" name="title" required maxlength="160" placeholder="What are they asking for?"></div>
        <div class="app-field"><label for="f-pillar">Service pillar</label><select id="f-pillar" name="service_pillar">${opt(ME.pillars, {})}</select></div>
        <div class="app-field"><label for="f-source">Source</label><select id="f-source" name="source">${opt(ME.sources, {})}</select></div>
        <div class="full"><button class="crm-btn primary" type="submit">Capture lead</button></div>
      </form><div id="lead-msg"></div></div>
      <p class="crm-h">Your leads</p>${table(["Ref", "Opportunity", "Company", "Stage", "Created"],
        rows.map((o) => `<tr class="row-link"><td>${oppLink(o)}</td><td>${esc(o.title)}</td><td>${esc(o.company_name)}</td><td>${pill(o.stage)}</td><td>${day(o.created_at)}</td></tr>`), "No leads yet.")}`;
    main.querySelectorAll("tr.row-link").forEach((tr) => tr.addEventListener("click", (e) => { if (e.target.tagName !== "A") tr.querySelector("a").click(); }));
    document.getElementById("lead-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target).entries());
      const res = await api("/leads", { method: "POST", body: f });
      const msg = document.getElementById("lead-msg");
      if (res.ok) { location.hash = "#/opportunity/" + res.data.id; return; }
      const d = res.data && res.data.detail;
      if (d && d.code === "duplicate_own") {
        msg.innerHTML = banner("info", d.message) + `<div class="crm-actions"><a class="crm-btn" href="#/company/${d.company_id}">Open that company</a></div>`;
      } else if (d && d.code === "duplicate_found") {
        msg.innerHTML = banner("info", d.message) + `<div class="crm-actions"><a class="crm-btn" href="#/company/${d.company_id}">Open it</a></div>`;
      } else msg.innerHTML = banner("error", errText(res));
    });
  }

  async function followups() {
    const r = await api("/followups"); if (!(await guard(r))) return;
    main.innerHTML = `<h1>Follow-ups</h1><p class="crm-sub">${isFounder() ? "Every open follow-up across the team." : "Your open follow-ups, earliest first."}</p>` +
      table(["Due", "Opportunity", "Company", "Note", ...(isFounder() ? ["Owner"] : []), ""], r.data.map((f) =>
        `<tr><td>${day(f.due_date)} ${f.overdue ? '<span class="crm-pill s-lost">overdue</span>' : ""}</td><td>${oppLink({ id: f.opportunity_id, ref: f.ref })} ${esc(f.title)}</td><td>${esc(f.company_name)}</td><td>${esc(f.note || "")}</td>${isFounder() ? `<td>${esc(f.owner_label)}</td>` : ""}<td><button class="crm-btn" data-done="${f.id}">Mark done</button></td></tr>`), "Nothing is waiting on you.");
    main.querySelectorAll("[data-done]").forEach((b) => b.addEventListener("click", async () => { await api("/followups/" + b.dataset.done, { method: "PATCH", body: { done: true } }); followups(); }));
  }

  async function companies() {
    const r = await api("/companies"); if (!(await guard(r))) return;
    main.innerHTML = `<h1>Companies</h1><p class="crm-sub">Each company has one owner. Its contacts and opportunities belong to the same owner.</p>` +
      table(["Company", "Industry", "Contacts", "Opportunities", ...(isFounder() ? ["Owner"] : []), "Last meaningful contact"], r.data.map((c) =>
        `<tr><td><a href="#/company/${c.id}">${esc(c.name)}</a></td><td>${esc(c.industry || "—")}</td><td>${c.contact_count}</td><td>${c.opportunity_count}</td>${isFounder() ? `<td>${esc(c.owner_label)}</td>` : ""}<td>${day(c.last_meaningful_contact_at)}</td></tr>`), "No companies yet.");
  }

  async function company(id) {
    const r = await api("/companies/" + id); if (!(await guard(r))) return;
    if (r.status === 404) { main.innerHTML = banner("error", "Not found."); return; }
    const c = r.data; let staffOpts = "";
    if (isFounder()) {
      const s = await api("/staff"); staffOpts = `<option value="">Choose new owner</option>` + (s.data || []).filter((x) => x.active).map((x) => `<option value="${x.user_id}">${esc(x.display_name)}</option>`).join("") + `<option value="founder">Founder</option>`;
    }
    main.innerHTML = `<p><a href="#/companies">← Companies</a></p><h1>${esc(c.name)}</h1>
      <dl class="crm-kv"><dt>Owner</dt><dd>${esc(c.owner_label)}</dd><dt>Industry</dt><dd>${esc(c.industry || "—")}</dd><dt>Website</dt><dd>${esc(c.website || "—")}</dd><dt>Last meaningful contact</dt><dd>${day(c.last_meaningful_contact_at)}</dd></dl>
      <p class="crm-h">Contacts</p>${table(["Name", "Role", "Email", "Phone"], c.contacts.map((k) => `<tr><td>${esc(k.full_name)}</td><td>${esc(k.role_title || "—")}</td><td>${esc(k.email || "—")}</td><td>${esc(k.phone || "—")}</td></tr>`), "No contacts.")}
      <form id="addk" class="crm-form" style="margin-top:12px"><div class="app-field"><label for="k-name">Add a contact</label><input id="k-name" name="full_name" required maxlength="120" placeholder="Full name"></div><div class="app-field"><label for="k-mail">Email</label><input id="k-mail" name="email" type="email"></div><div class="full"><button class="crm-btn" type="submit">Add contact</button></div></form><div id="k-msg"></div>
      <p class="crm-h">Opportunities</p>${table(["Ref", "Opportunity", "Stage", "Updated"], c.opportunities.map((o) => `<tr><td>${oppLink(o)}</td><td>${esc(o.title)}</td><td>${pill(o.stage)}</td><td>${day(o.updated_at)}</td></tr>`), "None.")}
      <p class="crm-actions"><a class="crm-btn" href="#/leads" id="newopp">Capture another lead</a></p>
      ${isFounder() ? `<p class="crm-h">Founder actions</p><div class="crm-actions"><select id="re-owner" aria-label="New owner">${staffOpts}</select><button class="crm-btn" id="re-go">Reassign the whole company</button><button class="crm-btn danger" id="arch">Archive</button></div><div id="fa-msg"></div>` : ""}`;
    document.getElementById("newopp").addEventListener("click", (e) => { e.preventDefault(); leads("", { company_id: c.id }); });
    document.getElementById("addk").addEventListener("submit", async (e) => { e.preventDefault(); const res = await api(`/companies/${id}/contacts`, { method: "POST", body: Object.fromEntries(new FormData(e.target).entries()) }); res.ok ? company(id) : (document.getElementById("k-msg").innerHTML = banner("error", errText(res))); });
    if (isFounder()) {
      document.getElementById("re-go").addEventListener("click", async () => {
        const v = document.getElementById("re-owner").value; if (!v) return;
        const fid = v === "founder" ? ME.user_id : Number(v);
        const res = await api(`/companies/${id}/reassign`, { method: "POST", body: { owner_user_id: fid } });
        document.getElementById("fa-msg").innerHTML = res.ok ? banner("success", `Moved ${res.data.contacts} contacts, ${res.data.opportunities} opportunities and ${res.data.followups} follow-ups.`) : banner("error", errText(res));
        if (res.ok) setTimeout(() => company(id), 700);
      });
      document.getElementById("arch").addEventListener("click", async () => { const res = await api(`/companies/${id}/archive`, { method: "POST" }); res.ok ? (location.hash = "#/companies") : (document.getElementById("fa-msg").innerHTML = banner("error", errText(res))); });
    }
  }
  async function contacts() {
    const r = await api("/contacts"); if (!(await guard(r))) return;
    main.innerHTML = `<h1>Contacts</h1>` + table(["Name", "Company", "Role", "Email", "Last meaningful contact"], r.data.map((k) =>
      `<tr><td>${esc(k.full_name)}</td><td><a href="#/company/${k.company_id}">${esc(k.company_name)}</a></td><td>${esc(k.role_title || "—")}</td><td>${esc(k.email || "—")}</td><td>${day(k.last_meaningful_contact_at)}</td></tr>`), "No contacts yet.");
  }

  async function search(q) {
    const r = await api("/search?q=" + encodeURIComponent(q)); if (!(await guard(r))) return;
    const s = r.data || {};
    if (!r.ok) { main.innerHTML = banner("error", errText(r)); return; }
    const none = !(s.companies.length || s.contacts.length || s.opportunities.length);
    main.innerHTML = `<h1>Search</h1><p class="crm-sub">Results for “${esc(q)}” among ${isFounder() ? "all records" : "your records"}.</p>` + (none ? `<div class="crm-empty">No matches.</div>` : "") +
      (s.companies.length ? `<p class="crm-h">Companies</p>` + table(["Company"], s.companies.map((c) => `<tr><td><a href="#/company/${c.id}">${esc(c.name)}</a></td></tr>`), "") : "") +
      (s.contacts.length ? `<p class="crm-h">Contacts</p>` + table(["Name"], s.contacts.map((c) => `<tr><td><a href="#/company/${c.company_id}">${esc(c.full_name)}</a></td></tr>`), "") : "") +
      (s.opportunities.length ? `<p class="crm-h">Opportunities</p>` + table(["Ref", "Title", "Stage"], s.opportunities.map((o) => `<tr><td>${oppLink(o)}</td><td>${esc(o.title)}</td><td>${pill(o.stage)}</td></tr>`), "") : "");
  }

  async function opportunity(id) {
    const r = await api("/opportunities/" + id); if (!(await guard(r))) return;
    if (r.status === 404) { main.innerHTML = banner("error", "Not found."); return; }
    const o = r.data; const closed = ["Won", "Lost"].includes(o.stage);
    const reasonLine = o.reason_code ? `<dt>Reason</dt><dd>${esc(label(o.stage === "Lost" ? ME.lost_reasons : ME.hold_reasons, o.reason_code))}: ${esc(o.reason_note || "")}${o.hold_review_date ? ` · review on ${day(o.hold_review_date)}` : ""}${o.lost_hold_review === "pending" ? ' <span class="crm-pill s-review">awaiting Founder review</span>' : ""}</dd>` : "";
    const rsn = (m) => Object.entries(m).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
    const nextBtns = o.allowed_next.map((s) => `<button class="crm-btn primary" data-stage="${esc(s)}">Move to ${esc(s)}</button>`).join("");
    main.innerHTML = `<p><a href="#/pipeline">← Pipeline</a></p><h1>${esc(o.title)}</h1>
      <p class="crm-sub">${esc(o.ref)} · <a href="#/company/${o.company.id}">${esc(o.company.name)}</a> · ${pill(o.stage)}</p><div id="op-msg"></div>
      <div class="crm-card"><dl class="crm-kv"><dt>Owner</dt><dd>${esc(o.owner_label)}</dd><dt>Contact</dt><dd>${o.contact ? esc(o.contact.full_name) + (o.contact.email ? " · " + esc(o.contact.email) : "") : "—"}</dd>
        <dt>Pillar</dt><dd>${esc(label({}, o.service_pillar))}</dd><dt>Source</dt><dd>${esc(o.source)}</dd><dt>Value</dt><dd>${money(o.estimated_value_inr)}</dd>
        <dt>Proposal</dt><dd>${esc(o.proposal_status)}</dd><dt>Last meaningful contact</dt><dd>${day(o.last_meaningful_contact_at)}</dd>${reasonLine}</dl></div>
      ${closed || o.stage === "On Hold" ? "" : `<div class="crm-actions">${nextBtns}</div>`}
      ${o.stage === "Founder Review" ? banner("info", "Waiting for the Founder's review.") : ""}
      ${o.stage === "On Hold" ? `<form id="resume-form" class="crm-card"><div class="app-field"><label for="rs-note">Resume note</label><input id="rs-note" name="note" required minlength="5"></div><button class="crm-btn primary">Resume opportunity</button></form>` : ""}
      ${o.stage === "Lost" && isFounder() ? `<form id="reopen-form" class="crm-card"><div class="app-field"><label for="ro-note">Reopen note</label><input id="ro-note" name="note" required minlength="5"></div><button class="crm-btn primary">Reopen (Founder)</button></form>` : ""}
      ${closed || o.archived ? "" : `<details class="crm-card"><summary><b>Details and requirement summary</b></summary><form id="edit-form" class="crm-form" style="margin-top:12px">
        <div class="app-field full"><label for="e-title">Title</label><input id="e-title" name="title" value="${esc(o.title)}"></div>
        <div class="app-field full"><label for="e-req">Requirement summary</label><textarea id="e-req" name="requirement_summary">${esc(o.requirement_summary || "")}</textarea></div>
        <div class="app-field"><label for="e-val">Estimated value (INR)</label><input id="e-val" name="estimated_value_inr" type="number" min="0" value="${o.estimated_value_inr ?? ""}"></div>
        <div class="full"><button class="crm-btn" type="submit">Save</button></div></form></details>`}
      <div class="crm-card"><b>Log an activity</b><form id="act-form" class="crm-form" style="margin-top:10px">
        <div class="app-field"><label for="a-kind">Type</label><select id="a-kind" name="kind"><option>note</option><option>call</option><option>email</option><option>whatsapp</option><option>meeting</option><option>assessment</option></select></div>
        <div class="app-field"><label class="crm-check" for="a-mean"><input id="a-mean" name="meaningful" type="checkbox"> Meaningful contact</label><span class="hint">A two-way conversation or a documented meeting or call that advanced or clarified the opportunity. Unanswered messages do not count.</span></div>
        <div class="app-field full"><label for="a-sum">What happened</label><textarea id="a-sum" name="summary" required></textarea></div>
        <div class="full"><button class="crm-btn" type="submit">Add to history</button></div></form></div>
      <div class="crm-card"><b>Schedule a follow-up</b><form id="fu-form" class="crm-form" style="margin-top:10px"><div class="app-field"><label for="fu-date">Due date</label><input id="fu-date" name="due_date" type="date" required></div><div class="app-field"><label for="fu-note">Note</label><input id="fu-note" name="note"></div><div class="full"><button class="crm-btn" type="submit">Add follow-up</button></div></form>
        ${o.followups.length ? `<ul class="crm-timeline" style="margin-top:12px">${o.followups.map((f) => `<li>${day(f.due_date)} · ${esc(f.note || "")} ${f.done_at ? "· done" : `<button class="crm-btn" data-fudone="${f.id}">Done</button>`}</li>`).join("")}</ul>` : ""}</div>
      ${closed || o.archived ? "" : `<div class="crm-card"><b>Lost, on hold or other requests</b>
        ${o.stage === "Proposal Preparation" && !["requested", "approved"].includes(o.proposal_status) ? `<div class="crm-actions"><button class="crm-btn" id="req-com">Request commercial approval</button></div>` : ""}
        ${o.stage === "On Hold" ? "" : `<form id="hold-form" class="crm-form" style="margin-top:10px"><div class="app-field"><label for="h-r">On hold: reason</label><select id="h-r" name="reason_code">${rsn(ME.hold_reasons)}</select></div><div class="app-field"><label for="h-d">Review date</label><input id="h-d" name="hold_review_date" type="date" required></div><div class="app-field full"><label for="h-n">Note</label><input id="h-n" name="note" required minlength="5"></div><div class="full"><button class="crm-btn" type="submit">Put on hold</button></div></form>`}
        <form id="lost-form" class="crm-form" style="margin-top:10px"><div class="app-field"><label for="l-r">Lost: reason</label><select id="l-r" name="reason_code">${rsn(ME.lost_reasons)}</select></div><div class="app-field"><label for="l-n">Note</label><input id="l-n" name="note" required minlength="5"></div><div class="full"><button class="crm-btn danger" type="submit">Mark as lost</button></div></form></div>`}
      <p class="crm-h">History</p><ul class="crm-timeline">${o.activities.map((a) => `<li class="${a.meaningful ? "meaningful" : ""}"><b>${esc(a.kind.replace("_", " "))}</b>${a.meaningful ? " · meaningful contact" : ""}<small>${dtm(a.occurred_at)}</small>${esc(a.summary)}</li>`).join("") || "<li>No history yet.</li>"}</ul>
      ${o.reviews.length ? `<p class="crm-h">Review history</p><ul class="crm-timeline">${o.reviews.map((x) => `<li><b>${esc(x.kind.replace("_", " "))}</b> · ${esc(x.state)}<small>${dtm(x.requested_at)}</small>${esc(x.decision_note || "")}</li>`).join("")}</ul>` : ""}`;

    const msg = (t, kind = "error") => (document.getElementById("op-msg").innerHTML = banner(kind, t), window.scrollTo({ top: 0 }));
    const act = async (p, body, method = "POST") => { const res = await api(p, { method, body }); if (res.ok) return opportunity(id); msg(errText(res)); };
    main.querySelectorAll("[data-stage]").forEach((b) => b.addEventListener("click", () => act(`/opportunities/${id}/stage`, { to: b.dataset.stage })));
    main.querySelectorAll("[data-fudone]").forEach((b) => b.addEventListener("click", () => act(`/followups/${b.dataset.fudone}`, { done: true }, "PATCH")));
    const form = (sel, fn) => { const f = document.querySelector(sel); if (f) f.addEventListener("submit", (e) => { e.preventDefault(); fn(Object.fromEntries(new FormData(e.target).entries()), e.target); }); };
    form("#edit-form", (v) => act(`/opportunities/${id}`, { title: v.title, requirement_summary: v.requirement_summary, estimated_value_inr: v.estimated_value_inr === "" ? null : Number(v.estimated_value_inr) }, "PATCH"));
    form("#act-form", (v, f) => act(`/opportunities/${id}/activities`, { kind: v.kind, summary: v.summary, meaningful: f.querySelector("#a-mean").checked }));
    form("#fu-form", (v) => act(`/opportunities/${id}/followups`, v));
    form("#hold-form", (v) => act(`/opportunities/${id}/on-hold`, v));
    form("#lost-form", (v) => act(`/opportunities/${id}/lost`, v));
    form("#resume-form", (v) => act(`/opportunities/${id}/resume`, v));
    form("#reopen-form", (v) => act(`/opportunities/${id}/reopen`, v));
    const rc = document.getElementById("req-com"); if (rc) rc.addEventListener("click", () => act(`/opportunities/${id}/commercial-review`, {}));
  }

  async function reviews() {
    const r = await api("/reviews"); if (!(await guard(r))) return;
    if (!r.ok) { main.innerHTML = banner("error", errText(r)); return; }
    const item = (x) => {
      const lh = x.kind === "lost" || x.kind === "on_hold";
      const detail = lh ? `Reason: ${esc(label({ ...ME.lost_reasons, ...ME.hold_reasons }, x.reason_code))} — ${esc(x.reason_note || "")}${x.hold_review_date ? " · review " + day(x.hold_review_date) : ""} · was in ${esc(x.stage_before_hold || "—")}` : esc(x.requirement_summary || x.request_note || "");
      const btns = lh ? `<button class="crm-btn" data-d="confirm">Confirm</button><button class="crm-btn" data-d="reopen">Reopen</button>` : `<button class="crm-btn primary" data-d="approve">Approve</button><button class="crm-btn" data-d="hold">Hold</button><button class="crm-btn danger" data-d="reject">Reject</button>`;
      return `<div class="crm-card" data-rid="${x.id}"><b>${esc(x.kind.replace("_", " "))} review</b> · ${oppLink({ id: x.opportunity_id, ref: x.ref })} ${esc(x.title)} · ${esc(x.company_name)} · owner ${esc(x.owner_label)} · ${day(x.requested_at)}<p>${detail}</p>
        <div class="app-field"><label>Decision note (required)</label><input class="rv-note" minlength="5"></div><div class="crm-actions">${btns}</div><div class="rv-msg"></div></div>`;
    };
    main.innerHTML = `<h1>Founder review</h1><p class="crm-sub">Opportunity and commercial reviews, and Lost / On Hold decisions set by specialists. Every decision needs a note and is audited.</p>` + (r.data.map(item).join("") || `<div class="crm-empty">Nothing is waiting for review.</div>`);
    main.querySelectorAll("[data-rid]").forEach((card) => card.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", async () => {
      const res = await api(`/reviews/${card.dataset.rid}/decide`, { method: "POST", body: { decision: b.dataset.d, note: card.querySelector(".rv-note").value } });
      res.ok ? reviews() : (card.querySelector(".rv-msg").innerHTML = banner("error", errText(res)));
    })));
  }

  async function reports() {
    const r = await api("/reports"); if (!(await guard(r))) return;
    const d = r.data; const t = (m) => Object.entries(m).map(([k, n]) => `<tr><td>${esc(label({}, k))}</td><td>${n}</td></tr>`);
    main.innerHTML = `<h1>Reports</h1><p class="crm-sub">${d.scope === "all" ? "All records, with a breakdown by owner." : "Your own figures only."}</p>
      <div class="crm-grid"><div class="crm-stat"><b>${d.total}</b><span>Opportunities</span></div><div class="crm-stat"><b>${d.won}</b><span>Won</span></div><div class="crm-stat"><b>${money(d.open_value_inr)}</b><span>Open pipeline value</span></div></div>
      <p class="crm-h">By stage</p>${table(["Stage", "Count"], t(d.by_stage), "No data.")}<p class="crm-h">By source</p>${table(["Source", "Count"], t(d.by_source), "No data.")}
      <p class="crm-h">By pillar</p>${table(["Pillar", "Count"], t(d.by_pillar), "No data.")}<p class="crm-h">Lost reasons</p>${table(["Reason", "Count"], t(d.lost_reasons), "None lost.")}
      ${d.per_owner ? `<p class="crm-h">By owner</p>${table(["Owner", "Count"], t(d.per_owner), "")}<div class="crm-actions"><a class="crm-btn" href="${API}/export.csv" download>Export opportunities (CSV, audited)</a></div>` : ""}`;
  }

  async function staff() {
    const [r, n, rq] = await Promise.all([api("/staff"), api("/needs-owner"), api("/reviews")]); if (!(await guard(r))) return;
    if (!r.ok) { main.innerHTML = banner("error", errText(r)); return; }
    main.innerHTML = `<h1>Staff and security</h1><p class="crm-sub">Activate a specialist by their registered business email. They then set up their own authenticator. Deactivation takes effect on their next request.</p>
      <div class="crm-card"><form id="act-staff" class="crm-form"><div class="app-field"><label for="s-mail">Registered email</label><input id="s-mail" name="email" type="email" required></div><div class="app-field"><label for="s-name">Display name</label><input id="s-name" name="display_name" required maxlength="80"></div><div class="full"><button class="crm-btn primary" type="submit">Activate CRM access</button></div></form><div id="s-msg"></div></div>
      <p class="crm-h">Specialists</p>${table(["Name", "Email", "Status", "Authenticator", ""], r.data.map((x) => `<tr><td>${esc(x.display_name)}</td><td>${esc(x.email)}</td><td>${x.active ? "Active" : "Deactivated"}${x.lock !== "none" ? ` <span class="crm-pill s-lost">${esc(x.lock)} lock</span>` : ""}</td><td>${x.mfa_enrolled ? "Set up" : "Not set up"}</td>
        <td><div class="crm-actions">${x.active ? `<button class="crm-btn danger" data-deact="${x.user_id}">Deactivate</button>` : ""}<button class="crm-btn" data-reset="${x.user_id}">Reset authenticator</button>${x.lock !== "none" ? `<button class="crm-btn" data-unlock="${x.user_id}">Unlock</button>` : ""}</div></td></tr>`), "No specialists activated yet.")}
      <p class="crm-h">Companies needing an owner</p>${table(["Company", ""], (n.data || []).map((c) => `<tr><td>${esc(c.name)}</td><td><a href="#/company/${c.id}">Reassign</a></td></tr>`), "None.")}`;
    const msg = (res, ok) => (document.getElementById("s-msg").innerHTML = res.ok ? banner("success", ok) : banner("error", errText(res)));
    document.getElementById("act-staff").addEventListener("submit", async (e) => { e.preventDefault(); const res = await api("/staff/activate", { method: "POST", body: Object.fromEntries(new FormData(e.target).entries()) }); res.ok ? staff() : msg(res); });
    const bind = (attr, path, ok) => main.querySelectorAll(`[${attr}]`).forEach((b) => b.addEventListener("click", async () => { const res = await api(`/staff/${b.getAttribute(attr)}/${path}`, { method: "POST" }); res.ok ? staff() : msg(res, ok); }));
    bind("data-deact", "deactivate"); bind("data-reset", "reset-mfa"); bind("data-unlock", "unlock");
  }

  async function retention() {
    const r = await api("/retention"); if (!(await guard(r))) return;
    if (!r.ok) { main.innerHTML = banner("error", errText(r)); return; }
    main.innerHTML = `<h1>Retention</h1><p class="crm-sub">Companies whose last meaningful contact was more than 12 months ago. This list only flags records; nothing is deleted or changed automatically. Any purge needs your action after legal advice.</p>` +
      table(["Company", "Owner", "Last meaningful contact", "Due"], r.data.map((x) => `<tr><td><a href="#/company/${x.company_id}">${esc(x.name)}</a></td><td>${esc(x.owner_label)}</td><td>${day(x.last_meaningful_contact_at)}</td><td>${day(x.retention_due_at)}</td></tr>`), "No records are due.");
  }

  async function audit() {
    const r = await api("/audit"); if (!(await guard(r))) return;
    if (!r.ok) { main.innerHTML = banner("error", errText(r)); return; }
    main.innerHTML = `<h1>Audit log</h1><p class="crm-sub">The latest 200 entries. Append-only; entries hold codes and ids, never names or message text.</p>` +
      table(["When", "Who", "Action", "Entity", "Outcome", "Detail"], r.data.map((x) => `<tr><td>${dtm(x.at)}</td><td>${esc(x.actor_role || "—")} ${esc(x.actor_user_id ?? "")}</td><td>${esc(x.action)}</td><td>${esc(x.entity || "")} ${esc(x.entity_id ?? "")}</td><td>${esc(x.outcome)}</td><td>${esc(x.detail || "")}</td></tr>`), "Empty.");
  }

  boot().catch(() => gateMsg("The CRM could not load. Reload to try again."));
})();
