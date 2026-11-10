"""
Per-person serialisation for every state-changing CRM account operation (scope 2.8a).

Fixed lock order, always: staff row, then credential row, then MFA row. On PostgreSQL each is `SELECT ... FOR UPDATE`;
on SQLite the equivalent is `BEGIN IMMEDIATE` (one writer). Callers start their transaction with `lock_person`.
A lock wait longer than the role's lock_timeout surfaces as `LockBusy` (mapped to a 409 busy_retry by the routes).
"""

from fastapi import HTTPException

ORDER = ("crm_staff", "crm_credentials", "crm_mfa")


class LockBusy(HTTPException):
    def __init__(self):
        super().__init__(status_code=409, detail={"code": "busy_retry", "message": "Busy. Please try again."})


def lock_person(conn, user_id):
    from . import credentials, mfa   # imported here: mfa itself uses this module
    if getattr(conn, "_conn", None) is None:          # SQLite (tests, local)
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass                                       # a transaction is already open and holds the write lock
        credentials.ensure_row(conn, user_id)
        mfa.ensure_row(conn, user_id)
        return
    try:
        credentials.ensure_row(conn, user_id)
        mfa.ensure_row(conn, user_id)
        conn.execute("SELECT user_id FROM crm_staff WHERE user_id = ? FOR UPDATE", (user_id,)).fetchall()
        conn.execute("SELECT user_id FROM crm_credentials WHERE user_id = ? FOR UPDATE", (user_id,)).fetchall()
        conn.execute("SELECT user_id FROM crm_mfa WHERE user_id = ? FOR UPDATE", (user_id,)).fetchall()
    except Exception as e:                             # psycopg2.errors.LockNotAvailable / QueryCanceled
        name = type(e).__name__
        if name in ("LockNotAvailable", "QueryCanceled", "DeadlockDetected"):
            raise LockBusy()
        raise
