"""
PWA release gates that can be checked without a browser:

  * the manifest is valid, same-origin and installable (icons really are the sizes they claim);
  * every page carries the PWA head tags and loads pwa.js;
  * the service worker's caching policy (run in node against the real sw.js);
  * the ONE-DOMAIN rule: no served file names another customer-facing host.

Behaviour in a real browser and on a real Preview is verified separately (see the release evidence).
"""

import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "manifest.webmanifest").read_text(encoding="utf-8"))
INTERNAL = {"design-lab", "ritanya-handoff", "tests", "app", "api", ".claude", ".git", "storage", "tools",
            "__pycache__", ".vercel", "node_modules", ".pytest_cache"}


def served_files(suffixes):
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.is_file() and rel.parts[0] not in INTERNAL and p.suffix.lower() in suffixes:
            yield rel


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


# ------------------------------------------------------------------ manifest

def test_manifest_is_installable_and_same_origin():
    m = MANIFEST
    assert m["name"] == "Rithavo" and m["short_name"] == "Rithavo"
    assert m["display"] == "standalone"
    assert m["scope"] == "/" and m["id"] == "/"
    assert m["start_url"].startswith("/") and not m["start_url"].startswith("//")
    assert m["start_url"].split("?")[0] == "/home/"
    for key in ("background_color", "theme_color"):
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", m[key])
    for icon in m["icons"]:
        assert icon["src"].startswith("/assets/pwa/"), icon
    for shortcut in m["shortcuts"]:
        assert shortcut["url"].startswith("/") and not shortcut["url"].startswith("//"), shortcut
        assert shortcut["url"].split("?")[0] in {"/career-intelligence/", "/application-diagnosis/", "/resumes/"}
        for icon in shortcut["icons"]:
            assert icon["src"].startswith("/assets/pwa/")


def test_manifest_icons_exist_with_the_sizes_they_declare():
    purposes = set()
    for icon in MANIFEST["icons"]:
        path = ROOT / icon["src"].lstrip("/")
        assert path.is_file(), icon["src"]
        width, height = png_size(path)
        assert f"{width}x{height}" == icon["sizes"], (icon["src"], width, height)
        purposes.add((icon["sizes"], icon["purpose"]))
    assert ("192x192", "any") in purposes and ("512x512", "any") in purposes and ("512x512", "maskable") in purposes
    assert png_size(ROOT / "assets/pwa/apple-touch-icon.png") == (180, 180)


# ------------------------------------------------------------------ pages

def test_every_page_has_the_pwa_head_tags_and_script():
    pages = [p for p in served_files({".html"}) if p.as_posix() != "offline/index.html"]
    assert len(pages) >= 20
    needed = ['rel="manifest" href="/manifest.webmanifest"', 'name="theme-color"',
              'rel="apple-touch-icon" href="/assets/pwa/apple-touch-icon.png"', 'name="apple-mobile-web-app-capable"',
              "viewport-fit=cover", '<script src="/pwa.js" defer></script>']
    missing = [p.as_posix() for p in pages if any(t not in (ROOT / p).read_text(encoding="utf-8") for t in needed)]
    assert missing == []
    offline = (ROOT / "offline/index.html").read_text(encoding="utf-8")
    assert "viewport-fit=cover" in offline and 'href="/pwa.css"' in offline


def test_the_home_page_offers_no_card_handoff_in_the_browser_or_the_installed_app():
    home = (ROOT / "home/index.html").read_text(encoding="utf-8")
    for needle in ("card-handoff", "card-continue", "card-view-btn", "card-share-btn", "/card/continue", "handoff_url", "form.submit", "app.rithavo"):
        assert needle not in home, needle
    assert 'href="/profile/"' in home and "isn't available on rithavo.com" in home   # accurate on-domain status, Profile link kept
    css = (ROOT / "pwa.css").read_text(encoding="utf-8")
    assert "@media (display-mode: standalone) { .pwa-hide-in-app { display: none !important; } }" in css   # kept for any future app-only hiding


def test_sign_out_clears_the_apps_caches():
    shell = (ROOT / "app-shell.js").read_text(encoding="utf-8")
    assert shell.index("/auth/logout") < shell.index("clearPrivateState") < shell.index('window.location.href = "/"')


# ------------------------------------------------------------------ service worker policy (node)

def test_every_page_that_loads_the_signed_in_shell_is_network_only_in_the_worker():
    sw = (ROOT / "sw.js").read_text(encoding="utf-8")
    listed = set(re.findall(r'"(/[a-z-]+)"', re.search(r"PRIVATE_PAGE_PREFIXES = \[(.*?)\];", sw, re.S).group(1)))
    # /admin is not listed: the worker never touches it at all (NEVER_HANDLE_PREFIXES)
    never = {"/admin"}
    shell_pages = {"/" + p.relative_to(ROOT).parts[0] for p in ROOT.rglob("index.html")
                   if len(p.relative_to(ROOT).parts) == 2 and "app-shell.js" in p.read_text(encoding="utf-8")}
    assert shell_pages, "no app-shell pages found"
    assert shell_pages - never <= listed, sorted(shell_pages - never - listed)
    assert listed <= shell_pages, sorted(listed - shell_pages)   # nothing public listed by mistake


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_service_worker_policy_in_node():
    result = subprocess.run(["node", "--test", str(ROOT / "tests" / "sw_policy.test.js")], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


# ------------------------------------------------------------------ the one-domain rule

# Hosts a customer's browser may contact from a Rithavo page. Third-party libraries and fonts only; every
# Rithavo address must be rithavo.com itself. Adding a host here is a deliberate, reviewed decision.
ALLOWED_THIRD_PARTY_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com", "checkout.razorpay.com", "sdk.cashfree.com", "www.w3.org"}
FORBIDDEN_RITHAVO = re.compile(r"\b(?:[a-z0-9-]+\.rithavo\.com|rithavo\.app|rithavo\.applications\.com|rithavo\.in|rithavo\.co)\b", re.I)
# Pages allowed to name the sibling host in visible text: none (the contact page's mention was removed).
KNOWN_VISIBLE_MENTION = set()


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    return re.sub(r"(?m)(^|\s)//[^\n]*", " ", text)


def test_no_served_file_points_a_customer_at_another_host():
    offenders = []
    for rel in served_files({".html", ".js", ".css", ".webmanifest"}):
        raw = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        for host in re.findall(r"(?:https?:)?//([a-z0-9.-]+\.[a-z]{2,})", raw, re.I):
            if host.lower() not in ALLOWED_THIRD_PARTY_HOSTS:
                offenders.append((rel.as_posix(), host))
        if rel.as_posix() not in KNOWN_VISIBLE_MENTION and FORBIDDEN_RITHAVO.search(_strip_comments(raw)):
            offenders.append((rel.as_posix(), "rithavo subdomain/domain"))
    assert offenders == []


def test_pwa_files_name_no_host_at_all():
    for name in ("sw.js", "pwa.js", "pwa.css", "manifest.webmanifest", "offline/index.html"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), name
        assert "rithavo.com" not in _strip_comments(text).replace("hello@rithavo.com", ""), name


def test_vercel_json_introduces_no_other_domain():
    config = (ROOT / "vercel.json").read_text(encoding="utf-8")
    hosts = set(re.findall(r"(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|app|in|co|io|net|org))", config.replace("\\\\.", ".")))
    hosts = {h for h in hosts if not re.fullmatch(r"(?:html|css|js|png|docx|ico|svg|webp|jpg|jpeg|gif|woff2?|webmanifest|py|xml|txt)", h)}
    assert hosts <= {"www.rithavo.com", "rithavo.com", "api/index.py"} or all(h.endswith("rithavo.com") for h in hosts), hosts
    assert "app.rithavo.com" not in config and "rithavo.app" not in config


def test_cname_is_the_canonical_domain_only():
    assert (ROOT / "CNAME").read_text(encoding="utf-8").strip() == "rithavo.com"
