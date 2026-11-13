"""
R1: the member-profile page shell (scaffold) and the profile-scoped design assets. The shell is served only while
RITHAVO_PUBLIC_PROFILE_ENABLED is on. The page composition itself is built to the signed-off specimens (V6) and is not part
of this scaffold; these tests pin the properties every later version must keep.
"""

import hashlib
import re
from pathlib import Path

import pytest

from app.pages.public_profile_shell import HTML as SHELL_HTML

import config
from app import profile_client, routes_public_profile
from tests.conftest import login_via_magic_link

ROOT = Path(__file__).resolve().parent.parent
SLUG = "AbCdEfGh12345678"

# Pinned SHA-256 of the profile-scoped Bearing copies (V3). The same TOKENS value is pinned in the profile service repo.
TOKENS_SHA256 = "bcc8df370eb38d99b7564138b1fb7cc16767fe7ffc5681028590d931afaa144c"  # the DERIVED copy (see derive_tokens)
COMPONENTS_SHA256 = "7a9a2490bcd8b5929215e0159ca66979ecda23a1fdb0b42d7c48a33d80ec570d"
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
    assert re.search(r'<a class="b-skip" href="#profile-root">', html)
    assert '<main id="profile-root"' in html and "<header" in html and '<html lang="en">' in html


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
        if ref == "/":
            continue
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
    assert _sha(ROOT / "assets" / "profile" / "bearing-components.css") == COMPONENTS_SHA256


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
    theme = (ROOT / "assets" / "profile" / "profile-theme.css").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", theme, flags=re.S)
    assert '--f-display: "Manrope"' in code and '--f-body:    "Manrope"' in code and '--f-data:    "DM Mono"' in code
    for forbidden in ("Inter", "Bricolage", "Hanken", "IBM Pl" + "ex", "@font-face", "@import", "googleapis"):
        assert forbidden not in code, forbidden
    # no external resource: the only url() allowed is an inline data: URI (the chevron mask)
    assert all(u.startswith(('"data:image/svg+xml', "'data:image/svg+xml", "data:image/svg+xml")) for u in re.findall(r"url\(([^)]*)\)", code))
    for css in (ROOT / "assets" / "profile").glob("*.css"):
        text = css.read_text(encoding="utf-8")
        assert "googleapis" not in text and "@import" not in text and "http://" not in text.replace("http://www.w3.org", "")


def test_the_gradient_is_confined_to_one_portrait_backdrop_token_and_the_logo():
    """V1 (hybrid): the brand gradient is allowed only in the logo and as ONE contained, static backdrop behind the portrait."""
    theme = (ROOT / "assets" / "profile" / "profile-theme.css").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", theme, flags=re.S)
    assert code.count("linear-gradient(") == 1
    assert "--portrait-backdrop: linear-gradient(115deg, var(--brand-g1) 0%, var(--brand-g2) 52%, var(--brand-g3) 100%)" in code
    assert "animation" not in code.split("--portrait-backdrop")[1].split(";")[0]
    # the Bearing component CSS uses gradients only as hard-stop drawing for Scale ticks, never as colour
    components = (ROOT / "assets" / "profile" / "bearing-components.css").read_text(encoding="utf-8")
    for match in re.findall(r"linear-gradient\(([^)]*)\)", components):
        assert "brand" not in match and "--g1" not in match


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

