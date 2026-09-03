/* Phase 2B-1.7A — small shared helper for every authenticated rithavo.com
   page. Vanilla JS, no framework, no build step, matching the rest of
   this site. Every authenticated call goes through the existing session
   cookie (credentials: 'same-origin') — nothing here ever sends or
   trusts a client-side user id. */

const RithavoApp = (() => {
  const API_BASE = "/api";

  async function apiFetch(path, options = {}) {
    const resp = await fetch(API_BASE + path, {
      credentials: "same-origin",
      headers: options.body && !(options.headers && options.headers["Content-Type"] === "form")
        ? { "Content-Type": "application/json", ...(options.headers || {}) }
        : (options.headers || {}),
      ...options,
    });
    return resp;
  }

  async function apiJson(path, options = {}) {
    const resp = await apiFetch(path, options);
    let data = null;
    try { data = await resp.json(); } catch (e) { /* empty body, fine */ }
    if (!resp.ok) {
      const message = (data && data.detail) ? data.detail : `Request failed (${resp.status}).`;
      const err = new Error(message);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  /* Redirects to /sign-in/ if there's no live session. Returns
     {user_id, email} on success — call this at the top of every
     authenticated page before rendering anything that assumes a user. */
  async function requireSession() {
    try {
      return await apiJson("/me");
    } catch (err) {
      if (err.status === 401) {
        const next = encodeURIComponent(window.location.pathname);
        window.location.href = `/sign-in/?next=${next}`;
        return null;
      }
      throw err;
    }
  }

  const TABS = [
    { key: "home", label: "Home", href: "/home/" },
    { key: "profile", label: "Profile", href: "/profile/" },
    { key: "career-intelligence", label: "Career Intelligence", href: "/career-intelligence/" },
    { key: "application-diagnosis", label: "Application Diagnosis", href: "/application-diagnosis/" },
    { key: "resumes", label: "Resumes", href: "/resumes/" },
    { key: "history", label: "History", href: "/history/" },
  ];

  function renderTopbar(activeKey, email) {
    const mount = document.getElementById("app-topbar");
    if (!mount) return;
    const tabsHtml = TABS.map(t =>
      `<li><a href="${t.href}" ${t.key === activeKey ? 'aria-current="page"' : ""}>${t.label}</a></li>`
    ).join("");
    mount.innerHTML = `
      <a class="wordmark" href="/home/">
        <svg class="mark" viewBox="0 0 24 24" aria-hidden="true" width="24" height="24">
          <path d="M4 5 L12.5 12 L4 19" fill="none" stroke="var(--g1)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" opacity="0.65"/>
          <path d="M11 4 L21 12 L11 20" fill="none" stroke="var(--g3)" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>Rithavo</span>
      </a>
      <ul class="app-tabs">${tabsHtml}</ul>
      <div class="app-identity">
        ${email ? `<span>${email}</span>` : ""}
        <a class="app-tabs-signout" href="/account/" ${activeKey === "account" ? 'aria-current="page"' : ""}>Account</a>
      </div>
    `;
  }

  async function signOut() {
    try { await apiFetch("/auth/logout", { method: "POST" }); } catch (e) { /* best-effort */ }
    window.location.href = "/";
  }

  function banner(container, kind, text) {
    container.innerHTML = `<div class="app-banner app-banner-${kind}">${text}</div>`;
  }

  return { apiFetch, apiJson, requireSession, renderTopbar, signOut, banner, TABS };
})();
