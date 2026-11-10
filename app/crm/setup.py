"""Wiring for CRM Lite (Rithavo Nexus). The router is always mounted; the gate answers 404 for every
route while the flag is off. Schema creation happens only with the flag on, and only
on an isolated database (see schema.ensure_schema); on failure the CRM stays off.

On a pre-provisioned Preview database (config.DB_PREPROVISIONED) nothing is created at start-up. The application
must be connected as a non-owner, non-superuser, non-BYPASSRLS role with the ownership policies installed AND the
authentication tables, policies, grants and identity marker present, otherwise the CRM stays off (fail closed), and
every transaction carries the authenticated caller into PostgreSQL (rls.install).

In every mode the CRM also refuses to run without a valid rate-limiter key and a non-empty Founder allowlist."""

import logging

import config

from . import diag, ratelimit, rls
from .routes import router
from .schema import ensure_schema

logger = logging.getLogger("rithavo_web.crm")


def _connected_project_ref():
    """The Supabase project reference of the configured connection (pooler username suffix or db.<ref> host)."""
    from urllib.parse import urlparse
    try:
        u = urlparse(config.DATABASE_URL or "")
    except Exception:
        return None
    user = u.username or ""
    if "." in user:
        return user.rsplit(".", 1)[-1]
    host = u.hostname or ""
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return host[3:-len(".supabase.co")]
    return None


def _preflight():
    ratelimit.validate()                       # RateLimitKeyError("rate_limit_key_invalid") fails closed
    if not config.CRM_FOUNDER_EMAILS:
        raise rls.LeastPrivilegeError("founder_allowlist_missing")


def init_crm(app) -> None:
    app.include_router(router)
    if not config.CRM_ENABLED:
        return
    db = app.state.db
    try:
        _preflight()
        if config.DB_PREPROVISIONED:
            verified = rls.assert_least_privilege(db)   # PostgreSQL confirms the role; raises otherwise (never SQLite)
            rls.assert_auth_objects(db, expected_ref=_connected_project_ref())
            rls.install(db)
            diag.boot(db, provisioned=True, verified=verified)
        else:
            ensure_schema(db)
            diag.boot(db, provisioned=False)
    except Exception as e:
        # Category only: database errors can echo host or role names, so no traceback and no exception text
        # unless it is one of our own category-only errors.
        ours = (rls.LeastPrivilegeError, config.PreviewDatabaseUrlError, ratelimit.RateLimitKeyError)
        safe = str(e) if isinstance(e, ours) else type(e).__name__
        logger.error("CRM stays disabled: %s", safe)
        config.CRM_ENABLED = False
        return
    if config.CRM_SEED_ENABLED and config.ISOLATED_DEPLOYMENT:
        import os
        from .seed import seed_synthetic
        try:
            seed_synthetic(db, os.environ.get("RITHAVO_CRM_SEED_PASSWORD", ""))
        except Exception:
            logger.exception("CRM synthetic seed skipped.")
