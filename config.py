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

# Phase P0.2A correction — this used to fall back to a fixed insecure
# string when unset, which is exactly the "silently insecure" outcome
# a handoff secret must never have. No fallback of any kind now: unset
# means None, and every place that uses this (app/card_handoff.py's
# issue_card_handoff_token, POST /card/continue) must treat None as
# "the handoff is not configured" and refuse to operate — a 503, never
# a token signed with a value anyone could guess. Local dev servers set
# this themselves via os.environ.setdefault in their own launcher
# scripts (never here); tests set it explicitly via
# monkeypatch/env, also never by relying on a default in this file.
#
# The ONE secret deliberately shared with the sibling app, and only for
# this one narrow purpose: signing the short-lived, single-use
# "Profile -> Rithavo Card" handoff token so an already-authenticated
# rithavo.com user can reach their existing Rithavo Card without a
# second manual sign-in. Never SESSION_SECRET or RITHAVO_WEB_SESSION_SECRET,
# and never used for anything else. Must be set to the exact same value
# as the sibling app's own RITHAVO_CARD_HANDOFF_SECRET in production.
CARD_HANDOFF_SECRET = os.environ.get("RITHAVO_CARD_HANDOFF_SECRET")

# P0.5A-3 — a second, dedicated secret shared with the sibling app, for
# ONE narrow purpose: minting a short-lived, server-to-server-only token
# proving "this already-authenticated rithavo.com session belongs to
# users.id = N" so the sibling can hand back that person's own current
# Card photo (never anyone else's). Same no-fallback rule as
# CARD_HANDOFF_SECRET, and a DIFFERENT value from it in production.
# Never reaches the browser: minted and consumed entirely inside one
# server-to-server call from this service's own backend
# (app/photo_access.py).
PHOTO_ACCESS_SECRET = os.environ.get("RITHAVO_PHOTO_ACCESS_SECRET")

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

# Home/Explore/Admin Integration Phase — rithavo.com is the one
# canonical customer-facing surface; app.rithavo.com is now an
# internal-only boundary this service's own backend calls
# server-to-server (never from a browser). Same no-fallback rule as
# CARD_HANDOFF_SECRET/PHOTO_ACCESS_SECRET above: unset means None, and
# every caller must treat that as "not configured" and refuse to
# operate, never fall back to an insecure default. Must be set to the
# exact same value as the sibling's own RITHAVO_INTERNAL_SERVICE_SECRET
# in production.
INTERNAL_SERVICE_SECRET = os.environ.get("RITHAVO_INTERNAL_SERVICE_SECRET")

# Where the sibling's internal service API actually lives — same
# service, same host as CARD_APP_BASE_URL, but named for its own
# purpose so the two are never confused when read independently.
INTERNAL_SERVICE_BASE_URL = os.environ.get("RITHAVO_INTERNAL_SERVICE_URL", CARD_APP_BASE_URL)

# One dedicated Rithavo Super Admin account. Bootstrapped idempotently
# at startup (see app/super_admin.py) only when BOTH are set — a
# missing password never creates a passwordless admin account. The
# plaintext password is read once, hashed immediately via the existing
# hash_password (same scrypt KDF as ordinary user passwords), and never
# logged, stored, or reachable again in plaintext form.
SUPER_ADMIN_EMAIL = os.environ.get("RITHAVO_SUPER_ADMIN_EMAIL")
SUPER_ADMIN_PASSWORD = os.environ.get("RITHAVO_SUPER_ADMIN_PASSWORD")

# Pre-Launch Product Lock phase — the one centralized launch-date
# configuration point (see app/launch_lock.py for how it's used). Must
# be an ISO 8601 datetime, e.g. "2026-10-15T00:00:00+05:30". Unset (the
# default, and the correct state until a real date is provided) means
# "no launch date configured yet" — app/launch_lock.py treats that as
# LOCKED, never as "launch already happened." This is a hard business
# rule: CI/AD purchases must never become purchasable merely because
# this value was left unset by accident.
LAUNCH_DATE = os.environ.get("RITHAVO_LAUNCH_DATE")

if not DATABASE_URL:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
