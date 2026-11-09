"""
The CRM gate and the one data-access scope.

Order of checks on every CRM request (each re-reads the database; the signed
cookie only carries stamps, never authority):

  flag on -> signed in (401) -> role: Founder or ACTIVE staff (403)
  -> MFA enrolled and session verified (401 with a machine code)
  -> not locked -> epoch matches -> absolute 8 h -> idle 30 min -> password
     unchanged since the MFA stamp.

Foreign records are a plain 404 (never 403) via `Scope.fetch`.
"""

import time
from dataclasses import dataclass

import config
from fastapi import HTTPException, Request

from . import mfa


@dataclass
class Actor:
    user_id: int
    role: str          # "founder" | "specialist"
    email: str
    display_name: str
    mfa_ok: bool = False

    @property
    def is_founder(self) -> bool:
        return self.role == "founder"


def _db(request: Request):
    return request.app.state.db


def _err(status, detail, code=None):
    body = {"detail": detail}
    if code:
        body["code"] = code
    return HTTPException(status_code=status, detail=body if code else detail)


def crm_enabled_or_404():
    if not config.CRM_ENABLED:
        raise HTTPException(status_code=404, detail="Not found.")


def resolve_actor(request: Request) -> Actor:
    """Signed-in user -> Founder / active specialist, else 401/403. No MFA check."""
    crm_enabled_or_404()
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not signed in.")
    db = _db(request)
    user = db.get_user_by_id(user_id)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not signed in.")
    if db.is_super_admin(user_id):
        return Actor(user_id, "founder", user["email"], "Founder")
    with db.connect() as conn:
        staff = conn.execute("SELECT * FROM crm_staff WHERE user_id = ?", (user_id,)).fetchone()
    if staff is None or not staff["active"]:
        raise HTTPException(status_code=403, detail="CRM access has not been activated for this account.")
    return Actor(user_id, "specialist", user["email"], staff["display_name"])


def mfa_state(request: Request, actor: Actor):
    """Returns (state, epoch) where state is one of
    enrol | verify | locked_soft | locked_hard | ok."""
    db = _db(request)
    with db.connect() as conn:
        row = mfa.get_row(conn, actor.user_id)
        user = conn.execute("SELECT password_updated_at FROM users WHERE id = ?", (actor.user_id,)).fetchone()
    if not mfa.is_enrolled(row):
        return "enrol", None
    lock = mfa.lock_state(row)
    if lock == "hard":
        return "locked_hard", row["session_epoch"]
    if lock == "soft":
        return "locked_soft", row["session_epoch"]
    stamp = request.session.get("crm")
    if not isinstance(stamp, dict):
        return "verify", row["session_epoch"]
    now = int(time.time())
    pw_now = (user["password_updated_at"] if user is not None else None) or ""
    valid = (
        stamp.get("epoch") == row["session_epoch"]
        and now - int(stamp.get("at", 0)) <= config.CRM_SESSION_ABSOLUTE_SECONDS
        and now - int(stamp.get("last", 0)) <= config.CRM_SESSION_IDLE_SECONDS
        and (stamp.get("pw") or "") == pw_now
        and stamp.get("uid") == actor.user_id
    )
    if not valid:
        request.session.pop("crm", None)
        return "verify", row["session_epoch"]
    return "ok", row["session_epoch"]


def stamp_session(request: Request, actor: Actor, epoch: int):
    db = _db(request)
    with db.connect() as conn:
        user = conn.execute("SELECT password_updated_at FROM users WHERE id = ?", (actor.user_id,)).fetchone()
    now = int(time.time())
    request.session["crm"] = {
        "uid": actor.user_id, "epoch": epoch, "at": now, "last": now,
        "pw": (user["password_updated_at"] if user is not None else None) or "",
    }


def require_crm_actor(request: Request) -> Actor:
    """The gate for every CRM data route."""
    actor = resolve_actor(request)
    state, _epoch = mfa_state(request, actor)
    if state == "ok":
        # Reassign (not mutate in place): Starlette only re-issues the cookie when the session is marked modified.
        request.session["crm"] = {**request.session["crm"], "last": int(time.time())}
        actor.mfa_ok = True
        return actor
    codes = {"enrol": "mfa_enrol_required", "verify": "mfa_required",
             "locked_soft": "mfa_locked", "locked_hard": "mfa_locked_hard"}
    status = {"enrol": 401, "verify": 401, "locked_soft": 429, "locked_hard": 423}[state]
    raise _err(status, "Multi-factor authentication is required.", codes[state])


def require_founder(request: Request) -> Actor:
    actor = require_crm_actor(request)
    if not actor.is_founder:
        audit_denied(_db(request), actor, "founder_only", None, None)
        raise HTTPException(status_code=403, detail="Founder only.")
    return actor


def audit_denied(db, actor, action, entity, entity_id):
    """Denials are written on their own connection, so the audit row survives the
    exception that rolls back the request's transaction."""
    from datetime import datetime, timezone
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO crm_audit_log (at, actor_user_id, actor_role, action, entity, entity_id, outcome, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, 'denied', NULL)",
                (datetime.now(timezone.utc).isoformat(), actor.user_id if actor else None,
                 actor.role if actor else None, action, entity, entity_id))
    except Exception:
        pass


class Scope:
    """THE data-access scope. Founder sees everything; a specialist sees rows whose
    owner_user_id is theirs. Routes never write their own owner filters."""

    def __init__(self, db, actor: Actor):
        self.db = db
        self.actor = actor

    def clause(self, alias: str = ""):
        if self.actor.is_founder:
            return "1=1", ()
        p = f"{alias}." if alias else ""
        return f"{p}owner_user_id = ?", (self.actor.user_id,)

    def fetch(self, conn, table: str, row_id: int, entity: str):
        """Fetch-then-check. Missing and foreign are indistinguishable (404); a foreign hit
        is audited as a denial."""
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found.")
        if not self.actor.is_founder and row["owner_user_id"] != self.actor.user_id:
            audit_denied(self.db, self.actor, "foreign_record", entity, row_id)
            raise HTTPException(status_code=404, detail="Not found.")
        return row
