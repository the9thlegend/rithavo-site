"""
R1: deployment routing for the member profile (S-R1-08, A-R1-01): vercel.json, robots.txt, the service worker, and the
guarantee that no existing route changed. Static checks plus the real sw.js and theme.js run in Node.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_COMMIT = "71ffab1"  # origin/main this release was branched from


def _routes():
    return json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["routes"]


def _first_match(path):
    for rule in _routes():
        if "has" in rule:  # host-conditional redirect rule
            continue
        if re.fullmatch(rule["src"], path):
            return rule
    return None


def test_member_profile_paths_route_to_the_python_function_so_the_flag_can_answer_404():
    for path in ("/c/AbCdEfGh12345678", "/c/AbCdEfGh12345678/", "/c/a_b-c"):
        assert _first_match(path)["dest"] == "api/index.py", path


def test_the_new_rule_does_not_capture_unrelated_public_pages():
    for path in ("/career/", "/contact/", "/career-intelligence/", "/cookies", "/", "/about/"):
        rule = _first_match(path)
        assert rule is None or "/c/" not in rule["src"], path


def test_the_new_rule_sits_after_the_percent_encoding_rule_and_before_the_generic_static_rules():
    srcs = [r["src"] for r in _routes()]
    new = srcs.index("/c/([^/]+)/?")
    assert srcs.index("/(.*%.*)") < new < srcs.index("/((?:[^./]+/)*[^./]*)")
    assert srcs[1:4] == ["/api/(.*)", "/profile/edit(/.*)?", "/(.*%.*)"]   # the existing exposure guard's order is intact


def test_function_configuration_is_unchanged_and_the_shell_ships_inside_app():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["functions"] == {"api/index.py": {"maxDuration": 60}}
    assert (ROOT / "app" / "pages" / "public_profile_shell.py").is_file()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_no_existing_route_was_removed_or_reordered():
    try:
        base = subprocess.run(["git", "show", f"{BASE_COMMIT}:vercel.json"], cwd=ROOT, capture_output=True, text=True,
                              timeout=30, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("base commit not available in this checkout")
    base_routes = json.loads(base)["routes"]
    current = _routes()
    assert [r for r in current if r["src"] != "/c/([^/]+)/?"] == base_routes
    assert json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["functions"] == json.loads(base)["functions"]


def test_robots_disallows_the_member_profile_and_keeps_the_rest():
    text = (ROOT / "robots.txt").read_text(encoding="utf-8")
    assert "Disallow: /c/" in text.splitlines()
    for kept in ("Disallow: /api/", "Disallow: /admin/", "Disallow: /profile/", "Sitemap: https://rithavo.com/sitemap.xml"):
        assert kept in text.splitlines()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script", ["sw_profile_paths.test.js", "theme_preference.test.js"])
def test_node_suites(script):
    r = subprocess.run(["node", "--test", str(ROOT / "tests" / script)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2500:] + r.stderr[-1500:]
