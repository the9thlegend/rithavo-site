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
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Preview / Development isolation. Vercel sets VERCEL_ENV to "production",
# "preview" or "development" for every deployment. A Preview deployment must
# NEVER open the production database: before this guard, Preview functions
# read the same RITHAVO_WEB_DATABASE_URL name and ran init_schema() against
# the live Supabase project. Here a non-production Vercel deployment ignores
# RITHAVO_WEB_DATABASE_URL entirely and uses either an explicitly separate
# RITHAVO_PREVIEW_DATABASE_URL (opt-in) or a throwaway SQLite file in the
# temp directory (/tmp on Vercel, the only writable path). Only an exact
# "preview"/"development" value isolates, so production and local runs
# (VERCEL_ENV unset or "production") behave exactly as before.
ISOLATED_DEPLOYMENT = os.environ.get("VERCEL_ENV") in ("preview", "development")

from urllib.parse import urlparse as _urlparse


class PreviewDatabaseUrlError(RuntimeError):
    """The Preview database URL is unusable. The message names a category only: never the value, the user or the host."""


# The Production Supabase project reference (not a secret) is refused outright for any Preview database URL.
_FORBIDDEN_PROJECT_REFS = {"todgmhddihfgqeofzudb"}
PREVIEW_APP_ROLE = "crm_app"


def validate_preview_database_url(raw):
    """Validate and normalise RITHAVO_PREVIEW_DATABASE_URL for a pre-provisioned Preview database.

    Accepted: a PostgreSQL URL (`postgresql://` or `postgres://`), optionally with accidental leading/trailing
    whitespace, which is trimmed. Rejected, with a category-only error and never silently treated as SQLite:
    empty, surrounding quotes, internal whitespace (shell prefixes such as `psql ...`), any other scheme or a bare
    file path, a malformed URL, the Production project, and any role other than `crm_app`."""
    def fail(category):
        raise PreviewDatabaseUrlError(
            f"RITHAVO_PREVIEW_DATABASE_URL is not usable ({category}). Expected a postgresql:// URL for the "
            f"{PREVIEW_APP_ROLE} role, with no quotes or shell prefix.")

    if raw is None or not str(raw).strip():
        fail("missing_or_empty")
    url = str(raw).strip()
    if url[0] in "\"'`" or url[-1] in "\"'`":
        fail("surrounded_by_quotes")
    if any(ch.isspace() for ch in url):
        fail("contains_whitespace_or_shell_prefix")
    if not url.startswith(("postgresql://", "postgres://")):
        fail("scheme_is_not_postgresql")
    try:
        parsed = _urlparse(url)
        _ = parsed.port
    except ValueError:
        fail("malformed_url")
    if not parsed.hostname:
        fail("malformed_url_no_host")
    if not parsed.password:
        fail("malformed_url_no_password")
    if not (parsed.path or "").lstrip("/"):
        fail("malformed_url_no_database")
    user = parsed.username or ""
    refs = {user.rsplit(".", 1)[-1]} if "." in user else set()
    host = parsed.hostname
    if host.startswith("db.") and host.endswith(".supabase.co"):
        refs.add(host[3:-len(".supabase.co")])
    if refs & _FORBIDDEN_PROJECT_REFS:
        fail("production_project")
    if user != PREVIEW_APP_ROLE and not user.startswith(PREVIEW_APP_ROLE + "."):
        fail("role_must_be_crm_app")
    return url


if ISOLATED_DEPLOYMENT:
    _raw_preview_url = os.environ.get("RITHAVO_PREVIEW_DATABASE_URL")  # never the production URL
    if os.environ.get("RITHAVO_PREVIEW_DB_PREPROVISIONED") == "1":
        # Pre-provisioned mode never falls back to SQLite: a bad value stops start-up with a clear error.
        DATABASE_URL = validate_preview_database_url(_raw_preview_url)
    else:
        DATABASE_URL = _raw_preview_url
    DB_PATH = Path(os.environ.get("RITHAVO_WEB_DB_PATH", str(Path(tempfile.gettempdir()) / "rithavo_preview.db")))
else:
    DB_PATH = Path(os.environ.get("RITHAVO_WEB_DB_PATH", "./storage/web_platform.db"))
    DATABASE_URL = os.environ.get("RITHAVO_WEB_DATABASE_URL")  # shared Postgres, prod only

if ISOLATED_DEPLOYMENT and DATABASE_URL:
    # Fail closed if the "separate" Preview database is really the production one (same server,
    # port, database and user; a shared pooler host differs by user, so a different Supabase
    # project is accepted).
    def _target(url):
        parsed = _urlparse(url)
        return (parsed.hostname, parsed.port, (parsed.path or "").lstrip("/"), parsed.username)

    _production_url = os.environ.get("RITHAVO_WEB_DATABASE_URL")
    if _production_url and _target(_production_url) == _target(DATABASE_URL):
        raise RuntimeError("RITHAVO_PREVIEW_DATABASE_URL must not point at the production database")

    # A different role in the SAME Supabase project would pass the comparison above (the pooler username
    # differs), so the project reference is checked too (the `.<ref>` suffix of the pooler username or the
    # `db.<ref>.supabase.co` host).
    _p = _urlparse(DATABASE_URL)
    _refs = {(_p.username or "").rsplit(".", 1)[-1] if "." in (_p.username or "") else ""}
    _h = _p.hostname or ""
    if _h.startswith("db.") and _h.endswith(".supabase.co"):
        _refs.add(_h[3:-len(".supabase.co")])
    if _refs & _FORBIDDEN_PROJECT_REFS:
        raise RuntimeError("RITHAVO_PREVIEW_DATABASE_URL must not point at the production Supabase project")

# The Preview database is provisioned out of band (tables, role, policies). When this is set, the application
# connects as a non-owner role, never runs DDL at start-up, and refuses to start the CRM unless PostgreSQL itself
# confirms the role is least-privilege. Honoured only on an isolated deployment; its URL was validated above.
DB_PREPROVISIONED = bool(ISOLATED_DEPLOYMENT and DATABASE_URL and os.environ.get("RITHAVO_PREVIEW_DB_PREPROVISIONED") == "1")

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

# One-domain rule: the Rithavo Card is served from another host (app.rithavo.com), so the cross-host handoff
# (POST /card/continue, which hands the browser a token and that host's address) is OFF unless this is set. Off,
# no customer-facing page or response sends anyone to another Rithavo host. Setting it does not bring the Card to
# rithavo.com; it only re-enables the old handoff.
CARD_HANDOFF_ENABLED = os.environ.get("RITHAVO_CARD_HANDOFF_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

# Phase 2B-1.5 finding (production smoke test): the session cookie was
# missing the Secure flag — harmless for local HTTP dev, a real gap once
# this serves real HTTPS traffic. Tied to DATABASE_URL (the same signal
# already used above to distinguish "real deployment" from "local dev")
# rather than a separate env var, so it can never be forgotten when
# pointing this service at production and never wrongly forces Secure
# during local http://127.0.0.1 development or the SQLite-backed test
# suite (both would silently drop the cookie, breaking every session).
SESSION_COOKIE_HTTPS_ONLY = bool(DATABASE_URL) or ISOLATED_DEPLOYMENT  # previews are served over HTTPS

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


# ---- CRM Lite (Preview build) ----------------------------------------------
# Default OFF. With the flag unset every /crm/* route answers 404, the CRM
# schema is never created, and nothing else in the application changes. The
# schema is additionally refused on any non-isolated (Production) deployment:
# Production gets it only from a separately approved migration file.
CRM_ENABLED = os.environ.get("RITHAVO_CRM_ENABLED") == "1"
# One or more comma-separated Fernet keys (first encrypts, all decrypt).
# Never logged. Absent in Production => MFA enrolment refuses (fail closed).
CRM_MFA_ENCRYPTION_KEY = os.environ.get("RITHAVO_WEB_MFA_ENCRYPTION_KEY")
CRM_SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60
CRM_SESSION_IDLE_SECONDS = 30 * 60
CRM_MFA_SOFT_LOCK_FAILURES = 5
CRM_MFA_SOFT_LOCK_SECONDS = 15 * 60
CRM_MFA_HARD_LOCK_FAILURES = 10
# Synthetic seed (Preview/dev only; refuses anywhere else).
CRM_SEED_ENABLED = os.environ.get("RITHAVO_CRM_SEED") == "1"

# ---- CRM independent authentication (Preview build; see CRM scope Revision 3) ------------------------------
# Founder identity = Super Admin row AND an email on this allowlist (comma-separated). Never inherited from /admin/.
CRM_FOUNDER_EMAILS = tuple(
    e.strip().lower() for e in os.environ.get("RITHAVO_CRM_FOUNDER_EMAILS", "").split(",") if e.strip())
CRM_PASSWORD_MIN = 12
CRM_PASSWORD_MAX = 200
CRM_PRE_SECONDS = 10 * 60                 # absolute life of the password-only (pre-MFA) stage
CRM_INVITE_TTL_SECONDS = 24 * 60 * 60
CRM_BOOTSTRAP_TTL_SECONDS = 30 * 60
# Dedicated rate-limiter HMAC key (base64url, >= 32 random bytes). No default and no generated fallback: when it is
# missing or invalid CRM authentication is unavailable (fails closed). PREVIOUS exists only during a rotation window.
CRM_RL_HMAC_KEY = os.environ.get("RITHAVO_CRM_RL_HMAC_KEY")
CRM_RL_HMAC_KEY_PREVIOUS = os.environ.get("RITHAVO_CRM_RL_HMAC_KEY_PREVIOUS")
# Vercel sets VERCEL=1 in the process environment; a request cannot influence it.
ON_VERCEL = os.environ.get("VERCEL") == "1"
# (failures, window seconds) per scope and dimension. These values govern (scope document 2.5).
CRM_LIMITS = {
    "login_pair":        (5, 15 * 60),
    "login_account":     (20, 15 * 60),
    "login_source":      (20, 15 * 60),
    "invite_source":     (10, 15 * 60),
    "invite_token":      (20, 15 * 60),
    "bootstrap_source":  (5, 30 * 60),
    "bootstrap_global":  (10, 30 * 60),
    "mfa_source":        (30, 15 * 60),
    "password_account":  (5, 15 * 60),
}
