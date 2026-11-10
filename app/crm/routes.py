"""
Rithavo Nexus (CRM) HTTP API (`/crm/*`; deployed as `/api/crm/*`). Nothing here decides who may see
what: the gate (`access.require_*`) and the scope (`Scope`) do, once.

Every route declares exactly one dependency: `require_onboarding` (password-stage and MFA operations only),
`require_crm_actor` (active specialist or Founder, verified), `require_founder`, or is one of the deliberately
public sign-in and sign-out routes in PUBLIC_ROUTES. `classification()` exposes this so a test fails when a route
is added without one (default deny).

Status contract: 404 flag off or a record is not yours; 401 no CRM session (one uniform body, also for customer-only
sessions, deactivated people and removed Founders); 401 with a `code` for MFA stages (only after the password step);
403 `awaiting_activation` for a specialist who is not yet active; 403 for Founder-only actions; 429 rate limited.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from . import access, authflow, credentials, diag, mfa, ratelimit, session, store
from .access import Actor, require_crm_actor, require_founder, require_onboarding
from .source import source_bucket

logger = logging.getLogger("rithavo_web.crm")
router = APIRouter(prefix="/crm")

PUBLIC_ROUTES = {
    ("POST", "/crm/auth/login"), ("POST", "/crm/auth/accept"), ("POST", "/crm/auth/logout"),
    ("POST", "/crm/mfa/signout"),
}


def _db(request):
    return request.app.state.db


def _traced(stage, request, fn):
    """Run a handler body and emit one safe diagnostic line (see diag.py): stage, internal user id, pending/enrolled
    state on THIS instance's database, instance/db identity, outcome, error category. Never tokens or passwords."""
    db = _db(request)
    uid = session.uid(request)
    pending, enrolled = diag.state(db, uid) if uid else (None, None)
    try:
        out = fn()
    except Exception as e:
        diag.log(stage, db, uid, pending, enrolled, "error", e)
        raise
    diag.log(stage, db, uid, pending, enrolled, "ok")
    return out


def _stamp_cred(request):
    st = session.pre(request) or session.full(request)
    return st["cred"]


# ---- sign-in, invitation acceptance, password ---------------------------------

@router.post("/auth/login")
def auth_login(request: Request, email: str = Body(..., embed=True), password: str = Body(..., embed=True)):
    return _traced("login", request, lambda: authflow.login(request, _db(request), email, password))


@router.post("/auth/accept")
def auth_accept(request: Request, token: str = Body(..., embed=True), password: str = Body(..., embed=True)):
    return _traced("accept", request, lambda: authflow.accept(request, _db(request), token, password))


@router.post("/auth/logout")
def auth_logout(request: Request):
    session.clear(request)
    return {"ok": True}


@router.post("/auth/change-password")
def auth_change_password(request: Request, current: str = Body(..., embed=True), new: str = Body(..., embed=True),
                         actor: Actor = Depends(require_onboarding)):
    return _traced("change_password", request, lambda: authflow.change_password(request, _db(request), actor, current, new))


# ---- own status and MFA (reachable with the password stage only) ---------------

@router.get("/me")
def me(request: Request, actor: Actor = Depends(require_onboarding)):
    return _traced("me", request, lambda: _me(request, actor))


def _me(request, actor):
    with _db(request).connect() as conn:
        m = mfa.get_row(conn, actor.user_id)
    status = access.mfa_status(access._Ctx(actor.stage, None, None, None, m))
    if actor.role == "specialist" and actor.state != "active":
        return {"state": actor.state, "mfa": status}           # own state only: no names, roles, counts or data
    return {"user_id": actor.user_id, "email": actor.email, "role": actor.role, "name": actor.display_name,
            "state": actor.state, "mfa": status,
            "stages": store.STAGES, "lost_reasons": store.LOST_REASONS, "hold_reasons": store.HOLD_REASONS,
            "pillars": list(store.PILLARS), "sources": list(store.SOURCES)}


@router.post("/mfa/enrol/start")
def enrol_start(request: Request, actor: Actor = Depends(require_onboarding)):
    return _traced("enrol_start", request, lambda: mfa.start_enrolment(_db(request), actor.user_id, actor.email))


@router.post("/mfa/enrol/confirm")
def enrol_confirm(request: Request, code: str = Body(..., embed=True), actor: Actor = Depends(require_onboarding)):
    def run():
        codes, epoch = mfa.confirm_enrolment(_db(request), actor.user_id, code)
        session.stamp_full(request, actor.user_id, _stamp_cred(request), epoch)
        with _db(request).connect() as conn:
            store.audit(conn, actor, "mfa_enrolled", "staff", actor.user_id)
        return {"recovery_codes": codes}
    return _traced("enrol_confirm", request, run)


def _mfa_limited(request, fn):
    """Source limit for MFA verify and recovery (30 failures / 15 min); counts only failed codes."""
    db = _db(request)
    dims = [("mfa_source", source_bucket(request))]
    ratelimit.ready()
    ratelimit.enforce(db, dims)
    try:
        return fn()
    except HTTPException as e:
        if e.status_code == 401:
            ratelimit.fail(db, dims)
        raise


@router.post("/mfa/verify")
def verify(request: Request, code: str = Body(..., embed=True), actor: Actor = Depends(require_onboarding)):
    def run():
        try:
            epoch = _mfa_limited(request, lambda: mfa.verify_code(_db(request), actor.user_id, code))
        except Exception as e:
            with _db(request).connect() as conn:
                store.audit(conn, actor, "mfa_verify", "staff", actor.user_id, outcome="failed")
            raise e
        session.stamp_full(request, actor.user_id, _stamp_cred(request), epoch)
        with _db(request).connect() as conn:
            store.audit(conn, actor, "mfa_verify", "staff", actor.user_id)
        return {"ok": True}
    return _traced("verify", request, run)


@router.post("/mfa/recover")
def recover(request: Request, code: str = Body(..., embed=True), actor: Actor = Depends(require_onboarding)):
    """Recovery code (scope 2.8b). Only from the password stage. Afterwards ONLY this session's `crm_pre` stamp is
    updated to the new MFA epoch (its original expiry is kept, never extended); every other session of the person
    ends on its next request; the person must enrol and confirm a new authenticator, and a specialist must be
    activated again by the Founder before any CRM data is readable."""
    def run():
        if actor.stage != "pre":
            raise HTTPException(status_code=409, detail={"code": "use_password_stage",
                                                         "message": "Sign in again with your password to use a recovery code."})
        res = _mfa_limited(request, lambda: mfa.use_recovery_code(_db(request), actor.user_id, code, role=actor.role))
        session.update_pre_mfa_epoch(request, res["epoch"])
        return {"ok": True, "next": "enrol", "returned_to_inactive": res["returned_to_inactive"]}
    return _traced("recover", request, run)


@router.post("/mfa/signout")
def mfa_signout(request: Request):
    session.clear(request)
    return {"ok": True}


# ---- reads -------------------------------------------------------------------

@router.get("/overview")
def overview(request: Request, actor: Actor = Depends(require_crm_actor)):
    return store.overview(_db(request), actor)


@router.get("/opportunities")
def opportunities(request: Request, stage: str = None, actor: Actor = Depends(require_crm_actor)):
    return store.list_opportunities(_db(request), actor, stage=stage)


@router.get("/opportunities/{opp_id}")
def opportunity(request: Request, opp_id: int, actor: Actor = Depends(require_crm_actor)):
    return store.get_opportunity(_db(request), actor, opp_id)


@router.get("/companies")
def companies(request: Request, archived: bool = False, actor: Actor = Depends(require_crm_actor)):
    return store.list_companies(_db(request), actor, archived)


@router.get("/companies/{company_id}")
def company(request: Request, company_id: int, actor: Actor = Depends(require_crm_actor)):
    return store.get_company(_db(request), actor, company_id)


@router.get("/contacts")
def contacts(request: Request, actor: Actor = Depends(require_crm_actor)):
    return store.list_contacts(_db(request), actor)


@router.get("/followups")
def followups(request: Request, include_done: bool = False, actor: Actor = Depends(require_crm_actor)):
    return store.list_followups(_db(request), actor, include_done)


@router.get("/search")
def search(request: Request, q: str = "", actor: Actor = Depends(require_crm_actor)):
    return store.search(_db(request), actor, q)


@router.get("/reports")
def reports(request: Request, actor: Actor = Depends(require_crm_actor)):
    return store.reports(_db(request), actor)


@router.get("/review/no-contact")
def no_contact(request: Request, actor: Actor = Depends(require_crm_actor)):
    return store.no_contact_queue(_db(request), actor)


@router.get("/reviews/mine")
def my_reviews(request: Request, actor: Actor = Depends(require_crm_actor)):
    return store.my_review_status(_db(request), actor)


# ---- writes (owner-scoped inside the store) ----------------------------------

@router.post("/duplicate-check")
def duplicate_check(request: Request, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.check_duplicate(_db(request), actor, payload.get("company_name"),
                                 payload.get("contact_email"))


@router.post("/leads")
def capture(request: Request, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.capture_lead(_db(request), actor, payload)


@router.patch("/opportunities/{opp_id}")
def update_opp(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.update_opportunity(_db(request), actor, opp_id, payload)


@router.post("/opportunities/{opp_id}/stage")
def stage(request: Request, opp_id: int, to: str = Body(..., embed=True), actor: Actor = Depends(require_crm_actor)):
    return store.move_stage(_db(request), actor, opp_id, to)


@router.post("/opportunities/{opp_id}/activities")
def activity(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.add_activity(_db(request), actor, opp_id, payload)


@router.post("/opportunities/{opp_id}/followups")
def followup(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.add_followup(_db(request), actor, opp_id, payload)


@router.patch("/followups/{fu_id}")
def followup_update(request: Request, fu_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.update_followup(_db(request), actor, fu_id, payload)


@router.post("/opportunities/{opp_id}/lost")
def lost(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.set_lost(_db(request), actor, opp_id, payload.get("reason_code"),
                          payload.get("note"))


@router.post("/opportunities/{opp_id}/on-hold")
def on_hold(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.set_on_hold(_db(request), actor, opp_id, payload.get("reason_code"),
                             payload.get("note"), payload.get("hold_review_date"))


@router.post("/opportunities/{opp_id}/resume")
def resume(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.resume(_db(request), actor, opp_id, payload.get("note"))


@router.post("/opportunities/{opp_id}/commercial-review")
def commercial_review(request: Request, opp_id: int, payload: dict = Body(default={}), actor: Actor = Depends(require_crm_actor)):
    return store.request_commercial_review(_db(request), actor, opp_id, payload.get("note"))


@router.post("/opportunities/{opp_id}/request-archive")
def request_archive(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.request_archive(_db(request), actor, opp_id, payload.get("note"))


@router.post("/companies/{company_id}/contacts")
def add_contact(request: Request, company_id: int, payload: dict = Body(...), actor: Actor = Depends(require_crm_actor)):
    return store.add_contact(_db(request), actor, company_id, payload)


# ---- Founder only ------------------------------------------------------------

@router.get("/reviews")
def reviews(request: Request, actor: Actor = Depends(require_founder)):
    return store.review_queue(_db(request), actor)


@router.post("/reviews/{review_id}/decide")
def decide(request: Request, review_id: int, payload: dict = Body(...), actor: Actor = Depends(require_founder)):
    return store.decide_review(_db(request), actor, review_id, payload)


@router.post("/opportunities/{opp_id}/reopen")
def reopen(request: Request, opp_id: int, payload: dict = Body(...), actor: Actor = Depends(require_founder)):
    return store.reopen_lost(_db(request), actor, opp_id, payload.get("note"), payload.get("stage"))


@router.post("/companies/{company_id}/reassign")
def reassign(request: Request, company_id: int, owner_user_id: int = Body(..., embed=True), actor: Actor = Depends(require_founder)):
    return store.reassign_company(_db(request), actor, company_id, owner_user_id)


@router.post("/companies/{company_id}/archive")
def archive(request: Request, company_id: int, actor: Actor = Depends(require_founder)):
    return store.archive_company(_db(request), actor, company_id)


@router.get("/needs-owner")
def needs_owner(request: Request, actor: Actor = Depends(require_founder)):
    return store.needs_owner(_db(request), actor)


@router.get("/retention")
def retention(request: Request, actor: Actor = Depends(require_founder)):
    return store.retention_queue(_db(request), actor)


@router.get("/audit")
def audit_log(request: Request, actor: Actor = Depends(require_founder)):
    return store.audit_view(_db(request), actor)


@router.get("/export.csv")
def export(request: Request, actor: Actor = Depends(require_founder)):
    return PlainTextResponse(store.export_csv(_db(request), actor), media_type="text/csv")


@router.get("/staff")
def staff(request: Request, actor: Actor = Depends(require_founder)):
    return store.list_staff(_db(request), actor)


def _deliver(request, token, email):
    """Console sender (Preview, no mail provider) would print the body into logs, so the link is shown once to the
    Founder instead and never emailed or logged. With a real sender the link is emailed and not returned."""
    from app.email_sender import ConsoleEmailSender
    link = f"{request.url.scheme}://{request.url.netloc}/crm/#/accept?token={token}"
    sender = request.app.state.email_sender
    if isinstance(sender, ConsoleEmailSender):
        return {"delivery": "link", "link": link}
    try:
        sender.send(to=email, subject="Your Rithavo Nexus invitation",
                    body=("You have been invited to Rithavo Nexus. Use the link below to set your password. It can be used "
                          f"once and expires in 24 hours.\n\n{link}\n\nIf you did not expect this, ignore this email."))
    except Exception:
        logger.warning("crm_invite_email_failed")
        raise HTTPException(status_code=502, detail="We could not send the invitation email. Use Resend to try again.")
    return {"delivery": "email"}


@router.post("/staff/invite")
def staff_invite(request: Request, email: str = Body(..., embed=True), display_name: str = Body(..., embed=True),
                 actor: Actor = Depends(require_founder)):
    token, uid, em = store.invite_staff(_db(request), actor, email, display_name)
    return {"user_id": uid, **_deliver(request, token, em)}


@router.post("/staff/{user_id}/resend")
def staff_resend(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    token, uid, em = store.resend_invite(_db(request), actor, user_id)
    return {"user_id": uid, **_deliver(request, token, em)}


@router.post("/staff/{user_id}/revoke-invite")
def staff_revoke(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    return store.revoke_invite(_db(request), actor, user_id)


@router.post("/staff/{user_id}/activate")
def staff_activate(request: Request, user_id: int, credential_epoch: int = Body(..., embed=True),
                   mfa_epoch: int = Body(..., embed=True), actor: Actor = Depends(require_founder)):
    return store.activate_staff(_db(request), actor, user_id, credential_epoch, mfa_epoch)


@router.post("/staff/{user_id}/deactivate")
def deactivate(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    return store.deactivate_staff(_db(request), actor, user_id)


@router.post("/staff/{user_id}/reset-mfa")
def reset_mfa(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    return store.reset_staff_mfa(_db(request), actor, user_id)


@router.post("/staff/{user_id}/unlock")
def unlock(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    return store.reset_staff_mfa(_db(request), actor, user_id, unlock_only=True)


def classification():
    """{(METHOD, path): "active" | "founder" | "onboarding" | "public" | "unclassified"} for every CRM route."""
    out = {}
    for r in router.routes:
        calls = [d.call for d in r.dependant.dependencies]
        kind = ("founder" if require_founder in calls else "active" if require_crm_actor in calls
                else "onboarding" if require_onboarding in calls else "unclassified")
        for m in r.methods - {"HEAD", "OPTIONS"}:
            key = (m, "/crm" + r.path[len("/crm"):] if r.path.startswith("/crm") else r.path)
            out[key] = "public" if (kind == "unclassified" and key in PUBLIC_ROUTES) else kind
    return out
