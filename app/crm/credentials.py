"""
CRM credentials: a password store that is independent of the customer accounts (scope 2.1).
The CRM never reads the customer accounts' password columns. Setting, changing or disabling a password increments `credential_epoch`,
which ends every CRM session for the person. Hashing reuses the application's existing helpers by import.
"""

from datetime import datetime, timezone

import config
from fastapi import HTTPException

from .. import auth

_COMMON = {
    "password", "password1", "password123", "passw0rd", "qwerty123", "letmein123", "welcome123", "iloveyou123",
    "123456789012", "1234567890123", "administrator", "changeme123", "rithavo123456", "rithavonexus", "nexus123456",
}
_DUMMY = {}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def check_policy(password, email=None):
    if not isinstance(password, str):
        raise HTTPException(status_code=422, detail="Password is not valid.")
    if len(password) < config.CRM_PASSWORD_MIN:
        raise HTTPException(status_code=422, detail=f"Password must be at least {config.CRM_PASSWORD_MIN} characters.")
    if len(password) > config.CRM_PASSWORD_MAX:
        raise HTTPException(status_code=422, detail="Password is too long.")
    low = password.lower()
    if email and low == email.strip().lower():
        raise HTTPException(status_code=422, detail="Password must not be your email address.")
    if low in _COMMON or len(set(low)) < 4:
        raise HTTPException(status_code=422, detail="That password is too easy to guess.")


def get(conn, user_id):
    return conn.execute("SELECT * FROM crm_credentials WHERE user_id = ?", (user_id,)).fetchone()


def ensure_row(conn, user_id):
    conn.execute("INSERT INTO crm_credentials (user_id, password_hash, credential_epoch) VALUES (?, NULL, 1) "
                 "ON CONFLICT (user_id) DO NOTHING", (user_id,))
    return get(conn, user_id)


def has_password(row):
    return bool(row and row["password_hash"])


def set_password(conn, user_id, password, set_by=None):
    """Store a new password and bump the epoch. Returns the new credential epoch."""
    ensure_row(conn, user_id)
    conn.execute("UPDATE crm_credentials SET password_hash = ?, credential_epoch = credential_epoch + 1, set_at = ?, "
                 "set_by = ? WHERE user_id = ?", (auth.hash_password(password), _now_iso(), set_by, user_id))
    return get(conn, user_id)["credential_epoch"]


def disable(conn, user_id):
    """No usable password until a new invitation is accepted; every session for the person ends."""
    ensure_row(conn, user_id)
    conn.execute("UPDATE crm_credentials SET password_hash = NULL, credential_epoch = credential_epoch + 1 "
                 "WHERE user_id = ?", (user_id,))


def verify(conn_or_row, password):
    """Constant-work check: a missing credential still costs a hash so timing does not reveal it."""
    row = conn_or_row
    h = row["password_hash"] if row is not None else None
    if not h:
        if "h" not in _DUMMY:
            _DUMMY["h"] = auth.hash_password("rithavo-crm-dummy-credential")
        auth.verify_password(password if isinstance(password, str) else "", _DUMMY["h"])
        return False
    return auth.verify_password(password if isinstance(password, str) else "", h)
