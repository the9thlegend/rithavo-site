// Real-Chrome check of the PWA on a DEPLOYED Vercel Preview (or any URL), signed out. Driven over the Chrome DevTools Protocol, no packages.
//
//   node tests/e2e/pwa_preview_check.js <URL> <OUT_DIR>
//
// URL is the deployment root, or its Preview URL with the _vercel_share parameter (opening it sets Vercel's protection cookie).
// A protected Preview answers 401 to requests that carry no cookie, and Chrome fetches the web-app manifest without
// credentials, so a protected Preview can never look installable. To judge the app rather than the protection, this
// script adds the share cookie to those credential-less requests (CDP Fetch interception), which is exactly what an
// unprotected host would serve. On an unprotected host no cookie exists and nothing is added.
//
// Signed-in journeys are covered by pwa_browser_check.js (local server with seeded throwaway accounts); a Preview's
// database is isolated and throwaway, so no account is created here.
// Exit code 0 only if every check passes.

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const INPUT = process.argv[2];
const OUT = process.argv[3] || path.join(os.tmpdir(), "pwa-preview-evidence");
if (!INPUT) { console.error("usage: pwa_preview_check.js <URL> <OUT_DIR>"); process.exit(2); }
const BASE = new URL(INPUT).origin;
const HAS_SHARE = new URL(INPUT).searchParams.has("_vercel_share");
fs.mkdirSync(OUT, { recursive: true });
const CANDIDATES = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].filter(Boolean);
const chromePath = CANDIDATES.find((p) => fs.existsSync(p));
if (!chromePath) { console.error("No Chromium found"); process.exit(2); }

const results = [];
function check(name, ok, detail = "") { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (detail ? `  [${detail}]` : "")); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const port = 9300 + Math.floor(Math.random() * 500);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "pwa-prev-chrome-"));
  const chrome = spawn(chromePath, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--headless=new", "--no-first-run",
    "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"], { stdio: "ignore" });
  let browserWs;
  for (let i = 0; i < 60 && !browserWs; i++) { try { browserWs = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; } catch (e) { /* not up */ } if (!browserWs) await sleep(250); }
  if (!browserWs) throw new Error("Chrome did not start");
  const ws = new WebSocket(browserWs);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const events = [];
  let cookieHeader = "", swOverride = null, pageSession = null;
  const sendTo = (sessionId, method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, (d) => (d.error ? reject(new Error(method + ": " + d.error.message)) : resolve(d.result))); ws.send(JSON.stringify({ id: i, method, params, ...(sessionId ? { sessionId } : {}) })); });
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
    if (d.method === "Fetch.requestPaused" && d.sessionId) {
      const { requestId, request } = d.params;
      const u = new URL(request.url);
      if (swOverride && u.pathname === "/sw.js") {
        sendTo(d.sessionId, "Fetch.fulfillRequest", { requestId, responseCode: 200, responseHeaders: [{ name: "Content-Type", value: "text/javascript" }, { name: "Cache-Control", value: "no-cache" }], body: Buffer.from(swOverride).toString("base64") }).catch(() => {});
        return;
      }
      const headers = Object.entries(request.headers).map(([name, value]) => ({ name, value }));
      if (cookieHeader && !headers.some((h) => h.name.toLowerCase() === "cookie")) headers.push({ name: "Cookie", value: cookieHeader });
      sendTo(d.sessionId, "Fetch.continueRequest", { requestId, headers }).catch(() => {});
      return;
    }
    if (d.method) events.push(d);
  };
  const { targetId } = await sendTo(null, "Target.createTarget", { url: "about:blank" });
  ({ sessionId: pageSession } = await sendTo(null, "Target.attachToTarget", { targetId, flatten: true }));
  const send = (method, params = {}) => sendTo(pageSession, method, params);
  const workerSessions = new Map();
  async function setOffline(offline) {
    const conditions = { offline, latency: 0, downloadThroughput: offline ? 0 : -1, uploadThroughput: offline ? 0 : -1 };
    await send("Network.emulateNetworkConditions", conditions);
    const { targetInfos } = await sendTo(null, "Target.getTargets");
    for (const t of targetInfos.filter((x) => x.type === "service_worker")) {
      try {
        let sessionId = workerSessions.get(t.targetId);
        if (!sessionId) { ({ sessionId } = await sendTo(null, "Target.attachToTarget", { targetId: t.targetId, flatten: true })); workerSessions.set(t.targetId, sessionId); await sendTo(sessionId, "Network.enable"); }
        await sendTo(sessionId, "Network.emulateNetworkConditions", conditions);
      } catch (e) { /* worker gone */ }
    }
  }
  const evalJs = async (expression) => { const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed"); return r.result.value; };
  const pageEvents = () => events.filter((e) => e.sessionId === pageSession);
  const goto = async (url) => { events.length = 0; await send("Page.navigate", { url }); for (let i = 0; i < 80; i++) { if (pageEvents().some((e) => e.method === "Page.loadEventFired")) break; await sleep(150); } await sleep(400); };
  const shot = async (name) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, name + ".png"), Buffer.from(r.data, "base64")); };
  const waitFor = async (expr, ms = 8000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await evalJs(expr)) return true; } catch (e) { /* retry */ } await sleep(200); } return false; };
  const cacheDump = () => evalJs(`(async()=>{const out=[];for(const n of await caches.keys()){const c=await caches.open(n);for(const r of await c.keys())out.push({cache:n,path:new URL(r.url).pathname})}return out})()`);

  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");

  // ---------------------------------------------------------------- open the deployment (share link sets Vercel's cookie)
  await goto(INPUT);
  if (HAS_SHARE) {
    const { cookies } = await send("Network.getCookies", { urls: [BASE + "/"] });
    cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    check("protected Preview opened with the share link (cookie set)", cookies.length > 0);
  }
  await send("Fetch.enable", { patterns: [{ urlPattern: BASE + "/*" }] });
  await goto(BASE + "/");
  check("home page loads (200, Rithavo page, stays on the same origin)", (await evalJs(`location.origin`)) === BASE && /Rithavo/.test(await evalJs(`document.title`)), await evalJs(`document.title`));

  // ---------------------------------------------------------------- what the deployment itself serves
  const headers = await evalJs(`(async()=>{const out={};for(const p of ['/manifest.webmanifest','/sw.js','/offline/','/home/','/assets/pwa/icon-512.png','/pwa.js','/styles.css']){const r=await fetch(p,{cache:'no-store'});out[p]={s:r.status,ct:r.headers.get('content-type'),cc:r.headers.get('cache-control'),n:(await r.arrayBuffer()).byteLength}}return out})()`);
  console.log("INFO response headers: " + JSON.stringify(headers));
  check("manifest, worker, offline page, icon, script, stylesheet and a signed-in page shell all answer 200", Object.values(headers).every((h) => h.s === 200), Object.entries(headers).map(([p, h]) => p + "=" + h.s).join(" "));
  check("manifest is served as a manifest type", /manifest\+json|application\/json/.test(headers["/manifest.webmanifest"].ct || ""), headers["/manifest.webmanifest"].ct);
  check("sw.js is served as JavaScript and is not long-cached", /javascript/.test(headers["/sw.js"].ct || "") && !/max-age=([1-9]\d{2,})/.test(headers["/sw.js"].cc || ""), `${headers["/sw.js"].ct} | ${headers["/sw.js"].cc}`);
  check("static pages revalidate (an offline copy is never served by the HTTP cache)", /max-age=0|no-cache|must-revalidate/.test(headers["/home/"].cc || ""), headers["/home/"].cc);

  // ---------------------------------------------------------------- manifest and installability (judged by Chrome itself)
  await goto(BASE + "/sign-in/");
  const manifest = await send("Page.getAppManifest");
  check("manifest parses with no errors", (manifest.errors || []).length === 0, JSON.stringify(manifest.errors || []));
  const m = JSON.parse(manifest.data || "{}");
  check("manifest: scope /, start_url /home/, standalone, 192/512/maskable icons, same origin",
    m.scope === "/" && /^\/home\//.test(m.start_url) && m.display === "standalone" && m.icons.some((i) => i.sizes === "192x192") && m.icons.some((i) => i.sizes === "512x512") && m.icons.some((i) => i.purpose === "maskable"),
    `${m.scope} ${m.start_url} ${m.display}`);
  const inst = await send("Page.getInstallabilityErrors");
  check("Chrome reports the app installable (no installability errors)", (inst.installabilityErrors || []).length === 0, JSON.stringify(inst.installabilityErrors || []));

  // ---------------------------------------------------------------- service worker + caching policy on the deployment
  check("service worker registers at scope / and activates", await waitFor(`navigator.serviceWorker.getRegistration().then(r=>!!(r&&r.active))`, 20000), await evalJs(`navigator.serviceWorker.getRegistration().then(r=>r&&r.scope)`));
  await goto(BASE + "/sign-in/");
  check("page is controlled by the worker after reload", await evalJs(`!!navigator.serviceWorker.controller`));
  for (const p of ["/career/", "/systems/", "/growth/", "/about/"]) await goto(BASE + p);
  await goto(BASE + "/home/");                                   // signed out: the shell sends the visitor to sign-in
  await goto(BASE + "/account/");
  await goto(BASE + "/resumes/");
  const meStatus = await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.status)`);
  await evalJs(`fetch('/api/resumes',{credentials:'same-origin'}).then(r=>r.status)`);
  await sleep(800);
  const dump = await cacheDump();
  console.log("INFO cached entries: " + dump.map((e) => e.cache.replace("rithavo-", "") + ":" + e.path).join("  "));
  const PRIVATE = ["/home/", "/account/", "/profile/", "/resumes/", "/history/", "/mentorship/", "/career-intelligence/", "/application-diagnosis/", "/onboarding/", "/admin/"];
  check("signed-out API answers 401 through the worker (not a cached copy)", meStatus === 401, String(meStatus));
  check("no /api response is in any cache", dump.every((e) => !e.path.startsWith("/api")));
  check("no signed-in app page (/home/, /account/, /resumes/ ...) is in any cache", dump.every((e) => !PRIVATE.includes(e.path)), dump.filter((e) => PRIVATE.includes(e.path)).map((e) => e.path).join(","));
  check("public pages visited are cached", ["/career/", "/systems/", "/growth/", "/about/"].every((p) => dump.some((e) => e.cache.startsWith("rithavo-pages") && e.path === p)));
  check("shell precache is complete", ["/offline/", "/styles.css", "/pwa.js", "/assets/pwa/icon-192.png"].every((p) => dump.some((e) => e.cache.startsWith("rithavo-shell") && e.path === p)));

  // ---------------------------------------------------------------- offline
  await setOffline(true);
  await goto(BASE + "/career/");
  check("a visited public page opens offline", /Career/i.test(await evalJs(`document.title`)) && !/^Offline/.test(await evalJs(`document.title`)), await evalJs(`document.title`));
  check("offline notice appears", await waitFor(`!!document.querySelector('.pwa-offline-bar')`, 4000));
  await goto(BASE + "/home/?source=pwa");
  check("offline launch (start_url) shows the offline page", /^Offline/.test(await evalJs(`document.title`)), await evalJs(`document.title`));
  await shot("preview-offline-launch");
  await goto(BASE + "/contact/");
  check("an unvisited page shows the offline page", /^Offline/.test(await evalJs(`document.title`)));
  check("API fails offline rather than answering from a cache", (await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(()=>'served').catch(()=>'failed')`)) === "failed");
  await setOffline(false);
  await sleep(1200);
  await goto(BASE + "/career/");
  check("back online: notice gone and the page is live", await waitFor(`!document.querySelector('.pwa-offline-bar')`, 5000));

  // ---------------------------------------------------------------- update lifecycle
  // A worker-script update cannot be simulated on a protected Preview (Chrome's update fetch is not interceptable), so the
  // upgrade is proven across two real deployments by the separate probe described in the release evidence; the local
  // pwa_browser_check.js (PWA_SW_FILE) covers the same cycle on every run.
  const swText = await (await fetch(BASE + "/sw.js", { headers: cookieHeader ? { Cookie: cookieHeader } : {} })).text();
  const vm = swText.match(/const VERSION = "(v\d+)"/);
  check("deployed worker declares a cache version", !!vm, vm && vm[1]);

  // ---------------------------------------------------------------- mobile (installed-app mode is simulated; a Preview cannot be installed on a phone here)
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await goto(BASE + "/systems/");
  check("browser tab on a phone: no app bottom bar on a public pillar page", !(await evalJs(`!!document.querySelector('.pwa-bottomnav') && !document.querySelector('.pwa-bottomnav').hidden`)));
  const shim = await send("Page.addScriptToEvaluateOnNewDocument", { source: "Object.defineProperty(navigator,'standalone',{get:()=>true})" });
  for (const [p, label] of [["/systems/", "Product Studio"], ["/growth/", "Business Transformation"], ["/career/", "Career"]]) {
    await goto(BASE + p);
    await waitFor(`!!document.querySelector('.pwa-bottomnav')`, 5000);
    const nav = await evalJs(`(()=>{const n=document.querySelector('.pwa-bottomnav');if(!n||n.hidden)return null;const items=[...n.querySelectorAll('a,button')].map(x=>{const b=x.getBoundingClientRect();return{t:x.textContent.trim(),w:Math.round(b.width),h:Math.round(b.height),cur:x.getAttribute('aria-current')}});return{items,overflowX:document.documentElement.scrollWidth>window.innerWidth}})()`);
    check(`installed app on a phone, ${label}: bottom bar with Home/Career/Studio/Transform/Account, targets >=44px, no sideways scroll`,
      !!nav && nav.items.length === 5 && nav.items.every((i) => i.w >= 44 && i.h >= 44) && !nav.overflowX && /Studio/.test(nav.items[2].t) && /Transform/.test(nav.items[3].t) && !/In development/.test(nav.items[2].t + nav.items[3].t), nav && nav.items.map((i) => i.t).join(" | "));
    if (p === "/systems/") await shot("preview-mobile-systems");
    if (p === "/growth/") await shot("preview-mobile-growth");
    if (p === "/career/") await shot("preview-mobile-career");
  }
  await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: shim.identifier });
  await send("Emulation.clearDeviceMetricsOverride");

  // ---------------------------------------------------------------- pillars, Card, one domain
  for (const [p, label, rx] of [["/systems/", "Product Studio", /Building products that solve real problems/i], ["/growth/", "Business Transformation", /Turning business potential into measurable performance/i]]) {
    await goto(BASE + p);
    const txt = await evalJs(`document.body.innerText`);
    check(`${label} page presents the new pillar`, rx.test(txt) && !/Rithavo Systems|Rithavo Growth/.test(txt) && !/buy now|add to cart|purchase now/i.test(txt));
  }
  await goto(BASE + "/career/");
  const careerTxt = await evalJs(`document.body.innerText`);
  check("Career page says 'Not yet available for purchase.' and shows no price or launch claim", /Not yet available for purchase\./.test(careerTxt) && !/available now|live now|available at launch|launching soon|₹/i.test(careerTxt));
  const crawl = await send("Page.addScriptToEvaluateOnNewDocument", { source: "Object.defineProperty(navigator,'standalone',{get:()=>true})" });
  const findings = [], textMentions = [];
  const PUBLIC = ["/", "/career/", "/systems/", "/growth/", "/about/", "/contact/", "/privacy/", "/terms/", "/sign-in/", "/sign-up/", "/career/career-intelligence/", "/career/application-diagnostic/"];
  for (const p of PUBLIC) {
    await goto(BASE + p);
    const f = await evalJs(`(()=>{const ok=new Set([location.host,'rithavo.com','sdk.cashfree.com']);const bad=[],text=[];
      document.querySelectorAll('a[href],form[action],iframe[src],link[href],script[src],img[src]').forEach(e=>{const u=e.getAttribute('href')||e.getAttribute('action')||e.getAttribute('src')||'';let h='';try{h=new URL(u,location.href).host}catch(x){}if(h&&!ok.has(h))bad.push(u)});
      if(/app[.]rithavo[.]com/i.test(document.body.innerText))text.push('names app.rithavo.com in visible text');
      return {bad,text,origin:location.origin}})()`);
    if (f.bad.length) findings.push(p + " -> " + f.bad.join(", "));
    if (f.text.length) textMentions.push(p);
    if (f.origin !== BASE) findings.push(p + " left the origin: " + f.origin);
  }
  await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: crawl.identifier });
  check("installed-app mode, " + PUBLIC.length + " public pages: no link, form or script points at another host (Card or otherwise)", findings.length === 0, findings.join(" | "));
  check("no page names another host in visible text", textMentions.length === 0, textMentions.join(","));

  // ---------------------------------------------------------------- routing allowlist still holds on this deployment (status and size only)
  const probe = await evalJs(`(async()=>{const out={};for(const p of ['/config%2Epy','/app%2Fmain%2Epy','/requirements%2Etxt','/vercel%2Ejson','/tests%2Ftest_pwa%2Epy','/sw.js%2Emap','/manifest.json','/pwa.py','/tools/make_pwa_icons.py','/config.py','/design-lab/']){const r=await fetch(p,{cache:'no-store',redirect:'manual'});out[p]=r.status+'/'+(await r.arrayBuffer()).byteLength}return out})()`);
  console.log("INFO internal/encoded paths (status/bytes): " + JSON.stringify(probe));
  check("internal files and encoded paths all answer 404", Object.values(probe).every((v) => v.startsWith("404/")), JSON.stringify(probe));

  await shot("preview-desktop-home-end");
  ws.close(); chrome.kill();
  const failed = results.filter((r) => !r).length;
  console.log(`\n${failed === 0 ? "ALL PASS" : "FAILURES: " + failed} (${results.length - failed}/${results.length})  target: ${BASE}  screenshots: ${OUT}`);
  process.exit(failed === 0 ? 0 : 1);
}
main().catch((e) => { console.error("ERROR", e); process.exit(2); });
