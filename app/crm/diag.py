"""
Safe MFA diagnostics. One log line per MFA request, written to stdout so Vercel runtime logs capture it.

Logged: stage, internal user id, whether pending/enrolled state exists on THIS instance's database,
instance and database identity, outcome and an error category.
Never logged: setup secrets, one-time codes, recovery codes, cookies, passwords, request bodies.
Values are fixed tokens or integers, so nothing user-supplied can reach the line.
"""

import hashlib
import logging
import os
import secrets
import sys
import time

from fastapi import HTTPException

logger = logging.getLogger("rithavo_web.crm.diag")
logger.setLevel(logging.INFO)
if not any(getattr(h, "_crm_diag", False) for h in logger.handlers):
    _h = logging.StreamHandler(sys.stdout)
    _h._crm_diag = True
    _h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_h)

# One random token per process (= per serverless instance while it lives). Not derived from any secret.
INSTANCE_ID = secrets.token_hex(4)
_BOOT = time.time()


def db_identity(db) -> str:
    """Kind plus a short fingerprint of the concrete store, so two instances over two files differ."""
    if getattr(db, "_is_postgres", False):
        return "postgres"
    try:
        st = os.stat(db.db_path)
        raw = f"{db.db_path}|{getattr(st, 'st_ino', 0)}|{getattr(st, 'st_ctime_ns', 0)}"
        return "sqlite:" + hashlib.sha256(raw.encode()).hexdigest()[:8]
    except Exception:
        return "sqlite:unknown"


def category(exc) -> str:
    if not isinstance(exc, HTTPException):
        return "internal_error"
    d = exc.detail if isinstance(exc.detail, str) else ""
    if exc.status_code == 409 and d.startswith("Start MFA setup first"):
        return "no_pending_setup"
    if exc.status_code == 409:
        return "already_enrolled"
    if exc.status_code == 401 and "already used" in d:
        return "replayed_code"
    if exc.status_code == 401 and ("not correct" in d or "not valid" in d):
        return "invalid_code"
    if exc.status_code == 401 and "setup required" in d:
        return "not_enrolled"
    if exc.status_code in (423, 429):
        return "locked"
    if exc.status_code == 503:
        return "mfa_key_unavailable"
    if exc.status_code in (401, 403, 404):
        return "gate_" + str(exc.status_code)
    return "http_" + str(exc.status_code)


def state(db, user_id):
    """(pending, enrolled) as seen by this instance's database. Booleans only; never the values."""
    from . import mfa
    try:
        with db.connect() as conn:
            row = mfa.get_row(conn, user_id)
        return bool(row and row["pending_secret_enc"]), mfa.is_enrolled(row)
    except Exception:
        return None, None


def _b(v):
    return "unknown" if v is None else str(bool(v)).lower()


def log(stage, db, user_id, pending, enrolled, outcome, exc=None):
    cat = category(exc) if exc is not None else "none"
    logger.info(
        "crm_mfa_diag stage=%s user=%s pending=%s enrolled=%s instance=%s pid=%s uptime_s=%d db=%s outcome=%s category=%s",
        stage, user_id if user_id is not None else "none", _b(pending), _b(enrolled), INSTANCE_ID, os.getpid(),
        int(time.time() - _BOOT), db_identity(db), outcome, cat)
