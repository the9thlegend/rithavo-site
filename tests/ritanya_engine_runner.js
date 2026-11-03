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

if (timing) {
  const out = cases.map((c) => { const t0 = process.hrtime.bigint(); run(c); return Number(process.hrtime.bigint() - t0) / 1e6; });
  process.stdout.write(JSON.stringify({ maxMs: Math.max(...out), count: out.length }));
} else {
  process.stdout.write(JSON.stringify(cases.map(run)));
}
