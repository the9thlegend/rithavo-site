"""
The CRM gate and the one data-access scope.

CRM authentication is independent of the customer site (scope 2): the gate reads only the CRM session keys
(`crm_pre`, `crm`), never the public `user_id`, and re-reads the database on every request. The signed cookie
carries stamps only (user id, credential epoch, MFA epoch, times), never authority.

Two dependencies, and every route must declare one (see routes.CLASSIFICATION):
  require_onboarding  a valid password-only (`crm_pre`) or verified (`crm`) stage for a Founder or a specialist who
                      is credentialed, awaiting activation or active. Used ONLY for the onboarding and MFA operations.
  require_crm_actor   a verified `crm` stage AND the Founder, or a specialist in the active state. Every data,
                      search, export, report, audit and staff-management route.
A specialist who is not yet active (invited, credentialed, awaiting activation) gets 403 `awaiting_activation` on
anything behind require_crm_actor, with no data. Anonymous callers, customer-only sessions, deactivated people and
removed Founders all get the same 401 "Not signed in.".

Foreign records are a plain 404 (never 403) via `Scope.fetch`.
"""

import time
from dataclasses import dataclass

import config
from fastapi import HTTPException, Request

from starlette.concurrency import run_in_threadpool

from . import audit, credentials, identity, mfa, rls, session


@dataclass
class Actor:
    user_id: int
    role: str          # "founder" | "specialist"
    email: str
    display_name: str
    mfa_ok: bool = False
    state: str = "active"
    stage: str = "full"        # "pre" (password only) or "full" (password and MFA)

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


class _Ctx:
    def __init__(self, stage, stamp, ident, cred, mfa_row):
        self.stage, self.stamp, self.ident, self.cred, self.mfa = stage, stamp, ident, cred, mfa_row


def _deny(request):
    session.clear(request)
    raise HTTPException(status_code=401, detail="Not signed in.")


def load(request: Request) -> _Ctx:
    """Validate the CRM session against the database. Raises the uniform 401 on anything wrong."""
    crm_enabled_or_404()
    full, pre = session.full(request), session.pre(request)
    stage, stamp = ("full", full) if full else (("pre", pre) if pre else (None, None))
    if stamp is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    with _db(request).connect() as conn:
        ident = identity.resolve(conn, stamp["uid"])
        cred = credentials.get(conn, stamp["uid"])
        m = mfa.get_row(conn, stamp["uid"])
    if ident is None or ident.role is None or ident.state in ("deactivated", "invited"):
        _deny(request)
    if not credentials.has_password(cred) or stamp["cred"] != cred["credential_epoch"]:
        _deny(request)
    now = int(time.time())
    epoch = m["session_epoch"] if m is not None else 1
    if stamp["mfa"] != epoch:
        _deny(request)
    if stage == "pre":
        if now - stamp["at"] > config.CRM_PRE_SECONDS:
            _deny(request)
    else:
        if now - stamp["at"] > config.CRM_SESSION_ABSOLUTE_SECONDS or now - stamp["last"] > config.CRM_SESSION_IDLE_SECONDS:
            _deny(request)
        if not mfa.is_enrolled(m):
            _deny(request)
    return _Ctx(stage, stamp, ident, cred, m)


def _actor(ctx: _Ctx) -> Actor:
    i = ctx.ident
    return Actor(i.user_id, i.role, i.email, i.display_name, mfa_ok=(ctx.stage == "full"), state=i.state or "active",
                 stage=ctx.stage)


def mfa_status(ctx: _Ctx) -> str:
    """enrol | verify | locked_soft | locked_hard | ok (own state; shown only after the password step)."""
    lock = mfa.lock_state(ctx.mfa)
    if lock == "hard":
        return "locked_hard"
    if lock == "soft":
        return "locked_soft"
    if not mfa.is_enrolled(ctx.mfa):
        return "enrol"
    return "ok" if ctx.stage == "full" else "verify"


def _gate_onboarding(request: Request) -> Actor:
    ctx = load(request)
    if ctx.stage == "full":
        session.touch(request)
    return _actor(ctx)


def _gate_data(request: Request) -> Actor:
    ctx = load(request)
    i = ctx.ident
    if i.role == "specialist" and i.state != "active":
        route = request.scope.get("route")
        audit.write_own(_db(request), i.user_id, "specialist", "denied_awaiting_activation",
                        getattr(route, "name", None) or request.url.path, None, "denied", None)
        raise _err(403, "Your account is waiting for Founder activation.", "awaiting_activation")
    lock = mfa.lock_state(ctx.mfa)
    if lock == "hard":
        raise _err(423, "Locked. Ask the Founder to unlock your account.", "mfa_locked_hard")
    if lock == "soft":
        raise _err(429, "Too many incorrect codes. Try again later.", "mfa_locked")
    if not mfa.is_enrolled(ctx.mfa):
        raise _err(401, "Multi-factor authentication is required.", "mfa_enrol_required")
    if ctx.stage != "full":
        raise _err(401, "Multi-factor authentication is required.", "mfa_required")
    session.touch(request)
    return _actor(ctx)


async def require_onboarding(request: Request) -> Actor:
    return await run_in_threadpool(_gate_onboarding, request)


async def require_crm_actor(request: Request) -> Actor:
    """The gate for every CRM data route. Async on purpose: the blocking checks run in a worker thread, then the
    authenticated caller is recorded in this request's context so that every later database transaction in the
    same request carries it into PostgreSQL (see rls.install)."""
    actor = await run_in_threadpool(_gate_data, request)
    rls.set_actor(actor)
    return actor


async def require_founder(request: Request) -> Actor:
    actor = await require_crm_actor(request)
    if not actor.is_founder:
        audit_denied(_db(request), actor, "founder_only", None, None)
        raise HTTPException(status_code=403, detail="Founder only.")
    return actor


def audit_denied(db, actor, action, entity, entity_id):
    """Denials are written on their own connection, so the audit row survives the
    exception that rolls back the request's transaction."""
    audit.write_own(db, actor.user_id if actor else None, actor.role if actor else None, action, entity, entity_id,
                    "denied", None)


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
