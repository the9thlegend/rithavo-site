"""
Rithavo Web Platform — Phase 0.

New rithavo.com backend. Foundation only: identity, session, and the
authorization layer, plus the two new additive profile tables (education,
experience_entries). No payment, no Application Diagnosis, no resume
generation yet — those are later phases, gated on this foundation being
proven safe first.

This service never imports from, calls into, or otherwise depends on the
sibling `rithavo-career-profile` codebase (app.rithavo.com) at runtime.
The only thing connecting them, in production, is a shared row in the
`users` table when the same email is used on both.
"""

import json
import logging
import uuid

from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

import config
from app.auth import MagicLinkError, issue_magic_link_token, verify_and_consume_magic_link_token
from app.career_intelligence import get_career_intelligence_view, resume_inputs_for_view
from app.db import Database
from app.diagnosis_engine import InvalidJobDescriptionError, cta_for_verdict, evaluate, verdict_for_score
from app.email_sender import get_email_sender
from app.payment_gateway import get_gateway
from app.rate_limit import RateLimiter
from app.resume_export import build_tailored_resume, render_tailored_resume_docx, TailoredResume
from app.security import (
    owned_career_profile, owned_diagnosis, owned_education, owned_entitlement,
    owned_experience_entry, owned_purchase, owned_resume, require_user,
)

logger = logging.getLogger("rithavo_web.diagnosis")

app = FastAPI(title="Rithavo Web Platform")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, https_only=config.SESSION_COOKIE_HTTPS_ONLY)

app.state.db = Database(config.DATABASE_URL or config.DB_PATH)
app.state.db.init_schema()
app.state.email_sender = get_email_sender()
app.state.payment_gateway = get_gateway()
app.state.auth_rate_limiter = RateLimiter()


def _row_to_dict(row):
    return dict(row) if row is not None else None


@app.get("/")
def index():
    return {"service": "rithavo-web-platform", "status": "phase-0"}


# ---- auth ----

@app.post("/auth/start")
def auth_start(request: Request, email: str = Form(...)):
    normalized_email = email.strip().lower()
    if not request.app.state.auth_rate_limiter.allow(normalized_email):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in requests for this email — please wait a few minutes and try again.",
        )
    db = request.app.state.db
    token = issue_magic_link_token(db, config.SESSION_SECRET, email)
    link = f"{request.base_url}auth/verify?token={token}"
    body = (
        "Hi,\n\n"
        "Use the link below to sign in to your Rithavo account:\n\n"
        f"{link}\n\n"
        "This link expires in 15 minutes and can only be used once. If you "
        "didn't request this, you can safely ignore this email — no one can "
        "access your account without clicking it.\n\n"
        "Need help? Reply to this email or reach us at hello@rithavo.com.\n\n"
        "— Rithavo"
    )
    try:
        request.app.state.email_sender.send(
            to=normalized_email,
            subject="Your Rithavo sign-in link",
            body=body,
        )
    except Exception:
        # Never leak provider internals (host, credentials, SMTP response
        # text) to the client, and never log the token or the link itself
        # — only that a send attempt failed, which is enough to diagnose
        # from server-side provider logs.
        logger.warning("auth_start email_send_failed to_domain=%s", normalized_email.split("@")[-1])
        raise HTTPException(
            status_code=502,
            detail="We couldn't send your sign-in email right now. Please try again in a moment.",
        )
    return {"status": "sent"}


@app.get("/auth/verify")
def auth_verify(request: Request, token: str):
    db = request.app.state.db
    try:
        email = verify_and_consume_magic_link_token(db, config.SESSION_SECRET, token)
    except MagicLinkError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    user_id = db.get_or_create_user(email)
    request.session["user_id"] = user_id
    # Phase 2B-1.7A: the destination is now a static customer-facing page
    # (rithavo-site/home/index.html), not this API's own /profile route —
    # per the brief, /api/profile stays an API endpoint and is never
    # rendered as HTML. Unlike the Phase 2B-1.6 fix (request.url_for,
    # which correctly resolves an *API* route under root_path="/api" in
    # production), a static route is never under /api at all, so the
    # target must be built from the request's origin only — scheme +
    # netloc, deliberately ignoring root_path — never a hardcoded
    # "https://rithavo.com", so this keeps working unprefixed against
    # local dev/tests (http://testserver/home/) and correctly prefix-free
    # in production (https://rithavo.com/home/, even though this request
    # itself arrived at /api/auth/verify).
    destination = f"{request.url.scheme}://{request.url.netloc}/home/"
    return RedirectResponse(destination, status_code=302)


@app.post("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return {"status": "logged_out"}


# ---- profile (read-only in Phase 0 — shared table, owned by the sibling app) ----

@app.get("/profile", name="profile")
def get_profile(request: Request):
    db = request.app.state.db
    session_user_id = require_user(request)
    profile = owned_career_profile(db, session_user_id, session_user_id)
    return {"user_id": session_user_id, "career_profile": _row_to_dict(profile)}


@app.get("/career-intelligence")
def get_career_intelligence(request: Request):
    """Phase 2B-1.7A: read-only presentation of the caller's existing
    Career Intelligence results — see app/career_intelligence.py's
    module docstring for exactly what is a verbatim read versus the two
    small pieces of assembly this does itself. Never runs, reruns, or
    modifies anything in the sibling app's CI engine."""
    db = request.app.state.db
    session_user_id = require_user(request)
    return get_career_intelligence_view(db, session_user_id)


@app.post("/career-intelligence/resume")
def create_career_intelligence_resume(request: Request):
    """Phase 2B-1.7A Task 2: "Improve My Resume" (Stay) / "Create My
    Resume for [Target]" (New Role / New Industry / Major Transition).

    Deliberately touches NOTHING in the commerce layer — no entitlement
    check, no purchase, no consumption of anything — a Career
    Intelligence resume is not gated by this service's own Application
    Diagnosis credit system at all (points H/I of the phase brief).
    Uses the exact same build_tailored_resume/render_tailored_resume_docx
    pipeline as an Application Diagnosis resume (point D: reusing this
    service's own already-approved resume architecture, not the sibling
    app's), so /resume/{id}/download works for a CI resume for free —
    same ownership check, same immutable/versioned INSERT-only pattern.
    """
    db = request.app.state.db
    session_user_id = require_user(request)

    view = get_career_intelligence_view(db, session_user_id)
    resume_inputs = resume_inputs_for_view(view)
    if resume_inputs is None:
        raise HTTPException(
            status_code=422,
            detail="You need a Career Intelligence assessment before Rithavo can build you a resume for it.",
        )

    profile_json = _profile_json(db, session_user_id) or {}
    capabilities = db.list_active_capabilities_for_user(session_user_id)
    education = db.list_education_for_user(session_user_id)
    experience = db.list_experience_for_user(session_user_id)

    resume = build_tailored_resume(
        profile_json, capabilities, education, experience,
        jd_text=resume_inputs["target_label"], matched_keywords=resume_inputs["matched_keywords"],
    )
    existing_count = len(db.list_resumes_for_user_and_target(session_user_id, resume.target_context))
    version = existing_count + 1
    resume_id = db.create_resume_for_career_intelligence(
        session_user_id, resume.to_dict(),
        label=f"Resume for {resume.target_context or resume_inputs['target_label']} — v{version}",
        resume_context=resume_inputs["resume_context"],
    )
    return {"resume_id": resume_id, "version": version, "resume_context": resume_inputs["resume_context"]}


@app.get("/me")
def get_me(request: Request):
    """Phase 2B-1.7A: the minimum an authenticated shell needs to greet
    the caller and confirm a live session — just enough to render a nav
    bar, nothing profile-shaped. Deliberately separate from /profile
    (which reaches into the shared career_profiles table) so a page that
    only needs "am I signed in, and as whom" never has to reason about
    that table's shape."""
    db = request.app.state.db
    session_user_id = require_user(request)
    user = db.get_user_by_id(session_user_id)
    return {"user_id": session_user_id, "email": user["email"] if user else None}


# ---- education (new, owned by this service) ----

@app.post("/education")
def create_education(request: Request, degree: str = Form(""), institution: str = Form(""),
                      field: str = Form(""), start_date: str = Form(None), end_date: str = Form(None)):
    db = request.app.state.db
    session_user_id = require_user(request)
    education_id = db.add_education(session_user_id, degree, institution, field, start_date, end_date)
    return _row_to_dict(db.get_education(education_id))


@app.get("/education")
def list_education(request: Request):
    db = request.app.state.db
    session_user_id = require_user(request)
    return [_row_to_dict(r) for r in db.list_education_for_user(session_user_id)]


@app.get("/education/{education_id}")
def get_education(request: Request, education_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    row = owned_education(db, education_id, session_user_id)
    return _row_to_dict(row)


@app.delete("/education/{education_id}")
def delete_education(request: Request, education_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    owned_education(db, education_id, session_user_id)  # 404s before any mutation if not owned
    db.delete_education(education_id)
    return {"status": "deleted"}


# ---- experience (new, owned by this service) ----

@app.post("/experience")
def create_experience(request: Request, company: str = Form(""), role: str = Form(""),
                       start_date: str = Form(None), end_date: str = Form(None),
                       responsibilities: str = Form(""), achievements: str = Form(""),
                       industry: str = Form(""), function: str = Form("")):
    db = request.app.state.db
    session_user_id = require_user(request)
    entry_id = db.add_experience_entry(
        session_user_id, company=company, role=role, start_date=start_date, end_date=end_date,
        responsibilities=responsibilities, achievements=achievements, industry=industry, function=function,
    )
    return _row_to_dict(db.get_experience_entry(entry_id))


@app.get("/experience")
def list_experience(request: Request):
    db = request.app.state.db
    session_user_id = require_user(request)
    return [_row_to_dict(r) for r in db.list_experience_for_user(session_user_id)]


@app.get("/experience/{entry_id}")
def get_experience(request: Request, entry_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    row = owned_experience_entry(db, entry_id, session_user_id)
    return _row_to_dict(row)


@app.delete("/experience/{entry_id}")
def delete_experience(request: Request, entry_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    owned_experience_entry(db, entry_id, session_user_id)
    db.delete_experience_entry(entry_id)
    return {"status": "deleted"}


# ---- Application Diagnosis: purchase + pricing ----

@app.get("/diagnosis/price")
def preview_ad_price(request: Request):
    """Read-only, side-effect-free. What the frontend's 'Your price
    today: ₹X' display calls — informational only, per the approved
    architecture (§11): this never creates a purchase row, so simply
    loading a pricing page can never spam the purchases table. The
    authoritative price at the moment of actually buying is still
    whatever begin_ad_purchase computes inside its own locked
    transaction — this endpoint's number and that number can only ever
    differ if the customer's qualifying count changes in between (a
    completed purchase elsewhere), which is expected and correct."""
    db = request.app.state.db
    session_user_id = require_user(request)
    return db.preview_ad_price(session_user_id)


@app.get("/diagnosis/entitlement/active")
def get_active_ad_entitlement(request: Request):
    db = request.app.state.db
    session_user_id = require_user(request)
    entitlement = db.find_active_ad_entitlement(session_user_id)
    return {"has_active_entitlement": entitlement is not None,
            "entitlement_id": entitlement["id"] if entitlement else None}


@app.post("/diagnosis/purchase")
def create_ad_purchase(request: Request, idempotency_key: str = Body(None, embed=True)):
    """Starts a purchase. The price is computed entirely server-side
    (Database.begin_ad_purchase, race-safe against this same user's
    concurrent attempts) — nothing here accepts or trusts a client-supplied
    amount. idempotency_key (optional): a client-generated retry token —
    resending the same key (e.g. after a network timeout, or a
    double-click) returns the SAME purchase rather than creating a
    second one. Returns a gateway reference the client uses to complete
    payment; for the real gateway that would be a checkout redirect, for
    the test gateway it's a value the test suite can hand straight to
    /payments/webhook, exactly the way the real gateway's own webhook
    delivery would."""
    db = request.app.state.db
    session_user_id = require_user(request)
    gateway = request.app.state.payment_gateway
    result = db.begin_ad_purchase(session_user_id, gateway=gateway.name, idempotency_key=idempotency_key)
    if result.get("replayed") and result["payment_status"] != "CREATED":
        # A retried request landed on a purchase that already moved past
        # CREATED (e.g. already PENDING or even SUCCEEDED) — nothing new
        # to do, hand back what already exists rather than re-issuing a
        # second gateway intent for the same purchase.
        return result
    user = db.get_user_by_id(session_user_id)
    intent = gateway.create_payment_intent(result["purchase_id"], result["amount_inr"], user["email"])
    db.mark_purchase_pending(result["purchase_id"], intent.get("gateway_reference"))
    return {**result, **intent, "payment_status": "PENDING"}


@app.post("/payments/webhook")
async def payments_webhook(request: Request):
    """Server-to-server only — this is where a real gateway would deliver
    signed payment-outcome notifications. Never callable by a browser
    session in any way that lets it mark its own purchase paid: the
    signature check in verify_webhook is the only thing that authorizes a
    status transition here, not the caller's identity (there isn't one)."""
    db = request.app.state.db
    gateway = request.app.state.payment_gateway
    payload = await request.json()
    try:
        # request.headers is case-insensitive (ASGI/ Starlette normalizes
        # incoming header names to lowercase) — passing it straight
        # through, rather than a plain dict(...), so a lookup by any
        # casing (as a real gateway's docs might specify) still works.
        event = gateway.verify_webhook(payload, request.headers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    purchase = db.get_purchase(event["purchase_id"])
    if purchase is None:
        raise HTTPException(status_code=404, detail="Unknown purchase.")

    if event["outcome"] == "SUCCEEDED":
        result = db.confirm_ad_purchase(purchase["id"], event["gateway_reference"])
        return {"status": "confirmed", **result}
    db.fail_ad_purchase(purchase["id"])
    return {"status": "failed", "purchase_id": purchase["id"]}


@app.get("/purchases")
def list_purchases(request: Request):
    db = request.app.state.db
    session_user_id = require_user(request)
    return [_row_to_dict(r) for r in db.list_purchases_for_user(session_user_id)]


@app.get("/purchases/{purchase_id}")
def get_purchase(request: Request, purchase_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    return _row_to_dict(owned_purchase(db, purchase_id, session_user_id))


@app.get("/entitlements/{entitlement_id}")
def get_entitlement(request: Request, entitlement_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    return _row_to_dict(owned_entitlement(db, entitlement_id, session_user_id))


# ---- Application Diagnosis: the diagnosis itself ----

def _profile_json(db, user_id: int) -> dict:
    profile_row = db.get_career_profile(user_id)
    if profile_row is None:
        return None
    return json.loads(profile_row["profile_json"])


@app.post("/diagnosis")
def create_diagnosis(request: Request, jd_text: str = Body(..., embed=True), job_id: int = Body(None, embed=True),
                      idempotency_key: str = Body(None, embed=True)):
    """Phase 2B-1 production Application Diagnosis workflow:
    authenticated user -> existing profile -> active entitlement ->
    deterministic diagnosis -> (insufficient signal: stop, no side
    effects) -> atomic entitlement-consumption + diagnosis persistence ->
    result. idempotency_key: an optional client-generated retry token —
    see Database.create_ad_diagnosis for the replay semantics."""
    db = request.app.state.db
    session_user_id = require_user(request)
    # Safe correlation id for every log line below — never the JD text,
    # profile content, or anything else private. This is generated once
    # per request and reused as the diagnosis's own ai_run_id on success,
    # so a support investigation can follow one request end to end.
    request_id = str(uuid.uuid4())

    # Idempotent-replay fast path: checked FIRST, before touching the
    # profile/entitlement/engine at all — if this exact retry token
    # already produced a diagnosis, that's the answer, full stop. This
    # covers the ordinary sequential-retry case (lost response, browser
    # resend); the genuinely-concurrent case (two requests with the same
    # key arriving before either commits) is separately handled inside
    # Database.create_ad_diagnosis itself, which re-checks under the
    # entitlement's row lock so both concurrent callers still converge on
    # the same diagnostic_id rather than one erroring.
    if idempotency_key:
        existing = db.get_diagnosis_by_idempotency_key(session_user_id, idempotency_key)
        if existing is not None:
            logger.info("diagnosis request_id=%s user_id=%s result=replayed diagnostic_id=%s",
                        request_id, session_user_id, existing["id"])
            return {
                "diagnostic_id": existing["id"], "replayed": True, "entitlement_consumed": False,
                "overall_score": existing["overall_score"],
                "note": "This retry matched an already-completed diagnosis. "
                        "Fetch GET /diagnosis/{id} for the full result.",
            }

    profile_json = _profile_json(db, session_user_id)
    if profile_json is None:
        raise HTTPException(
            status_code=422,
            detail="You need a Rithavo Profile before requesting an Application Diagnosis.",
        )

    entitlement = db.find_active_ad_entitlement(session_user_id)
    if entitlement is None:
        logger.info("diagnosis request_id=%s user_id=%s result=no_entitlement",
                    request_id, session_user_id)
        raise HTTPException(
            status_code=402,
            detail="No available Application Diagnosis credit — purchase one to continue.",
        )

    try:
        capabilities = db.list_active_capabilities_for_user(session_user_id)
        education = db.list_education_for_user(session_user_id)
        experience = db.list_experience_for_user(session_user_id)
        report = evaluate(profile_json, capabilities, education, experience, jd_text)
    except InvalidJobDescriptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Phase 1.5 policy (explicit decision): an INSUFFICIENT_JD_SIGNAL
    # result means Rithavo could not produce a meaningful diagnosis, so
    # the customer has not received the purchased diagnosis — the
    # entitlement must NOT be consumed and nothing is persisted as a
    # delivered diagnosis. This return happens BEFORE db.create_ad_diagnosis
    # (the only place that ever consumes an entitlement or writes a
    # diagnostics row), so this path performs zero database writes —
    # nothing to race against. The customer keeps their credit and can
    # submit a different JD immediately.
    if report.insufficient_signal:
        logger.info("diagnosis request_id=%s user_id=%s result=insufficient_signal",
                    request_id, session_user_id)
        return {
            "diagnostic_id": None,
            "insufficient_signal": True,
            "entitlement_consumed": False,
            "verdict": report.verdict,
            "cta": report.cta,
            "narrative_summary": report.narrative_summary,
            "next_steps": report.next_steps,
        }

    findings = [f.to_dict() for f in report.all_findings()]
    try:
        result = db.create_ad_diagnosis(
            session_user_id, entitlement["id"], request_id, jd_text.strip(), report.overall_score,
            findings, route="EXTERNAL_JD", job_id=job_id, idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        # Entitlement was consumed by a concurrent/duplicate submission
        # between the check above and this call — not this request's turn.
        logger.info("diagnosis request_id=%s user_id=%s result=entitlement_unavailable reason=%s",
                    request_id, session_user_id, exc)
        raise HTTPException(status_code=409, detail=str(exc))
    diagnostic_id = result["diagnostic_id"]
    if result["replayed"]:
        # A genuinely concurrent duplicate (same idempotency_key,
        # arrived before the winner committed) — the ANSWER belongs to
        # whichever request actually won the entitlement race, not to
        # this request's own freshly-recomputed `report`, which must be
        # discarded here rather than returned as if it were authoritative.
        won_diagnosis = db.get_diagnosis(diagnostic_id)
        logger.info("diagnosis request_id=%s user_id=%s result=replayed_concurrent diagnostic_id=%s",
                    request_id, session_user_id, diagnostic_id)
        return {
            "diagnostic_id": diagnostic_id, "replayed": True, "entitlement_consumed": False,
            "overall_score": won_diagnosis["overall_score"],
            "note": "A concurrent identical request already completed this diagnosis. "
                    "Fetch GET /diagnosis/{id} for the full result.",
        }
    logger.info("diagnosis request_id=%s user_id=%s result=success diagnostic_id=%s entitlement_id=%s "
                "overall_score=%s verdict=%s",
                request_id, session_user_id, diagnostic_id, entitlement["id"], report.overall_score, report.verdict)

    return {
        "diagnostic_id": diagnostic_id,
        "replayed": False,
        "entitlement_consumed": True,
        "overall_score": report.overall_score,
        "verdict": report.verdict,
        "cta": report.cta,
        "insufficient_signal": report.insufficient_signal,
        "category_scores": report.category_scores,
        "narrative_summary": report.narrative_summary,
        "direct_matches": [f.to_dict() for f in report.direct_matches],
        "related_matches": [f.to_dict() for f in report.related_matches],
        "gaps": [f.to_dict() for f in report.gaps],
        "responsibility_findings": [f.to_dict() for f in report.responsibility_findings],
        "seniority_scope_finding": report.seniority_scope_finding.to_dict() if report.seniority_scope_finding else None,
        "education_domain_findings": [f.to_dict() for f in report.education_domain_findings],
        "career_transition_notice": report.career_transition_notice,
        "next_steps": report.next_steps,
    }


@app.get("/diagnosis")
def list_diagnoses(request: Request):
    db = request.app.state.db
    session_user_id = require_user(request)
    return [_row_to_dict(r) for r in db.list_diagnoses_for_user(session_user_id)]


def _diagnosis_detail_response(db, diagnosis) -> dict:
    """Builds the full diagnosis detail response FROM PERSISTED STATE —
    used both by GET /diagnosis/{id} and by POST /diagnosis's idempotent-
    replay path, so a replayed response always reflects what was actually
    stored, never a value freshly recomputed against the customer's
    CURRENT (possibly since-changed) profile."""
    diagnostic_id = diagnosis["id"]
    findings = [_row_to_dict(f) for f in db.list_findings_for_diagnosis(diagnostic_id)]
    # diagnostics has no dedicated verdict column (Phase 1.5 avoided a
    # migration for this) — reconstruct it from the stored score plus
    # whichever persisted SENIORITY_SCOPE finding's text flags a major
    # mismatch, exactly reproducing the live override in evaluate().
    seniority_scope_major = any(
        f["dimension"] == "SENIORITY_SCOPE" and "major seniority/scope mismatch" in f["finding_text"]
        for f in findings
    )
    verdict = verdict_for_score(diagnosis["overall_score"], seniority_scope_major)
    return {
        "diagnosis": _row_to_dict(diagnosis),
        "findings": findings,
        "verdict": verdict,
        "cta": cta_for_verdict(verdict),
    }


@app.get("/diagnosis/{diagnostic_id}")
def get_diagnosis_route(request: Request, diagnostic_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    diagnosis = owned_diagnosis(db, diagnostic_id, session_user_id)
    return _diagnosis_detail_response(db, diagnosis)


# ---- Application Diagnosis: "Create My Resume for This Job" ----

@app.post("/diagnosis/{diagnostic_id}/resume")
def create_diagnosis_resume(request: Request, diagnostic_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    diagnosis = owned_diagnosis(db, diagnostic_id, session_user_id)

    profile_json = _profile_json(db, session_user_id) or {}
    capabilities = db.list_active_capabilities_for_user(session_user_id)
    education = db.list_education_for_user(session_user_id)
    experience = db.list_experience_for_user(session_user_id)

    # Re-derive matched keywords against the SAME frozen JD text this
    # diagnosis was created for, so the resume prioritizes whatever's
    # relevant to that job — using the customer's current profile (a
    # resume should reflect who they are now), not a fabricated one.
    report = evaluate(profile_json, capabilities, education, experience, diagnosis["target_jd_text"])
    resume = build_tailored_resume(
        profile_json, capabilities, education, experience,
        diagnosis["target_jd_text"], report.matched_keywords,
    )
    existing_count = len(db.list_resumes_for_diagnosis(diagnostic_id))
    version = existing_count + 1
    resume_id = db.create_resume_for_diagnosis(
        session_user_id, diagnostic_id, resume.to_dict(),
        label=f"Resume for {resume.target_context or 'this job'} — v{version}",
    )
    return {"resume_id": resume_id, "version": version}


@app.get("/diagnosis/{diagnostic_id}/resumes")
def list_diagnosis_resumes(request: Request, diagnostic_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    owned_diagnosis(db, diagnostic_id, session_user_id)
    return [_row_to_dict(r) for r in db.list_resumes_for_diagnosis(diagnostic_id)]


@app.get("/resumes")
def list_resumes(request: Request):
    """Phase 2B-1.7A: the full resume history tab needs every resume for
    this user, not just the ones tied to one Application Diagnosis
    (GET /diagnosis/{id}/resumes, unchanged, still exists for that
    narrower case). A row this service didn't create — e.g. one the
    sibling Career Intelligence product generated — may have no
    content_json; `downloadable` tells the frontend not to offer a
    download link it knows would fail, without guessing why."""
    db = request.app.state.db
    session_user_id = require_user(request)
    rows = [_row_to_dict(r) for r in db.list_resumes_for_user(session_user_id)]
    for row in rows:
        row["downloadable"] = bool(row.get("content_json"))
    return rows


@app.get("/resume/{resume_id}/download")
def download_resume(request: Request, resume_id: int):
    db = request.app.state.db
    session_user_id = require_user(request)
    resume_row = owned_resume(db, resume_id, session_user_id)
    resume = TailoredResume.from_dict(json.loads(resume_row["content_json"]))
    docx_bytes = render_tailored_resume_docx(resume)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="resume_{resume_id}.docx"'},
    )
