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

if not DATABASE_URL:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
