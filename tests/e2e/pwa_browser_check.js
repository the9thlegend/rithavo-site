// Real-browser check of the PWA, driven over the Chrome DevTools Protocol (no packages, no network beyond BASE).
//
//   node tests/e2e/pwa_browser_check.js <BASE_URL> <OUT_DIR> [--seed-users]
//
// BASE_URL must be a server running this branch with two accounts whose credentials are in the env:
//   PWA_USER_A / PWA_PASS_A and PWA_USER_B / PWA_PASS_B   (throwaway test accounts only)
// Needs a Chromium on this machine (CHROME_PATH, or Chrome / Edge in their default Windows locations).
// Exit code 0 only if every check passes. Screenshots are written to OUT_DIR.

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const BASE = (process.argv[2] || "http://127.0.0.1:8533").replace(/\/$/, "");
const OUT = process.argv[3] || path.join(os.tmpdir(), "pwa-evidence");
const A = { email: process.env.PWA_USER_A, pass: process.env.PWA_PASS_A };
const B = { email: process.env.PWA_USER_B, pass: process.env.PWA_PASS_B };
const RESUME_A = process.env.PWA_RESUME_A, RESUME_B = process.env.PWA_RESUME_B;   // ids of a resume owned by A / by B (local seed)
const MARKER_A = process.env.PWA_MARKER_A;                                          // text that exists only inside A's resume
const EXTRA_COOKIE = process.env.PWA_EXTRA_COOKIE;       // optional "name=value" (e.g. a Vercel share cookie), set before navigation
if (!A.email || !A.pass || !B.email || !B.pass) { console.error("Set PWA_USER_A/PASS_A/USER_B/PASS_B"); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const CANDIDATES = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].filter(Boolean);
const chromePath = CANDIDATES.find((p) => fs.existsSync(p));
if (!chromePath) { console.error("No Chromium found"); process.exit(2); }

const results = [];
function check(name, ok, detail = "") { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (detail ? `  [${detail}]` : "")); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const port = 9300 + Math.floor(Math.random() * 500);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "pwa-chrome-"));
  const chrome = spawn(chromePath, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--headless=new", "--no-first-run",
    "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"], { stdio: "ignore" });
  let browserWs;
  for (let i = 0; i < 60 && !browserWs; i++) {
    try { browserWs = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; } catch (e) { /* not up yet */ }
    if (!browserWs) await sleep(250);
  }
  if (!browserWs) throw new Error("Chrome did not start");
  const ws = new WebSocket(browserWs);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const events = [];
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d); };
  const sendTo = (sessionId, method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, (d) => (d.error ? reject(new Error(method + ": " + d.error.message)) : resolve(d.result))); ws.send(JSON.stringify({ id: i, method, params, ...(sessionId ? { sessionId } : {}) })); });
  const { targetId } = await sendTo(null, "Target.createTarget", { url: "about:blank" });
  const { sessionId: pageSession } = await sendTo(null, "Target.attachToTarget", { targetId, flatten: true });
  const send = (method, params = {}) => sendTo(pageSession, method, params);
  // The page's offline switch does not reach the service worker's own requests; apply it to the worker target too.
  const workerSessions = new Map();
  async function setOffline(offline) {
    const conditions = { offline, latency: 0, downloadThroughput: offline ? 0 : -1, uploadThroughput: offline ? 0 : -1 };
    await send("Network.emulateNetworkConditions", conditions);
    const { targetInfos } = await sendTo(null, "Target.getTargets");
    for (const t of targetInfos.filter((x) => x.type === "service_worker")) {
      try {
        // reuse one session per worker: emulation is per session, so a second session cannot undo the first
        let sessionId = workerSessions.get(t.targetId);
        if (!sessionId) {
          ({ sessionId } = await sendTo(null, "Target.attachToTarget", { targetId: t.targetId, flatten: true }));
          workerSessions.set(t.targetId, sessionId);
          await sendTo(sessionId, "Network.enable");
        }
        await sendTo(sessionId, "Network.emulateNetworkConditions", conditions);
      } catch (e) { /* worker already gone */ }
    }
  }
  const evalJs = async (expression) => { const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed"); return r.result.value; };
  const pageEvents = () => events.filter((e) => e.sessionId === pageSession);
  const goto = async (url) => { events.length = 0; await send("Page.navigate", { url }); for (let i = 0; i < 80; i++) { if (pageEvents().some((e) => e.method === "Page.loadEventFired")) break; await sleep(150); } await sleep(300); };
  const shot = async (name) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, name + ".png"), Buffer.from(r.data, "base64")); };
  const waitFor = async (expr, ms = 8000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await evalJs(expr)) return true; } catch (e) { /* retry */ } await sleep(200); } return false; };

  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  if (EXTRA_COOKIE) { const [n, ...v] = EXTRA_COOKIE.split("="); await send("Network.setCookie", { name: n, value: v.join("="), url: BASE + "/" }); }
  const login = (u) => evalJs(`fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({email:${JSON.stringify(u.email)},password:${JSON.stringify(u.pass)}})}).then(r=>r.status)`);

  // ---------------------------------------------------------------- manifest and installability
  await goto(BASE + "/sign-in/");
  const manifest = await send("Page.getAppManifest");
  check("manifest parses with no errors", (manifest.errors || []).length === 0, JSON.stringify(manifest.errors || []));
  const m = JSON.parse(manifest.data || "{}");
  check("manifest scope is the site root and start_url is /home/", m.scope === "/" && m.start_url.startsWith("/home/"), `${m.scope} ${m.start_url}`);
  const inst = await send("Page.getInstallabilityErrors");
  check("browser reports the app installable (no installability errors)", (inst.installabilityErrors || []).length === 0, JSON.stringify(inst.installabilityErrors || []));

  // ---------------------------------------------------------------- service worker
  const swUp = await waitFor(`navigator.serviceWorker.getRegistration().then(r=>!!(r&&r.active))`, 15000);
  check("service worker registers at scope / and activates", swUp, await evalJs(`navigator.serviceWorker.getRegistration().then(r=>r&&r.scope)`));
  await goto(BASE + "/sign-in/");   // reload so the page is controlled
  check("page is controlled by the worker after reload", await evalJs(`!!navigator.serviceWorker.controller`));
  const shellKeys = await evalJs(`caches.open('rithavo-shell-v2').then(c=>c.keys()).then(k=>k.map(r=>new URL(r.url).pathname))`);
  check("shell precache holds the offline page and core assets", ["/offline/", "/styles.css", "/pwa.js", "/assets/pwa/icon-192.png"].every((p) => shellKeys.includes(p)), shellKeys.join(","));

  // ---------------------------------------------------------------- signed in: private data never reaches a cache
  check("user A can sign in", (await login(A)) === 200);
  const PRIVATE_PAGES = ["/home/", "/account/", "/profile/", "/resumes/", "/history/", "/mentorship/", "/career-intelligence/", "/application-diagnosis/"];
  for (const pth of PRIVATE_PAGES) await goto(BASE + pth);
  for (const pth of ["/career/", "/systems/", "/growth/"]) await goto(BASE + pth);
  const meStatus = await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.status)`);
  check("authenticated API works through the worker", meStatus === 200);
  await evalJs(`fetch('/api/resumes',{credentials:'same-origin'}).then(r=>r.status)`);
  await sleep(800);
  const dump = await evalJs(`(async()=>{const out=[];for(const n of await caches.keys()){const c=await caches.open(n);for(const r of await c.keys()){const resp=await c.match(r);const text=(resp.headers.get('content-type')||'').match(/text|json|html|javascript/)?await resp.clone().text():'';out.push({cache:n,path:new URL(r.url).pathname,hasEmail:text.includes(${JSON.stringify(A.email)}),hasMarker:${JSON.stringify(MARKER_A || "")}!==''&&text.includes(${JSON.stringify(MARKER_A || "")}),ct:resp.headers.get('content-type')});}}return out;})()`);
  check("no /api response is in any cache", dump.every((e) => !e.path.startsWith("/api")), dump.filter((e) => e.path.startsWith("/api")).map((e) => e.path).join(","));
  check("no cached entry contains the signed-in user's email", dump.every((e) => !e.hasEmail));
  check("no JSON is cached anywhere", dump.every((e) => !(e.ct || "").includes("json")));
  const pagesCached = dump.filter((e) => e.cache.startsWith("rithavo-pages"));
  check("public pages visited are cached as HTML only", pagesCached.every((e) => (e.ct || "").startsWith("text/html")) && ["/career/", "/systems/", "/growth/"].every((x) => pagesCached.some((e) => e.path === x)), pagesCached.map((e) => e.path).join(","));
  check("NO signed-in app page is in any cache (all " + PRIVATE_PAGES.length + " were visited while signed in)", dump.every((e) => !PRIVATE_PAGES.includes(e.path)), dump.filter((e) => PRIVATE_PAGES.includes(e.path)).map((e) => e.path).join(","));
  check("no cached entry contains the private resume text", dump.every((e) => !e.hasMarker));
  console.log("INFO cached entries: " + dump.map((e) => e.cache.replace("rithavo-", "") + ":" + e.path).join("  "));

  // ---------------------------------------------------------------- resume download: network only, owner only
  if (RESUME_A && RESUME_B) {
    const list = await evalJs(`fetch('/api/resumes',{credentials:'same-origin'}).then(r=>r.json())`);
    check("resume list returns the signed-in user's resume only", Array.isArray(list) && list.some((r) => String(r.id) === RESUME_A) && !list.some((r) => String(r.id) === RESUME_B));
    const dlDir = path.join(OUT, "downloads"); fs.mkdirSync(dlDir, { recursive: true });
    for (const f of fs.readdirSync(dlDir)) fs.rmSync(path.join(dlDir, f));
    await sendTo(null, "Browser.setDownloadBehavior", { behavior: "allow", downloadPath: dlDir, eventsEnabled: true });
    events.length = 0;
    await send("Page.navigate", { url: BASE + "/api/resume/" + RESUME_A + "/download" });
    let done = false;
    for (let i = 0; i < 60 && !done; i++) { done = events.some((e) => e.method === "Browser.downloadProgress" && e.params.state === "completed"); if (!done) await sleep(250); }
    const files = fs.readdirSync(dlDir).filter((f) => !f.endsWith(".crdownload"));
    const bytes = files.length ? fs.readFileSync(path.join(dlDir, files[0])) : Buffer.alloc(0);
    check("clicking a resume download (top-level navigation) saves a real .docx", done && files.length === 1 && /\.docx$/.test(files[0]) && bytes.length > 1000 && bytes.slice(0, 2).toString() === "PK", `${files.join(",")} ${bytes.length} bytes`);
    for (const f of files) fs.rmSync(path.join(dlDir, f));
    await goto(BASE + "/resumes/");
    const header = await evalJs(`fetch('/api/resume/${RESUME_A}/download',{credentials:'same-origin'}).then(r=>({s:r.status,ct:r.headers.get('content-type'),cd:r.headers.get('content-disposition'),cc:r.headers.get('cache-control')}))`);
    check("download response is a .docx attachment", header.s === 200 && /wordprocessingml/.test(header.ct) && /attachment/.test(header.cd), JSON.stringify(header));
    const other = await evalJs(`fetch('/api/resume/${RESUME_B}/download',{credentials:'same-origin'}).then(r=>r.status)`);
    check("another user's resume cannot be downloaded (404/403)", other === 404 || other === 403, String(other));
    const after = await evalJs(`(async()=>{const out=[];for(const n of await caches.keys()){const c=await caches.open(n);for(const r of await c.keys())out.push(new URL(r.url).pathname)}return out})()`);
    check("after downloading, no resume URL or API path is in any cache", after.every((x) => !/^\/api|\/resume\//.test(x)), after.filter((x) => /^\/api|\/resume\//.test(x)).join(","));
  } else console.log("SKIP resume download checks (set PWA_RESUME_A / PWA_RESUME_B)");

  // ---------------------------------------------------------------- session continuity
  await goto(BASE + "/home/?source=pwa");                      // the installed app's start_url
  check("start_url /home/?source=pwa opens signed in (topbar present)", await waitFor(`!!document.getElementById('app-topbar')`, 8000));
  await goto(BASE + "/career/");
  await goto(BASE + "/account/");
  check("session survives navigation between public and app pages (account loads)", await waitFor(`!!document.getElementById('app-topbar')`, 8000));
  await evalJs(`location.reload()`); await sleep(1500);
  check("session survives a reload", await waitFor(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.status===200)`, 6000));

  // ---------------------------------------------------------------- offline and reconnect
  await setOffline(true);
  await goto(BASE + "/career/");
  check("a visited public page still opens offline from the cache", await evalJs(`/Career/i.test(document.title) && !/^Offline/.test(document.title)`), await evalJs(`document.title`));
  check("offline notice is shown on the page", await waitFor(`!!document.querySelector('.pwa-offline-bar')`, 4000));
  await shot("offline-notice");
  await goto(BASE + "/home/?source=pwa");
  const homeOffline = await evalJs(`document.title + '|' + document.body.innerText.slice(0,200)`);
  check("offline launch of the installed app (start_url) shows the offline page, not a stuck 'Loading' screen", /^Offline/.test(homeOffline) && !/Loading your/i.test(homeOffline) && !(await evalJs(`!!document.getElementById('app-topbar')`)), homeOffline.replace(/\n/g, " ").slice(0, 70));
  await shot("offline-launch");
  await goto(BASE + "/account/");
  check("a signed-in page visited earlier is NOT served from cache offline", /^Offline/.test(await evalJs(`document.title`)));
  const apiOffline = await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(()=> 'served').catch(()=> 'failed')`);
  check("API calls fail offline instead of returning a cached copy", apiOffline === "failed", apiOffline);
  await goto(BASE + "/contact/");
  const offlineText = await evalJs(`document.title + '|' + (document.body.innerText||'').slice(0,160)`);
  check("an unvisited public page shows the offline page, not an error", /^Offline/.test(offlineText) && /offline/i.test(offlineText), offlineText.replace(/\n/g, " ").slice(0, 80));
  await shot("offline-page");
  await setOffline(false);
  await sleep(1200);
  await goto(BASE + "/home/");
  const apiBack = await waitFor(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.status===200)`, 6000);
  const barGone = await waitFor(`!document.querySelector('.pwa-offline-bar')`, 4000);
  check("back online: the offline notice is gone and the API answers", apiBack && barGone, `api=${apiBack} barGone=${barGone} online=${await evalJs("navigator.onLine")} url=${await evalJs("location.pathname")}`);

  // ---------------------------------------------------------------- mobile layout and navigation
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await goto(BASE + "/home/");
  await waitFor(`!!document.querySelector('.pwa-bottomnav')`, 6000);
  const nav = await evalJs(`(()=>{const n=document.querySelector('.pwa-bottomnav');if(!n)return null;const r=n.getBoundingClientRect();const items=[...n.querySelectorAll('a,button')].map(x=>{const b=x.getBoundingClientRect();return{label:x.textContent.trim(),w:Math.round(b.width),h:Math.round(b.height)}});return{visible:getComputedStyle(n).display!=='none',bottom:Math.round(window.innerHeight-r.bottom),items,bodyClass:document.body.classList.contains('has-bottom-nav'),tabsHidden:getComputedStyle(document.querySelector('.app-tabs')).display==='none',overflowX:document.documentElement.scrollWidth>window.innerWidth}})()`);
  check("mobile: bottom navigation is shown with the three pillars", !!nav && nav.visible && nav.items.length === 5 && ["Home", "Career", "Systems", "Growth", "Account"].every((l, i) => nav.items[i].label.startsWith(l)), nav && nav.items.map((i) => i.label).join(" | "));
  check("mobile: every nav target is at least 44px", !!nav && nav.items.every((i) => i.w >= 44 && i.h >= 44), nav && nav.items.map((i) => `${i.w}x${i.h}`).join(" "));
  check("mobile: desktop tab strip hidden, no horizontal scroll", !!nav && nav.tabsHidden && !nav.overflowX);
  check("Systems and Growth are labelled as in development", await evalJs(`/In development/.test(document.querySelector('.pwa-bottomnav').innerText)`));
  await shot("mobile-home");
  await evalJs(`document.querySelector('.pwa-bottomnav button[data-key=career]').click()`);
  const sheetOpen = await evalJs(`(()=>{const s=document.querySelector('.pwa-sheet');return{open:!s.hidden,links:[...s.querySelectorAll('a')].map(a=>a.textContent.trim()),focusInside:s.contains(document.activeElement),expanded:document.querySelector('.pwa-bottomnav button[data-key=career]').getAttribute('aria-expanded')}})()`);
  check("Career sheet opens with the career tools, focus inside, aria-expanded set", sheetOpen.open && sheetOpen.focusInside && sheetOpen.expanded === "true" && ["Profile", "Career Intelligence", "Application Diagnosis", "Resumes", "Mentorship"].every((l) => sheetOpen.links.includes(l)), sheetOpen.links.join(", "));
  check("the Rithavo Card is not offered in navigation", !sheetOpen.links.some((l) => /card/i.test(l)));
  await shot("mobile-career-sheet");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await sleep(300);
  const closed = await evalJs(`({hidden:document.querySelector('.pwa-sheet').hidden,focus:document.activeElement&&document.activeElement.getAttribute('data-key'),expanded:document.querySelector('.pwa-bottomnav button[data-key=career]').getAttribute('aria-expanded')})`);
  check("Escape closes the sheet and returns focus to Career", closed.hidden && closed.focus === "career" && closed.expanded === "false", JSON.stringify(closed));
  await goto(BASE + "/systems/");
  const sysInfo = await evalJs(`document.title + '|' + (document.body.innerText || '').slice(0, 80).split(String.fromCharCode(10)).join(' ')`);
  check("direct URL /systems/ loads (public pillar page)", /Systems/i.test(sysInfo) && !/^Offline/.test(sysInfo), sysInfo);
  await goto(BASE + "/account/");
  await shot("mobile-account");
  check("Account page shows install guidance for a browser tab", await evalJs(`(()=>{const s=document.querySelector('[data-pwa-install]');return !!s && !s.hidden && s.innerText.length>20})()`));

  // ---------------------------------------------------------------- installed-app mode: the Card entry is hidden
  const injected = await send("Page.addScriptToEvaluateOnNewDocument", { source: "Object.defineProperty(navigator,'standalone',{get:()=>true})" });
  await goto(BASE + "/home/");
  const standalone = await evalJs(`(()=>{const d=document.createElement('div');d.className='pwa-hide-in-app';d.textContent='card';document.body.appendChild(d);return{isPwa:document.documentElement.classList.contains('is-pwa'),display:getComputedStyle(d).display}})()`);
  check("installed-app mode is detected and .pwa-hide-in-app elements (the Card) are hidden", standalone.isPwa && standalone.display === "none", JSON.stringify(standalone));
  await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: injected.identifier });
  await send("Emulation.clearDeviceMetricsOverride");

  // ---------------------------------------------------------------- the three pillars
  for (const [pth, rx, label] of [["/systems/", /in development and not yet available/i, "Systems"], ["/growth/", /in development and not yet available/i, "Growth"]]) {
    await goto(BASE + pth);
    const txt = await evalJs(`document.body.innerText`);
    check(label + " page says it is in development and not yet available", rx.test(txt) && !/buy now|add to cart|purchase now/i.test(txt));
  }
  await goto(BASE + "/career/");
  const careerTxt = await evalJs(`document.body.innerText`);
  check("Career page presents Career Intelligence and Application Diagnostic as available at launch (not 'live now')", /available at launch/i.test(careerTxt) && !/available now|live now/i.test(careerTxt));

  // ---------------------------------------------------------------- Card: never reachable in the installed app, never points at another domain
  const CARD_PAGES = ["/", "/home/", "/account/", "/profile/", "/resumes/", "/history/", "/mentorship/", "/career/", "/systems/", "/growth/", "/about/", "/contact/", "/privacy/", "/terms/", "/career-intelligence/", "/application-diagnosis/"];
  const standaloneShim = await send("Page.addScriptToEvaluateOnNewDocument", { source: "Object.defineProperty(navigator,'standalone',{get:()=>true})" });
  const cardFindings = [], textMentions = [];
  for (const pth of CARD_PAGES) {
    await goto(BASE + pth);
    await sleep(500);
    const f = await evalJs(`(()=>{
      const ok=new Set([location.host,'rithavo.com','checkout.razorpay.com','cdn.razorpay.com','api.razorpay.com','sdk.cashfree.com']);   // the payment SDK's own subresources are not Rithavo hosts
      const bad=[],text=[];
      const vis=(e)=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'};
      document.querySelectorAll('a[href],form[action],iframe[src],link[href],script[src],img[src]').forEach(e=>{
        const u=e.getAttribute('href')||e.getAttribute('action')||e.getAttribute('src')||'';
        let h='';try{h=new URL(u,location.href).host}catch(x){}
        if(h&&!ok.has(h))bad.push('link:'+u);
      });
      document.querySelectorAll('.card-handoff-btn,#card-handoff-form').forEach(e=>{if(vis(e))bad.push('visible card control:'+(e.id||e.className))});
      if(/app[.]rithavo[.]com/i.test(document.body.innerText))text.push('visible text names app.rithavo.com');
      return {bad,text,standalone:document.documentElement.classList.contains('is-pwa')};
    })()`);
    if (f.text.length) textMentions.push(pth + " -> " + f.text.join("; "));
    if (f.bad.length) cardFindings.push(pth + " -> " + f.bad.join("; "));
    if (!f.standalone) cardFindings.push(pth + " -> installed-app mode was not detected");
  }
  await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: standaloneShim.identifier });
  check("installed-app mode: no visible Card control, no link or form to another host, on " + CARD_PAGES.length + " pages", cardFindings.length === 0, cardFindings.join(" | "));
  // Known, pre-existing wording on main (contact page names the sibling host as plain text; fixed on launch/privacy-and-wording, not merged here).
  check("no page names another host in visible text", textMentions.length === 0, textMentions.join(" | "));

  // ---------------------------------------------------------------- sign out clears caches; cross-user isolation
  await goto(BASE + "/account/");
  const before = await evalJs(`caches.keys().then(k=>k.filter(n=>n.startsWith('rithavo-')).length)`);
  const cleared = await evalJs(`RithavoPWA.clearPrivateState().then(()=>caches.keys()).then(k=>k.filter(n=>n.startsWith('rithavo-')))`);
  check("clearPrivateState (called by sign-out) deletes the page and asset caches and keeps only the public shell", before > 0 && cleared.length === 1 && cleared[0].startsWith("rithavo-shell-"), `before=${before} after=${cleared.join(",")}`);
  await evalJs(`RithavoApp.signOut()`);
  await sleep(2500);
  const afterOut = await evalJs(`(async()=>{const paths=[];for(const n of await caches.keys()){if(!n.startsWith('rithavo-'))continue;for(const r of await (await caches.open(n)).keys())paths.push(new URL(r.url).pathname)}return{paths,me:await fetch('/api/me',{credentials:'same-origin'}).then(r=>r.status)}})()`);
  check("sign-out ends the session (API answers 401)", afterOut.me === 401, String(afterOut.me));
  check("after sign-out no signed-in page (/home/, /account/) is cached", !afterOut.paths.some((p) => ["/home/", "/account/", "/profile/", "/resumes/"].includes(p)), afterOut.paths.join(","));
  await goto(BASE + "/home/");
  check("after sign-out, a signed-in page sends the visitor to sign-in", await waitFor(`location.pathname.startsWith('/sign-in')`, 8000), await evalJs(`location.pathname + location.search`));
  await setOffline(true);
  await goto(BASE + "/privacy/");
  const offlineAfterOut = await evalJs(`document.title`);
  await setOffline(false);
  check("after sign-out the offline page still works", /^Offline/.test(offlineAfterOut), offlineAfterOut);
  await goto(BASE + "/sign-in/");
  check("user B can sign in on the same browser", (await login(B)) === 200);
  const meB = await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.json())`);
  check("user B sees user B's identity, never user A's", meB.email === B.email && meB.email !== A.email, meB.email);
  await goto(BASE + "/home/");
  const dumpB = await evalJs(`(async()=>{let leaked=false;for(const n of await caches.keys()){const c=await caches.open(n);for(const r of await c.keys()){const resp=await c.match(r);const t=await resp.clone().text().catch(()=> '');if(t.includes(${JSON.stringify(A.email)}))leaked=true;}}return leaked;})()`);
  check("no cache holds anything belonging to user A", dumpB === false);
  const direct = await evalJs(`(async()=>{await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'});return fetch('/api/resumes',{credentials:'same-origin'}).then(r=>r.status)})()`);
  check("direct API access without a session is refused", direct === 401, String(direct));

  // ---------------------------------------------------------------- service-worker update (local only: edits sw.js on disk, then restores it)
  const SW_FILE = process.env.PWA_SW_FILE;
  if (SW_FILE) {
    const original = fs.readFileSync(SW_FILE, "utf8");
    try {
      await goto(BASE + "/sign-in/");
      await waitFor(`navigator.serviceWorker.getRegistration().then(r=>!!(r&&r.active))`, 10000);
      const v1 = await evalJs(`caches.keys().then(k=>k.filter(n=>n.startsWith('rithavo-')))`);
      fs.writeFileSync(SW_FILE, original.replace('const VERSION = "v2";', 'const VERSION = "v3";'));
      await evalJs(`navigator.serviceWorker.getRegistration().then(r=>r.update()).then(()=>true)`);
      const waiting = await waitFor(`navigator.serviceWorker.getRegistration().then(r=>!!r.waiting)`, 10000);
      check("update: the new worker installs but waits (no forced takeover)", waiting && (await evalJs(`caches.keys().then(k=>k.includes('rithavo-shell-v3')&&k.includes('rithavo-shell-v2'))`)));
      check("update: an in-page prompt offers a refresh", await waitFor(`!!document.querySelector('.pwa-toast-btn') && /new version/i.test(document.querySelector('.pwa-toast').innerText)`, 5000));
      await shot("update-prompt");
      await evalJs(`document.querySelector('.pwa-toast-btn').click()`);
      await sleep(2500);
      const after = await evalJs(`caches.keys().then(k=>k.filter(n=>n.startsWith('rithavo-')))`);
      check("update: after accepting, the new worker controls the page and old caches are deleted", after.includes("rithavo-shell-v3") && !after.some((n) => n.endsWith("-v2")), `before=${v1.join(",")} after=${after.join(",")}`);
    } finally { fs.writeFileSync(SW_FILE, original); }
  }

  ws.close(); chrome.kill();
  const failed = results.filter((r) => !r).length;
  console.log(`\n${failed === 0 ? "ALL PASS" : "FAILURES: " + failed} (${results.length - failed}/${results.length})  screenshots: ${OUT}`);
  process.exit(failed === 0 ? 0 : 1);
}
main().catch((e) => { console.error("ERROR", e); process.exit(2); });
