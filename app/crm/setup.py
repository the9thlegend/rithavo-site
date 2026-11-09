"""Wiring for CRM Lite. The router is always mounted; the gate answers 404 for every
route while the flag is off. Schema creation happens only with the flag on, and only
on an isolated database (see schema.ensure_schema); on failure the CRM stays off.

On a pre-provisioned Preview database (config.DB_PREPROVISIONED) nothing is created at start-up. The application
must be connected as a non-owner, non-superuser, non-BYPASSRLS role with the ownership policies installed, otherwise
the CRM stays off (fail closed), and every transaction carries the authenticated caller into PostgreSQL (rls.install)."""

import logging

import config

from . import diag, rls
from .routes import router
from .schema import ensure_schema

logger = logging.getLogger("rithavo_web.crm")


def init_crm(app) -> None:
    app.include_router(router)
    if not config.CRM_ENABLED:
        return
    db = app.state.db
    try:
        if config.DB_PREPROVISIONED:
            rls.assert_least_privilege(db)   # raises unless role is least-privilege and the tables/policies exist
            rls.install(db)
            diag.boot(db, provisioned=True)
        else:
            ensure_schema(db)
            diag.boot(db, provisioned=False)
    except Exception:
        logger.exception("CRM schema/role could not be verified or prepared; CRM stays disabled.")
        config.CRM_ENABLED = False
        return
    if config.CRM_SEED_ENABLED and config.ISOLATED_DEPLOYMENT:
        import os
        from .seed import seed_synthetic
        try:
            seed_synthetic(db, os.environ.get("RITHAVO_CRM_SEED_PASSWORD", ""))
        except Exception:
            logger.exception("CRM synthetic seed skipped.")
