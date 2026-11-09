"""Wiring for CRM Lite. The router is always mounted; the gate answers 404 for every
route while the flag is off. Schema creation happens only with the flag on, and only
on an isolated database (see schema.ensure_schema); on failure the CRM stays off."""

import logging

import config

from .routes import router
from .schema import ensure_schema

logger = logging.getLogger("rithavo_web.crm")


def init_crm(app) -> None:
    app.include_router(router)
    if not config.CRM_ENABLED:
        return
    try:
        ensure_schema(app.state.db)
    except Exception:
        logger.exception("CRM schema could not be prepared; CRM stays disabled.")
        config.CRM_ENABLED = False
        return
    if config.CRM_SEED_ENABLED and config.ISOLATED_DEPLOYMENT:
        import os
        from .seed import seed_synthetic
        try:
            seed_synthetic(app.state.db, os.environ.get("RITHAVO_CRM_SEED_PASSWORD", ""))
        except Exception:
            logger.exception("CRM synthetic seed skipped.")
