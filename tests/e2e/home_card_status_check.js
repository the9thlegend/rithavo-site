// Real-Chrome check of the Home page's on-domain Rithavo Card status (no cross-host handoff), signed in.
//   node tests/e2e/home_card_status_check.js <BASE_URL> <OUT_DIR>
// BASE_URL: a local server running this branch with TWO seeded THROWAWAY accounts, both with a Rithavo Profile:
//   PWA_USER_A / PWA_PASS_A has no Card row, PWA_USER_B / PWA_PASS_B has one (set PWA_HAS_CARD_B=1).
// Exit code 0 only if every check passes.
const { spawn } = require("node:child_process");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const BASE = (process.argv[2] || "").replace(/\/$/, ""), OUT = process.argv[3] || path.join(os.tmpdir(), "home-card-evidence");
const U = { A: { email: process.env.PWA_USER_A, pass: process.env.PWA_PASS_A }, B: { email: process.env.PWA_USER_B, pass: process.env.PWA_PASS_B } };
if (!BASE || !U.A.email || !U.B.email) { console.error("usage: home_card_status_check.js <BASE> <OUT> (+ PWA_USER_A/PASS_A/USER_B/PASS_B)"); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });
const chromePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].filter(Boolean).find((p) => fs.existsSync(p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = []; const check = (n, ok, d = "") => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + n + (d ? `  [${String(d).replace(U.A.email, "<A>").replace(U.B.email, "<B>").slice(0, 220)}]` : "")); };

async function session() {
  const port = 9300 + Math.floor(Math.random() * 500);
  const chrome = spawn(chromePath, [`--remote-debugging-port=${port}`, `--user-data-dir=${fs.mkdtempSync(path.join(os.tmpdir(), "hc-chrome-"))}`, "--headless=new", "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
  let bw; for (let i = 0; i < 60 && !bw; i++) { try { bw = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; } catch (e) {} if (!bw) await sleep(250); }
  const ws = new WebSocket(bw); await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const events = []; const requests = []; let sess;
  const sendTo = (s, method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, (d) => (d.error ? rej(new Error(method + ": " + d.error.message)) : res(d.result))); ws.send(JSON.stringify({ id: i, method, params, ...(s ? { sessionId: s } : {}) })); });
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) { events.push(d); if (d.method === "Network.requestWillBeSent") requests.push({ url: d.params.request.url, method: d.params.request.method }); } };
  const { targetId } = await sendTo(null, "Target.createTarget", { url: "about:blank" });
  ({ sessionId: sess } = await sendTo(null, "Target.attachToTarget", { targetId, flatten: true }));
  const send = (m, p) => sendTo(sess, m, p);
  const evalJs = async (e) => { const r = await send("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval"); return r.result.value; };
  const goto = async (u) => { events.length = 0; await send("Page.navigate", { url: u }); for (let i = 0; i < 80; i++) { if (events.some((e) => e.sessionId === sess && e.method === "Page.loadEventFired")) break; await sleep(150); } await sleep(900); };
  const waitFor = async (expr, ms = 10000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await evalJs(expr)) return true; } catch (e) {} await sleep(200); } return false; };
  const shot = async (n) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, n + ".png"), Buffer.from(r.data, "base64")); };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  return { send, evalJs, goto, waitFor, shot, requests, close: () => { ws.close(); chrome.kill(); } };
}

async function runFor(label, u, expectCard) {
  const s = await session();
  await s.goto(BASE + "/sign-in/");
  const st = await s.evalJs(`fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({email:${JSON.stringify(u.email)},password:${JSON.stringify(u.pass)}})}).then(r=>r.status)`);
  check(`${label}: signs in`, st === 200);
  s.requests.length = 0;
  await s.goto(BASE + "/home/");
  check(`${label}: the Rithavo Card status panel is shown`, await s.waitFor(`!!document.getElementById('card-status')`));
  const panel = await s.evalJs(`(()=>{const p=document.getElementById('card-status');return{h:p.querySelector('h3').textContent.trim(),t:p.innerText.replace(/\\s+/g,' '),links:[...p.querySelectorAll('a')].map(a=>a.getAttribute('href')),buttons:p.querySelectorAll('button').length,handoff:document.querySelectorAll('.card-handoff-btn,#card-handoff-form,form[method=POST][style*="display:none"]').length}})()`);
  check(`${label}: wording is accurate (${expectCard ? "has a Card from an earlier step" : "Profile ready"}) and says the Card isn't available on rithavo.com`, /isn't available on rithavo\.com/.test(panel.t) && (expectCard ? /earlier step/.test(panel.h) && /can't be viewed or shared from here/.test(panel.t) : /Profile is ready/.test(panel.h)), panel.h + " | " + panel.t.slice(0, 110));
  check(`${label}: no Card button, no hidden handoff form, only a link to the on-domain Profile`, panel.buttons === 0 && panel.handoff === 0 && panel.links.length === 1 && panel.links[0] === "/profile/", JSON.stringify(panel.links));
  await sleep(1500);
  const other = s.requests.filter((r) => { try { const h = new URL(r.url).host; return h !== new URL(BASE).host && !/^fonts\.(googleapis|gstatic)\.com$/.test(h); } catch (e) { return false; } });
  check(`${label}: the page loaded and made no request to any other host`, other.length === 0, other.map((r) => r.url).join(" "));
  check(`${label}: the Card handoff endpoint is never called`, !s.requests.some((r) => /\/card\/continue/.test(r.url)));
  const direct = await s.evalJs(`fetch('/api/card/continue',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:'{}'}).then(async r=>({s:r.status,t:await r.text()}))`);
  check(`${label}: calling the old handoff endpoint directly gets 404 and no token or other host`, direct.s === 404 && !/token|handoff_url|app\.rithavo/.test(direct.t), `${direct.s} ${direct.t.slice(0, 80)}`);
  check(`${label}: Explore still mounts on Home (Profile and Explore functionality preserved)`, await s.evalJs(`!!document.getElementById('explore-root')`));
  await s.shot(label + "-home");
  await s.evalJs(`document.querySelector('#card-status a').click()`); await sleep(1500);
  check(`${label}: 'Open your Profile' stays on rithavo.com and opens the Profile page`, (await s.evalJs(`location.origin`)) === BASE && (await s.evalJs(`location.pathname`)) === "/profile/");
  const me = await s.evalJs(`fetch('/api/profile',{credentials:'same-origin'}).then(r=>r.status)`);
  check(`${label}: the Profile API still answers`, me === 200, String(me));
  // installed-app mode: the same honest message, still no handoff
  await s.send("Page.addScriptToEvaluateOnNewDocument", { source: "Object.defineProperty(navigator,'standalone',{get:()=>true})" });
  await s.goto(BASE + "/home/");
  await s.waitFor(`!!document.getElementById('card-status')`);
  const app = await s.evalJs(`({pwa:document.documentElement.classList.contains('is-pwa'),shown:getComputedStyle(document.getElementById('card-status')).display!=='none',handoff:document.querySelectorAll('.card-handoff-btn').length})`);
  check(`${label}: in the installed app the same on-domain message shows and there is still no handoff`, app.pwa && app.shown && app.handoff === 0, JSON.stringify(app));
  s.close();
}

(async () => {
  await runFor("A (Profile, no Card)", U.A, false);
  await runFor("B (Profile + Card row)", U.B, true);
  const failed = results.filter((r) => !r).length;
  console.log(`\n${failed === 0 ? "ALL PASS" : "FAILURES: " + failed} (${results.length - failed}/${results.length})  screenshots: ${OUT}`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => { console.error("ERROR", e); process.exit(2); });
