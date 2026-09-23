"""
Home/Explore/Admin Integration Phase — regular-user Mentorship pages,
proxied server-to-server to app.rithavo.com's already-built, already-
tested mentor lifecycle (app/mentorship.py + app/db.py's guarded
transitions on the sibling side). No lifecycle rule is reimplemented
here — every accept/schedule/start/complete call is the sibling's own
atomic, guarded UPDATE, unchanged.

user_id is always require_user(request) — this service's own
already-authenticated session — never a client-supplied field, exactly
like every other protected route in this codebase (see
app/security.py's own module docstring). It is sent to the sibling
explicitly (as a JSON field/query param) because there is no
career-profile session in this flow at all; the sibling trusts it
because the request itself is authenticated with the shared internal
service secret, which only this backend can ever hold.
"""

from fastapi import APIRouter, HTTPException, Request

from .internal_client import InternalServiceError, get as internal_get, post as internal_post
from .security import require_user

router = APIRouter(prefix="/mentor")


def _proxy_get(path: str, params: dict = None):
    try:
        return internal_get(path, params=params)
    except InternalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail="Mentorship is temporarily unavailable.")


def _proxy_post(path: str, json_body: dict = None):
    try:
        return internal_post(path, json_body=json_body)
    except InternalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail="Mentorship is temporarily unavailable.")


@router.get("/discover")
def discover_mentors(request: Request):
    require_user(request)
    return _proxy_get("/internal/api/mentors/discover")


@router.get("/status")
def my_status(request: Request):
    user_id = require_user(request)
    return _proxy_get("/internal/api/mentors/status", params={"user_id": user_id})


@router.post("/apply")
def apply(request: Request, payload: dict):
    user_id = require_user(request)
    return _proxy_post("/internal/api/mentors/apply", {**payload, "user_id": user_id})


@router.get("/sessions")
def my_sessions(request: Request):
    user_id = require_user(request)
    return _proxy_get("/internal/api/mentors/sessions/mine", params={"user_id": user_id})


@router.post("/{mentor_user_id}/request-session")
def request_session(request: Request, mentor_user_id: int):
    user_id = require_user(request)
    return _proxy_post(f"/internal/api/mentors/{mentor_user_id}/request-session", {"user_id": user_id})


@router.post("/sessions/{session_id}/accept")
def accept_session(request: Request, session_id: int):
    user_id = require_user(request)
    return _proxy_post(f"/internal/api/mentors/sessions/{session_id}/accept", {"user_id": user_id})


@router.post("/sessions/{session_id}/schedule")
def schedule_session(request: Request, session_id: int, payload: dict):
    user_id = require_user(request)
    return _proxy_post(
        f"/internal/api/mentors/sessions/{session_id}/schedule",
        {"user_id": user_id, "scheduled_at": payload.get("scheduled_at", "")},
    )


@router.post("/sessions/{session_id}/start")
def start_session(request: Request, session_id: int):
    user_id = require_user(request)
    return _proxy_post(f"/internal/api/mentors/sessions/{session_id}/start", {"user_id": user_id})


@router.post("/sessions/{session_id}/complete")
def complete_session(request: Request, session_id: int):
    user_id = require_user(request)
    return _proxy_post(f"/internal/api/mentors/sessions/{session_id}/complete", {"user_id": user_id})
