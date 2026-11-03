/* Rithavo service worker.

   Scope: the whole origin (served from /sw.js, so the default scope is "/"). Same origin only.

   What it caches
   - A small public app shell: the stylesheet, scripts, icons and the offline page. These files are the same for
     every visitor and contain no personal data.
   - Public page navigations (the static HTML of pages like /, /career/, /systems/) as they are visited, so a
     page can be shown if the network drops. Nothing on those pages is personal.

   What it NEVER caches or intercepts (network only, passed straight through)
   - Anything that is not a GET request.
   - Anything on another origin (Google Fonts, the payment providers' scripts).
   - /api/* (all authenticated data, sign-in, resume downloads, payments, the Ritanya endpoint),
     /profile/edit* (server-rendered pages containing the person's profile) and /admin/.
   - Signed-in app pages (/home/, /account/, /profile/, /resumes/ and the other pages that load app-shell.js):
     always fetched from the network and never stored. Offline they show the offline page rather than a copy that
     could only sit on "Loading...".
   - Any response that is not a plain 200 from this origin, was redirected, or says Cache-Control no-store/private,
     or sets a cookie.

   On sign-out the page sends CLEAR_CACHES and the page and asset caches are deleted (the public shell stays). Cache names carry a version;
   old versions are deleted when a new worker activates. A new worker waits until the person accepts the
   "update available" prompt (SKIP_WAITING) so a page is never swapped under someone mid-task. */

const VERSION = "v2";   // v2: signed-in app pages are no longer cached; v1 caches are deleted on activate
const SHELL_CACHE = "rithavo-shell-" + VERSION;
const PAGES_CACHE = "rithavo-pages-" + VERSION;
const ASSETS_CACHE = "rithavo-assets-" + VERSION;
const OWNED = [SHELL_CACHE, PAGES_CACHE, ASSETS_CACHE];
const MAX_PAGES = 30;
const MAX_ASSETS = 80;
const NAVIGATION_TIMEOUT_MS = 6000;
const OFFLINE_URL = "/offline/";

const SHELL_URLS = [
  OFFLINE_URL,
  "/styles.css",
  "/app-shell.css",
  "/app-shell.js",
  "/pwa.css",
  "/pwa.js",
  "/assets/pwa/icon-192.png",
  "/assets/pwa/icon-512.png",
];

// Matched against the decoded, lower-cased, slash-collapsed path, so an encoded spelling of a private path
// (/%61pi/me, //api/me, /API/me) is treated exactly like the plain one.
const NEVER_HANDLE_PREFIXES = [
  "/api", "/profile/edit", "/profile/photo", "/admin", "/auth", "/payments", "/card", "/resume",
  "/diagnosis", "/career-intelligence/resume", "/onboarding/resume", "/sw.js",
];
// Pages that need a signed-in session. Keep in step with the pages that load app-shell.js
// (tests/test_pwa.py fails if one is missing here).
const PRIVATE_PAGE_PREFIXES = [
  "/home", "/account", "/profile", "/resumes", "/history", "/mentorship",
  "/career-intelligence", "/application-diagnosis", "/onboarding",
];
const ASSET_EXTENSION = /\.(?:css|js|png|jpe?g|gif|svg|webp|ico|woff2?)$/i;

function normalisePath(pathname) {
  let p = pathname;
  for (let i = 0; i < 3; i++) {       // decode repeatedly: %2561 -> %61 -> a
    let next;
    try { next = decodeURIComponent(p); } catch (e) { return null; }
    if (next === p) break;
    p = next;
  }
  return p.toLowerCase().replace(/\\/g, "/").replace(/\/{2,}/g, "/");
}

/* Decides what to do with a request. Pure function so it can be tested without a browser.
   Returns one of: "pass" (do not intercept), "navigation" (public page), "private-navigation" (signed-in page,
   network only), "asset". */
function classify(method, urlString, mode, selfOrigin) {
  if (method !== "GET") return "pass";
  let url;
  try { url = new URL(urlString); } catch (e) { return "pass"; }
  if (url.origin !== selfOrigin) return "pass";
  const path = normalisePath(url.pathname);
  if (path === null) return "pass";
  for (const prefix of NEVER_HANDLE_PREFIXES) {
    if (path === prefix || path.startsWith(prefix + "/") || path.startsWith(prefix + ".")) return "pass";
  }
  if (mode === "navigate") {
    const isPrivate = PRIVATE_PAGE_PREFIXES.some((prefix) => path === prefix || path.startsWith(prefix + "/"));
    return isPrivate ? "private-navigation" : "navigation";
  }
  if (ASSET_EXTENSION.test(path)) return "asset";
  return "pass";
}

/* True only for a response that is safe to keep in a shared cache. `kind` is "navigation" or "asset". */
function cacheable(response, kind) {
  if (!response || response.status !== 200 || response.type !== "basic" || response.redirected) return false;
  const cc = (response.headers.get("cache-control") || "").toLowerCase();
  if (cc.includes("no-store") || cc.includes("private")) return false;
  if (response.headers.has("set-cookie")) return false;
  const type = (response.headers.get("content-type") || "").toLowerCase();
  if (kind === "navigation" && !type.startsWith("text/html")) return false;   // never keep JSON or files as a "page"
  if (type.startsWith("application/json")) return false;
  return true;
}

async function trim(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i]);
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

async function offlineResponse() {
  const offline = await caches.match(OFFLINE_URL, { cacheName: SHELL_CACHE });
  if (offline) return offline;
  return new Response("You are offline.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
}

// Signed-in pages: straight to the network, nothing stored; only a failed connection falls back to the offline page.
async function handlePrivateNavigation(request) {
  try {
    return await fetch(request);
  } catch (e) {
    return offlineResponse();
  }
}

async function handleNavigation(request) {
  try {
    const response = await withTimeout(fetch(request), NAVIGATION_TIMEOUT_MS);
    if (cacheable(response, "navigation")) {
      const cache = await caches.open(PAGES_CACHE);
      await cache.put(request, response.clone());
      trim(PAGES_CACHE, MAX_PAGES);
    }
    return response;
  } catch (e) {
    const cached = await caches.match(request, { cacheName: PAGES_CACHE });
    if (cached) return cached;
    return offlineResponse();
  }
}

async function handleAsset(request, event) {
  const cache = await caches.open(ASSETS_CACHE);
  const cached = (await cache.match(request)) || (await caches.match(request, { cacheName: SHELL_CACHE }));
  const refresh = fetch(request).then((response) => {
    if (cacheable(response, "asset")) {
      cache.put(request, response.clone());
      trim(ASSETS_CACHE, MAX_ASSETS);
    }
    return response;
  });
  if (cached) {
    event.waitUntil(refresh.catch(() => {}));
    return cached;
  }
  return refresh;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      // addAll is all-or-nothing; a failed precache must not leave a half-installed worker.
      cache.addAll(SHELL_URLS.map((u) => new Request(u, { cache: "reload" })))
    )
  );
  // Deliberately no skipWaiting(): the page asks for it after the person accepts the update prompt.
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n.startsWith("rithavo-") && !OWNED.includes(n)).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const kind = classify(request.method, request.url, request.mode, self.location.origin);
  if (kind === "navigation") event.respondWith(handleNavigation(request));
  else if (kind === "private-navigation") event.respondWith(handlePrivateNavigation(request));
  else if (kind === "asset") event.respondWith(handleAsset(request, event));
  // "pass": no respondWith, the browser handles the request exactly as if there were no worker.
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "SKIP_WAITING") {
    self.skipWaiting();
  } else if (data.type === "CLEAR_CACHES") {
    event.waitUntil(
      // Pages and assets visited while signed in go. The shell (offline page, stylesheet, scripts, icons) is the same for
      // everyone and holds no personal data, so it stays and the offline page keeps working after sign-out.
      caches.keys().then((names) => Promise.all(names.filter((n) => n.startsWith("rithavo-") && !n.startsWith("rithavo-shell-")).map((n) => caches.delete(n))))
        .then(() => event.source && event.source.postMessage({ type: "CACHES_CLEARED" }))
    );
  }
});

// Exposed for the automated policy tests (tests/sw_policy.test.js); harmless in a browser.
self.__rithavoSW = { classify, cacheable, SHELL_URLS, NEVER_HANDLE_PREFIXES, PRIVATE_PAGE_PREFIXES, VERSION };
