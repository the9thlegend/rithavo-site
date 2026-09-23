"""
Home/Explore/Admin Integration Phase, corrected by the Product
Correction Phase — the Explore feed embedded in authenticated Home.

Product Correction Phase changes:
- Explore is now authenticated-only. Every route here requires
  require_user — a logged-out visitor gets a plain 401, never feed
  content, never even the (now-removed) industry list. There is no
  separate customer-facing Explore page in this service at all; Home
  is the only place a signed-in visitor ever sees this feed.
- No customer-facing filters: story_type/industry query parameters are
  gone from this proxy entirely. The underlying taxonomy, story_type
  values, and the sibling's own filtered list_published_explore_stories
  remain untouched for Super Admin/editorial use (see routes_admin.py)
  — only the CUSTOMER filter UI and its backing request shape were
  scrapped this phase.
- The feed itself is now profile-based relevance, not the generic
  published list: this proxies to the sibling's new, real DB-paginated
  GET /internal/api/explore/relevant-feed (one combined "relevant
  first, then broader" ordering — see that endpoint's own docstring),
  passing this session's own user_id. No ranking happens here or in
  the browser; the sibling's server-side query does all of it.
"""

from fastapi import APIRouter, HTTPException, Request

from .internal_client import InternalServiceError, get as internal_get
from .security import require_user

router = APIRouter(prefix="/explore")


def _proxy_get(path: str, params: dict = None):
    try:
        return internal_get(path, params=params)
    except InternalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail="Explore is temporarily unavailable.")


@router.get("/stories")
def explore_stories(request: Request, page: int = 1, page_size: int = 12):
    user_id = require_user(request)
    return _proxy_get(
        "/internal/api/explore/relevant-feed",
        params={"user_id": user_id, "page": page, "page_size": page_size},
    )


@router.get("/{story_id}")
def explore_story_detail(request: Request, story_id: int):
    require_user(request)
    return _proxy_get(f"/explore/{story_id}/json")
