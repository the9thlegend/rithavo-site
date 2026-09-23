"""
Rithavo Web Platform — Phase 0.

New rithavo.com backend. Foundation only: identity, session, and the
authorization layer, plus the two new additive profile tables (education,
experience_entries). No payment, no Application Diagnosis, no resume
generation yet — those are later phases, gated on this foundation being
proven safe first.

This service never imports the sibling `rithavo-career-profile`
codebase (app.rithavo.com) and never depends on it at import time — the
`users` table row a shared email resolves to is the only thing tying
the two together at rest. P0.5A-3 added exactly one narrow runtime
exception: GET /profile/photo makes a short-lived, signed,
server-to-server HTTP call to the sibling to proxy back this session's
own current Card photo (see app/photo_access.py) — every other route in
this file is still browser-mediated only (a redirect or a URL handed to
the client), never a direct backend-to-backend call.
"""

import json
import logging
import re
import uuid

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

import config
from app.auth import (
    MagicLinkError, PasswordResetError, hash_password, issue_magic_link_token, issue_password_reset_token,
    verify_and_consume_magic_link_token, verify_and_consume_password_reset_token, verify_password,
)
from app.card_handoff import issue_card_handoff_token
from app.career_intelligence import get_career_intelligence_view, resume_inputs_for_view
from app.db import Database
from app.diagnosis_engine import InvalidJobDescriptionError, cta_for_verdict, evaluate, verdict_for_score
from app.email_sender import get_email_sender
from app.payment_gateway import get_gateway
from app.photo_access import fetch_selected_photo, issue_photo_access_token
from app.pricing import CAREER_INTELLIGENCE_PRICE_INR
from app.razorpay_gateway import RazorpayVerificationError
from app.rate_limit import RateLimiter
from app.resume_export import build_tailored_resume, render_tailored_resume_docx, TailoredResume
from app.resume_extraction import UnsupportedResumeFormatError, extract_text, parse_resume_text
from app.security import (
    owned_career_profile, owned_diagnosis, owned_education, owned_entitlement,
    owned_experience_entry, owned_purchase, owned_resume, require_user,
)
from app.super_admin import bootstrap_super_admin
from app.launch_lock import is_purchase_locked
from app import routes_admin, routes_cashfree, routes_explore, routes_mentor

logger = logging.getLogger("rithavo_web.diagnosis")

app = FastAPI(title="Rithavo Web Platform")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, https_only=config.SESSION_COOKIE_HTTPS_ONLY)

app.state.db = Database(config.DATABASE_URL or config.DB_PATH)
app.state.db.init_schema()
bootstrap_super_admin(app.state.db, config.SUPER_ADMIN_EMAIL, config.SUPER_ADMIN_PASSWORD)
app.state.email_sender = get_email_sender()
app.state.payment_gateway = get_gateway()
app.state.auth_rate_limiter = RateLimiter()
# P0 (conventional sign-in rework): a separate instance/budget from
# auth_rate_limiter above -- failed password attempts and magic-link
# email requests must not share one counter, or five failed passwords
# would also block a legitimate magic-link request for the same address.
app.state.login_rate_limiter = RateLimiter()

app.include_router(routes_explore.router)
app.include_router(routes_admin.router)
app.include_router(routes_mentor.router)
app.include_router(routes_cashfree.router)


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


def _auth_error_code(exc: MagicLinkError) -> str:
    """P0.5 auth hardening: maps auth.py's existing, unchanged
    MagicLinkError messages to a small, generic, closed set of display
    codes for the frontend — never the raw exception text itself (which
    is an internal implementation detail, not something to expose
    verbatim), and never anything that reveals whether the token named
    a real account. auth.py itself is not modified by this mapping."""
    message = str(exc)
    if "expired" in message:
        return "expired"
    if "already been used" in message:
        return "used"
    return "invalid"


@app.get("/auth/verify")
def auth_verify(request: Request, token: str):
    db = request.app.state.db
    try:
        email = verify_and_consume_magic_link_token(db, config.SESSION_SECRET, token)
    except MagicLinkError as exc:
        # P0.5 auth hardening: this route is what the emailed link
        # itself points at, so a failure here was previously rendered
        # as raw JSON text in the browser. Redirects to the existing
        # sign-in page instead, which shows a proper Rithavo-styled
        # error banner and the exact same "enter your email" form as
        # the next action — never the token, the exception's own
        # wording, or anything about whether the address has an
        # account. Built from the request's own origin, same rule as
        # the success-path destination below, never a hardcoded domain.
        error_code = _auth_error_code(exc)
        destination = f"{request.url.scheme}://{request.url.netloc}/sign-in/?auth_error={error_code}"
        return RedirectResponse(destination, status_code=302)
    user_id = db.get_or_create_user(email)
    request.session["user_id"] = user_id
    return _post_login_redirect(request, db, user_id)


def _post_login_redirect(request: Request, db, user_id: int) -> RedirectResponse:
    """Shared by every "just authenticated" route (magic-link verify,
    password login, password-reset success). The destination is a static
    customer-facing page (rithavo-site/home/index.html), not this API's
    own /profile route — per the brief, /api/profile stays an API
    endpoint and is never rendered as HTML. Unlike the Phase 2B-1.6 fix
    (request.url_for, which correctly resolves an *API* route under
    root_path="/api" in production), a static route is never under /api
    at all, so the target must be built from the request's origin only —
    scheme + netloc, deliberately ignoring root_path — never a hardcoded
    "https://rithavo.com", so this keeps working unprefixed against
    local dev/tests (http://testserver/home/) and correctly prefix-free
    in production (https://rithavo.com/home/).

    P0 onboarding: a user with no career_profiles row yet (never touched
    app.rithavo.com, and hasn't completed rithavo.com's own onboarding
    either) goes to /onboarding/ instead of straight to /home/ —
    everyone else (existing profile, however it was created) is
    unaffected and still lands on /home/ exactly as before.

    Home/Explore/Admin Integration Phase: a Super Admin lands on
    /admin/ instead — checked first, since the bootstrap account has no
    career_profiles row at all and would otherwise be sent to
    /onboarding/ like any other brand-new signup."""
    if db.is_super_admin(user_id):
        path = "admin"
    else:
        has_profile = db.get_career_profile(user_id) is not None
        path = "home" if has_profile else "onboarding"
    destination = f"{request.url.scheme}://{request.url.netloc}/{path}/"
    return RedirectResponse(destination, status_code=302)


@app.post("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return {"status": "logged_out"}


# ---- P0 (conventional sign-in rework): email + password, alongside the
#      existing magic-link flow above, which is unmodified and remains
#      fully available as a secondary option. ----

_GENERIC_LOGIN_ERROR = "Invalid email or password."
_GENERIC_RESET_REQUESTED_MESSAGE = (
    "If that email has a Rithavo account, we've sent a link to set or reset its password. "
    "It expires in 30 minutes and can only be used once."
)


@app.post("/auth/login")
def auth_login(request: Request, email: str = Body(..., embed=True), password: str = Body(..., embed=True)):
    """Deliberately returns the exact same error, with the exact same
    status code, whether the email doesn't exist, has no password set
    yet, or the password is simply wrong — account-enumeration
    protection, same principle already applied to /auth/password/forgot
    below and to the existing magic-link flow's own error wording."""
    normalized_email = email.strip().lower()
    if not request.app.state.login_rate_limiter.allow(normalized_email):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts for this email — please wait a few minutes and try again.",
        )
    db = request.app.state.db
    user = db.get_user_by_email(normalized_email)
    if user is None or not user["password_hash"] or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail=_GENERIC_LOGIN_ERROR)
    request.session["user_id"] = user["id"]
    redirect = _post_login_redirect(request, db, user["id"])
    return {"status": "ok", "redirect": redirect.headers["location"]}


@app.post("/auth/password/forgot")
def auth_password_forgot(request: Request, email: str = Body(..., embed=True)):
    """Always the same response regardless of whether the account exists
    -- the only observable difference is whether an email actually goes
    out, which the caller (a browser) cannot see. Reuses auth_rate_limiter
    (the same "please send me an email" budget /auth/start already uses),
    not login_rate_limiter, which is specifically for password attempts."""
    normalized_email = email.strip().lower()
    if not request.app.state.auth_rate_limiter.allow(normalized_email):
        raise HTTPException(
            status_code=429,
            detail="Too many requests for this email — please wait a few minutes and try again.",
        )
    db = request.app.state.db
    user = db.get_user_by_email(normalized_email)
    if user is not None:
        token = issue_password_reset_token(db, config.SESSION_SECRET, normalized_email)
        link = f"{request.url.scheme}://{request.url.netloc}/reset-password/?token={token}"
        is_first_setup = not user["password_hash"]
        body = (
            "Hi,\n\n"
            + ("Use the link below to set a password for your Rithavo account:\n\n" if is_first_setup
               else "Use the link below to reset your Rithavo account password:\n\n")
            + f"{link}\n\n"
            "This link expires in 30 minutes and can only be used once. If you didn't request this, "
            "you can safely ignore this email — no one can access your account without clicking it.\n\n"
            "Need help? Reply to this email or reach us at hello@rithavo.com.\n\n"
            "— Rithavo"
        )
        try:
            request.app.state.email_sender.send(
                to=normalized_email,
                subject="Set your Rithavo password" if is_first_setup else "Reset your Rithavo password",
                body=body,
            )
        except Exception:
            logger.exception("password_reset_email_send_failed")
    return {"status": "ok", "message": _GENERIC_RESET_REQUESTED_MESSAGE}


@app.post("/auth/password/reset")
def auth_password_reset(request: Request, token: str = Body(..., embed=True), password: str = Body(..., embed=True)):
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    db = request.app.state.db
    try:
        email = verify_and_consume_password_reset_token(db, config.SESSION_SECRET, token)
    except PasswordResetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # A reset token is only ever issued for an email that already had an
    # account (see /auth/password/forgot) -- this is a reset, never an
    # account-creation path, so look up rather than get-or-create.
    user = db.get_user_by_email(email)
    if user is None:
        raise HTTPException(status_code=400, detail="This password reset link is invalid.")
    db.set_user_password(user["id"], hash_password(password))
    request.session["user_id"] = user["id"]
    redirect = _post_login_redirect(request, db, user["id"])
    return {"status": "ok", "redirect": redirect.headers["location"]}


# ---- P0 onboarding: resume upload -> extract (preview only) -> confirm
#      (the only step that writes anything). See app/resume_extraction.py
#      for why this is a heuristic, not ML, parser — the review/edit step
#      between these two endpoints is the product's own designed safety
#      net for that, not a gap this code needs to close. ----

_MAX_RESUME_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB — generous for a text resume, small enough to reject a mistaken/abusive upload before touching it


@app.post("/onboarding/resume/extract")
async def extract_resume(request: Request, resume: UploadFile = File(...)):
    """Preview only — reads the uploaded file, returns a best-effort
    structured draft. Never touches the database. The client is expected
    to show this to the user for editing and only send the (possibly
    corrected) result to /onboarding/confirm afterward."""
    require_user(request)
    data = await resume.read()
    if len(data) > _MAX_RESUME_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That resume file is too large (max 10 MB).")
    try:
        text = extract_text(resume.filename or "", data)
    except UnsupportedResumeFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.warning("onboarding_resume_extract_failed")
        raise HTTPException(
            status_code=422,
            detail="Rithavo couldn't read that file. Please try a different PDF/DOCX, or enter your details manually.",
        )
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Rithavo couldn't find any text in that file (it may be a scanned image). "
                    "Please try a different file, or enter your details manually.",
        )
    return parse_resume_text(text)


@app.post("/onboarding/confirm")
def confirm_onboarding(request: Request,
                        name: str = Body("", embed=True), headline: str = Body("", embed=True),
                        location: str = Body("", embed=True), years_of_experience: str = Body("", embed=True),
                        current_role: str = Body("", embed=True), previous_roles: list = Body(None, embed=True),
                        companies: list = Body(None, embed=True), industries: list = Body(None, embed=True),
                        functions: list = Body(None, embed=True),
                        education: list = Body(None, embed=True), experience: list = Body(None, embed=True)):
    """The ONLY step in the whole onboarding flow that writes canonical
    profile data — reached after the user has reviewed/edited whatever
    came out of resume extraction, or after filling the same fields in
    from scratch (manual entry). Both paths converge here; there is no
    separate manual-entry save path, so there is exactly one place that
    ever creates a career_profiles row from this service."""
    db = request.app.state.db
    session_user_id = require_user(request)

    profile_json = {
        "identity": {
            "name": {"value": name}, "headline": {"value": headline},
            "location": {"value": location}, "years_of_experience": {"value": years_of_experience},
        },
        "background": {
            "current_role": {"value": current_role},
            "previous_roles": previous_roles or [], "companies": companies or [],
            "industries": industries or [], "functions": functions or [],
        },
    }
    db.upsert_career_profile(session_user_id, profile_json)

    for entry in (education or []):
        db.add_education(
            session_user_id, degree=entry.get("degree", ""), institution=entry.get("institution", ""),
            field=entry.get("field", ""), start_date=entry.get("start_date") or None,
            end_date=entry.get("end_date") or None,
        )
    for entry in (experience or []):
        db.add_experience_entry(
            session_user_id, company=entry.get("company", ""), role=entry.get("role", ""),
            start_date=entry.get("start_date") or None, end_date=entry.get("end_date") or None,
            responsibilities=entry.get("responsibilities", ""), achievements=entry.get("achievements", ""),
            industry=entry.get("industry", ""), function=entry.get("function", ""),
        )
    return {"status": "confirmed"}


# ---- Phase P0.2: Profile -> Rithavo Card handoff. This service never
#      creates, reads (beyond the one existence check), or renders Card
#      data itself — see app/card_handoff.py's module docstring. The
#      sibling app (app.rithavo.com) remains the sole Card engine. ----

@app.get("/card/status")
def get_card_status(request: Request):
    """Read-only: does this person already have a Rithavo Card. Used by
    the Home page to choose 'Generate Your Rithavo Card' vs a
    continue-to-existing-Card wording — never to decide whether to
    create one; creation only ever happens on the sibling."""
    db = request.app.state.db
    session_user_id = require_user(request)
    return {"has_card": db.has_card_for_person(session_user_id)}


@app.post("/card/continue")
def card_continue(request: Request):
    """Mints the short-lived handoff token (app/card_handoff.py) that
    lets the browser carry proof of this session's identity to the
    sibling app in a single POST, without a second manual sign-in and
    without this service ever touching Card data. Requires a confirmed
    profile — there is nothing to hand off otherwise."""
    db = request.app.state.db
    session_user_id = require_user(request)
    if not config.CARD_HANDOFF_SECRET:
        # Phase P0.2A: fail closed, never silently sign with a fallback.
        raise HTTPException(status_code=503, detail="Card handoff is not available right now.")
    if db.get_career_profile(session_user_id) is None:
        raise HTTPException(status_code=400, detail="No Rithavo Profile yet.")
    token = issue_card_handoff_token(config.CARD_HANDOFF_SECRET, session_user_id)
    return {
        "handoff_url": f"{config.CARD_APP_BASE_URL}/handoff/card",
        "token": token,
    }


# ---- P0.5A-3: proxy this session's own current Card photo from the
#      sibling, server-to-server. This service never stores, caches, or
#      duplicates photo bytes anywhere — every request re-fetches fresh
#      from the sibling, which remains the sole source of truth. ----

@app.get("/profile/photo")
def get_profile_photo(request: Request):
    """Every failure mode (no secret configured, no Card, no selected
    photo, sibling unreachable/slow, a non-image response) collapses to
    the same plain 404 — the frontend's only job on anything but 200 is
    to keep its existing initial-letter avatar, never show a broken
    image or a technical error."""
    session_user_id = require_user(request)
    if not config.PHOTO_ACCESS_SECRET:
        raise HTTPException(status_code=404)
    token = issue_photo_access_token(config.PHOTO_ACCESS_SECRET, session_user_id)
    result = fetch_selected_photo(config.CARD_APP_BASE_URL, token)
    if result is None:
        raise HTTPException(status_code=404)
    content, content_type = result
    return Response(content=content, media_type=content_type)


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

    Uses the exact same build_tailored_resume/render_tailored_resume_docx
    pipeline as an Application Diagnosis resume (point D: reusing this
    service's own already-approved resume architecture, not the sibling
    app's), so /resume/{id}/download works for a CI resume for free —
    same ownership check, same immutable/versioned INSERT-only pattern.

    CI validity foundation: still not gated by this service's own
    Application Diagnosis credit system (points H/I of the original
    phase brief remain true — no consumed_at, no AD entitlement
    involved) — but it must not be reachable with NO Career Intelligence
    purchase at all, which the original implementation allowed. This is
    a VIEW-type check only (find_active_ci_entitlement, status='ACTIVE',
    not expiry/evaluations-aware) so a member whose entitlement has
    since expired or run out of evaluations can still regenerate a
    resume from their existing assessment, per 'previous results remain
    viewable'. No formatting/tailoring logic below is touched.
    """
    db = request.app.state.db
    session_user_id = require_user(request)

    entitlement = db.find_active_ci_entitlement(session_user_id)
    if entitlement is None:
        raise HTTPException(
            status_code=402,
            detail="A Career Intelligence purchase is required before Rithavo can build you a resume for it.",
        )

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


_INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def _validate_customer_phone_for_gateway(gateway, customer_phone):
    """Cashfree customer-phone phase. When Cashfree is the active
    gateway, a valid 10-digit Indian mobile number is REQUIRED — never
    a hardcoded placeholder and never the merchant's own KYC contact
    number, which is separate, Cashfree-account-level information this
    service never touches. When any other gateway is active
    (Razorpay/test), this is a no-op that returns None regardless of
    what was submitted — Razorpay's own customer flow is completely
    unaffected, per instruction."""
    if getattr(gateway, "name", None) != "cashfree":
        return None
    phone = (customer_phone or "").strip()
    if not _INDIAN_MOBILE_RE.match(phone):
        raise HTTPException(
            status_code=400, detail="Please enter a valid 10-digit Indian mobile number to continue.",
        )
    return phone


# ---- Career Intelligence: purchase (Phase 2B-2) ----

@app.get("/career-intelligence/price")
def preview_ci_price(request: Request):
    """Read-only, side-effect-free — mirrors GET /diagnosis/price's own
    purpose exactly. CI has no ladder (flat ₹799), but this still goes
    through require_user (not a public price list) and never creates a
    purchase row, for the same reason preview_ad_price doesn't.
    launch_locked/gateway (Pre-Launch Product Lock + Cashfree phone
    phases): lets the frontend show the pre-launch teaser and decide
    whether to collect a customer phone number BEFORE ever attempting
    to create a purchase — informational only, the actual enforcement
    is server-side in create_ci_purchase below regardless of what this
    reports."""
    require_user(request)
    gateway = request.app.state.payment_gateway
    return {
        "amount_inr": CAREER_INTELLIGENCE_PRICE_INR,
        "launch_locked": is_purchase_locked(),
        "gateway": gateway.name,
    }


@app.post("/career-intelligence/purchase")
def create_ci_purchase(
    request: Request, idempotency_key: str = Body(None, embed=True), customer_phone: str = Body(None, embed=True),
):
    """Career Intelligence's equivalent of POST /diagnosis/purchase —
    same idempotency-key semantics, same gateway-agnostic
    create_payment_intent call. The price is always
    CAREER_INTELLIGENCE_PRICE_INR, computed server-side inside
    Database.begin_ci_purchase; nothing here accepts a client-supplied
    amount.

    Pre-Launch Product Lock: checked FIRST, before any purchase row (or
    Cashfree/Razorpay order) is ever created — a hard 403, never just a
    hidden/disabled frontend button. Direct/manual API calls hit this
    exact same check.

    Cashfree customer phone: required and validated only when Cashfree
    is the active gateway (Razorpay's own flow is completely
    unaffected — it never receives or needs this value)."""
    if is_purchase_locked():
        raise HTTPException(status_code=403, detail="Career Intelligence is not available for purchase yet.")
    db = request.app.state.db
    session_user_id = require_user(request)
    gateway = request.app.state.payment_gateway
    validated_phone = _validate_customer_phone_for_gateway(gateway, customer_phone)
    try:
        result = db.begin_ci_purchase(session_user_id, gateway=gateway.name, idempotency_key=idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result.get("replayed") and result["payment_status"] != "CREATED":
        return result
    user = db.get_user_by_id(session_user_id)
    if validated_phone:
        db.set_purchase_customer_phone(result["purchase_id"], validated_phone)
    intent = gateway.create_payment_intent(
        result["purchase_id"], result["amount_inr"], user["email"], customer_phone=validated_phone,
    )
    db.mark_purchase_pending(result["purchase_id"], intent.get("gateway_reference"))
    return {**result, **intent, "payment_status": "PENDING"}


@app.post("/career-intelligence/purchase/{purchase_id}/confirm")
def confirm_ci_purchase_route(request: Request, purchase_id: int,
                               razorpay_order_id: str = Body(..., embed=True),
                               razorpay_payment_id: str = Body(..., embed=True),
                               razorpay_signature: str = Body(..., embed=True)):
    db = request.app.state.db
    result = _verify_and_confirm_razorpay_payment(
        request, purchase_id, "CAREER_INTELLIGENCE", razorpay_order_id, razorpay_payment_id, razorpay_signature,
        confirm_fn=lambda pid, oid, payid: db.confirm_ci_purchase(pid, oid, payid),
    )
    return {"status": "confirmed", **result}


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
    return {
        "user_id": session_user_id, "email": user["email"] if user else None,
        # Home/Explore/Admin Integration Phase — the shared nav shell
        # (app-shell.js) uses this to decide whether to render the
        # Admin tab; every server-side Admin route still independently
        # re-checks db.is_super_admin itself (see require_super_admin),
        # so this flag is a UI convenience only, never a security
        # boundary on its own.
        "is_super_admin": db.is_super_admin(session_user_id),
    }


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
    gateway = request.app.state.payment_gateway
    return {**db.preview_ad_price(session_user_id), "launch_locked": is_purchase_locked(), "gateway": gateway.name}


@app.get("/diagnosis/entitlement/active")
def get_active_ad_entitlement(request: Request):
    db = request.app.state.db
    session_user_id = require_user(request)
    entitlement = db.find_active_ad_entitlement(session_user_id)
    return {"has_active_entitlement": entitlement is not None,
            "entitlement_id": entitlement["id"] if entitlement else None}


@app.post("/diagnosis/purchase")
def create_ad_purchase(
    request: Request, idempotency_key: str = Body(None, embed=True), customer_phone: str = Body(None, embed=True),
):
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
    delivery would.

    Pre-Launch Product Lock + Cashfree customer phone: see
    create_ci_purchase's own docstring — identical rules, this is
    Application Diagnostic's copy of the same two checks."""
    if is_purchase_locked():
        raise HTTPException(status_code=403, detail="Application Diagnostic is not available for purchase yet.")
    db = request.app.state.db
    session_user_id = require_user(request)
    gateway = request.app.state.payment_gateway
    validated_phone = _validate_customer_phone_for_gateway(gateway, customer_phone)
    result = db.begin_ad_purchase(session_user_id, gateway=gateway.name, idempotency_key=idempotency_key)
    if result.get("replayed") and result["payment_status"] != "CREATED":
        # A retried request landed on a purchase that already moved past
        # CREATED (e.g. already PENDING or even SUCCEEDED) — nothing new
        # to do, hand back what already exists rather than re-issuing a
        # second gateway intent for the same purchase.
        return result
    user = db.get_user_by_id(session_user_id)
    if validated_phone:
        db.set_purchase_customer_phone(result["purchase_id"], validated_phone)
    intent = gateway.create_payment_intent(
        result["purchase_id"], result["amount_inr"], user["email"], customer_phone=validated_phone,
    )
    db.mark_purchase_pending(result["purchase_id"], intent.get("gateway_reference"))
    return {**result, **intent, "payment_status": "PENDING"}


def _verify_and_confirm_razorpay_payment(request: Request, purchase_id: int, expected_product: str,
                                          razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str,
                                          confirm_fn) -> dict:
    """Phase 2B-2 Task 5 — the client-side ("Checkout succeeded, here's
    my payment id") verification path, shared by the Application
    Diagnosis and Career Intelligence confirm endpoints below. This is
    NEVER the only path a purchase can be confirmed through — the
    webhook (below) independently confirms the same purchase, and
    confirm_fn's own idempotency (already_confirmed) makes whichever
    arrives second a safe no-op, not a duplicate entitlement.

    Ownership: owned_purchase() raises 404 for a purchase belonging to
    another user before any of this runs — a client can name any
    purchase_id, but can only ever act on their own. expected_product
    guards the (admittedly narrower) case of a caller confirming their
    OWN purchase_id but for the wrong product (e.g. an AD confirm call
    against what's actually their CI purchase).

    Never trusts razorpay_order_id/razorpay_payment_id/razorpay_signature
    as bare claims: the order id must match what THIS server itself
    already stored as gateway_reference at PENDING time (a client can't
    substitute a different, unrelated Razorpay order it doesn't own),
    the signature is verified against the Key Secret, and the payment's
    own status is re-fetched from Razorpay directly — reaching this
    endpoint's success response is not itself sufficient without both."""
    db = request.app.state.db
    session_user_id = require_user(request)
    gateway = request.app.state.payment_gateway
    if getattr(gateway, "name", None) != "razorpay":
        raise HTTPException(status_code=400, detail="Real payment verification is not configured.")

    purchase = owned_purchase(db, purchase_id, session_user_id)
    if purchase["product"] != expected_product:
        raise HTTPException(status_code=404, detail="Not found.")
    if purchase["gateway_reference"] != razorpay_order_id:
        # Never log the mismatched values themselves — just that a
        # mismatch occurred, enough to investigate server-side.
        logger.warning("razorpay_confirm order_mismatch purchase_id=%s", purchase_id)
        raise HTTPException(status_code=400, detail="This payment does not match the expected order.")

    try:
        gateway.verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)
        status = gateway.fetch_payment_status(razorpay_payment_id)
    except RazorpayVerificationError:
        logger.warning("razorpay_confirm verification_failed purchase_id=%s", purchase_id)
        raise HTTPException(
            status_code=402,
            detail="We couldn't verify this payment. If money was deducted, it will be confirmed automatically "
                   "shortly — otherwise, please try again.",
        )
    if status != "captured":
        logger.info("razorpay_confirm not_captured purchase_id=%s status=%s", purchase_id, status)
        raise HTTPException(status_code=402, detail="This payment has not completed yet.")

    return confirm_fn(purchase_id, razorpay_order_id, razorpay_payment_id)


@app.post("/diagnosis/purchase/{purchase_id}/confirm")
def confirm_ad_purchase_route(request: Request, purchase_id: int,
                               razorpay_order_id: str = Body(..., embed=True),
                               razorpay_payment_id: str = Body(..., embed=True),
                               razorpay_signature: str = Body(..., embed=True)):
    db = request.app.state.db
    result = _verify_and_confirm_razorpay_payment(
        request, purchase_id, "APPLICATION_DIAGNOSIS", razorpay_order_id, razorpay_payment_id, razorpay_signature,
        confirm_fn=lambda pid, oid, payid: db.confirm_ad_purchase(pid, oid, payid),
    )
    return {"status": "confirmed", **result}


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


# Recognized Razorpay webhook events this endpoint acts on. Every other
# event (order.paid, refund.processed, etc.) is acknowledged with 200
# but otherwise ignored — an unrecognized event must never become a 4xx/5xx
# that makes Razorpay retry it forever, but also must never be silently
# treated as a success/failure it isn't.
_RAZORPAY_SUCCESS_EVENTS = {"payment.captured"}
_RAZORPAY_FAILURE_EVENTS = {"payment.failed"}


@app.post("/payments/razorpay/webhook")
async def razorpay_webhook(request: Request):
    """Phase 2B-2 Task 6. Eventual production path: /api/payments/razorpay/webhook
    (this route's own path, unprefixed here — see api/index.py's /api
    stripping, same convention as every other route in this file). The
    production Razorpay webhook is NOT configured this phase (Task 11/
    the phase brief's stop condition) — this endpoint exists and is
    tested, but nothing points a real Razorpay account at it yet.

    Verifies the signature against the RAW request body (captured via
    request.body() before any JSON parsing — see razorpay_gateway.py's
    verify_webhook_signature docstring for why a pre-parsed dict isn't
    sufficient). Resolves the purchase purely via gateway_reference
    (the Order id this server itself created) — never via anything
    identity-shaped in the payload. Idempotent by construction: routes
    into the same confirm_ad_purchase/confirm_ci_purchase this service's
    client-side confirm endpoints use, which already returns
    already_confirmed=True on a second delivery rather than creating a
    second entitlement."""
    db = request.app.state.db
    gateway = request.app.state.payment_gateway
    if getattr(gateway, "name", None) != "razorpay":
        raise HTTPException(status_code=404, detail="Not found.")

    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    try:
        gateway.verify_webhook_signature(raw_body, signature)
    except RazorpayVerificationError:
        logger.warning("razorpay_webhook invalid_signature")
        raise HTTPException(status_code=400, detail="Invalid signature.")

    payload = json.loads(raw_body)
    event = payload.get("event", "")
    payment_entity = (payload.get("payload") or {}).get("payment", {}).get("entity", {}) or {}
    order_id = payment_entity.get("order_id")
    payment_id = payment_entity.get("id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Missing order reference.")

    purchase = db.get_purchase_by_gateway_reference(order_id)
    if purchase is None:
        # Not necessarily an error — could be a webhook for an order this
        # service never created (shouldn't happen for a correctly scoped
        # Razorpay account, but never assume). Acknowledge, don't retry.
        logger.info("razorpay_webhook unknown_order event=%s", event)
        return {"status": "ignored", "reason": "unknown_order"}

    if event in _RAZORPAY_SUCCESS_EVENTS:
        confirm_fn = db.confirm_ci_purchase if purchase["product"] == "CAREER_INTELLIGENCE" else db.confirm_ad_purchase
        try:
            result = confirm_fn(purchase["id"], order_id, payment_id)
        except ValueError as exc:
            # e.g. "cannot confirm a purchase in status REFUNDED" — an
            # out-of-order/late webhook arriving after the purchase moved
            # on for an unrelated reason. Acknowledge (don't make
            # Razorpay retry forever); log for investigation.
            logger.warning("razorpay_webhook confirm_rejected purchase_id=%s reason=%s", purchase["id"], exc)
            return {"status": "ignored", "reason": "state_conflict"}
        return {"status": "confirmed", **result}

    if event in _RAZORPAY_FAILURE_EVENTS:
        db.fail_ad_purchase(purchase["id"])  # product-agnostic despite the name — see its own docstring
        return {"status": "failed", "purchase_id": purchase["id"]}

    return {"status": "ignored", "event": event}


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
