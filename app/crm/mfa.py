"""
CRM MFA service. All state (secrets, counters, locks, replay step, epoch) is in
the database so it is shared by every serverless instance. Nothing here keeps
state in process memory.
"""

import time

import config
from fastapi import HTTPException

from . import audit, locking, totp


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def get_row(conn, user_id):
    return conn.execute("SELECT * FROM crm_mfa WHERE user_id = ?", (user_id,)).fetchone()


def ensure_row(conn, user_id):
    row = get_row(conn, user_id)
    if row is None:
        conn.execute(
            "INSERT INTO crm_mfa (user_id, updated_at) VALUES (?, ?) ON CONFLICT (user_id) DO NOTHING",
            (user_id, _now_iso()))
        row = get_row(conn, user_id)
    return row


def is_enrolled(row) -> bool:
    return bool(row and row["secret_enc"])


def lock_state(row, now=None) -> str:
    now = int(time.time() if now is None else now)
    if row is None:
        return "none"
    if row["hard_locked"]:
        return "hard"
    if row["locked_until"] and row["locked_until"] > now:
        return "soft"
    return "none"


def _refuse_if_locked(row):
    state = lock_state(row)
    if state == "hard":
        raise HTTPException(status_code=423, detail="Locked. Ask the Founder to unlock your account.")
    if state == "soft":
        raise HTTPException(status_code=429, detail="Too many incorrect codes. Try again later.")


def _record_failure(db, user_id):
    """Committed on its own so the counter survives the 401 we raise afterwards."""
    with db.connect() as conn:
        conn.execute("UPDATE crm_mfa SET fail_count = fail_count + 1, updated_at = ? WHERE user_id = ?",
                     (_now_iso(), user_id))
        row = get_row(conn, user_id)
        n = row["fail_count"]
        if n >= config.CRM_MFA_HARD_LOCK_FAILURES:
            conn.execute("UPDATE crm_mfa SET hard_locked = 1 WHERE user_id = ?", (user_id,))
        elif n >= config.CRM_MFA_SOFT_LOCK_FAILURES:
            conn.execute("UPDATE crm_mfa SET locked_until = ? WHERE user_id = ?",
                         (int(time.time()) + config.CRM_MFA_SOFT_LOCK_SECONDS, user_id))
    return n


def _record_success(conn, user_id):
    conn.execute("UPDATE crm_mfa SET fail_count = 0, locked_until = 0, updated_at = ? WHERE user_id = ?",
                 (_now_iso(), user_id))


def start_enrolment(db, user_id, account_label):
    try:
        secret = totp.new_secret()
        enc = totp.encrypt_secret(secret)
    except totp.MfaKeyUnavailable:
        raise HTTPException(status_code=503, detail="MFA is not available on this deployment.")
    with db.connect() as conn:
        row = ensure_row(conn, user_id)
        if is_enrolled(row):
            raise HTTPException(status_code=409, detail="MFA is already set up.")
        _refuse_if_locked(row)
        conn.execute("UPDATE crm_mfa SET pending_secret_enc = ?, updated_at = ? WHERE user_id = ?",
                     (enc, _now_iso(), user_id))
    return {"secret": totp.b32(secret), "otpauth_uri": totp.otpauth_uri(secret, account_label)}


def confirm_enrolment(db, user_id, code):
    """Returns (recovery_codes, epoch). Codes are shown to the user once."""
    with db.connect() as conn:
        row = ensure_row(conn, user_id)
        if is_enrolled(row):
            raise HTTPException(status_code=409, detail="MFA is already set up.")
        _refuse_if_locked(row)
        pending = row["pending_secret_enc"]
    if not pending:
        raise HTTPException(status_code=409, detail="Start MFA setup first.")
    secret = totp.decrypt_secret(pending)
    step = totp.matching_step(secret, code)
    if step is None:
        _record_failure(db, user_id)
        raise HTTPException(status_code=401, detail="That code is not correct.")
    codes = totp.new_recovery_codes()
    with db.connect() as conn:
        locking.lock_person(conn, user_id)
        cur = conn.execute(
            "UPDATE crm_mfa SET secret_enc = ?, pending_secret_enc = NULL, enrolled_at = ?, last_used_step = ?, "
            "fail_count = 0, locked_until = 0, updated_at = ? WHERE user_id = ? AND secret_enc IS NULL",
            (pending, _now_iso(), step, _now_iso(), user_id))
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="MFA is already set up.")
        conn.execute("DELETE FROM crm_mfa_recovery_codes WHERE user_id = ?", (user_id,))
        for c in codes:
            conn.execute("INSERT INTO crm_mfa_recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, ?)",
                         (user_id, totp.recovery_hash(c), _now_iso()))
        epoch = get_row(conn, user_id)["session_epoch"]
    return codes, epoch


def verify_code(db, user_id, code):
    """Returns the session epoch on success. Raises 401/423/429 otherwise."""
    with db.connect() as conn:
        row = get_row(conn, user_id)
    if not is_enrolled(row):
        raise HTTPException(status_code=401, detail="MFA setup required.")
    _refuse_if_locked(row)
    secret = totp.decrypt_secret(row["secret_enc"])
    step = totp.matching_step(secret, code)
    if step is None:
        _record_failure(db, user_id)
        raise HTTPException(status_code=401, detail="That code is not correct.")
    with db.connect() as conn:
        # Atomic replay guard: only a strictly newer step wins.
        cur = conn.execute(
            "UPDATE crm_mfa SET last_used_step = ?, updated_at = ? WHERE user_id = ? AND last_used_step < ?",
            (step, _now_iso(), user_id, step))
        if cur.rowcount != 1:
            replay = True
        else:
            replay = False
            _record_success(conn, user_id)
            epoch = get_row(conn, user_id)["session_epoch"]
    if replay:
        _record_failure(db, user_id)
        raise HTTPException(status_code=401, detail="That code was already used. Wait for the next one.")
    return epoch


def use_recovery_code(db, user_id, code, *, role="specialist"):
    """A valid recovery code is single use. Everything below happens in ONE transaction, after `lock_person`:
    the authenticator and all remaining codes are cleared, the MFA epoch is bumped (every session of the person,
    on every instance, ends on its next request), the failure counters are reset, and a specialist who was active is
    returned to the inactive state (scope 2.8b) so no CRM data is readable until they re-enrol AND the Founder
    activates them again. Recovery never activates anyone. Returns
    {"epoch": new MFA epoch, "returned_to_inactive": bool}."""
    with db.connect() as conn:
        locking.lock_person(conn, user_id)
        row = get_row(conn, user_id)
        if not is_enrolled(row):
            raise HTTPException(status_code=401, detail="MFA setup required.")
        _refuse_if_locked(row)
        hashes = totp.recovery_hash_candidates(code)
        marks = ",".join("?" for _ in hashes)
        cur = conn.execute(
            f"UPDATE crm_mfa_recovery_codes SET used_at = ? WHERE user_id = ? AND used_at IS NULL "
            f"AND code_hash IN ({marks})", (_now_iso(), user_id, *hashes))
        ok = cur.rowcount == 1
        returned = False
        epoch = None
        if ok:
            reset_user(conn, user_id)                       # clears codes and authenticator, bumps the epoch
            epoch = get_row(conn, user_id)["session_epoch"]
            if role == "specialist":
                returned = return_to_inactive(conn, user_id)
            audit.write(conn, user_id, role, "mfa_recovery_used" if role == "specialist" else "founder_mfa_recovered",
                        "staff", user_id)
            if returned:
                audit.write(conn, user_id, role, "staff_returned_to_awaiting_activation", "staff", user_id)
    if not ok:
        _record_failure(db, user_id)
        raise HTTPException(status_code=401, detail="That recovery code is not valid.")
    return {"epoch": epoch, "returned_to_inactive": returned}


def return_to_inactive(conn, user_id):
    """Active specialist back to inactive after an MFA recovery or a Founder MFA reset. Returns True if it changed."""
    cur = conn.execute("UPDATE crm_staff SET active = 0, activated_by = NULL, activated_at = NULL "
                       "WHERE user_id = ? AND active = 1", (user_id,))
    return cur.rowcount == 1


def reset_user(conn, user_id, *, unlock_only=False):
    """Founder action (called inside the caller's transaction)."""
    ensure_row(conn, user_id)
    if unlock_only:
        conn.execute("UPDATE crm_mfa SET fail_count = 0, locked_until = 0, hard_locked = 0, updated_at = ? "
                     "WHERE user_id = ?", (_now_iso(), user_id))
        return
    conn.execute("DELETE FROM crm_mfa_recovery_codes WHERE user_id = ?", (user_id,))
    conn.execute(
        "UPDATE crm_mfa SET secret_enc = NULL, pending_secret_enc = NULL, enrolled_at = NULL, last_used_step = 0, "
        "fail_count = 0, locked_until = 0, hard_locked = 0, session_epoch = session_epoch + 1, updated_at = ? "
        "WHERE user_id = ?", (_now_iso(), user_id))
