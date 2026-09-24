"""
Home/Explore/Admin Integration Phase — the Super Admin dashboard's own
backend. Every route here requires require_super_admin (rithavo.com's
own session + explicit super_admins membership) and then proxies the
actual operation, server-to-server, to app.rithavo.com's internal
service API (app/routers/internal_api.py on the sibling side) — which
already implements every CRUD/state-machine invariant (guarded
transitions, taxonomy scoping, publish/unpublish semantics) exactly
once. This file adds authorization and page-facing shape, never a
second copy of that business logic.
"""

from fastapi import APIRouter, HTTPException, Request, UploadFile

from .internal_client import InternalServiceError, get as internal_get, post as internal_post, post_file
from .super_admin import require_super_admin

router = APIRouter(prefix="/admin")


def _proxy_get(path: str, params: dict = None):
    try:
        return internal_get(path, params=params)
    except InternalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail="Admin service is temporarily unavailable.")


def _proxy_post(path: str, json_body: dict = None, timeout: float = None):
    try:
        kwargs = {"json_body": json_body}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return internal_post(path, **kwargs)
    except InternalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail="Admin service is temporarily unavailable.")


# ---- Explore: story CRUD ----

@router.get("/explore/stories")
def list_stories(request: Request):
    require_super_admin(request)
    return _proxy_get("/internal/api/explore/stories")


@router.get("/explore/stories/pending-review")
def list_pending_review(request: Request):
    require_super_admin(request)
    return _proxy_get("/internal/api/explore/stories/pending-review")


@router.get("/explore/stories/{story_id}")
def get_story(request: Request, story_id: int):
    require_super_admin(request)
    return _proxy_get(f"/internal/api/explore/stories/{story_id}")


@router.post("/explore/stories")
def create_story(request: Request, payload: dict):
    require_super_admin(request)
    return _proxy_post("/internal/api/explore/stories", payload)


@router.post("/explore/stories/{story_id}")
def edit_story(request: Request, story_id: int, payload: dict):
    require_super_admin(request)
    return _proxy_post(f"/internal/api/explore/stories/{story_id}", payload)


@router.post("/explore/stories/{story_id}/publish")
def publish_story(request: Request, story_id: int):
    require_super_admin(request)
    return _proxy_post(f"/internal/api/explore/stories/{story_id}/publish")


@router.post("/explore/stories/{story_id}/unpublish")
def unpublish_story(request: Request, story_id: int):
    require_super_admin(request)
    return _proxy_post(f"/internal/api/explore/stories/{story_id}/unpublish")


@router.post("/explore/stories/{story_id}/delete")
def delete_story(request: Request, story_id: int):
    require_super_admin(request)
    return _proxy_post(f"/internal/api/explore/stories/{story_id}/delete")


@router.post("/explore/stories/{story_id}/image")
async def upload_story_image(request: Request, story_id: int, image: UploadFile):
    require_super_admin(request)
    content = await image.read()
    if not content:
        raise HTTPException(status_code=422, detail="No image data received.")
    try:
        return post_file(
            f"/internal/api/explore/stories/{story_id}/image", "image",
            image.filename or "upload", content, image.content_type or "application/octet-stream",
        )
    except InternalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail="Admin service is temporarily unavailable.")


# ---- Explore: taxonomy CRUD ----

@router.get("/explore/industries")
def list_industries(request: Request):
    require_super_admin(request)
    return _proxy_get("/internal/api/explore/industries")


@router.post("/explore/industries")
def create_industry(request: Request, payload: dict):
    require_super_admin(request)
    return _proxy_post("/internal/api/explore/industries", payload)


@router.post("/explore/industries/{industry_id}")
def edit_industry(request: Request, industry_id: int, payload: dict):
    require_super_admin(request)
    return _proxy_post(f"/internal/api/explore/industries/{industry_id}", payload)


@router.post("/explore/industries/{industry_id}/deactivate")
def deactivate_industry(request: Request, industry_id: int):
    require_super_admin(request)
    return _proxy_post(f"/internal/api/explore/industries/{industry_id}/deactivate")


# ---- Explore: news ingestion ----

# Production incident: ingestion fetches every configured RSS feed and
# routinely takes longer than the internal client's normal, fast-API-call
# timeout (see app/internal_client.py's own default) -- this is the one
# proxy call known to genuinely need longer, so it alone gets an explicit
# override rather than raising the timeout for every internal API call.
# Chosen with headroom over the sibling's own worst-case bound for this
# operation (app/explore_ingest.py's per-feed timeout x its concurrency
# batching), and within both services' 60-second function ceiling
# (vercel.json).
_INGESTION_TIMEOUT_SECONDS = 45.0


@router.post("/explore/ingest")
def trigger_ingestion(request: Request):
    require_super_admin(request)
    return _proxy_post("/internal/api/explore/ingest", timeout=_INGESTION_TIMEOUT_SECONDS)


# ---- Mentorship: admin ----

@router.get("/mentors/applications")
def list_mentor_applications(request: Request):
    require_super_admin(request)
    return _proxy_get("/internal/api/mentors/applications")


@router.post("/mentors/applications/{application_id}/verify")
def verify_mentor_application(request: Request, application_id: int):
    require_super_admin(request)
    user = request.app.state.db.get_user_by_id(request.session.get("user_id"))
    return _proxy_post(
        f"/internal/api/mentors/applications/{application_id}/verify",
        {"reviewed_by": user["email"] if user else "rithavo.com admin"},
    )


@router.post("/mentors/applications/{application_id}/reject")
def reject_mentor_application(request: Request, application_id: int):
    require_super_admin(request)
    user = request.app.state.db.get_user_by_id(request.session.get("user_id"))
    return _proxy_post(
        f"/internal/api/mentors/applications/{application_id}/reject",
        {"reviewed_by": user["email"] if user else "rithavo.com admin"},
    )


@router.get("/mentors/sessions")
def list_all_sessions(request: Request):
    require_super_admin(request)
    return _proxy_get("/internal/api/mentors/sessions")


# ---- Users ----

@router.get("/users")
def search_users(request: Request, q: str = "", limit: int = 25):
    require_super_admin(request)
    return _proxy_get("/internal/api/users", params={"q": q, "limit": limit})
