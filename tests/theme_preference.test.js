// Runs the real assets/profile/theme.js against stubbed window/document objects. No browser.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "assets", "profile", "theme.js"), "utf8");

function load({ stored, storageThrows = false, canvas = "#F3F4F1" } = {}) {
  const calls = { set: [], remove: [] };
  const attrs = {};
  const meta = { content: "", setAttribute(k, v) { if (k === "content") this.content = v; } };
  const buttons = ["light", "dark", "system"].map((c) => {
    const el = { choice: c, pressed: null, listeners: {},
      getAttribute(k) { return k === "data-theme-choice" ? this.choice : null; },
      setAttribute(k, v) { if (k === "aria-pressed") this.pressed = v; },
      addEventListener(t, fn) { this.listeners[t] = fn; } };
    return el;
  });
  const root = {
    setAttribute(k, v) { attrs[k] = v; }, removeAttribute(k) { delete attrs[k]; },
    getAttribute(k) { return k in attrs ? attrs[k] : null; },
  };
  const storage = {
    getItem() { if (storageThrows) throw new Error("blocked"); return stored === undefined ? null : stored; },
    setItem(k, v) { if (storageThrows) throw new Error("blocked"); calls.set.push([k, v]); },
    removeItem(k) { if (storageThrows) throw new Error("blocked"); calls.remove.push(k); },
  };
  let domReady = null;
  const document = {
    documentElement: root,
    querySelector(sel) { return sel === 'meta[name="theme-color"]' ? meta : null; },
    querySelectorAll(sel) { return sel === "[data-theme-choice]" ? buttons : []; },
    addEventListener(t, fn) { if (t === "DOMContentLoaded") domReady = fn; },
  };
  const window = { localStorage: storage, matchMedia: () => ({ addEventListener() {} }) };
  const sandbox = { window, document, getComputedStyle: () => ({ getPropertyValue: () => canvas }) };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { api: window.RithavoTheme, attrs, calls, buttons, meta, ready: () => domReady && domReady() };
}

test("the default is System: no data-theme attribute", () => {
  const t = load();
  assert.equal(t.api.get(), "system");
  assert.equal(t.attrs["data-theme"], undefined);
});

test("a stored Light or Dark choice is applied before first paint", () => {
  assert.equal(load({ stored: "dark" }).attrs["data-theme"], "dark");
  assert.equal(load({ stored: "light" }).attrs["data-theme"], "light");
});

test("an unknown stored value falls back to System", () => {
  const t = load({ stored: "<script>" });
  assert.equal(t.api.get(), "system");
  assert.equal(t.attrs["data-theme"], undefined);
});

test("blocked or throwing storage never breaks the page and falls back to System", () => {
  const t = load({ storageThrows: true });
  assert.equal(t.api.get(), "system");
  t.api.set("dark");                       // must not throw; the choice still applies for this page view
  assert.equal(t.attrs["data-theme"], "dark");
});

test("choosing Light or Dark stores it on this device only; System removes the stored value", () => {
  const t = load();
  t.api.set("light");
  assert.deepEqual(t.calls.set.at(-1), ["rithavo-theme", "light"]);
  t.api.set("system");
  assert.deepEqual(t.calls.remove, ["rithavo-theme"]);
  assert.equal(t.attrs["data-theme"], undefined);
  t.api.set("garbage");                    // anything unknown means System, never stored
  assert.equal(t.calls.set.length, 1);
});

test("the selector buttons report the active choice with aria-pressed and respond to clicks", () => {
  const t = load({ stored: "dark" });
  t.ready();
  assert.deepEqual(t.buttons.map((b) => b.pressed), ["false", "true", "false"]);
  t.buttons[0].listeners.click({ currentTarget: t.buttons[0] });
  assert.equal(t.attrs["data-theme"], "light");
  assert.deepEqual(t.buttons.map((b) => b.pressed), ["true", "false", "false"]);
});

test("the theme-color meta follows the canvas colour", () => {
  const t = load({ canvas: "#0D0F1E" });
  assert.equal(t.meta.content, "#0D0F1E");
});

test("the script makes no network request and sends nothing anywhere", () => {
  assert.ok(!/fetch\(|XMLHttpRequest|sendBeacon|WebSocket|document\.cookie|import\(/.test(source));
});
