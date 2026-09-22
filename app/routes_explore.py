"""
Home/Explore/Admin Integration Phase — the public Explore feed, proxied
server-to-server from app.rithavo.com's already-public, already-tested
GET /explore/stories, GET /explore/industries, and GET /explore/{id}/json
endpoints (app/routers/explore.py on the sibling side, itself PUBLISHED-
only and unauthenticated on that side too).

No Explore data, schema, pagination, or filtering logic is duplicated
here — every route below is a thin pass-through plus one uniform error
mapping. This same feed powers both the embedded Explore section of
rithavo.com/home/ and any dedicated /explore/ page on this domain; there
is exactly one underlying implementation (the sibling's), never two.

Public by design, matching the source: no session/auth required to read
the feed, exactly like the sibling's own /explore.
"""

from fastapi import APIRouter, HTTPException

from .internal_client import InternalServiceError, get as internal_get

router = APIRouter(prefix="/api/explore")


def _proxy_get(path: str, params: dict = None):
    try:
        return internal_get(path, params=params)
    except InternalServiceError as e:
        raise HTTPException(status_code=e.status_code, detail="Explore is temporarily unavailable.")


@router.get("/stories")
def explore_stories(page: int = 1, story_type: str = "", industry: str = ""):
    return _proxy_get("/explore/stories", params={"page": page, "story_type": story_type, "industry": industry})


@router.get("/industries")
def explore_industries():
    return _proxy_get("/explore/industries")


@router.get("/{story_id}")
def explore_story_detail(story_id: int):
    return _proxy_get(f"/explore/{story_id}/json")
