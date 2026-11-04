"""
Fonts are served from rithavo.com. Google Fonts must never be requested again.

Fraunces, Manrope and DM Mono are SIL Open Font License 1.1 fonts (google/fonts ofl/*/METADATA.pb). The files in assets/fonts are
Google's own latin and latin-ext woff2 subsets of the exact families, weights and unicode ranges the pages used before, with the
license text alongside (assets/fonts/LICENSES.txt, served as assets/fonts/licenses.html).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"
SKIP = {"tests", "node_modules", ".git", "tools"}


def _site_files(*suffixes):
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in suffixes and not (set(p.relative_to(ROOT).parts) & SKIP):
            yield p


def test_no_page_script_or_stylesheet_requests_google_fonts():
    offenders = []
    for p in _site_files(".html", ".css", ".js", ".py"):
        if p == FONTS / "fonts.css":
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"fonts\.(googleapis|gstatic)\.com", text):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"these files request Google Fonts: {offenders}"


def test_every_page_with_the_site_font_stylesheet_uses_the_self_hosted_one():
    pages = [p for p in _site_files(".html") if p.as_posix().find("offline/") < 0 and "/styles.css" in p.read_text(encoding="utf-8", errors="ignore")]
    assert len(pages) >= 20
    missing = [str(p.relative_to(ROOT)) for p in pages if 'href="/assets/fonts/fonts.css"' not in p.read_text(encoding="utf-8", errors="ignore")
               and "/assets/fonts/" not in p.read_text(encoding="utf-8", errors="ignore")]
    assert not missing, missing[:5]


def test_font_files_exist_and_are_real_woff2():
    files = sorted(FONTS.glob("*.woff2"))
    assert len(files) == 8
    for f in files:
        data = f.read_bytes()
        assert data[:4] == b"wOF2" and len(data) > 5000, f.name
    assert sum(f.stat().st_size for f in files) < 400_000     # the whole set stays small


def test_stylesheet_keeps_the_same_families_weights_and_swap_and_points_only_at_local_files():
    css = (FONTS / "fonts.css").read_text(encoding="utf-8")
    rules = re.findall(r"@font-face \{(.*?)\}", css, re.S)
    assert len(rules) == 20
    seen = {(re.search(r"font-family: '([^']+)'", r).group(1), re.search(r"font-weight: ([\d ]+);", r).group(1)) for r in rules}
    assert seen == ({("Fraunces", w) for w in ("400", "500", "600")} | {("Manrope", w) for w in ("400", "500", "600", "700", "800")}
                    | {("DM Mono", w) for w in ("400", "500 600")})
    for r in rules:
        assert "font-display: swap" in r and "unicode-range:" in r
        url = re.search(r"url\(([^)]+)\)", r).group(1)
        assert url.startswith("/assets/fonts/") and (ROOT / url.lstrip("/")).is_file(), url
    assert "http" not in re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_license_texts_travel_with_the_fonts():
    lic = (FONTS / "LICENSES.txt").read_text(encoding="utf-8")
    for name in ("Fraunces Project Authors", "Manrope Project Authors", "DM Mono Project Authors"):
        assert name in lic
    assert lic.count("SIL OPEN FONT LICENSE Version 1.1") == 3
    page = (FONTS / "licenses.html").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in page and "<h2>Fraunces</h2>" in page


def test_dm_mono_licence_has_no_reserved_font_name_and_plex_is_gone():
    lic = (FONTS / "LICENSES.txt").read_text(encoding="utf-8")
    dm = lic[lic.index("=== DM Mono ==="):]
    head = dm.split("This Font Software is licensed")[0]
    assert "Copyright 2020 The DM Mono Project Authors" in head and "Reserved Font Name" not in head
    assert "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007" in dm
    for p in _site_files(".html", ".css", ".js", ".py"):
        assert not re.search(r"plex", p.read_text(encoding="utf-8", errors="ignore"), re.I), p.name
    assert not list(FONTS.glob("*plex*"))
    assert "IBM" not in lic


def test_monospace_stack_falls_back_to_manrope_for_the_rupee_sign():
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    stack = re.search(r"--font-mono:\s*([^;]+);", css).group(1)
    assert stack.startswith('"DM Mono", "Manrope"')          # DM Mono's web subset has no U+20B9; Manrope's latin-ext does
