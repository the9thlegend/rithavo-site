// Runs the real sw.js in a sandbox and checks that member profile pages and their APIs are never intercepted or cached.
// Executed by tests/test_public_profile_routes_config.py (node --test). No browser, no network.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "sw.js"), "utf8");
const sandbox = {
  self: { addEventListener() {}, location: { origin: "https://rithavo.com" }, clients: { claim() {} }, skipWaiting() {} },
  caches: {}, fetch() {}, URL, Request, Response, console, setTimeout, clearTimeout,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const sw = sandbox.self.__rithavoSW;
const ORIGIN = "https://rithavo.com";
const cls = (method, p, mode = "same-origin") => sw.classify(method, ORIGIN + p, mode, ORIGIN);

test("member profile pages are never handled by the worker, in any spelling", () => {
  for (const p of ["/c/AbCdEfGh12345678", "/c/AbCdEfGh12345678/", "/c", "/c/", "//c/abc", "/C/AbCdEfGh12345678",
                   "/%63/AbCdEfGh12345678", "/%2563/abc", "/c%2Fabc", "/c" + String.fromCharCode(92) + "abc", "/c.json"]) {
    assert.equal(cls("GET", p, "navigate"), "pass", "navigate " + p);
    assert.equal(cls("GET", p, "cors"), "pass", "fetch " + p);
  }
});

test("the new prefix does not swallow unrelated public pages that start with the same letter", () => {
  for (const p of ["/career/", "/career-intelligence/", "/contact/", "/careers", "/cookies", "/c-level/"]) {
    assert.notEqual(cls("GET", p, "navigate"), "pass", p);
  }
});

test("the profile APIs fall under /api and are never handled", () => {
  for (const p of ["/api/public-profile/AbCdEfGh12345678", "/api/public-profile/AbCdEfGh12345678/photo", "/api/my-public-profile",
                   "/api/my-photos", "/api/my-photo"]) {
    assert.equal(cls("GET", p, "cors"), "pass", p);
  }
});

test("the worker version was bumped so older caches are dropped", () => {
  assert.equal(sw.VERSION, "v3");
  assert.ok(sw.NEVER_HANDLE_PREFIXES.includes("/c"));
  assert.ok(!sw.PRIVATE_PAGE_PREFIXES.includes("/c"));
});
