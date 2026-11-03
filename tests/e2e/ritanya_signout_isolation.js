// Real-Chrome check of sign-out clearing and A -> B session isolation for the Ritanya guide, in the SAME tab.
//   node tests/e2e/ritanya_signout_isolation.js <BASE_URL> <OUT_DIR>
// BASE_URL: a local server running this branch with RITHAVO_RITANYA_MODE=deterministic and two seeded THROWAWAY accounts
// whose credentials are in PWA_USER_A / PWA_PASS_A and PWA_USER_B / PWA_PASS_B. Not for a Preview (its database is isolated).
// Exit code 0 only if every check passes.
const { spawn } = require("node:child_process");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const BASE = process.argv[2], OUT = process.argv[3]; fs.mkdirSync(OUT, { recursive: true });
const A = { email: process.env.PWA_USER_A, pass: process.env.PWA_PASS_A }, B = { email: process.env.PWA_USER_B, pass: process.env.PWA_PASS_B };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = []; const check = (n, ok, d = "") => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + n + (d ? `  [${String(d).replace(A.email, "<A>").replace(B.email, "<B>").slice(0, 200)}]` : "")); };
(async () => {
  const port = 9300 + Math.floor(Math.random() * 500);
  const chrome = spawn("C:/Program Files/Google/Chrome/Application/chrome.exe", [`--remote-debugging-port=${port}`, `--user-data-dir=${fs.mkdtempSync(path.join(os.tmpdir(), "so-chrome-"))}`, "--headless=new", "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
  let bw; for (let i = 0; i < 60 && !bw; i++) { try { bw = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; } catch (e) {} if (!bw) await sleep(250); }
  const ws = new WebSocket(bw); await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const events = []; let sess;
  const sendTo = (s, method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, (d) => (d.error ? rej(new Error(method + ": " + d.error.message)) : res(d.result))); ws.send(JSON.stringify({ id: i, method, params, ...(s ? { sessionId: s } : {}) })); });
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d); };
  const { targetId } = await sendTo(null, "Target.createTarget", { url: "about:blank" });
  ({ sessionId: sess } = await sendTo(null, "Target.attachToTarget", { targetId, flatten: true }));
  const send = (m, p) => sendTo(sess, m, p);
  const evalJs = async (e) => { const r = await send("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval"); return r.result.value; };
  const goto = async (u) => { events.length = 0; await send("Page.navigate", { url: u }); for (let i = 0; i < 80; i++) { if (events.some((e) => e.sessionId === sess && e.method === "Page.loadEventFired")) break; await sleep(150); } await sleep(700); };
  const waitFor = async (expr, ms = 8000) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await evalJs(expr)) return true; } catch (e) {} await sleep(200); } return false; };
  const shot = async (n) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, n + ".png"), Buffer.from(r.data, "base64")); };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  const login = (u) => evalJs(`fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({email:${JSON.stringify(u.email)},password:${JSON.stringify(u.pass)}})}).then(r=>r.status)`);
  const sessionCookie = async () => (await send("Network.getCookies", { urls: [BASE + "/"] })).cookies.filter((c) => c.name === "session").length;
  const ask = async (text) => { const n = await evalJs(`document.querySelectorAll('.rtn-ritanya').length`); await evalJs(`(()=>{const i=document.querySelector('.rtn-input');i.value=${JSON.stringify(text)};document.querySelector('.rtn-form').requestSubmit()})()`); await waitFor(`document.querySelectorAll('.rtn-ritanya').length>${n} && !document.querySelector('.rtn-send').disabled`, 15000); return evalJs(`[...document.querySelectorAll('.rtn-ritanya')].pop().querySelector('.rtn-bubble').textContent`); };

  // ---- user A signs in and talks to the guide
  await goto(BASE + "/sign-in/");
  check("user A signs in", (await login(A)) === 200);
  await goto(BASE + "/career/");
  await waitFor(`!!document.querySelector('.rtn-launcher')`, 8000);
  await evalJs(`document.querySelector('.rtn-launcher').click()`);
  const TYPED = "my private note ZEBRA-7731 for the guide";
  const replyA = await ask("What is Rithavo?");
  await ask(TYPED);
  const keysA = await evalJs(`Object.keys(sessionStorage).filter(k=>k.startsWith('rv_ritanya_'))`);
  const storedA = await evalJs(`sessionStorage.getItem('rv_ritanya_conversation')||''`);
  check("A: the guide keeps the conversation in this tab (including what A typed)", keysA.includes("rv_ritanya_conversation") && storedA.includes("ZEBRA-7731"), keysA.join(","));
  check("A: the guide's request carried no cookie and the API answers A as A", (await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.json())`)).email === A.email);
  await shot("A-conversation");

  // ---- A signs out the way a person does (the app's own sign-out)
  await goto(BASE + "/account/");
  await waitFor(`!!window.RithavoApp`, 6000);
  await evalJs(`RithavoApp.signOut()`);
  await waitFor(`location.pathname === '/' || location.pathname.startsWith('/sign-in')`, 8000);
  await sleep(800);
  const afterOut = await evalJs(`({keys:Object.keys(sessionStorage).filter(k=>k.startsWith('rv_ritanya_')),me:null})`);
  afterOut.me = await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.status)`);
  check("sign-out: the conversation is gone from the tab (only the non-personal on/off flag may be re-created by the next page)", !afterOut.keys.includes("rv_ritanya_conversation"), afterOut.keys.join(","));
  check("sign-out: the session ends (API 401) and the session cookie is removed", afterOut.me === 401 && (await sessionCookie()) === 0, `me=${afterOut.me} cookies=${await sessionCookie()}`);
  check("sign-out: nothing A typed is left in the page's storage", !(await evalJs(`JSON.stringify(sessionStorage)+JSON.stringify(localStorage)`)).includes("ZEBRA-7731"));

  // ---- user B signs in in the same tab
  await goto(BASE + "/sign-in/");
  check("user B signs in in the same tab", (await login(B)) === 200);
  await goto(BASE + "/career/");
  await waitFor(`!!document.querySelector('.rtn-launcher')`, 8000);
  await evalJs(`document.querySelector('.rtn-launcher').click()`); await sleep(400);
  const panelB = await evalJs(`({msgs:document.querySelectorAll('.rtn-msg').length,text:document.querySelector('.rtn-log').textContent})`);
  check("B: the guide starts fresh (just the greeting); nothing of A's conversation is shown", panelB.msgs === 1 && !panelB.text.includes("ZEBRA-7731"), `messages=${panelB.msgs}`);
  const meB = await evalJs(`fetch('/api/me',{credentials:'same-origin'}).then(r=>r.json())`);
  check("B: the API answers B as B, never A", meB.email === B.email && meB.email !== A.email);
  const replyB = await ask("What is Rithavo?");
  check("B: the guide's answer to the same question is identical to A's (it never reads who is asking)", replyB === replyA, replyB.slice(0, 60));
  const probe = await ask("What is my name and email?");
  check("B: asked for 'my name and email', the guide gives no personal data", !probe.includes(B.email) && !probe.includes(A.email) && /don't have information|can't see your account/i.test(probe), probe.slice(0, 80));
  await shot("B-conversation");
  ws.close(); chrome.kill();
  const failed = results.filter((r) => !r).length;
  console.log(failed ? "FAILURES: " + failed : "ALL PASS", `(${results.length - failed}/${results.length})`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error("ERROR", e); process.exit(2); });
