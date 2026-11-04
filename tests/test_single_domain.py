"""
The ONE-DOMAIN rule, as a permanent gate: nothing a customer sees or follows may link to, load from, post to, or redirect to
another Rithavo host (app.rithavo.com, application.rithavo.com, rithavo.app, ...). Only rithavo.com itself (and the
third-party hosts already approved for fonts and payment SDKs) may appear.

What it covers
  * every customer-facing HTML page: every URL-bearing attribute (href, src, action, formaction, poster, srcset, data-*
    URLs, <meta http-equiv=refresh>), the visible text, and every inline script;
  * every served script and stylesheet (root files and assets/);
  * server behaviour: no response body, header or redirect from any GET route (signed out and signed in) names another
    Rithavo host, and every redirect after authentication stays on the request's own origin;
  * the old cross-host mechanism is gone from the client (no handoff_url, no form action set from a server value, no
    location assignment to an absolute URL).
"""

import html
import re
from pathlib import Path

import config
import pytest

from .conftest import login_via_magic_link, seed_minimal_profile

ROOT = Path(__file__).resolve().parent.parent
INTERNAL = {"design-lab", "ritanya-handoff", "tests", "app", "api", "tools", "node_modules", ".git", "storage", ".claude", ".vercel", "__pycache__"}
THIRD_PARTY_OK = {"sdk.cashfree.com", "www.w3.org"}

# A Rithavo host that is not the canonical one: any subdomain of rithavo.com, or another rithavo.* domain.
OTHER_RITHAVO_HOST = re.compile(r"(?<![a-z0-9-])(?:[a-z0-9-]+\.rithavo\.com|rithavo\.(?!com\b)[a-z]{2,}|[a-z0-9-]+\.rithavo\.[a-z]{2,})(?![a-z0-9-])", re.I)
URL_ATTR = re.compile(r"""\b(?:href|src|action|formaction|poster|data-[a-z-]*(?:url|href|src|action)[a-z-]*|content)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
ABSOLUTE_URL = re.compile(r"https?://[^\s\"'`<>)\\]+", re.I)


def pages():
    out = []
    for p in sorted(ROOT.rglob("*.html")):
        rel = p.relative_to(ROOT)
        if rel.parts[0] in INTERNAL:
            continue
        out.append(p)
    return out


def served_scripts():
    out = []
    for pattern in ("*.js", "assets/**/*.js", "*.css", "assets/**/*.css", "*.webmanifest"):
        for p in sorted(ROOT.glob(pattern)):
            rel = p.relative_to(ROOT)
            if rel.parts[0] not in INTERNAL:
                out.append(p)
    return out


def _hosts_in(text):
    return sorted({m.group(0).lower() for m in OTHER_RITHAVO_HOST.finditer(text)})


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    return re.sub(r"(?m)(^|\s)//[^\n]*", " ", text)


def test_there_are_pages_and_scripts_to_check():
    assert len(pages()) >= 20 and len(served_scripts()) >= 8


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_page_attributes_and_text_never_name_another_rithavo_host(page):
    raw = page.read_text(encoding="utf-8")
    attr_urls = [m.group(1) or m.group(2) or "" for m in URL_ATTR.finditer(raw)]
    bad_attrs = [u for u in attr_urls if _hosts_in(u)]
    assert bad_attrs == [], bad_attrs
    visible = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.S)
    visible = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", " ", visible, flags=re.S)))
    assert _hosts_in(visible) == [], _hosts_in(visible)


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_inline_scripts_never_name_another_rithavo_host(page):
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", page.read_text(encoding="utf-8"), flags=re.S | re.I)
    assert _hosts_in(_strip_comments("\n".join(scripts))) == []


@pytest.mark.parametrize("path", served_scripts(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_served_scripts_and_styles_never_name_another_rithavo_host(path):
    assert _hosts_in(_strip_comments(path.read_text(encoding="utf-8"))) == []


def test_every_absolute_url_a_page_loads_is_rithavo_com_or_an_approved_third_party():
    bad = {}
    for p in pages():
        raw = p.read_text(encoding="utf-8")
        for m in URL_ATTR.finditer(raw):
            url = m.group(1) or m.group(2) or ""
            for absolute in ABSOLUTE_URL.findall(url):
                host = re.sub(r"^https?://", "", absolute).split("/")[0].split(":")[0].lower()
                if host not in THIRD_PARTY_OK and host != "rithavo.com":
                    bad.setdefault(p.relative_to(ROOT).as_posix(), []).append(host)
    assert bad == {}


def test_the_old_cross_host_handoff_mechanism_is_not_in_any_client_code():
    for p in pages() + served_scripts():
        text = _strip_comments(p.read_text(encoding="utf-8"))
        assert "handoff_url" not in text, p
        assert not re.search(r"""\.action\s*=\s*[A-Za-z_]""", text), p                       # a form action taken from a variable
        assert not re.search(r"""location(?:\.href)?\s*=\s*["'`]https?://""", text), p        # navigation to an absolute URL
        assert not re.search(r"""window\.open\(\s*["'`]https?://""", text), p


def test_pages_do_not_meta_refresh_or_script_redirect_off_origin():
    for p in pages():
        raw = p.read_text(encoding="utf-8")
        for m in re.finditer(r"""<meta[^>]+http-equiv=["']refresh["'][^>]*>""", raw, flags=re.I):
            assert not re.search(r"url\s*=\s*https?://", m.group(0), flags=re.I), (p, m.group(0))


# ------------------------------------------------------------------ server behaviour

def _get_routes_without_params(app):
    out = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if "GET" in methods and "{" not in path and path not in {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}:
            out.append(path)
    return sorted(set(out))


def _assert_response_is_single_domain(resp, label):
    blob = resp.text + " " + " ".join(f"{k}: {v}" for k, v in resp.headers.items())
    assert _hosts_in(blob) == [], (label, _hosts_in(blob))


def test_no_get_route_response_or_header_names_another_rithavo_host(app_and_client, db):
    app, client = app_and_client
    routes = _get_routes_without_params(app)
    assert len(routes) >= 10
    for path in routes:                                           # signed out
        _assert_response_is_single_domain(client.get(path, follow_redirects=False), "signed-out " + path)
    user_id = login_via_magic_link(client, app, "single-domain@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")
    for path in routes:                                           # signed in, with a Profile
        _assert_response_is_single_domain(client.get(path, follow_redirects=False), "signed-in " + path)


def test_authentication_redirects_stay_on_the_requests_own_origin(app_and_client):
    app, client = app_and_client
    app.state.email_sender.sent.clear()
    client.post("/auth/start", data={"email": "redirect-check@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    good = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    bad = client.get("/auth/verify?token=not-a-real-token", follow_redirects=False)
    for resp in (good, bad):
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert location.startswith("http://testserver/"), location
        assert _hosts_in(location) == []


def test_the_card_handoff_is_off_and_names_no_other_host(app_and_client, db):
    assert config.CARD_HANDOFF_ENABLED is False
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "handoff-off@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")
    resp = client.post("/card/continue")
    assert resp.status_code == 404
    _assert_response_is_single_domain(resp, "POST /card/continue")


def test_the_guard_itself_catches_a_violation():
    assert _hosts_in('<a href="https://app.rithavo.com/card">x</a>') == ["app.rithavo.com"]
    assert _hosts_in("visit rithavo.app now") == ["rithavo.app"]
    assert _hosts_in("write to hello@rithavo.com or visit https://rithavo.com/career/") == []
    assert _hosts_in("https://www.rithavo.com/") == ["www.rithavo.com"]
