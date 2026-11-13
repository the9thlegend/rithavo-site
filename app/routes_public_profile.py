"""
Profile redesign (R1): the rithavo.com public profile and its owner management, as native routes.

Browser -> these routes (session required) -> app/profile_client.py -> the profile service's slug-based endpoints. The
service applies every visibility rule; this module only authenticates the member, takes the viewer id from the member's OWN
session, limits request rates, and relays a vetted response. Nothing here redirects to another host.

Page:  GET /c/{slug}                        static shell (flag RITHAVO_PUBLIC_PROFILE_ENABLED)
APIs:  /api/public-profile/{slug}[/photo|/follow|/unfollow|/share]     (public flag)
       /api/my-public-profile, /api/my-photos, /api/my-photo           (flag RITHAVO_PROFILE_MANAGEMENT_ENABLED)
(The Vercel entrypoint strips the leading /api, so the API routes below are registered without it.)

Every flag is OFF by default: a disabled feature answers the same 404 as an unknown path, before any authentication,
so its existence is not revealed. A missing or short profile secret behaves exactly like a disabled flag.
"""

import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

import config
from app import profile_client
from app.profile_client import ProfileNotConfigured, ProfileUnavailable
from app.security import require_user

logger = logging.getLogger("rithavo_web.profile")
router = APIRouter()

SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SHELL_PATH = Path(__file__).resolve().parent.parent / "c" / "index.html"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}
MAX_BODY_BYTES = 10_000

LOGO_STYLE_ATTR_HASH = "/3kWSXHts8LrwfemLzY9W0tOv5I4eLIhrf0pT8cU0WI="  # base64 SHA-256 of: position:absolute

# Nothing on this surface may be cached by a browser, a proxy or the service worker, or content-sniffed.
NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
PAGE_HEADERS = {
    **NO_STORE,
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        # The verbatim logo markup carries exactly one inline style attribute (style="position:absolute" on its hidden gradient
        # definitions). Only that exact value is permitted, by hash; no other inline style is.
        f"style-src-attr 'unsafe-hashes' 'sha256-{LOGO_STYLE_ATTR_HASH}'; "
        "img-src 'self' blob: data:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}

# Keys that must never reach a browser from this surface. The service never sends them; this is defence in depth.
FORBIDDEN_KEYS = {
    "id", "user_id", "person_user_id", "owner_user_id", "followee_id", "follower_id", "card_user_id", "business_id",
    "photo_id", "profile_photo_id", "email", "contact_email", "contact_phone",
}
# The owner's OWN management payload legitimately carries capability and photo ids and the owner's own contact details.
OWNER_PAYLOAD_ALLOWED_KEYS = {"id", "contact_email", "contact_phone"}


class _Limiter:
    """Best-effort in-memory sliding window, per viewer. Per-process, so on serverless it limits per instance only; a
    durable limiter would need a new table, which this release does not add."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits = defaultdict(list)

    def _live(self, key):
        cutoff = time.time() - self.window
        self._hits[key] = [t for t in self._hits[key] if t > cutoff]
        return self._hits[key]

    def blocked(self, key) -> bool:
        return len(self._live(key)) >= self.limit

    def hit(self, key) -> None:
        self._live(key).append(time.time())

    def reset(self) -> None:
        self._hits.clear()


reads = _Limiter(limit=120, window_seconds=60)
misses = _Limiter(limit=20, window_seconds=300)   # slug probing: counts only unknown or hidden profiles
writes = _Limiter(limit=30, window_seconds=60)


def reset_limiters() -> None:
    for limiter in (reads, misses, writes):
        limiter.reset()


def _json(payload, status: int = 200, extra: dict = None) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers={**NO_STORE, **(extra or {})})


def _not_found() -> JSONResponse:
    return _json({"detail": "Not found."}, 404)


def _too_many() -> JSONResponse:
    return _json({"detail": "Too many requests. Please wait a moment and try again."}, 429, {"Retry-After": "60"})


def _enabled(flag: bool) -> bool:
    return bool(flag) and profile_client.is_configured()


def _has_forbidden_keys(value, allowed: set = frozenset()) -> bool:
    if isinstance(value, dict):
        return any((k in FORBIDDEN_KEYS and k not in allowed) or _has_forbidden_keys(v, allowed)
                   for k, v in value.items())
    if isinstance(value, list):
        return any(_has_forbidden_keys(v, allowed) for v in value)
    return False


def _clean_error(body) -> dict:
    detail = body.get("detail") if isinstance(body, dict) else None
    code = body.get("code") if isinstance(body, dict) else None
    out = {"detail": detail[:200] if isinstance(detail, str) else "Request not allowed."}
    if isinstance(code, str):
        out["code"] = code[:40]
    return out


def _relay(result, *, owner_payload: bool = False) -> JSONResponse:
    """Maps the service's answer to ours. Unknown statuses become a generic 502: service internals never reach a browser."""
    if result.status == 200 and isinstance(result.json, (dict, list)):
        if _has_forbidden_keys(result.json, OWNER_PAYLOAD_ALLOWED_KEYS if owner_payload else frozenset()):
            logger.warning("profile_upstream outcome=forbidden_field")
            return _json({"detail": "Profile service is temporarily unavailable."}, 502)
        return _json(result.json)
    if result.status == 404:
        return _not_found()
    if result.status in (400, 403, 409, 422):
        return _json(_clean_error(result.json), result.status)
    logger.warning("profile_upstream outcome=unexpected status=%s", result.status)
    return _json({"detail": "Profile service is temporarily unavailable."}, 502)


def _call(method: str, path: str, viewer: int, body=None):
    try:
        return profile_client.call(method, path, viewer, body), None
    except ProfileNotConfigured:
        return None, _not_found()
    except ProfileUnavailable:
        logger.warning("profile_upstream outcome=unreachable")
        return None, _json({"detail": "Profile service is temporarily unavailable."}, 502)


def _public_gate(request: Request):
    """Returns (viewer_user_id, None) or (None, response). Flag first (404 even for anonymous callers), then the session."""
    if not _enabled(config.PUBLIC_PROFILE_ENABLED):
        return None, _not_found()
    return require_user(request), None


def _management_gate(request: Request):
    if not _enabled(config.PROFILE_MANAGEMENT_ENABLED):
        return None, _not_found()
    return require_user(request), None


def _valid_slug(slug: str) -> bool:
    return isinstance(slug, str) and bool(SLUG_RE.match(slug))


async def _read_object(request: Request, required: bool):
    """Reads a small JSON object by hand, AFTER the feature gate, so a disabled feature never answers a validation error
    (which would reveal that it exists). Returns (dict, None) or (None, error response)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None, _json({"detail": "Request not allowed."}, 422)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return None, _json({"detail": "Request not allowed."}, 422)
    if not raw.strip():
        return ({}, None) if not required else (None, _json({"detail": "Request not allowed."}, 422))
    try:
        value = json.loads(raw)
    except ValueError:
        return None, _json({"detail": "Request not allowed."}, 422)
    if not isinstance(value, dict):
        return None, _json({"detail": "Request not allowed."}, 422)
    return value, None


# ---- page ---------------------------------------------------------------------------------------------------------------

@router.get("/c/{slug}")
def public_profile_page(request: Request, slug: str):
    if not _enabled(config.PUBLIC_PROFILE_ENABLED) or not _valid_slug(slug):
        return _not_found()
    if not request.session.get("user_id"):
        return RedirectResponse(f"/sign-in/?next=/c/{slug}", status_code=302, headers=NO_STORE)
    if not SHELL_PATH.is_file():
        return _not_found()
    return FileResponse(SHELL_PATH, media_type="text/html", headers=PAGE_HEADERS)


# ---- public profile APIs -------------------------------------------------------------------------------------------------

@router.get("/public-profile/{slug}")
def read_public_profile(request: Request, slug: str):
    viewer, early = _public_gate(request)
    if early:
        return early
    if reads.blocked(viewer) or misses.blocked(viewer):
        return _too_many()
    if not _valid_slug(slug):
        misses.hit(viewer)
        return _not_found()
    reads.hit(viewer)
    result, err = _call("GET", f"/internal/api/cards/{slug}", viewer)
    if err:
        return err
    if result.status == 404:
        misses.hit(viewer)
    return _relay(result)


@router.get("/public-profile/{slug}/photo")
def read_public_profile_photo(request: Request, slug: str):
    viewer, early = _public_gate(request)
    if early:
        return early
    if reads.blocked(viewer) or misses.blocked(viewer):
        return _too_many()
    if not _valid_slug(slug):
        misses.hit(viewer)
        return _not_found()
    reads.hit(viewer)
    result, err = _call("GET", f"/internal/api/cards/{slug}/photo", viewer)
    if err:
        return err
    if result.status == 404:
        misses.hit(viewer)
        return _not_found()
    content_type = (result.headers.get("content-type") or "").split(";")[0].strip().lower()
    if result.status != 200 or content_type not in ALLOWED_IMAGE_TYPES or not result.content:
        return _not_found()
    headers = {
        **NO_STORE,
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Rithavo-Photo-Alpha": "1" if result.headers.get("x-rithavo-photo-alpha") == "1" else "0",
    }
    return Response(content=result.content, media_type=content_type, headers=headers)


def _write(request: Request, slug: str, action: str, body=None, viewer: int = None):
    if viewer is None:
        viewer, early = _public_gate(request)
        if early:
            return early
    if writes.blocked(viewer) or misses.blocked(viewer):
        return _too_many()
    if not _valid_slug(slug):
        misses.hit(viewer)
        return _not_found()
    writes.hit(viewer)
    result, err = _call("POST", f"/internal/api/cards/{slug}/{action}", viewer, body)
    if err:
        return err
    if result.status == 404:
        misses.hit(viewer)
    return _relay(result)


@router.post("/public-profile/{slug}/follow")
def follow_public_profile(request: Request, slug: str):
    return _write(request, slug, "follow")


@router.post("/public-profile/{slug}/unfollow")
def unfollow_public_profile(request: Request, slug: str):
    return _write(request, slug, "unfollow")


@router.post("/public-profile/{slug}/share")
async def share_public_profile(request: Request, slug: str):
    """Only the two contact choices are relayed; the recipient is the slug's owner and is never client-supplied."""
    viewer, early = _public_gate(request)
    if early:
        return early
    payload, bad = await _read_object(request, required=False)
    if bad:
        return bad
    body = {k: payload[k] for k in ("share_email", "share_phone") if isinstance(payload.get(k), bool)}
    return _write(request, slug, "share", body, viewer=viewer)


# ---- owner management APIs ----------------------------------------------------------------------------------------------

def _manage(viewer: int, method: str, path: str, body=None):
    limiter = writes if method == "POST" else reads
    if limiter.blocked(viewer):
        return _too_many()
    limiter.hit(viewer)
    result, err = _call(method, path, viewer, body)
    if err:
        return err
    return _relay(result, owner_payload=True)


@router.get("/my-public-profile")
def read_my_public_profile(request: Request):
    viewer, early = _management_gate(request)
    return early or _manage(viewer, "GET", "/internal/api/me/card")


@router.post("/my-public-profile")
async def update_my_public_profile(request: Request):
    viewer, early = _management_gate(request)
    if early:
        return early
    payload, bad = await _read_object(request, required=True)
    return bad or _manage(viewer, "POST", "/internal/api/me/card", payload)


@router.get("/my-photos")
def list_my_photos(request: Request):
    viewer, early = _management_gate(request)
    return early or _manage(viewer, "GET", "/internal/api/me/photos")


@router.post("/my-photo")
async def select_my_photo(request: Request):
    viewer, early = _management_gate(request)
    if early:
        return early
    payload, bad = await _read_object(request, required=True)
    if bad:
        return bad
    photo_id = payload.get("photo_id")
    if isinstance(photo_id, bool) or not isinstance(photo_id, int):
        return _json({"detail": "Request not allowed."}, 422)
    return _manage(viewer, "POST", "/internal/api/me/photo/select", {"photo_id": photo_id})
