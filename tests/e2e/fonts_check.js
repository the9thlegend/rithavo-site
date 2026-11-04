// Real-Chrome check of self-hosted fonts: which hosts are contacted, which font files render, and the page layout.
//
//   node tests/e2e/fonts_check.js <BASE_URL> <OUT_JSON>            measure and (if the page is the new build) assert
//   node tests/e2e/fonts_check.js --compare <BASELINE_JSON> <NEW_JSON>
//
// Per page and viewport (desktop 1280x900, phone 390x844) it records: every host contacted, the font files requested, the platform
// font Chrome actually used for headings, body and monospace text (CSS.getPlatformFontsForNode), the document height and the
// bounding boxes of the main headings. --compare asserts the new build has NO Google request, the SAME fonts rendered, and the same
// layout as the baseline (a build that still used Google Fonts). Exit code 0 only if every check passes.
const { spawn } = require("node:child_process");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PAGES = ["/", "/career/", "/about/", "/systems/", "/growth/", "/privacy/", "/contact/", "/sign-in/"];
const VIEWS = [["desktop", 1280, 900, false], ["phone", 390, 844, true]];

if (process.argv[2] === "--compare") {
  const base = JSON.parse(fs.readFileSync(process.argv[3], "utf8")), cur = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
  let failed = 0; const check = (n, ok, d = "") => { if (!ok) failed++; console.log((ok ? "PASS " : "FAIL ") + n + (d ? `  [${String(d).slice(0, 260)}]` : "")); };
  const google = cur.flatMap((r) => r.hosts.filter((h) => /fonts\.g(oogleapis|static)\.com/.test(h)));
  check("new build: no request to fonts.googleapis.com or fonts.gstatic.com on any page or viewport", google.length === 0, google.join(","));
  check("baseline really used Google Fonts (the comparison is meaningful)", base.some((r) => r.hosts.some((h) => /fonts\.gstatic\.com/.test(h))));
  const outside = cur.flatMap((r) => r.hosts.filter((h) => h !== cur[0].host));
  check("new build: every request goes to the site itself", outside.length === 0, [...new Set(outside)].join(","));
  const files = [...new Set(cur.flatMap((r) => r.fontFiles))].sort();
  check("new build: font files come from /assets/fonts/ only and are woff2", files.length > 0 && files.every((f) => /^\/assets\/fonts\/[a-z0-9-]+\.woff2$/.test(f)), files.join(","));
  const notOk = cur.flatMap((r) => r.fontStatus.filter((s) => s.status !== "loaded").map((s) => `${r.page}:${r.view}:${s.family}:${s.status}`));
  check("new build: every declared font face that the page needed reached 'loaded'", notOk.length === 0, notOk.slice(0, 5).join(" | "));
  let sameFonts = true, sameLayout = true, why = "";
  base.forEach((b, i) => {
    const c = cur[i];
    for (const k of ["h1", "body", "mono"]) {
      const bf = JSON.stringify(b.platform[k]), cf = JSON.stringify(c.platform[k]);
      if (bf !== cf) { sameFonts = false; why += `${c.page}:${c.view}:${k} ${bf} -> ${cf}; `; }
    }
    if (Math.abs(b.docHeight - c.docHeight) > 2 || JSON.stringify(b.rects) !== JSON.stringify(c.rects)) { sameLayout = false; why += `${c.page}:${c.view} layout ${b.docHeight}->${c.docHeight}; `; }
  });
  check("the same platform fonts render for headings, body and monospace text as with Google Fonts (all pages, both viewports)", sameFonts, why);
  check("the layout (document height and heading boxes) is identical to the Google Fonts baseline (all pages, both viewports)", sameLayout, why);
  console.log(`\n${failed === 0 ? "ALL PASS" : "FAILURES: " + failed}`);
  process.exit(failed ? 1 : 0);
}

const BASE = (process.argv[2] || "").replace(/\/$/, ""), OUT = process.argv[3];
if (!BASE || !OUT) { console.error("usage: fonts_check.js <BASE_URL> <OUT_JSON> | --compare <baseline> <new>"); process.exit(2); }
const chromePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].filter(Boolean).find((p) => fs.existsSync(p));
(async () => {
  const port = 9300 + Math.floor(Math.random() * 500);
  const chrome = spawn(chromePath, [`--remote-debugging-port=${port}`, `--user-data-dir=${fs.mkdtempSync(path.join(os.tmpdir(), "fc-chrome-"))}`, "--headless=new", "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
  let bw; for (let i = 0; i < 60 && !bw; i++) { try { bw = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; } catch (e) { /* not up */ } if (!bw) await sleep(250); }
  const ws = new WebSocket(bw); await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); let reqs = [], sess;
  const sendTo = (s, method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, (d) => (d.error ? rej(new Error(method + ": " + d.error.message)) : res(d.result))); ws.send(JSON.stringify({ id: i, method, params, ...(s ? { sessionId: s } : {}) })); });
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; } if (d.method === "Network.requestWillBeSent") reqs.push(d.params.request.url); };
  const t = await sendTo(null, "Target.createTarget", { url: "about:blank" });
  ({ sessionId: sess } = await sendTo(null, "Target.attachToTarget", { targetId: t.targetId, flatten: true }));
  const send = (m, p) => sendTo(sess, m, p);
  const ev = async (e) => (await send("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true })).result.value;
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable"); await send("DOM.enable"); await send("CSS.enable");
  await send("Network.setCacheDisabled", { cacheDisabled: true });
  const out = [];
  for (const [view, w, h, mobile] of VIEWS) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile });
    for (const page of PAGES) {
      reqs = [];
      await send("Page.navigate", { url: BASE + page });
      await sleep(2500);
      await ev(`document.fonts.ready.then(()=>true)`);
      const hosts = [...new Set(reqs.map((u) => { try { return new URL(u).host; } catch (e) { return ""; } }).filter(Boolean))].sort();
      const fontFiles = [...new Set(reqs.filter((u) => /\.woff2?(\?|$)/.test(u)).map((u) => { const x = new URL(u); return x.host === new URL(BASE).host ? x.pathname : u; }))].sort();
      const fontStatus = await ev(`[...document.fonts].filter(f=>f.status!=='unloaded').map(f=>({family:f.family.replace(/["']/g,''),weight:f.weight,status:f.status}))`);
      // fonts for headings, body and a monospace element (a .label if present), as Chrome actually used them
      const { root } = await send("DOM.getDocument", { depth: 0 });
      const plat = {};
      for (const [k, sel] of [["h1", "h1"], ["body", "p"], ["mono", ".label, code, .tag"]]) {
        try {
          const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: sel });
          if (!nodeId) { plat[k] = null; continue; }
          const { fonts } = await send("CSS.getPlatformFontsForNode", { nodeId });
          plat[k] = fonts.map((f) => `${f.familyName}${f.isCustomFont ? "*" : ""}`).sort();
        } catch (e) { plat[k] = null; }
      }
      // custom flag differs only if a face is local vs remote; compare family names alone
      for (const k of Object.keys(plat)) if (plat[k]) plat[k] = plat[k].map((x) => x.replace(/\*$/, ""));
      const rects = await ev(`[...document.querySelectorAll('h1,h2')].slice(0,8).map(e=>{const r=e.getBoundingClientRect();return [Math.round(r.width),Math.round(r.height)]})`);
      const docHeight = await ev(`document.documentElement.scrollHeight`);
      out.push({ page, view, host: new URL(BASE).host, hosts, fontFiles, fontStatus, platform: plat, rects, docHeight });
    }
  }
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  console.log(`measured ${out.length} page/viewport combinations -> ${OUT}`);
  ws.close(); chrome.kill(); process.exit(0);
})().catch((e) => { console.error("ERROR", e); process.exit(2); });
