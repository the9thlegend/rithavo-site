"""
Rithavo Web Platform — the new rithavo.com backend (Phase 0).

Deliberately a separate codebase, separate deployment, separate credentials
from `rithavo-career-profile` (which is what actually runs as
app.rithavo.com). This service is a new, additional consumer of the same
shared Postgres database in production — it never imports, calls, or
modifies anything in the other codebase, and nothing here ever touches
app.rithavo.com's own deployment, config, or environment.

Local/dev default is SQLite, same as the sibling project, for exactly the
same reason: zero setup, and no risk of a stray local run touching real
data. Production points RITHAVO_WEB_DATABASE_URL at the shared Supabase
Postgres — a deliberately different env var name from the sibling
project's RITHAVO_DATABASE_URL, so the two services' configs can never be
confused with each other even if both are ever set in the same shell.
"""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("RITHAVO_WEB_DB_PATH", "./storage/web_platform.db"))
DATABASE_URL = os.environ.get("RITHAVO_WEB_DATABASE_URL")  # shared Postgres, prod only

# Phase 1 correction (Phase 0.75 finding): the shared Supabase pooler does
# NOT enforce TLS server-side — it will accept sslmode=disable just as
# happily as sslmode=require. psycopg2's own default (sslmode=prefer)
# happens to negotiate TLS when available, but "happens to" is not a
# guarantee. This service pins sslmode=require explicitly, in code, so
# encryption in transit is never contingent on a client library default —
# never sslmode=prefer, never sslmode=disable. See Database._get_pg_pool.
DATABASE_SSLMODE = "require"

# Connection pool + per-connection timeout — a network hiccup to Supabase
# should fail fast, not hang a request indefinitely.
DATABASE_CONNECT_TIMEOUT_SECONDS = 10
DATABASE_POOL_MIN = 1
DATABASE_POOL_MAX = 5

# Own session secret — never shared with, derived from, or read out of the
# sibling app's SESSION_SECRET. A session issued here is meaningless to
# app.rithavo.com and vice versa; the only thing the two services ever
# share is the row in the `users` table a given email resolves to.
SESSION_SECRET = os.environ.get("RITHAVO_WEB_SESSION_SECRET", secrets.token_hex(32))

# Phase P0.2 — the ONE secret deliberately shared with the sibling app,
# and only for this one narrow purpose: signing the short-lived,
# single-use "Profile -> Rithavo Card" handoff token (app/card_handoff.py)
# so an already-authenticated rithavo.com user can reach their existing
# Rithavo Card without a second manual sign-in. Never SESSION_SECRET or
# RITHAVO_WEB_SESSION_SECRET, and never used for anything else. Must be
# set to the exact same value as the sibling app's own
# RITHAVO_CARD_HANDOFF_SECRET in production — the fallback below is for
# local dev/tests only and is never safe to leave unset in production
# (two different random per-process values would simply make every
# handoff token fail signature verification on the other side, not a
# silent security hole, but still worth calling out explicitly).
CARD_HANDOFF_SECRET = os.environ.get(
    "RITHAVO_CARD_HANDOFF_SECRET", "insecure-dev-only-card-handoff-secret"
)

# Where the sibling app actually lives, so the handoff's auto-submitted
# form has somewhere to POST to. Overridable for local dev against a
# locally-running sibling instance.
CARD_APP_BASE_URL = os.environ.get("RITHAVO_CARD_APP_URL", "https://app.rithavo.com")

# Phase 2B-1.5 finding (production smoke test): the session cookie was
# missing the Secure flag — harmless for local HTTP dev, a real gap once
# this serves real HTTPS traffic. Tied to DATABASE_URL (the same signal
# already used above to distinguish "real deployment" from "local dev")
# rather than a separate env var, so it can never be forgotten when
# pointing this service at production and never wrongly forces Secure
# during local http://127.0.0.1 development or the SQLite-backed test
# suite (both would silently drop the cookie, breaking every session).
SESSION_COOKIE_HTTPS_ONLY = bool(DATABASE_URL)

if not DATABASE_URL:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
