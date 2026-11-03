"""
Deployment exposure guard (local simulation).

vercel.json serves only an allowlist of public paths as static files: URLs
with no dot in them (pages / directories), a fixed set of asset extensions,
and a few named root files. Everything else, including the internal
directories, is handed to the Python function, which answers 404 for any path
it has no route for.

Shapes that were tried first and FAILED on real Preview deployments, so they
are not used:
  * a status-only `"status": 404` route (sets the status, still returns the file);
  * a `dest` that does not exist (the file is still served);
  * a deny-list by extension (`/config.py/` with a trailing slash still served the file).

These tests evaluate the route patterns locally with Python's `re`; they prove
the rules are written and ordered as intended, NOT how Vercel behaves. Real
behaviour is checked against a Preview deployment.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["routes"]

FUNCTION = "api/index.py"
STATIC = "/$1"
FUNCTION_SRCS = {"/api/(.*)", "/profile/edit(/.*)?"}  # the real function routes
INTERNAL_DIRS = ("design-lab", "ritanya-handoff", "tests", "app", ".claude")
PUBLIC_SUFFIXES = {".html", ".css", ".js", ".png", ".docx", ".webmanifest"}


def first_match(path: str):
    """The first route whose src fully matches (Vercel anchors src patterns)."""
    for route in ROUTES:
        if "has" in route:
            continue  # host-conditional www redirect; not relevant to path rules
        if re.fullmatch(route["src"], path):
            return route
    return None


def served_as_file(path: str) -> bool:
    route = first_match(path)
    return bool(route) and route.get("dest") == STATIC


def goes_to_function(path: str) -> bool:
    route = first_match(path)
    return bool(route) and route.get("dest") == FUNCTION


def test_rule_shape_and_order():
    srcs = [r["src"] for r in ROUTES]
    assert not any(r.get("status") == 404 and "dest" not in r for r in ROUTES)  # status-only 404 leaks bodies
    assert all("continue" not in r for r in ROUTES)
    assert srcs[1:4] == ["/api/(.*)", "/profile/edit(/.*)?", "/(.*%.*)"]  # the percent-encoding rule sits right after the function routes
    assert ROUTES[-1] == {"src": "/(.*)", "dest": FUNCTION}  # anything not allowlisted ends at the function
    internal_rule = next(i for i, r in enumerate(ROUTES) if "design-lab" in r["src"])
    first_static = next(i for i, r in enumerate(ROUTES) if r.get("dest") == STATIC)
    assert 2 < internal_rule < first_static  # the internal-directory rule is evaluated before any static rule


def test_internal_paths_and_bypass_variants_never_reach_the_file_system():
    internal = [
        "/design-lab/ritanya/guide-assistant.html", "/design-lab/ritanya/portrait/ritanya-hero.webp",
        "/design-lab/", "/design-lab", "/ritanya-handoff/00-G0-PROVENANCE-AND-RIGHTS.md",
        "/tests/conftest.py", "/app/main.py", "/app/ritanya_chat.py", "/app", "/.claude/launch.json",
        "/config.py", "/dev_server.py", "/run_dev_server.py", "/register_test_webhook.py",
        "/requirements.txt", "/vercel.json", "/RITANYA.md", "/DESIGN.md", "/RITANYA-PRODUCTION-MASTER-SPEC.md",
        "/storage/web_platform.db", "/anything/nested/secret.sql", "/.env", "/.env.production",
        "/.vercelignore", "/.gitignore",
        # variants seen or likely in the wild
        "/config.py/", "/config.py//", "/requirements.txt/", "/vercel.json/", "/.env/", "/CONFIG.PY", "/Config.PY/",
        "/config.py/x", "/notes.md/", "/x.py.txt", "/app/../config.py", "//app/main.py", "/%61pp/main.py",
        "/%2e%2e/config.py", "/./config.py", "/a/./b/../config.py", "/config.py%20", "/config.py;x",
    ]
    assert [p for p in internal if served_as_file(p)] == []
    assert [p for p in internal if not goes_to_function(p)] == []


def test_public_pages_and_assets_are_served():
    public = [
        "/", "/career", "/career/", "/career/index.html", "/about/", "/home/", "/profile/", "/sign-in/",
        "/styles.css", "/script.js", "/app-shell.js", "/app-shell.css", "/explore-feed.js",
        "/assets/hero/hero-final.png", "/CNAME", "/index.html",
        "/career/career-intelligence/demo/rithavo-demo-recommended-resume.docx",
        "/career/career-intelligence/demo/", "/application-diagnosis/", "/career-intelligence/",
        "/mentorship/", "/privacy/", "/terms/", "/reset-password/", "/onboarding/",
        "/favicon.ico", "/robots.txt", "/sitemap.xml", "/img/logo.svg", "/fonts/x.woff2",
    ]
    assert [p for p in public if not served_as_file(p)] == []


def test_unlisted_file_types_are_not_served():
    for path in ("/manifest.json", "/data.json", "/notes.txt", "/archive.zip", "/x.map", "/.well-known/security.txt"):
        assert goes_to_function(path), path


def test_function_routes_are_never_shadowed():
    assert first_match("/api/health")["src"] == "/api/(.*)"
    assert first_match("/profile/edit")["src"] == "/profile/edit(/.*)?"
    for path in ("/api/health", "/api/ritanya/chat", "/api/auth/start", "/api/analytics/event",
                 "/profile/edit", "/profile/edit/anything", "/api/diagnosis/price"):
        assert first_match(path)["dest"] == FUNCTION, path
        assert first_match(path)["src"] in FUNCTION_SRCS, path


def test_every_public_file_in_the_repo_is_still_served():
    """Walk the checkout: every site file outside the internal directories must be served."""
    blocked = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if not p.is_file() or rel.parts[0] in INTERNAL_DIRS or rel.parts[0] in {".git", "storage", "__pycache__", ".vercel", "node_modules"}:
            continue
        if p.suffix.lower() in PUBLIC_SUFFIXES or p.name == "CNAME":
            if not served_as_file("/" + rel.as_posix()):
                blocked.append(rel.as_posix())
    assert blocked == []


def test_vercelignore_never_lists_what_the_function_or_build_needs():
    lines = [l.strip() for l in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    needed_by_the_function = ("app", "api", "config.py", "requirements.txt", "vercel.json", "assets", "career")
    for line in lines:
        bare = line.strip("/")
        assert bare not in needed_by_the_function, line
        assert not line.startswith(("*", "!")), line  # no blanket or negated patterns
    for internal in ("/design-lab/", "/ritanya-handoff/", "/tests/", "/.claude/"):
        assert internal in lines, internal


def test_the_function_has_no_route_for_any_denied_path(app_and_client):
    """Denied paths are handed to the function; it must answer 404 and never a file or page."""
    _, client = app_and_client
    for path in ("/config.py", "/config.py/", "/app/main.py", "/app", "/tests/conftest.py", "/requirements.txt",
                 "/vercel.json", "/design-lab/", "/ritanya-handoff/x.md", "/.claude/launch.json", "/.env",
                 "/dev_server.py", "/manifest.json", "/notes.txt"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "import " not in response.text and "RITHAVO_" not in response.text, path


def test_pwa_files_are_served_as_static_files():
    for path in ("/manifest.webmanifest", "/sw.js", "/pwa.js", "/pwa.css", "/offline/", "/offline",
                 "/assets/pwa/icon-192.png", "/assets/pwa/icon-512.png", "/assets/pwa/icon-maskable-512.png",
                 "/assets/pwa/apple-touch-icon.png"):
        assert served_as_file(path), path


def test_only_the_manifest_extension_was_added_to_the_allowlist():
    for path in ("/manifest.json", "/sw.js.map", "/pwa.py", "/tools/make_pwa_icons.py", "/manifest.webmanifest.bak",
                 "/x.webmanifest.py", "/notes.webmanifest/x"):
        assert not served_as_file(path), path

def _deployed_source_files():
    """Files that are part of the deployment and must never be readable as static files."""
    names = ["config.py", "requirements.txt", "vercel.json", "api/index.py", ".vercelignore"]
    names += [p.relative_to(ROOT).as_posix() for p in (ROOT / "app").glob("*.py")]
    return names


def test_percent_encoded_dots_and_slashes_never_reach_the_file_system():
    """Regression: on 2026-11-03 /config%2Epy and /app%2Fmain%2Epy returned the backend source with 200. The
    dot-free allowlist rule matched them (a literal '.' is absent) and the host decoded %2E when it looked the
    file up. Every encoded spelling of every deployed source file must go to the function, which answers 404."""
    spellings = []
    for name in _deployed_source_files():
        for dot in ("%2E", "%2e", "%252E"):
            for slash in ("/", "%2F", "%2f", "%252F"):
                spellings.append("/" + name.replace(".", dot).replace("/", slash))
    spellings += ["/config%2Epy/", "/%2e%2e/config.py", "/assets/%2e%2e/config.py", "/assets%2F..%2Fconfig%2Epy",
                  "/index%2Ehtml", "/styles%2Ecss"]
    assert [p for p in spellings if served_as_file(p)] == []
    assert [p for p in spellings if not goes_to_function(p)] == []


def test_no_public_file_needs_a_percent_sign_in_its_url():
    """The percent-encoding rule is safe only if no real public URL contains '%'."""
    offenders = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if not p.is_file() or rel.parts[0] in INTERNAL_DIRS or rel.parts[0] in {".git", "storage", "__pycache__", ".vercel", "node_modules"}:
            continue
        if "%" in rel.as_posix() and p.suffix.lower() in PUBLIC_SUFFIXES:
            offenders.append(rel.as_posix())
    assert offenders == []
