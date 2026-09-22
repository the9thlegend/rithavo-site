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
    window.location.href = "/";
  }

  function banner(container, kind, text) {
    container.innerHTML = `<div class="app-banner app-banner-${kind}">${text}</div>`;
  }

  /* Phase 2B-2. Starts a real Razorpay purchase: calls the server to
     create the purchase + Razorpay Order (server computes the price —
     nothing here ever sends an amount), opens Razorpay's own Checkout
     widget, and on the widget's own success callback POSTs the three
     Razorpay-supplied values to the server's confirm endpoint for
     real signature verification. Resolves with the SERVER's confirm
     response (never treats reaching the callback itself as proof of
     payment) — the caller should re-fetch whatever entitlement/access
     state it displays from the server after this resolves, not flip
     its own UI to "purchased" based on this function returning.
     purchasePath: e.g. "/diagnosis/purchase" or "/career-intelligence/purchase".
     confirmPath(purchaseId): returns e.g. `/diagnosis/purchase/${id}/confirm`. */
  function startRazorpayPurchase({ purchasePath, confirmPath, description }) {
    return apiJson(purchasePath, { method: "POST", body: JSON.stringify({}) }).then(order => {
      return new Promise((resolve, reject) => {
        if (typeof Razorpay === "undefined") {
          reject(new Error("Payment could not be started — please reload the page and try again."));
          return;
        }
        const rzp = new Razorpay({
          key: order.razorpay_key_id,
          amount: order.amount_inr * 100,
          currency: "INR",
          order_id: order.razorpay_order_id,
          name: "Rithavo",
          description: description,
          handler: (response) => {
            apiJson(confirmPath(order.purchase_id), {
              method: "POST",
              body: JSON.stringify({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
              }),
            }).then(resolve).catch(reject);
          },
          modal: {
            ondismiss: () => reject(new Error("Checkout was closed before completing payment.")),
          },
        });
        rzp.on("payment.failed", () => reject(new Error("Payment failed — please try again.")));
        rzp.open();
      });
    });
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
     rithavo.com. Unlike Razorpay's client-supplied signature, nothing
     from the modal's own reported outcome is trusted: regardless of
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

  /* Cashfree Sandbox phase — self-contained sibling of
     startRazorpayPurchase: creates the purchase (server computes the
     price) then opens Cashfree's own Checkout for it. Use this
     directly only when the caller already knows Cashfree is the
     active gateway; startPurchase (below) is the entry point that
     doesn't need to know in advance. */
  function startCashfreePurchase({ purchasePath, confirmCashfreePath }) {
    return apiJson(purchasePath, { method: "POST", body: JSON.stringify({}) })
      .then(order => _openCashfreeCheckout(order, confirmCashfreePath));
  }

  /* Same widget-opening step startRazorpayPurchase performs, factored
     out so it can run against an order this function did NOT itself
     create (see startPurchase below — the purchase must be created
     exactly once, so whichever gateway turns out to be active reuses
     that same order rather than creating a second one).
     startRazorpayPurchase itself is completely unchanged and still
     works exactly as it always has for any existing caller. */
  function _openRazorpayCheckout(order, confirmPath, description) {
    return new Promise((resolve, reject) => {
      if (typeof Razorpay === "undefined") {
        reject(new Error("Payment could not be started — please reload the page and try again."));
        return;
      }
      const rzp = new Razorpay({
        key: order.razorpay_key_id,
        amount: order.amount_inr * 100,
        currency: "INR",
        order_id: order.razorpay_order_id,
        name: "Rithavo",
        description: description,
        handler: (response) => {
          apiJson(confirmPath(order.purchase_id), {
            method: "POST",
            body: JSON.stringify({
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            }),
          }).then(resolve).catch(reject);
        },
        modal: {
          ondismiss: () => reject(new Error("Checkout was closed before completing payment.")),
        },
      });
      rzp.on("payment.failed", () => reject(new Error("Payment failed — please try again.")));
      rzp.open();
    });
  }

  /* Unified entry point for a product's "Buy" button: creates the
     purchase EXACTLY ONCE, then opens whichever checkout the server
     actually activated for it (order.gateway, present on every
     gateway's own create_payment_intent response) — nothing here
     decides which gateway is "the" one; the server already did via
     payment_gateway.get_gateway(). confirmPath is Razorpay's confirm
     URL builder (unchanged), confirmCashfreePath is Cashfree's. */
  function startPurchase({ purchasePath, confirmPath, confirmCashfreePath, description }) {
    return apiJson(purchasePath, { method: "POST", body: JSON.stringify({}) }).then(order => {
      if (order.gateway === "cashfree") return _openCashfreeCheckout(order, confirmCashfreePath);
      if (order.gateway === "razorpay") return _openRazorpayCheckout(order, confirmPath, description);
      throw new Error("Payment is not available right now — please try again shortly.");
    });
  }

  return {
    apiFetch, apiJson, requireSession, renderTopbar, signOut, banner,
    startRazorpayPurchase, startCashfreePurchase, startPurchase, TABS,
  };
})();
