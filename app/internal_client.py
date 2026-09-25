"""
Home/Explore/Admin Integration Phase — the ONLY place this service
talks to app.rithavo.com's internal service API
(app/routers/internal_api.py on the sibling side).

app.rithavo.com is now treated as an internal-only service boundary,
not a second user-facing product: every Explore/Mentorship/Admin
capability rithavo.com exposes to a real person is implemented as a
page in THIS service, which calls the sibling's already-built,
already-tested business logic server-to-server, through this one
client. Nothing in this file is ever reachable from a browser — no
route here, no token here, reaches app.rithavo.com's public internet
surface at all; it is a synchronous backend-to-backend HTTP call
exactly like app/photo_access.py's existing fetch_selected_photo.

Every failure mode (secret unconfigured, network error, timeout,
non-2xx) raises InternalServiceError with the sibling's own status
code attached where known — callers turn that into whatever HTTP
response fits their own route (a 502/503 for an admin page, or a quiet
fallback for something less critical), but nothing here ever leaks the
sibling's raw response body or internal detail to the browser.
"""

import httpx

import config

_REQUEST_TIMEOUT_SECONDS = 10.0


class InternalServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _headers() -> dict:
    if not config.INTERNAL_SERVICE_SECRET:
        raise InternalServiceError("Internal service secret is not configured.", status_code=503)
    return {"Authorization": f"Bearer {config.INTERNAL_SERVICE_SECRET}"}


def _handle(resp: httpx.Response):
    if resp.status_code >= 400:
        raise InternalServiceError(f"Internal service returned HTTP {resp.status_code}", status_code=resp.status_code)
    return resp.json()


def get(path: str, params: dict = None):
    try:
        resp = httpx.get(
            f"{config.INTERNAL_SERVICE_BASE_URL}{path}", params=params or {},
            headers=_headers(), timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise InternalServiceError(f"Internal service request failed: {e}", status_code=502)
    return _handle(resp)


def get_bytes(path: str) -> tuple:
    """Same authenticated GET as get(), for a binary body (an Explore
    story image): returns (content, content_type) instead of parsing
    JSON. Same failure mapping as every other call here."""
    try:
        resp = httpx.get(
            f"{config.INTERNAL_SERVICE_BASE_URL}{path}", headers=_headers(), timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise InternalServiceError(f"Internal service request failed: {e}", status_code=502)
    if resp.status_code >= 400:
        raise InternalServiceError(f"Internal service returned HTTP {resp.status_code}", status_code=resp.status_code)
    return resp.content, resp.headers.get("content-type")


def post(path: str, json_body: dict = None, timeout: float = _REQUEST_TIMEOUT_SECONDS):
    """timeout defaults to the same fixed value every other call has
    always used -- pass an explicit, larger value only for a specific
    endpoint genuinely known to take longer (see routes_admin.py's own
    call for /explore/ingest), never as a blanket increase."""
    try:
        resp = httpx.post(
            f"{config.INTERNAL_SERVICE_BASE_URL}{path}", json=json_body or {},
            headers=_headers(), timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise InternalServiceError(f"Internal service request failed: {e}", status_code=502)
    return _handle(resp)


def post_file(path: str, field_name: str, filename: str, content: bytes, content_type: str):
    try:
        resp = httpx.post(
            f"{config.INTERNAL_SERVICE_BASE_URL}{path}",
            files={field_name: (filename, content, content_type)},
            headers=_headers(), timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise InternalServiceError(f"Internal service request failed: {e}", status_code=502)
    return _handle(resp)
