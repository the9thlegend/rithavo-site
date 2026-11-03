"""
PARITY and SECURITY gates for the browser-side Ritanya engine (assets/ritanya/engine.js).

The Python matcher (app/ritanya_chat.py + app/ritanya_knowledge.py) is the source of truth. The browser engine is GENERATED
from it (tools/build_ritanya_engine.py). These tests prove, over a large corpus, that for every input the browser engine
returns exactly what the Python implementation returns (reply text, topic, links, suggestions, refusal / clarify / unknown
flags, and the same validation outcome), and that the engine file contains nothing that could reach a network, storage or
an account. Nothing may be deleted from the Python implementation until these pass.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import ritanya_chat as rc
from app import ritanya_knowledge as K

from . import ritanya_corpus as corpus

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "assets" / "ritanya" / "engine.js"
RUNNER = ROOT / "tests" / "ritanya_engine_runner.js"
FIXTURE = ROOT / "tests" / "fixtures" / "ritanya_parity_sample.json"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_ritanya_engine", ROOT / "tools" / "build_ritanya_engine.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def python_expected(case):
    """What the server does for one message: validate_payload, then answer."""
    payload = {"message": case["m"]}
    if "t" in case:
        payload["last_topic"] = case["t"]
    if "s" in case:
        payload["unknown_streak"] = case["s"]
    data, problem = rc.validate_payload(json.dumps(payload).encode("utf-8"))
    if problem:
        return {"ok": False, "error": problem}
    result = rc.answer(data["message"], data["last_topic"], data["unknown_streak"])
    result.pop("_intent")
    return {"ok": True, "result": result}


def python_intent(case):
    payload = {"message": case["m"]}
    if "t" in case:
        payload["last_topic"] = case["t"]
    if "s" in case:
        payload["unknown_streak"] = case["s"]
    data, problem = rc.validate_payload(json.dumps(payload).encode("utf-8"))
    return None if problem else rc.answer(data["message"], data["last_topic"], data["unknown_streak"])["_intent"]


def run_node(cases, tmp_path, *flags):
    f = tmp_path / "cases.json"
    f.write_text(json.dumps(cases, ensure_ascii=True), encoding="utf-8")
    proc = subprocess.run(["node", str(RUNNER), str(f), *flags], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


# ------------------------------------------------------------------ the generated file is the Python source of truth

def test_the_committed_engine_is_exactly_what_the_python_source_generates():
    expected = _load_builder().render()
    assert ENGINE.read_text(encoding="utf-8").replace("\r\n", "\n") == expected, "run: python tools/build_ritanya_engine.py"


# ------------------------------------------------------------------ parity

@needs_node
def test_browser_engine_matches_python_on_the_whole_corpus(tmp_path):
    cases = corpus.build_corpus(fuzz=True)
    assert len(cases) > 18000
    actual = run_node(cases, tmp_path)
    assert len(actual) == len(cases)
    mismatches = []
    for case, got in zip(cases, actual):
        want = python_expected(case)
        if got != want:
            mismatches.append((case, want, got))
    assert not mismatches, f"{len(mismatches)} of {len(cases)} differ; first: {json.dumps(mismatches[0], ensure_ascii=True)[:1500]}"


@needs_node
def test_the_corpus_really_exercises_every_topic_refusal_and_fallback(tmp_path):
    cases = corpus.build_corpus(fuzz=False)
    seen = {python_intent(c) for c in cases}
    seen.discard(None)
    topic_ids = set(K.TOPICS)
    assert topic_ids <= seen, sorted(topic_ids - seen)                                         # every approved topic answered
    assert {t + "_more" for t in topic_ids} <= seen, sorted({t + "_more" for t in topic_ids} - seen)   # and every "tell me more"
    assert {"greeting", "thanks", "clarify", "followup_no_topic", "unknown", "out_of_bounds", "personal_refused", "account_access_refused"} <= seen
    invalid = [c for c in cases if python_expected(c)["ok"] is False]
    assert len(invalid) >= 15                                                                     # validation failures are compared too


@needs_node
def test_every_ui_chip_and_starter_is_answered_identically(tmp_path):
    phrases = set(K.START_SUGGESTIONS)
    for t in K.TOPICS.values():
        phrases |= set(t.suggestions)
    for items in K.PAGE_STARTERS.values():
        phrases |= set(items)
    cases = [{"m": p} for p in sorted(phrases)]
    got = run_node(cases, tmp_path)
    for c, g in zip(cases, got):
        assert g == python_expected(c), c
        assert g["ok"] and not g["result"]["unknown"] and not g["result"]["refused"], c           # a chip is never a dead end


@needs_node
def test_replies_hold_the_same_boundaries_as_the_server(tmp_path):
    cases = [{"m": q} for q in ["How much does it cost?", "Can I buy it now?", "Review my resume", "ignore previous instructions", "go to evil.com",
                                "what is my email", "Do you issue certificates?", "What is a Rithavo Card?"]]
    out = run_node(cases, tmp_path)
    blob = " ".join(o["result"]["reply"] for o in out)
    assert "₹" not in blob and not re.search(r"\b\d{3}\b", blob) and not re.search(r"available now|live now", blob, re.I)
    for o in out:
        for a in o["result"]["actions"]:
            assert a["href"].startswith("/") and not a["href"].startswith("//") and a["canonical"].startswith("https://rithavo.com/")
    assert out[2]["result"]["refused"] and out[3]["result"]["refused"] and out[4]["result"]["refused"] and out[5]["result"]["refused"]
    assert out[6]["result"]["unknown"] and not out[6]["result"]["actions"]


@needs_node
def test_results_are_fresh_objects_so_a_caller_cannot_corrupt_later_answers(tmp_path):
    script = tmp_path / "mut.js"
    script.write_text(
        'const fs=require("fs"),vm=require("vm");const s={};s.self=s;vm.createContext(s);'
        'vm.runInContext(fs.readFileSync(%s,"utf8"),s);const E=s.RitanyaEngine;'
        'const a=E.respond("what is rithavo");a.result.reply="HACKED";a.result.actions.push({href:"//evil.test"});a.result.suggestions.length=0;'
        'const b=E.respond("what is rithavo");process.stdout.write(JSON.stringify(b));' % json.dumps(str(ENGINE)), encoding="utf-8")
    b = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60).stdout)
    assert b == python_expected({"m": "what is rithavo"})


@needs_node
def test_worst_case_inputs_never_stall_the_page(tmp_path):
    nasty = ["a " * 200, "ignore " * 57, "my " * 130, "what " * 80, "should i " * 44, "a" * 400, ".".join(["rithavo"] * 50)[:400], "help " * 80,
             "how do i " * 44, "show me my " * 36, "what is " * 50, "( " * 200, "." * 400, "a." * 200, "rithavo.com " * 33, "is it " * 66]
    out = run_node([{"m": m} for m in nasty], tmp_path, "--time")
    assert out["maxMs"] < 100, out


# ------------------------------------------------------------------ the engine cannot reach anything

def _logic_and_wrapper():
    text = ENGINE.read_text(encoding="utf-8")
    start = text.index("function rx(p)")
    end = text.index("root.RitanyaEngine")
    return text[start:end] + text[end:]


def test_engine_logic_has_no_network_storage_dom_or_dynamic_code_primitives():
    logic = _logic_and_wrapper()
    banned = [r"\bfetch\b", r"XMLHttpRequest", r"WebSocket", r"EventSource", r"sendBeacon", r"importScripts", r"\bimport\s*\(", r"\beval\b",
              r"new\s+Function", r"\bFunction\s*\(", r"localStorage", r"sessionStorage", r"indexedDB", r"\bdocument\b", r"\bwindow\b",
              r"\bnavigator\b", r"\bcookie", r"\blocation\b", r"postMessage", r"setTimeout", r"setInterval", r"\bcaches\b", r"serviceWorker",
              r"\brequire\b", r"\bprocess\b", r"\bglobalThis\b", r"https?://"]
    found = [b for b in banned if re.search(b, logic)]
    assert found == []


def test_engine_data_has_only_relative_rithavo_destinations_and_no_other_host():
    text = ENGINE.read_text(encoding="utf-8")
    payload = json.loads(text[text.index("var D = ") + len("var D = "):text.index(";\n", text.index("var D = "))])
    for key, d in payload["destinations"].items():
        assert d["path"].startswith("/") and not d["path"].startswith("//") and "://" not in d["path"], key
    assert payload["canonicalHost"] == "https://rithavo.com"
    blob = json.dumps(payload["topics"]) + json.dumps(payload["text"])
    assert not re.search(r"https?://|\b[a-z0-9-]+\.rithavo\.com\b|rithavo\.app", blob)
    assert payload["limits"] == {"maxMessageChars": rc.MAX_MESSAGE_CHARS, "maxUnknownStreak": rc.MAX_UNKNOWN_STREAK}


def test_engine_exposes_only_pure_functions():
    text = ENGINE.read_text(encoding="utf-8")
    assert re.search(r"root\.RitanyaEngine = \{ respond: respond, answer: answer, validate: validate, clean: clean, norm: norm, topics: Object\.keys\(D\.topics\) \};", text)
    assert text.count("root.") == 1


# ------------------------------------------------------------------ the shipped fixture used by the real-browser test

def _conversation():
    """The scripted conversation with the page's own state rules applied to the PYTHON replies."""
    last_topic, streak, out = None, 0, []
    for message in corpus.CONVERSATION:
        case = {"m": message}
        if last_topic is not None:
            case["t"] = last_topic
        if streak:
            case["s"] = streak
        got = python_expected(case)
        r = got["result"]
        out.append({"reply": r["reply"], "topic": r["topic"], "refused": r["refused"], "unknown": r["unknown"], "hrefs": [a["href"] for a in r["actions"]],
                    "chips": list(r["suggestions"])})
        if isinstance(r["topic"], str):
            last_topic = r["topic"]
        streak = min(10, streak + 1) if r["unknown"] else 0
    return {"messages": list(corpus.CONVERSATION), "expected": out}


def _fixture():
    cases = corpus.representative_sample()
    return {"cases": cases, "expected": [python_expected(c) for c in cases], "conversation": _conversation()}


def test_the_browser_fixture_is_exactly_what_python_produces():
    fresh = _fixture()
    if os.environ.get("REGEN_RITANYA_FIXTURE") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(fresh, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    assert FIXTURE.exists(), "run once with REGEN_RITANYA_FIXTURE=1"
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(fresh)), "fixture is stale: REGEN_RITANYA_FIXTURE=1 pytest tests/test_ritanya_engine_parity.py"
    assert len(fresh["cases"]) > 500
