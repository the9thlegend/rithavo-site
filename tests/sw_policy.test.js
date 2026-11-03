// Runs the service worker's real code in a sandbox and checks its caching policy.
// Executed by tests/test_pwa.py (node --test). No browser, no network.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "sw.js"), "utf8");
const listeners = {};
const sandbox = {
  self: { addEventListener: (type, fn) => { listeners[type] = fn; }, location: { origin: "https://rithavo.com" }, clients: { claim() {} }, skipWaiting() {} },
  caches: {}, fetch() {}, URL, Request, Response, console, setTimeout, clearTimeout,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const sw = sandbox.self.__rithavoSW;
const ORIGIN = "https://rithavo.com";
const cls = (method, p, mode = "same-origin") => sw.classify(method, p.startsWith("http") ? p : ORIGIN + p, mode, ORIGIN);

test("registers the lifecycle handlers and never calls skipWaiting on install", () => {
  for (const t of ["install", "activate", "fetch", "message"]) assert.equal(typeof listeners[t], "function", t);
  assert.ok(!/addEventListener\("install"[\s\S]{0,600}skipWaiting\(\)[\s\S]{0,40}\}\);\s*\n\s*\n\s*self\.addEventListener\("activate"/.test(source));
});

test("private and API paths are never intercepted, in any spelling", () => {
  const privatePaths = [
    "/api/me", "/api/resumes", "/api/resume/12/download", "/api/auth/login", "/api/ritanya/chat", "/api/profile",
    "/api", "/api/", "//api/me", "/API/me", "/%61pi/me", "/%2561pi/me", "/a%70i/me", "/api%2Fme", "/api\\me",
    "/profile/edit", "/profile/edit/skills", "/profile/photo", "/admin/", "/admin", "/auth/verify", "/payments/webhook",
    "/card/continue", "/resume/3/download", "/diagnosis/9", "/career-intelligence/resume", "/sw.js",
  ];
  for (const p of privatePaths) {
    assert.equal(cls("GET", p, "navigate"), "pass", "navigate " + p);
    assert.equal(cls("GET", p, "cors"), "pass", "fetch " + p);
  }
});

test("only GET requests are handled", () => {
  for (const m of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
    assert.equal(cls(m, "/home/", "navigate"), "pass", m);
    assert.equal(cls(m, "/styles.css"), "pass", m);
  }
});

test("other origins are never handled (fonts, payment SDKs, anything)", () => {
  for (const u of ["https://fonts.googleapis.com/css2?family=Manrope", "https://sdk.cashfree.com/js/v3/cashfree.js",
                   "https://checkout.razorpay.com/v1/checkout.js", "https://app.rithavo.com/card", "http://rithavo.com/home/",
                   "https://www.rithavo.com/home/"]) {
    assert.equal(cls("GET", u, "navigate"), "pass", u);
    assert.equal(cls("GET", u, "no-cors"), "pass", u);
  }
});

test("page navigations and public assets are handled", () => {
  for (const p of ["/", "/career/", "/systems/", "/growth/", "/about/", "/privacy/", "/sign-in/", "/offline/"]) assert.equal(cls("GET", p, "navigate"), "navigation", p);
  for (const p of ["/styles.css", "/app-shell.js", "/pwa.js", "/assets/pwa/icon-192.png", "/assets/hero/hero-final.png"]) assert.equal(cls("GET", p), "asset", p);
  assert.equal(cls("GET", "/some-data-file"), "pass");   // a non-navigation, non-asset request is left alone
});

function resp(status, headers = {}, extra = {}) {
  return { status, type: "basic", redirected: false, headers: new Map(Object.entries(headers)), ...extra,
    headers: { get: (k) => headers[k.toLowerCase()] ?? null, has: (k) => k.toLowerCase() in headers } };
}

test("only plain, public, same-origin 200 responses are cacheable", () => {
  assert.equal(sw.cacheable(resp(200, { "content-type": "text/html; charset=utf-8" }), "navigation"), true);
  assert.equal(sw.cacheable(resp(200, { "content-type": "text/css" }), "asset"), true);
  assert.equal(sw.cacheable(resp(404, { "content-type": "text/html" }), "navigation"), false);
  assert.equal(sw.cacheable(resp(200, { "content-type": "text/html" }, { redirected: true }), "navigation"), false);
  assert.equal(sw.cacheable(resp(200, { "content-type": "text/html" }, { type: "opaque" }), "navigation"), false);
  assert.equal(sw.cacheable(resp(200, { "content-type": "text/html", "cache-control": "no-store" }), "navigation"), false);
  assert.equal(sw.cacheable(resp(200, { "content-type": "text/html", "cache-control": "private, max-age=60" }), "navigation"), false);
  assert.equal(sw.cacheable(resp(200, { "content-type": "text/html", "set-cookie": "session=x" }), "navigation"), false);
  assert.equal(sw.cacheable(resp(200, { "content-type": "application/json" }), "navigation"), false);   // JSON is never a "page"
  assert.equal(sw.cacheable(resp(200, { "content-type": "application/json" }), "asset"), false);
  assert.equal(sw.cacheable(resp(200, { "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }), "navigation"), false);
});

test("the precache list holds only public shell files on this origin", () => {
  for (const u of sw.SHELL_URLS) {
    assert.ok(u.startsWith("/") && !u.startsWith("//"), u);
    assert.notEqual(cls("GET", u, "navigate"), "pass", "precached but never served by the worker: " + u);
    for (const prefix of sw.NEVER_HANDLE_PREFIXES) assert.ok(!(u === prefix || u.startsWith(prefix + "/")), u);
  }
});

test("signed-in app pages are network-only: classified apart from public pages, in any spelling", () => {
  for (const p of ["/home/", "/home", "/account/", "/profile/", "/resumes/", "/history/", "/mentorship/", "/career-intelligence/",
                   "/application-diagnosis/", "/onboarding/", "/HOME/", "//home/", "/%68ome/", "/%2568ome/", "/ho%6De/"]) {
    assert.equal(cls("GET", p, "navigate"), "private-navigation", p);
  }
  // the public marketing pages that merely share a name stay ordinary cacheable navigations
  for (const p of ["/career/", "/career/career-intelligence/", "/career/career-intelligence/demo/", "/career/application-diagnostic/", "/homepage/"]) {
    assert.equal(cls("GET", p, "navigate"), "navigation", p);
  }
});

test("a signed-in page is fetched from the network and never written to any cache", async () => {
  const writes = [];
  const store = { put: async (...a) => { writes.push(a); }, keys: async () => [], delete: async () => true };
  const fetchLog = [];
  const sb = {
    self: { addEventListener: (t, f) => { listeners2[t] = f; }, location: { origin: ORIGIN }, clients: { claim() {} }, skipWaiting() {} },
    caches: { open: async () => store, match: async () => new Response("OFFLINE PAGE", { status: 200 }), keys: async () => [] },
    fetch: async (r) => {
      fetchLog.push(r.url);
      if (!sb.online) throw new TypeError("Failed to fetch");
      const res = new Response("<html>page</html>", { status: 200, headers: { "content-type": "text/html" } });
      Object.defineProperty(res, "type", { value: "basic" });   // what a real same-origin response reports
      return res;
    },
    online: true, URL, Request, Response, console, setTimeout, clearTimeout,
  };
  const listeners2 = {};
  vm.createContext(sb);
  vm.runInContext(source, sb);
  const run = async (url) => {
    let responded;
    listeners2.fetch({ request: { method: "GET", url, mode: "navigate" }, respondWith: (p) => { responded = p; }, waitUntil() {} });
    return responded ? await responded : null;
  };
  const online = await run(ORIGIN + "/account/");
  assert.equal(await online.text(), "<html>page</html>");
  assert.deepEqual(writes, [], "online response was cached");
  sb.online = false;
  const offline = await run(ORIGIN + "/account/");
  assert.equal(await offline.text(), "OFFLINE PAGE", "offline should serve the offline page, never a stored copy of the app page");
  assert.deepEqual(writes, []);
  // control: a public page IS stored when online
  sb.online = true;
  await run(ORIGIN + "/career/");
  assert.equal(writes.length, 1);
});
