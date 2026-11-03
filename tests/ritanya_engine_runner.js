// Runs the browser engine over a JSON file of cases and prints the results as JSON. Used by tests/test_ritanya_engine_parity.py.
//   node tests/ritanya_engine_runner.js <cases.json> [--time]
// The engine is loaded in a bare VM context: only the language built-ins exist (no fetch, no document, no storage), so the
// engine can neither reach a network nor read anything while it runs.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const casesFile = process.argv[2];
const timing = process.argv.includes("--time");
const source = fs.readFileSync(path.join(__dirname, "..", "assets", "ritanya", "engine.js"), "utf8");
const sandbox = {};
sandbox.self = sandbox;
vm.createContext(sandbox, { codeGeneration: { strings: false, wasm: false } });
vm.runInContext(source, sandbox);
const E = sandbox.RitanyaEngine;
const cases = JSON.parse(fs.readFileSync(casesFile, "utf8"));

function run(c) {
  return E.respond(c.m, "t" in c ? c.t : undefined, "s" in c ? c.s : undefined);
}

// The engine itself contains no regex look-behind (older browsers cannot parse a script that does). This is the form it replaced,
// kept here only as a reference: --norm prints both for a list of strings so the test can prove they are identical.
function lookbehindNorm(message) {
  let text = message.toLowerCase().replace(/(?<![a-z0-9.\-])rithavo\.com/g, "rithavo");
  text = text.replace(/[^a-z0-9@₹\s.]/gu, " ");
  text = text.replace(/(?<![a-z0-9])\.|\.(?![a-z0-9])/g, " ");
  return text.replace(/\s+/g, " ").trim();
}

if (process.argv.includes("--norm")) {
  process.stdout.write(JSON.stringify({ engine: cases.map((s) => E.norm(s)), reference: cases.map(lookbehindNorm) }));
} else if (timing) {
  const out = cases.map((c) => { const t0 = process.hrtime.bigint(); run(c); return Number(process.hrtime.bigint() - t0) / 1e6; });
  process.stdout.write(JSON.stringify({ maxMs: Math.max(...out), count: out.length }));
} else {
  process.stdout.write(JSON.stringify(cases.map(run)));
}
