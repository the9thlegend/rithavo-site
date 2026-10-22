"""
Deployment exposure guard (local simulation).

vercel.json answers 404 for internal paths before the catch-all route can
serve them as static files. These tests evaluate the route patterns locally
with Python's `re`; they prove the rules are written and ordered as intended,
NOT how Vercel behaves. Real behaviour is checked against a Preview deployment.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["routes"]

INTERNAL_DIRS = ("design-lab", "ritanya-handoff", "tests", "app", ".claude")
PUBLIC_SUFFIXES = {".html", ".css", ".js", ".png", ".docx"}


def first_match(path: str):
    """The first route whose src fully matches (Vercel anchors src patterns)."""
    for route in ROUTES:
        if "has" in route:
            continue  # host-conditional www redirect; not relevant to path rules
        if re.fullmatch(route["src"], path):
            return route
    return None


def is_denied(path: str) -> bool:
    route = first_match(path)
    return bool(route) and route.get("status") == 404 and "dest" not in route


def test_deny_rules_sit_between_the_function_routes_and_the_catch_all():
    srcs = [r["src"] for r in ROUTES]
    deny = [i for i, r in enumerate(ROUTES) if r.get("status") == 404]
    assert len(deny) == 3
    assert all("dest" not in ROUTES[i] and "continue" not in ROUTES[i] for i in deny)
    assert srcs.index("/api/(.*)") < min(deny)
    assert srcs.index("/profile/edit(/.*)?") < min(deny)
    assert max(deny) < srcs.index("/(.*)", 1)


def test_internal_directories_and_files_are_denied():
    internal = [
        "/design-lab/ritanya/guide-assistant.html",
        "/design-lab/ritanya/portrait/ritanya-hero.webp",
        "/design-lab/", "/design-lab",
        "/ritanya-handoff/00-G0-PROVENANCE-AND-RIGHTS.md",
        "/tests/conftest.py", "/tests/test_ritanya_chat.py",
        "/app/main.py", "/app/ritanya_chat.py", "/app",
        "/.claude/launch.json",
        "/config.py", "/dev_server.py", "/run_dev_server.py", "/register_test_webhook.py",
        "/requirements.txt", "/vercel.json",
        "/RITANYA.md", "/DESIGN.md", "/RITANYA-PRODUCTION-MASTER-SPEC.md",
        "/storage/web_platform.db", "/anything/nested/secret.sql",
        "/.env", "/.env.production", "/.vercelignore", "/.gitignore",
    ]
    assert [p for p in internal if not is_denied(p)] == []


def test_public_pages_and_assets_are_not_denied():
    public = [
        "/", "/career/", "/career/index.html", "/about/", "/home/", "/profile/", "/sign-in/",
        "/styles.css", "/script.js", "/app-shell.js", "/app-shell.css", "/explore-feed.js",
        "/assets/hero/hero-final.png", "/CNAME",
        "/career/career-intelligence/demo/rithavo-demo-recommended-resume.docx",
        "/application-diagnosis/", "/career-intelligence/", "/mentorship/", "/privacy/", "/terms/",
        "/favicon.ico",
        # ordinary site files that must stay possible later
        "/robots.txt", "/sitemap.xml", "/manifest.json", "/.well-known/security.txt",
        "/.well-known/assetlinks.json", "/assets/data/example.json",
    ]
    assert [p for p in public if is_denied(p)] == []


def test_function_routes_are_never_shadowed():
    for path in ("/api/health", "/api/ritanya/chat", "/api/auth/start", "/api/analytics/event",
                 "/profile/edit", "/profile/edit/anything"):
        assert first_match(path)["dest"] == "api/index.py", path


def test_every_public_file_in_the_repo_is_still_served():
    """Walk the checkout: every site file outside the internal directories must reach the catch-all."""
    blocked = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if not p.is_file() or rel.parts[0] in INTERNAL_DIRS or rel.parts[0] in {".git", "storage", "__pycache__", ".vercel", "node_modules"}:
            continue
        if p.suffix.lower() in PUBLIC_SUFFIXES or p.name == "CNAME":
            if is_denied("/" + rel.as_posix()):
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
