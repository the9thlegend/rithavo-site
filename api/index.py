"""
Vercel entrypoint for the rithavo.com backend (Phase 2B-1.5). Mirrors the
exact pattern already proven in production by the sibling
rithavo-career-profile project (app.rithavo.com) — adds the project root
to sys.path so the existing `app.*` package imports work unchanged, then
exposes the FastAPI ASGI app for Vercel's Python builder to find.

This file is deployment plumbing only — it contains no logic of its own.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.main import app  # noqa: E402
