"""
CRM Lite HTTP API (`/crm/*`; deployed as `/api/crm/*`). Nothing here decides who may see
what: the gate (`require_crm_actor`) and the scope (`Scope`) do, once.

Status contract: 404 when the flag is off or a record is not yours; 401 not signed in or MFA not
satisfied (body carries `code`); 403 signed in but no CRM role, or Founder-only action.
"""

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import PlainTextResponse

from . import access, diag, mfa, store
from .access import Actor, require_crm_actor, require_founder, resolve_actor

router = APIRouter(prefix="/crm")


def _db(request):
    return request.app.state.db


# ---- session / MFA (reachable before MFA is verified) ------------------------

def _traced(stage, request, fn):
    """Run an MFA handler body and emit one safe diagnostic line (see diag.py): stage, internal user id,
    pending/enrolled state on THIS instance's database, instance/db identity, outcome, error category."""
    db = _db(request)
    uid = request.session.get("user_id")
    pending, enrolled = diag.state(db, uid) if uid else (None, None)
    try:
        out = fn()
    except Exception as e:
        diag.log(stage, db, uid, pending, enrolled, "error", e)
        raise
    diag.log(stage, db, uid, pending, enrolled, "ok")
    return out


@router.get("/me")
def me(request: Request):
    return _traced("me", request, lambda: _me(request))


def _me(request: Request):
    actor = resolve_actor(request)
    state, _ = access.mfa_state(request, actor)
    return {"user_id": actor.user_id, "email": actor.email, "role": actor.role, "name": actor.display_name, "mfa": state,
            "stages": store.STAGES, "lost_reasons": store.LOST_REASONS, "hold_reasons": store.HOLD_REASONS,
            "pillars": list(store.PILLARS), "sources": list(store.SOURCES)}


@router.post("/mfa/enrol/start")
def enrol_start(request: Request):
    def run():
        actor = resolve_actor(request)
        return mfa.start_enrolment(_db(request), actor.user_id, actor.email)
    return _traced("enrol_start", request, run)


@router.post("/mfa/enrol/confirm")
def enrol_confirm(request: Request, code: str = Body(..., embed=True)):
    def run():
        actor = resolve_actor(request)
        codes, epoch = mfa.confirm_enrolment(_db(request), actor.user_id, code)
        access.stamp_session(request, actor, epoch)
        with _db(request).connect() as conn:
            store.audit(conn, actor, "mfa_enrolled", "staff", actor.user_id)
        return {"recovery_codes": codes}
    return _traced("enrol_confirm", request, run)


@router.post("/mfa/verify")
def verify(request: Request, code: str = Body(..., embed=True)):
    def run():
        actor = resolve_actor(request)
        try:
            epoch = mfa.verify_code(_db(request), actor.user_id, code)
        except Exception as e:
            with _db(request).connect() as conn:
                store.audit(conn, actor, "mfa_verify", "staff", actor.user_id, outcome="failed")
            raise e
        access.stamp_session(request, actor, epoch)
        with _db(request).connect() as conn:
            store.audit(conn, actor, "mfa_verify", "staff", actor.user_id)
        return {"ok": True}
    return _traced("verify", request, run)


@router.post("/mfa/recover")
def recover(request: Request, code: str = Body(..., embed=True)):
    actor = resolve_actor(request)
    mfa.use_recovery_code(_db(request), actor.user_id, code)
    request.session.pop("crm", None)
    with _db(request).connect() as conn:
        store.audit(conn, actor, "mfa_recovery_used", "staff", actor.user_id)
    return {"ok": True, "next": "enrol"}


@router.post("/mfa/signout")
def mfa_signout(request: Request):
    resolve_actor(request)
    request.session.pop("crm", None)
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


@router.post("/staff/activate")
def activate(request: Request, email: str = Body(..., embed=True), display_name: str = Body(..., embed=True), actor: Actor = Depends(require_founder)):
    return store.activate_staff(_db(request), actor, email, display_name)


@router.post("/staff/{user_id}/deactivate")
def deactivate(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    return store.deactivate_staff(_db(request), actor, user_id)


@router.post("/staff/{user_id}/reset-mfa")
def reset_mfa(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    return store.reset_staff_mfa(_db(request), actor, user_id)


@router.post("/staff/{user_id}/unlock")
def unlock(request: Request, user_id: int, actor: Actor = Depends(require_founder)):
    return store.reset_staff_mfa(_db(request), actor, user_id, unlock_only=True)
