"""
OLDER-BROWSER gate for the browser Ritanya engine and guide script.

A JavaScript file that contains a regex look-behind (?<= ... ) or (?<! ... ) is not parsed at all by a browser older than about
2023 (for example iOS Safari before 16.4): the WHOLE script fails, not just the line. The generated engine therefore must not use
it, and the matching must not change because of that. These tests prove both:

  * for every string in a systematic enumeration, the engine's text normalisation equals both the Python reference and the old
    look-behind form it replaced (the form is kept only inside tests/ritanya_engine_runner.js);
  * the two browser scripts contain none of the syntax that older engines cannot parse.
"""

import itertools
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import ritanya_chat as rc

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "ritanya_engine_runner.js"
SCRIPTS = [ROOT / "assets" / "ritanya" / "engine.js", ROOT / "assets" / "ritanya" / "ritanya.js"]

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

TOKENS = ["a", "Z", "7", ".", "-", " ", "rithavo.com", "RITHAVO.COM", "é", "_"]


def _enumeration(max_len=5):
    out = [""]
    for n in range(1, max_len + 1):
        out.extend("".join(p) for p in itertools.product(TOKENS, repeat=n))
    return out


EXTRA = [
    "rithavo.com", "www.rithavo.com", "x-rithavo.com", "1rithavo.com", "(rithavo.com)", "see rithavo.com.", "rithavo.com.rithavo.com",
    "rithavo.comrithavo.com", ".rithavo.com", "..", "...", "a.b", "a..b", "a. b", "a .b", ".a", "a.", "3.5", "3.5.", "v1.2.3", "e.g.", "U.S.A.",
    "What is rithavo.com?", "Is it rithavo.com or rithavo.in", "email me at me@rithavo.com", "https://rithavo.com/about", "RITHAVO.com",
]


@needs_node
def test_text_normalisation_is_identical_to_python_and_to_the_look_behind_form(tmp_path):
    cases = _enumeration() + EXTRA
    f = tmp_path / "norm.json"
    f.write_text(json.dumps(cases, ensure_ascii=True), encoding="utf-8")
    proc = subprocess.run(["node", str(RUNNER), str(f), "--norm"], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert len(out["engine"]) == len(cases) > 100000
    wrong = [(c, e, r) for c, e, r in zip(cases, out["engine"], out["reference"]) if e != r]
    assert not wrong, f"engine differs from the look-behind form on {len(wrong)} strings, first: {wrong[:3]}"
    wrong = [(c, e, rc._norm(c)) for c, e in zip(cases, out["engine"]) if e != rc._norm(c)]
    assert not wrong, f"engine differs from the Python reference on {len(wrong)} strings, first: {wrong[:3]}"


def _code_only(text):
    """Source with comments, string literals and the JSON data line removed, so that words inside them are not mistaken for syntax."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("//") and not line.startswith('  var D = {'))
    return re.sub(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'', '""', text)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_look_behind_anywhere_in_the_browser_scripts(script):
    source = script.read_text(encoding="utf-8")
    assert "(?<=" not in source and "(?<!" not in source, f"{script.name} contains a regex look-behind: older browsers cannot parse it"
    assert not re.search(r"\(\?<[A-Za-z]", source), "named capture groups are not supported by the oldest browsers we serve"
    assert "\\p{" not in source and "\\P{" not in source, "unicode property escapes are not supported by the oldest browsers we serve"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_syntax_or_methods_newer_than_2017_in_the_browser_scripts(script):
    code = _code_only(script.read_text(encoding="utf-8"))
    banned = {
        "optional chaining": r"\?\.(?!\d)",
        "nullish coalescing": r"\?\?",
        "optional catch binding": r"catch\s*\{",
        "object spread": r"\{\s*\.\.\.",
        "replaceAll": r"\.replaceAll\(",
        "matchAll": r"\.matchAll\(",
        ".at()": r"\.at\(",
        "flat/flatMap": r"\.flat(Map)?\(",
        "Object.fromEntries": r"Object\.fromEntries",
        "BigInt": r"\bBigInt\b|\d+n\b",
        "numeric separator": r"\d_\d",
        "class fields / private names": r"\s#[A-Za-z_]",
        "logical assignment": r"(\|\||&&|\?\?)=",
        "dotAll / sticky-d regex flag": r"/[gimuy]*[sd][gimuy]*[;,)\s.]",
    }
    hits = {name: re.findall(rx, code)[:3] for name, rx in banned.items() if re.search(rx, code)}
    assert not hits, f"{script.name} uses syntax that older browsers cannot parse: {hits}"
