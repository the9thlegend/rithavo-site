"""
Vercel entrypoint for the rithavo.com backend (Phase 2B-1.5). Mirrors the
exact pattern already proven in production by the sibling
rithavo-career-profile project (app.rithavo.com) — adds the project root
to sys.path so the existing `app.*` package imports work unchanged, then
exposes an ASGI app for Vercel's Python builder to find.

The one piece of real logic here (not present in the sibling project,
which has no static site sharing its origin): Vercel's rewrite sends the
FULL original path (e.g. "/api/diagnosis/price") to this function, but
app.main's routes are registered without an "/api" prefix (e.g.
"/diagnosis/price") — main.py is intentionally NOT changed to add one,
since it's also used for local dev/testing with no "/api" concept at
all. This thin wrapper strips the leading "/api" from the incoming
ASGI scope before handing off to the real app, so the two stay in sync
without embedding deployment-specific routing into the backend itself.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.main import app as _app  # noqa: E402


async def app(scope, receive, send):
    if scope["type"] == "http" and scope["path"].startswith("/api"):
        scope = dict(scope)
        scope["path"] = scope["path"][len("/api"):] or "/"
    await _app(scope, receive, send)
