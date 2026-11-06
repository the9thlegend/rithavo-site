// Real-Chrome check of the Ritanya browser engine's KILL SWITCH and FAILURE behaviour on a deployed URL (a protected Preview, or any
// host), or a local server.
//
//   node tests/e2e/ritanya_killswitch_check.js <URL> <OUT_DIR>
//
// The server must be running with RITHAVO_RITANYA_MODE=browser. URL may carry ?_vercel_share=... for a protected Preview.
//
// The page is the REAL deployed page. Only the answer to its one status request is controlled here (by intercepting that request
// in the browser: refused, delayed, "off", "paused", 404, a server error, a non-JSON page, or passed through to the real server),
// and the page's clock is moved forward with a Date.now offset kept in window.name, so five minutes of "no successful check" can be
// tested in seconds. Policy under test (assets/ritanya/ritanya.js): the page re-asks every 2 minutes; if the server cannot be asked,
// the last known "on" works for at most 5 minutes (GRACE_MS) from the last SUCCESSFUL answer; after that the guide is removed and the
// conversation cleared; "off" / "paused" remove it at once; a later successful "on" brings it back.
// Also proves: no request is ever made to the chat endpoint, and a browser engine that cannot run is never mounted.
// Exit code 0 only if every check passes.

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const INPUT = process.argv[2];
const OUT = process.argv[3] || path.join(os.tmpdir(), "ritanya-killswitch-evidence");
if (!INPUT) { console.error("usage: ritanya_killswitch_check.js <URL> <OUT_DIR>"); process.exit(2); }
const BASE = new URL(INPUT).origin;
const HAS_SHARE = new URL(INPUT).searchParams.has("_vercel_share");
fs.mkdirSync(OUT, { recursive: true });
const chromePath = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].filter(Boolean).find((p) => fs.existsSync(p));
if (!chromePath) { console.error("No Chromium found"); process.exit(2); }
const results = [];
function check(name, ok, detail = "") { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (detail ? `  [${String(detail).slice(0, 260)}]` : "")); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const MIN = 60000;

const SERVER_OFF_HEADERS = [{ name: "Content-Type", value: "application/json" }, { name: "Cache-Control", value: "public, max-age=60, s-maxage=300" }, { name: "X-Content-Type-Options", value: "nosniff" }];

async function main() {
  const port = 9300 + Math.floor(Math.random() * 500);
  const chrome = spawn(chromePath, [`--remote-debugging-port=${port}`, `--user-data-dir=${fs.mkdtempSync(path.join(os.tmpdir(), "ks-chrome-"))}`, "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"], { stdio: "ignore" });
  let bw; for (let i = 0; i < 60 && !bw; i++) { try { bw = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; } catch (e) { /* not up */ } if (!bw) await sleep(250); }
  if (!bw) throw new Error("Chrome did not start");
  const ws = new WebSocket(bw); await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const events = []; let cookieHeader = "", sess;
  const S = { mode: { kind: "pass" }, engineMock: null, statusCalls: 0, chatCalls: 0, engineCalls: 0 };
  const sendTo = (s, method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, (d) => (d.error ? rej(new Error(method + ": " + d.error.message)) : res(d.result))); ws.send(JSON.stringify({ id: i, method, params, ...(s ? { sessionId: s } : {}) })); });
  const cont = (d) => { const { requestId, request } = d.params; const headers = Object.entries(request.headers).map(([name, value]) => ({ name, value })); if (cookieHeader && !headers.some((h) => h.name.toLowerCase() === "cookie")) headers.push({ name: "Cookie", value: cookieHeader }); return sendTo(d.sessionId, "Fetch.continueRequest", { requestId, headers }).catch(() => {}); };
  const fulfill = (d, status, headers, body) => sendTo(d.sessionId, "Fetch.fulfillRequest", { requestId: d.params.requestId, responseCode: status, responseHeaders: headers, body: Buffer.from(body || "").toString("base64") }).catch(() => {});
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
    if (d.method === "Fetch.requestPaused" && d.sessionId) {
      const url = d.params.request.url;
      if (/\/api\/ritanya\/chat/.test(url)) { S.chatCalls++; cont(d); return; }
      if (/\/assets\/ritanya\/engine\.js/.test(url)) {
        S.engineCalls++;
        const em = S.engineMock;
        if (!em) { cont(d); return; }
        if (em.delay) setTimeout(() => fulfill(d, em.status, [{ name: "Content-Type", value: "application/javascript" }, { name: "Cache-Control", value: "no-store" }], em.body), em.delay);
        else fulfill(d, em.status, [{ name: "Content-Type", value: "application/javascript" }, { name: "Cache-Control", value: "no-store" }], em.body);
        return;
      }
      if (/\/api\/ritanya\/status/.test(url)) {
        S.statusCalls++;
        const k = S.mode.kind;
        if (k === "pass") cont(d);
        else if (k === "fail") sendTo(d.sessionId, "Fetch.failRequest", { requestId: d.params.requestId, errorReason: "ConnectionRefused" }).catch(() => {});
        else if (k === "delay") setTimeout(() => cont(d), S.mode.ms);
        else if (k === "off" || k === "paused") fulfill(d, 200, SERVER_OFF_HEADERS, JSON.stringify({ enabled: false }));   // exactly what the server answers when off or paused
        else if (k === "notfound") fulfill(d, 404, [{ name: "Content-Type", value: "application/json" }], JSON.stringify({ detail: "Not Found" }));
        else if (k === "http500") fulfill(d, 500, [{ name: "Content-Type", value: "text/plain" }], "boom");
        else if (k === "portal") fulfill(d, 200, [{ name: "Content-Type", value: "text/html" }], "<html><body>Sign in to the network</body></html>");
        else cont(d);
        return;
      }
      cont(d); return;
    }
    if (d.method) events.push(d);
  };
  const { targetId } = await sendTo(null, "Target.createTarget", { url: "about:blank" });
  ({ sessionId: sess } = await sendTo(null, "Target.attachToTarget", { targetId, flatten: true }));
  const send = (m, p) => sendTo(sess, m, p);
  const evalJs = async (e) => { const r = await send("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval"); return r.result.value; };
  const pageEvents = () => events.filter((e) => e.sessionId === sess);
  const goto = async (u) => { events.length = 0; await send("Page.navigate", { url: u }); for (let i = 0; i < 80; i++) { if (pageEvents().some((e) => e.method === "Page.loadEventFired")) break; await sleep(150); } await sleep(500); };
  const waitFor = async (expr, ms = 8000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await evalJs(expr)) return true; } catch (e) { /* retry */ } await sleep(150); } return false; };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  await goto(INPUT);
  if (HAS_SHARE) { const { cookies } = await send("Network.getCookies", { urls: [BASE + "/"] }); cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; "); }
  await send("Fetch.enable", { patterns: [{ urlPattern: BASE + "/*" }] });
  // The page's clock = real clock + an offset kept in window.name (it survives navigations in this tab; nothing on the site reads window.name).
  // Timers of a minute or longer are shortened 100x only while window.name contains "warp" (used once, for the idle-expiry check).
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `(()=>{const real=Date.now.bind(Date);const skew=()=>{const m=/skew=(\\d+)/.exec(window.name||'');return m?parseInt(m[1],10):0};Date.now=()=>real()+skew();const st=window.setTimeout.bind(window);window.setTimeout=(fn,ms,...a)=>st(fn,(ms>=60000&&/warp/.test(window.name||''))?ms/100:ms,...a)})()` });

  const setSkew = (ms, warp) => evalJs(`window.name='skew=${ms}'+(${!!warp}?' warp':'')`);
  const skewNow = () => evalJs(`parseInt((/skew=(\\d+)/.exec(window.name||'')||[0,0])[1],10)`);
  const advance = async (ms) => setSkew((await skewNow()) + ms);
  const ui = () => evalJs(`({launcher:!!document.querySelector('.rtn-launcher'),root:!!document.querySelector('.rtn-root'),spacer:!!document.querySelector('.rtn-spacer'),on:document.body.classList.contains('rtn-on'),lock:document.documentElement.classList.contains('rtn-lock'),conv:sessionStorage.getItem('rv_ritanya_conversation'),status:sessionStorage.getItem('rv_ritanya_status')})`);
  const isGone = (u) => !u.launcher && !u.root && !u.spacer && !u.on && !u.lock && u.conv === null;
  const goneNow = async () => isGone(await ui());
  const waitGone = (ms = 9000) => waitFor(`!document.querySelector('.rtn-launcher') && !document.querySelector('.rtn-root') && !document.querySelector('.rtn-spacer') && !document.body.classList.contains('rtn-on') && sessionStorage.getItem('rv_ritanya_conversation')===null`, ms);
  const statusT = async () => { const s = (await ui()).status; try { return JSON.parse(s).t; } catch (e) { return null; } };
  const openPanel = async () => { await evalJs(`document.querySelector('.rtn-launcher').click()`); return waitFor(`!document.querySelector('.rtn-root') || !document.querySelector('.rtn-root').hidden`, 4000); };
  const submit = (text) => evalJs(`(()=>{const i=document.querySelector('.rtn-input');i.value=${JSON.stringify(text)};document.querySelector('.rtn-form').requestSubmit()})()`);
  const replies = () => evalJs(`document.querySelectorAll('.rtn-ritanya').length`);
  const lastText = () => evalJs(`(()=>{const m=[...document.querySelectorAll('.rtn-ritanya')].pop();return m?m.querySelector('.rtn-bubble').textContent:null})()`);
  // ask and wait for the answer; returns {text, ms} or null if the guide went away / no answer came
  const ask = async (text, ms = 12000) => { const n = await replies(); const t0 = Date.now(); await submit(text); const ok = await waitFor(`document.querySelectorAll('.rtn-ritanya').length>${n} && !document.querySelector('.rtn-send').disabled`, ms); return ok ? { text: await lastText(), ms: Date.now() - t0 } : null; };
  const toggleVisibility = () => evalJs(`(()=>{const d=document;Object.defineProperty(d,'hidden',{configurable:true,get:()=>true});d.dispatchEvent(new Event('visibilitychange'));delete d.hidden;d.dispatchEvent(new Event('visibilitychange'))})()`);
  const answered = (r) => !!r && /Rithavo|Career Intelligence|four kinds|Stay, New Role/.test(r.text || "");
  async function fresh(p = "/career/") {
    S.mode = { kind: "pass" }; S.engineMock = null;
    await evalJs(`sessionStorage.clear(); window.name=''`);
    await goto(BASE + p);
    const ok = await waitFor(`!!document.querySelector('.rtn-launcher') && !!window.RitanyaEngine`, 10000);
    if (!ok) throw new Error("the guide did not start on " + p + " (is RITHAVO_RITANYA_MODE=browser set for this deployment?)");
  }

  // ===================================================================== A. baseline
  await fresh();
  await openPanel();
  const a = await ask("What is Rithavo?");
  check("A. baseline: the guide starts, opens, and answers; the status was fresh so no extra status request was needed", answered(a) && S.chatCalls === 0, a && a.text.slice(0, 50));

  // ===================================================================== B. unreachable INSIDE the grace period, then PAST it (one page, one conversation)
  await fresh(); await openPanel(); await ask("What is Rithavo?");
  const t0 = await statusT();
  await advance(3 * MIN);                        // the last successful answer is now 3 minutes old: stale (> 2 min) but inside the 5-minute grace period
  S.mode = { kind: "fail" };
  const calls0 = S.statusCalls;
  const b1 = await ask("What is Career Intelligence?");
  check("B1. status unreachable, last 'on' is 3 min old (inside grace): the page re-asked, could not, and the guide still answers", answered(b1) && S.statusCalls > calls0 && (await ui()).launcher, `${S.statusCalls - calls0} status attempts; ${b1 && b1.text.slice(0, 40)}`);
  check("B2. a failed attempt does NOT refresh the clock (the stored time of the last successful answer is unchanged)", (await statusT()) === t0, `${t0} -> ${await statusT()}`);
  await advance(Math.round(1.8 * MIN));          // age = 4.8 min: still inside
  const b3 = await ask("What is Product Studio?");
  check("B3. at 4.8 minutes without a successful check the guide still works (boundary just inside the grace period)", answered(b3));
  const convBefore = (await ui()).conv;
  await advance(Math.round(0.5 * MIN));          // age = 5.3 min: past the grace period
  const n0 = await replies();
  await submit("How much does it cost?");
  check("B4. past the grace period with no successful check, submitting a message removes the whole guide", await waitGone(), JSON.stringify(await ui()));
  const bu = await ui();
  check("B5. the conversation is cleared (sessionStorage), the stored status is removed, and no reply was produced for that message", !!convBefore && bu.conv === null && bu.status === null && (await replies()) === 0 && n0 > 0, JSON.stringify({ had: !!convBefore, conv: bu.conv, status: bu.status }));

  // ===================================================================== C/D/E. past the grace period, discovered by panel opening, by tab visibility, by message
  await fresh(); await advance(6 * MIN); S.mode = { kind: "fail" };
  await evalJs(`document.querySelector('.rtn-launcher').click()`);
  check("C. past grace, unreachable: opening the panel removes the guide", await waitGone());
  await fresh(); await ask("What is Rithavo?"); await advance(6 * MIN); S.mode = { kind: "fail" };
  await toggleVisibility();
  check("D. past grace, unreachable: the tab becoming visible again removes the guide and clears the conversation", await waitGone());

  // ===================================================================== F. an idle page is removed when the grace period ends, without being touched
  await fresh();
  await setSkew(150 * 1000, true);               // age 2.5 min, and long timers are now shortened 100x
  S.mode = { kind: "fail" };
  const c1 = S.statusCalls;
  await toggleVisibility();                      // stale -> asks -> cannot -> inside grace -> arms the end-of-grace timer (~151 s, now 1.5 s)
  await waitFor(`true`, 50); for (let i = 0; i < 30 && S.statusCalls === c1; i++) await sleep(50);
  const armedStill = await goneNow();
  await setSkew(150 * 1000 + 200 * 1000, true);  // time passes while nobody touches the page: age 5.8 min
  const idleGone = await waitGone(8000);
  check("F. an idle page whose server stays unreachable is removed by itself when the grace period ends (nobody touched it)", !armedStill && idleGone, `armedStillMounted=${!armedStill} idleGone=${idleGone}`);

  // ===================================================================== G. a successful OFF / PAUSED answer disables it at once, even inside the grace period
  for (const [label, kind, trigger] of [["off", "off", "panel"], ["paused", "paused", "message"], ["route gone (404)", "notfound", "visibility"]]) {
    await fresh(); await openPanel(); await ask("What is Rithavo?");
    await advance(3 * MIN);                      // stale, and well inside the grace period: grace must not delay a real "off"
    S.mode = { kind };
    const before = await statusT();
    if (trigger === "panel") { await evalJs(`document.querySelector('.rtn-iconbtn[aria-label="Close Ritanya"]').click()`); await evalJs(`document.querySelector('.rtn-launcher').click()`); }
    else if (trigger === "message") await submit("How much does it cost?");
    else await toggleVisibility();
    const g = await waitGone(5000);
    check(`G. server says ${label}: the guide is removed at once (3 min into the grace period) and the conversation cleared`, g && (await replies()) === 0, JSON.stringify(await ui()));
  }
  await fresh(); await openPanel(); await ask("What is Rithavo?"); await advance(3 * MIN);
  S.mode = { kind: "fail" }; const g4 = await ask("What is Career Intelligence?");
  S.mode = { kind: "paused" }; await toggleVisibility();
  check("G4. unreachable first (guide kept), then the server answers paused: removed at once, not at the end of the grace period", answered(g4) && (await waitGone(5000)));

  // ===================================================================== H. recovery after a successful 'on'
  await fresh(); await openPanel(); await ask("What is Rithavo?");
  await advance(3 * MIN); S.mode = { kind: "fail" }; const h1 = await ask("What is Career Intelligence?");
  S.mode = { kind: "pass" }; await advance(20 * 1000); await toggleVisibility();
  await sleep(1200);
  const tAfter = await statusT();
  const age = (await evalJs(`Date.now()`)) - tAfter;
  check("H1. inside grace, then the server answers 'on' again: the guide carries on and the clock restarts (stored answer is fresh)", answered(h1) && age < 15000 && (await ui()).launcher, `age=${age}ms`);
  await advance(4.5 * MIN); S.mode = { kind: "fail" };
  const h2 = await ask("What are Systems and Growth?");
  check("H2. after that recovery, a new outage gets a NEW grace period (4.5 min later it still works; it would have expired had the clock not restarted)", answered(h2));
  S.mode = { kind: "pass" };

  // ===================================================================== I. recovery AFTER the guide was removed for an outage
  await fresh(); await ask("What is Rithavo?"); await advance(6 * MIN); S.mode = { kind: "fail" }; await toggleVisibility();
  const iGone = await waitGone();
  S.mode = { kind: "pass" };
  await evalJs(`window.dispatchEvent(new Event('online'))`);
  const iBack = await waitFor(`!!document.querySelector('.rtn-launcher') && !!window.RitanyaEngine`, 10000);
  const iConv = await evalJs(`document.querySelectorAll('.rtn-msg').length`);
  await openPanel(); const i2 = await ask("What is Career Intelligence?");
  check("I1. removed after an outage, then the server answers 'on': the guide returns on the same page with a fresh, empty conversation, and works", iGone && iBack && answered(i2), `back=${iBack} msgsOnReturn=${iConv}`);
  check("I2. the returned guide has the greeting only (nothing from before the outage)", iConv === 1 || iConv === 0, String(iConv));
  await fresh(); await advance(6 * MIN); S.mode = { kind: "fail" }; await toggleVisibility(); await waitGone();
  S.mode = { kind: "off" }; await evalJs(`window.dispatchEvent(new Event('online'))`); await sleep(1200);
  S.mode = { kind: "pass" }; await evalJs(`window.dispatchEvent(new Event('online'))`); await sleep(1500);
  check("I3. removed after an outage and the server then says 'off': the guide does not come back, even if the server later says 'on' (needs a new page)", await goneNow());

  // ===================================================================== J. a delayed status answer
  await fresh(); await openPanel(); await ask("What is Rithavo?"); await advance(3 * MIN);
  S.mode = { kind: "delay", ms: 2500 }; const j1 = await ask("What is Career Intelligence?");
  const ageJ = (await evalJs(`Date.now()`)) - (await statusT());
  check("J1. status delayed 2.5 s (answers 'on'): the message waits for it, is answered, and the clock restarts", answered(j1) && j1.ms >= 2000 && ageJ < 15000, `reply after ${j1 && j1.ms} ms, age ${ageJ}`);
  await advance(3 * MIN); S.mode = { kind: "delay", ms: 9000 }; const j2 = await ask("What are Systems and Growth?", 15000);
  check("J2. status delayed 9 s (the page gives up after 5 s) while inside grace: the guide keeps working on the last known 'on'", answered(j2) && j2.ms >= 4500 && j2.ms < 8500, `reply after ${j2 && j2.ms} ms`);
  await advance(3 * MIN); const j3start = Date.now(); await submit("How much does it cost?");
  const j3 = await waitGone(12000); const j3ms = Date.now() - j3start;
  check("J3. status delayed 9 s past the grace period: the guide is removed when the 5 s wait ends, not after 9 s", j3 && j3ms < 8500, `${j3ms} ms`);
  S.mode = { kind: "pass" };

  // ===================================================================== K. unusable answers count as 'could not be asked'
  for (const kind of ["http500", "portal"]) {
    await fresh(); await openPanel(); await ask("What is Rithavo?"); await advance(3 * MIN); S.mode = { kind };
    const k1 = await ask("What is Career Intelligence?");
    await advance(3 * MIN); await submit("hello"); const k3 = await waitGone();
    check(`K. a ${kind === "http500" ? "server error (500)" : "non-JSON 200 page (a sign-in portal)"} is treated as unreachable: works inside grace, never counts as 'on', removed past it`, answered(k1) && k3, JSON.stringify({ inside: answered(k1), removedPast: k3 }));
  }

  // ===================================================================== L. a NEW page load during an outage
  await fresh(); await ask("What is Rithavo?"); await advance(3 * MIN); S.mode = { kind: "fail" };
  await goto(BASE + "/growth/"); const l1 = await waitFor(`!!document.querySelector('.rtn-launcher') && !!window.RitanyaEngine`, 8000);
  check("L1. a new page opened during a brief outage (last 'on' 3 min old) starts the guide from the last known state", l1);
  await advance(3 * MIN); await goto(BASE + "/systems/"); await sleep(1500);
  check("L2. a new page opened after the grace period during an outage shows no guide at all", !(await ui()).launcher && !(await ui()).root);
  S.mode = { kind: "pass" }; await goto(BASE + "/about/");
  check("L3. and when the server answers 'on' again, the next page shows the guide", await waitFor(`!!document.querySelector('.rtn-launcher') && !!window.RitanyaEngine`, 10000));

  // ===================================================================== M. an engine that cannot run is never mounted (stands in for a browser that cannot parse it)
  await send("Network.setBypassServiceWorker", { bypass: true });
  const MOCKS = [
    ["a script the browser cannot parse (what regex look-behind does to an old browser)", { status: 200, body: "(function(){ var broken = ; })();" }],
    ["an engine file that is a server error", { status: 500, body: "nope" }],
    ["an engine without the expected functions", { status: 200, body: "window.RitanyaEngine = {};" }],
    ["an engine whose answer function throws", { status: 200, body: "window.RitanyaEngine = { respond: function(){ throw new Error('x'); } };" }],
    ["an engine that never finishes loading (10 s limit)", { status: 200, body: "", delay: 13000 }],
  ];
  for (const [label, m] of MOCKS) {
    S.mode = { kind: "pass" }; await evalJs(`sessionStorage.clear(); window.name=''`);
    S.engineMock = m;
    await goto(BASE + "/contact/");
    await sleep(m.delay ? 11500 : 2500);
    const mu = await ui();
    const navs = await evalJs(`document.querySelectorAll('nav a[href]').length`);
    check(`M. ${label}: no guide is mounted (no launcher, panel or spacer) and the page's own navigation is intact`, !mu.launcher && !mu.root && !mu.spacer && !mu.on && navs > 0, JSON.stringify(mu));
  }
  S.engineMock = null; await send("Network.setBypassServiceWorker", { bypass: false });
  S.mode = { kind: "pass" }; await evalJs(`sessionStorage.clear(); window.name=''`);
  await goto(BASE + "/contact/");
  check("M. and with the real engine file the same page does mount the guide (the failures above were the mocks, not the page)", await waitFor(`!!document.querySelector('.rtn-launcher') && !!window.RitanyaEngine`, 10000));

  // ===================================================================== N. no message ever reached the server
  console.log(`INFO status requests intercepted: ${S.statusCalls}; engine file requests: ${S.engineCalls}; chat requests: ${S.chatCalls}`);
  check("N. across every scenario above (dozens of messages, every outage and mode) NOT ONE request was made to the chat endpoint", S.chatCalls === 0, String(S.chatCalls));

  ws.close(); chrome.kill();
  const failed = results.filter((x) => !x).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
}
main().catch((e) => { console.error("ERROR", e); process.exit(2); });
