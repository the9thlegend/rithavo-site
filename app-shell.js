/* Phase 2B-1.7A — small shared helper for every authenticated rithavo.com
   page. Vanilla JS, no framework, no build step, matching the rest of
   this site. Every authenticated call goes through the existing session
   cookie (credentials: 'same-origin') — nothing here ever sends or
   trusts a client-side user id. */

const RithavoApp = (() => {
  const API_BASE = "/api";

  async function apiFetch(path, options = {}) {
    // A FormData body (P0: resume file upload) must NEVER get a manual
    // Content-Type — fetch/the browser sets multipart/form-data with the
    // correct boundary itself only when no Content-Type header is
    // present at all; setting one (even to the "right-sounding" value)
    // breaks the boundary and the upload silently fails server-side.
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    const headers = isFormData
      ? (options.headers || {})
      : options.body && !(options.headers && options.headers["Content-Type"] === "form")
        ? { "Content-Type": "application/json", ...(options.headers || {}) }
        : (options.headers || {});
    const resp = await fetch(API_BASE + path, {
      credentials: "same-origin",
      ...options,
      headers,
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
    { key: "mentorship", label: "Mentorship", href: "/mentorship/" },
    { key: "history", label: "History", href: "/history/" },
  ];

  // Home/Explore/Admin Integration Phase — the Admin tab is appended
  // only for a confirmed Super Admin (isSuperAdmin from GET /me), never
  // rendered speculatively. Every /api/admin/* route still
  // independently re-checks db.is_super_admin server-side regardless of
  // whether this tab is shown — this is a UI convenience, not the
  // authorization boundary itself.
  function renderTopbar(activeKey, email, isSuperAdmin) {
    const mount = document.getElementById("app-topbar");
    if (!mount) return;
    const tabs = isSuperAdmin ? [...TABS, { key: "admin", label: "Admin", href: "/admin/" }] : TABS;
    const tabsHtml = tabs.map(t =>
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
    // PWA: nothing this app cached may outlive the session (see sw.js)
    try { if (window.RithavoPWA) await window.RithavoPWA.clearPrivateState(); } catch (e) { /* best-effort */ }
    window.location.href = "/";
  }

  function banner(container, kind, text) {
    container.innerHTML = `<div class="app-banner app-banner-${kind}">${text}</div>`;
  }

  let _cashfreeSdkPromise = null;
  function _loadCashfreeSdk() {
    if (typeof Cashfree !== "undefined") return Promise.resolve();
    if (_cashfreeSdkPromise) return _cashfreeSdkPromise;
    _cashfreeSdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://sdk.cashfree.com/js/v3/cashfree.js";
      script.onload = resolve;
      script.onerror = () => reject(new Error("Could not load the payment provider — please reload and try again."));
      document.head.appendChild(script);
    });
    return _cashfreeSdkPromise;
  }

  /* Opens Cashfree Checkout in a modal (redirectTarget: "_modal") for
     an ALREADY-CREATED order — never a navigation away from
     rithavo.com. Nothing from the modal's own reported outcome is trusted: regardless of
     what it reports (including a rejection — the visitor may simply
     have closed it after actually completing payment on an underlying
     redirect), this always calls the server's confirm-cashfree
     endpoint afterward, which independently re-fetches the order's
     real status from Cashfree before ever granting an entitlement. */
  function _openCashfreeCheckout(order, confirmCashfreePath) {
    return _loadCashfreeSdk()
      .then(() => {
        const cashfree = Cashfree({ mode: "sandbox" });
        return cashfree.checkout({
          paymentSessionId: order.cashfree_payment_session_id,
          redirectTarget: "_modal",
        }).catch(() => {});
      })
      .then(() => apiJson(confirmCashfreePath(order.purchase_id), { method: "POST", body: JSON.stringify({}) }));
  }

  /* Cashfree (sandbox): creates the purchase (server computes the
     price) then opens Cashfree's own Checkout for it. Use this
     directly only when the caller already knows Cashfree is the
     active gateway; startPurchase (below) is the entry point that
     doesn't need to know in advance. */
  function startCashfreePurchase({ purchasePath, confirmCashfreePath, customerPhone }) {
    return apiJson(purchasePath, { method: "POST", body: JSON.stringify({ customer_phone: customerPhone }) })
      .then(order => _openCashfreeCheckout(order, confirmCashfreePath));
  }

  /* Unified entry point for a product's "Buy" button: creates the
     purchase EXACTLY ONCE, then opens the checkout of the gateway the
     server actually activated for it (order.gateway, present on every
     gateway's create_payment_intent response). Cashfree is the only
     real gateway; anything else is refused. confirmCashfreePath builds
     the server-side confirmation URL. */
  function startPurchase({ purchasePath, confirmCashfreePath, customerPhone }) {
    return apiJson(purchasePath, { method: "POST", body: JSON.stringify({ customer_phone: customerPhone }) }).then(order => {
      if (order.gateway === "cashfree") return _openCashfreeCheckout(order, confirmCashfreePath);
      throw new Error("Payment is not available right now — please try again shortly.");
    });
  }

  return {
    apiFetch, apiJson, requireSession, renderTopbar, signOut, banner,
    startCashfreePurchase, startPurchase, TABS,
  };
})();
