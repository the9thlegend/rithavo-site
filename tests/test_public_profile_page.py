"""
R1: the member-profile page shells (public profile and owner panel) and the profile-scoped design assets. The pages are served
only while their feature flags are on. The visual direction is the Founder's approved dark references; these tests pin the
properties that must survive any later restyle: security headers, no inline script or style, same-origin assets, the existing
logo verbatim, shipped fonts only, and text inserted without markup.
"""

import hashlib
import re
from pathlib import Path

import pytest

from app.pages.public_profile_shell import HTML as SHELL_HTML, MANAGE_HTML

import config
from app import profile_client, routes_public_profile
from tests.conftest import login_via_magic_link

ROOT = Path(__file__).resolve().parent.parent
SLUG = "AbCdEfGh12345678"

# Pinned SHA-256 of the profile-scoped Bearing copies (V3). The same TOKENS value is pinned in the profile service repo.
TOKENS_SHA256 = "bcc8df370eb38d99b7564138b1fb7cc16767fe7ffc5681028590d931afaa144c"  # the DERIVED copy (see derive_tokens)
BEARING_SOURCE_TOKENS = Path(r"C:\Rithavo\rithavo-site\tokens.css")  # untracked original; compared only if present


def derive_tokens(source: str) -> str:
    """The profile-scoped token copy is the Bearing tokens.css minus its three candidate type families and its data face
    (this repository ships only Fraunces, Manrope and DM Mono). Identical code is used in the other repository."""
    start = source.index("/* -------- Type (EXPERIMENTAL")
    marker = ':root {\n  --f-data:     "IBM Pl' + 'ex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;\n'
    end = source.index(marker)
    note = ("/* -------- Type: profile-scoped copy --------\n"
            "   OMITTED from this copy: the three candidate type families Bearing proposes, and its --f-data face. This repository ships\n"
            "   only Fraunces, Manrope and DM Mono (self-hosted); the profile pages set --f-display, --f-body, --f-data and\n"
            "   --fw-display in profile-theme.css (decision V2). --f-wordmark is a BRAND ASSET (the existing wordmark, Fraunces). */\n")
    return source[:start] + note + ":root {\n" + source[end + len(marker):]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- the shell -----------------------------------------------------------------------------------------------------

def test_the_shell_is_a_python_module_not_a_file_that_must_be_packaged():
    """Python modules are always bundled with the function, so packaging cannot silently drop the page; and no public
    static file exists for a disabled feature."""
    assert not (ROOT / "c").exists()
    assert not list((ROOT / "app" / "pages").glob("*.html"))
    assert (ROOT / "app" / "pages" / "public_profile_shell.py").is_file()


def test_shell_carries_the_pwa_head_tags_like_every_other_member_page():
    html = SHELL_HTML
    for tag in ('rel="manifest" href="/manifest.webmanifest"', 'name="theme-color"',
                'rel="apple-touch-icon" href="/assets/pwa/apple-touch-icon.png"',
                'name="apple-mobile-web-app-capable"', "viewport-fit=cover", '<script src="/pwa.js?v=20261106" defer></script>'):
        assert tag in html, tag


def test_shell_is_noindex_and_has_a_skip_link_and_a_landmark():
    html = SHELL_HTML
    assert '<meta name="robots" content="noindex">' in html  # the repository's exact form; the header adds nofollow
    assert re.search(r'<a class="pf-skip" href="#pf-main">', html)
    assert '<main id="pf-main"' in html and "<header" in html and '<html lang="en"' in html


def test_shell_has_no_inline_script_no_inline_style_and_no_external_host():
    html = SHELL_HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline script"
    # the verbatim logo markup carries exactly one inline style attribute; the CSP permits that exact value by hash only
    assert re.findall(r' style="([^"]*)"', html) == ["position:absolute"] and "<style" not in html
    assert not re.search(r"https?://", html), "external or absolute URL"
    assert not re.search(r"\bon[a-z]+\s*=", html), "inline event handler"


def test_theme_script_loads_first_so_the_theme_applies_before_first_paint():
    html = SHELL_HTML
    head = html[html.index("<head>"):html.index("</head>")]
    assert head.index("/assets/profile/theme.js") < head.index("rel=\"stylesheet\"")


def test_every_asset_the_shell_references_exists():
    html = SHELL_HTML
    for ref in re.findall(r'(?:href|src)="(/[^"#?]+)"', html):
        if ref.endswith("/"):
            continue  # a page link (the sign-in and home pages), not an asset
        assert (ROOT / ref.lstrip("/")).is_file(), ref


def test_logo_is_the_existing_inline_svg_verbatim():
    home = (ROOT / "index.html").read_text(encoding="utf-8").splitlines()
    defs = "\n".join(home[39:51])
    lockup = home[54].strip()
    html = SHELL_HTML
    assert defs in html, "gradient definitions differ from index.html:40-51"
    assert lockup in html, "wordmark lockup differs from index.html:55"
    for path_data in re.findall(r'<path d="([^"]+)"', lockup):
        assert path_data in html
    assert "Rithavo</span></a>" in html


def test_brand_gradient_names_appear_only_inside_the_logo_definitions():
    html = SHELL_HTML
    outside = html.replace("\n".join((ROOT / "index.html").read_text(encoding="utf-8").splitlines()[39:51]), "")
    assert "var(--g1)" not in outside and "var(--g2)" not in outside and "var(--g3)" not in outside
    assert "gradient" not in outside.lower().replace("lineargradient", "")


# ---- the design assets ---------------------------------------------------------------------------------------------

def test_bearing_copies_match_their_pinned_hashes():
    assert _sha(ROOT / "assets" / "profile" / "bearing-tokens.css") == TOKENS_SHA256


@pytest.mark.skipif(not BEARING_SOURCE_TOKENS.is_file(), reason="the untracked Bearing original is not on this machine")
def test_token_copy_is_exactly_the_bearing_original_minus_the_omitted_type_block():
    derived = derive_tokens(BEARING_SOURCE_TOKENS.read_text(encoding="utf-8"))
    assert (ROOT / "assets" / "profile" / "bearing-tokens.css").read_text(encoding="utf-8") == derived


def test_token_copy_names_no_font_this_repository_does_not_ship():
    css = (ROOT / "assets" / "profile" / "bearing-tokens.css").read_text(encoding="utf-8")
    for name in ("Bricolage", "Hanken", "Geist", "IBM Pl" + "ex"):
        assert name not in css, name
    assert '--f-wordmark: "Fraunces"' in css and "--fs-h1" in css


def test_fonts_are_the_repository_fonts_and_nothing_is_loaded_from_outside():
    css = (ROOT / "assets" / "profile" / "profile.css").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert '--pf-display: "Manrope"' in code and '--pf-data: "DM Mono"' in code and '--pf-wordmark: "Fraunces"' in code
    for forbidden in ("Inter", "Bricolage", "Hanken", "IBM Pl" + "ex", "@font-face", "@import", "googleapis"):
        assert forbidden not in code, forbidden
    assert not re.findall(r"url\(", code), "no external or inline resource in the profile CSS"
    for f in (ROOT / "assets" / "profile").glob("*.css"):
        text = f.read_text(encoding="utf-8")
        assert "googleapis" not in text and "@import" not in text and "http://" not in text.replace("http://www.w3.org", "")


def test_the_profile_css_keeps_its_accessibility_rules():
    code = (ROOT / "assets" / "profile" / "profile.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in code and "animation: none !important" in code
    assert ":focus-visible" in code and "outline: 3px solid var(--pf-focus)" in code
    assert "min-height: 44px" in code and "forced-colors: active" in code
    # the floating indicators only animate when motion is allowed
    anim = code.index("animation: pf-float")
    assert "@media (prefers-reduced-motion: no-preference)" in code[:anim]


def test_profile_scripts_never_turn_server_text_into_markup():
    for name in ("common.js", "profile.js", "manage.js"):
        js = (ROOT / "assets" / "profile" / name).read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"style\"", "localStorage"):
            assert banned not in js, f"{name}: {banned}"
        # the SVG namespace identifier is not a network address; nothing else may look like one
        assert "http://" not in js.replace("http://www.w3.org/2000/svg", "") and "https://" not in js, name


def test_the_profile_pages_use_the_same_logo_and_the_dark_default():
    for html in (SHELL_HTML, MANAGE_HTML):
        assert 'data-theme-default="dark"' in html and 'href="/assets/profile/profile.css"' in html
        assert html.count('class="wordmark"') == 1 and "pf-theme" in html
    assert 'src="/assets/profile/profile.js"' in SHELL_HTML and 'src="/assets/profile/manage.js"' in MANAGE_HTML
    assert 'src="/assets/profile/common.js"' in SHELL_HTML and 'src="/assets/profile/common.js"' in MANAGE_HTML


def test_manage_shell_has_no_inline_script_no_inline_style_and_no_external_host():
    html = MANAGE_HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert re.findall(r' style="([^"]*)"', html) == ["position:absolute"] and "<style" not in html
    assert not re.search(r"https?://", html) and not re.search(r"\bon[a-z]+\s*=", html)
    assert '<meta name="robots" content="noindex">' in html


# ---- serving the shell ---------------------------------------------------------------------------------------------

@pytest.fixture
def served(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "PROFILE_SERVICE_SECRET", "page-test-secret-" + "p" * 24)
    monkeypatch.setattr(config, "PUBLIC_PROFILE_ENABLED", True)
    routes_public_profile.reset_limiters()
    login_via_magic_link(client, app, "member@example.test")
    return client


def test_signed_in_members_get_the_shell_with_hardening_headers(served):
    r = served.get(f"/c/{SLUG}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    h = r.headers
    assert h["cache-control"] == "private, no-store" and h["x-robots-tag"] == "noindex, nofollow"
    assert h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "style-src-attr 'unsafe-hashes' 'sha256-%s'" % routes_public_profile.LOGO_STYLE_ATTR_HASH in csp
    import base64, hashlib
    assert base64.b64encode(hashlib.sha256(b"position:absolute").digest()).decode() == routes_public_profile.LOGO_STYLE_ATTR_HASH
    assert re.search(r"https?:", csp) is None, "the policy must not allow any external origin"


def test_invalid_slugs_never_get_the_shell(served):
    for bad in ("short", "bad slug!", "a" * 65, "x" * 7):
        assert served.get(f"/c/{bad}", follow_redirects=False).status_code == 404



# ---- the owner panel page --------------------------------------------------------------------------------------------

@pytest.fixture
def managed(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "PROFILE_SERVICE_SECRET", "page-test-secret-" + "p" * 24)
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", True)
    routes_public_profile.reset_limiters()
    return app, client


def test_manage_page_is_a_literal_route_that_cannot_shadow_a_slug():
    assert not routes_public_profile.SLUG_RE.match("manage")
    paths = [r.path for r in routes_public_profile.router.routes]
    assert paths.index("/c/manage") < paths.index("/c/{slug}")


def test_manage_page_needs_its_flag_then_a_session(managed, monkeypatch):
    app, client = managed
    anon = client.get("/c/manage", follow_redirects=False)
    assert anon.status_code == 302 and anon.headers["location"] == "/sign-in/?next=/c/manage"
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", False)
    assert client.get("/c/manage", follow_redirects=False).status_code == 404  # flag first, even for anonymous callers
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", True)
    login_via_magic_link(client, app, "member@example.test")
    r = client.get("/c/manage")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store" and r.headers["x-robots-tag"] == "noindex, nofollow"
    assert "manage.js" in r.text and "unsafe-inline" not in r.headers["content-security-policy"]


def test_photo_thumbnail_route_is_gated_and_validated_by_hand(managed, monkeypatch):
    app, client = managed
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", False)
    assert client.get("/my-photos/1/image").status_code == 404  # disabled: 404 for anonymous callers, never a 422/401
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", True)
    assert client.get("/my-photos/1/image").status_code == 401
    login_via_magic_link(client, app, "member@example.test")
    for bad in ("abc", "1.5", "-1", "9" * 30):
        assert client.get(f"/my-photos/{bad}/image").status_code == 404


# ---- release R3 and the entry point on the private /profile/ page -------------------------------------------------------

def test_private_profile_page_gets_the_entry_point_script_and_no_unconditional_management_link():
    html = (ROOT / "profile" / "index.html").read_text(encoding="utf-8")
    assert 'id="public-profile-entry"' in html and "private-class.js" in html and 'src="/assets/profile/entry.js"' in html
    assert "/c/manage" not in html, "the link is created by script only when the management API answers 200 (flag on)"
    assert 'href="/profile/edit"' in html and "RithavoApp.requireSession" in html  # existing behaviour preserved


def test_entry_script_adds_links_only_on_a_200_and_never_on_failure():
    js = (ROOT / "assets" / "profile" / "entry.js").read_text(encoding="utf-8")
    assert 'res.status !== 200) { return; }' in js and ".catch(" in js and "innerHTML" not in js
    assert "/c/manage" in js and "view=business" in js


def test_private_profile_restyle_is_scoped_and_markup_data_is_escaped():
    css = (ROOT / "assets" / "profile" / "private.css").read_text(encoding="utf-8")
    selectors = [line.split("{")[0].strip() for line in css.splitlines() if "{" in line and not line.startswith((" ", "/*", "@"))]
    assert selectors and all(sel.startswith((".pf-private-root", ":root.pf-private-root")) for sel in selectors), selectors
    html = (ROOT / "profile" / "index.html").read_text(encoding="utf-8")
    assert "${r.degree}" not in html and "${r.company}" not in html and "esc(r.degree)" in html
