"""
Single-use invitation and Founder-bootstrap tokens (scope 2.4).
A token is 256 random bits; only its SHA-256 hash is stored. Consumption is one atomic conditional UPDATE
(unused, not revoked, not expired, expected purpose). The raw token is returned once to the caller and is never
logged, audited or stored.
"""

import hashlib
import secrets
import time
from datetime import datetime, timezone

STAFF_INVITE = "staff_invite"
FOUNDER_BOOTSTRAP = "founder_bootstrap"


def new_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now():
    return int(time.time())


def issue(conn, *, user_id, email, purpose, created_by, ttl, token=None):
    """Revoke any open token of the same purpose for this person, store the hash, return the raw token."""
    revoke_open(conn, user_id, purpose)
    token = token or new_token()
    conn.execute(
        "INSERT INTO crm_invites (token_hash, user_id, email, purpose, created_by, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (hash_token(token), user_id, email.strip().lower(), purpose, created_by,
         datetime.now(timezone.utc).isoformat(), _now() + int(ttl)))
    return token


def revoke_open(conn, user_id, purpose=None):
    sql = "UPDATE crm_invites SET revoked_at = ? WHERE user_id = ? AND used_at IS NULL AND revoked_at IS NULL"
    params = [_now(), user_id]
    if purpose:
        sql += " AND purpose = ?"
        params.append(purpose)
    return conn.execute(sql, tuple(params)).rowcount


def revoke_all_open(conn, purpose):
    return conn.execute("UPDATE crm_invites SET revoked_at = ? WHERE purpose = ? AND used_at IS NULL "
                        "AND revoked_at IS NULL", (_now(), purpose)).rowcount


def peek(conn, token, purposes):
    """The open invitation for this token, or None (unknown, used, revoked, expired, wrong purpose: all alike)."""
    if not isinstance(token, str) or not token or len(token) > 200:
        return None
    marks = ",".join("?" for _ in purposes)
    return conn.execute(
        f"SELECT * FROM crm_invites WHERE token_hash = ? AND used_at IS NULL AND revoked_at IS NULL "
        f"AND expires_at > ? AND purpose IN ({marks})", (hash_token(token), _now(), *purposes)).fetchone()


def consume(conn, token, purposes):
    """Atomically mark the token used. Returns the invitation row, or None if it was not consumable."""
    if not isinstance(token, str) or not token or len(token) > 200:
        return None
    marks = ",".join("?" for _ in purposes)
    cur = conn.execute(
        f"UPDATE crm_invites SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND revoked_at IS NULL "
        f"AND expires_at > ? AND purpose IN ({marks})", (_now(), hash_token(token), _now(), *purposes))
    if cur.rowcount != 1:
        return None
    return conn.execute("SELECT * FROM crm_invites WHERE token_hash = ?", (hash_token(token),)).fetchone()


def accepted_staff_invite(conn, user_id):
    return conn.execute(
        "SELECT 1 FROM crm_invites WHERE user_id = ? AND purpose = ? AND used_at IS NOT NULL AND revoked_at IS NULL",
        (user_id, STAFF_INVITE)).fetchone() is not None
