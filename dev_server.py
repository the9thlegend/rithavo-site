"""
Phase 2B-1.7A — local verification only, not part of the deployed app.
Combines the static site and the /api FastAPI backend on ONE origin,
exactly mirroring vercel.json's routing (api/index.py's own /api prefix
stripping + root_path, then a static file passthrough) — so cookies,
fetch() calls, and redirects all behave the same way they do in
production. Not referenced by vercel.json/api/index.py; safe to delete
after verification.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("RITHAVO_WEB_DB_PATH", "./storage/dev_verify.db")
# Phase P0.2 local verification only: point the Card handoff at whatever
# local instance of the sibling app is running (see
# rithavo-career-profile/run_server.py, default port 8010) instead of
# the real https://app.rithavo.com — never used in production, where
# RITHAVO_CARD_APP_URL is left unset so config.py's real default applies.
os.environ.setdefault("RITHAVO_CARD_APP_URL", "http://127.0.0.1:8010")

from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles
from starlette.routing import Mount

from app.main import app as api_app

static_app = StaticFiles(directory=".", html=True)


async def app(scope, receive, send):
    if scope["type"] == "http" and scope["path"].startswith("/api"):
        scope = dict(scope)
        scope["path"] = scope["path"][len("/api"):] or "/"
        scope["root_path"] = "/api"
        await api_app(scope, receive, send)
    else:
        await static_app(scope, receive, send)
