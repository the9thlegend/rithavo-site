"""
Profile redesign (R1) -- the ONLY place this service calls the profile service's /internal/api/cards/* and
/internal/api/me/* endpoints.

Server-to-server only; nothing here is reachable from a browser. It authenticates with the DEDICATED profile secret
(config.PROFILE_SERVICE_SECRET), never the general internal-service secret, and identifies the acting user with an
X-Rithavo-User-Id header that the caller takes from THIS service's own session (app/security.py require_user), never from
the request body, URL or any client-supplied value.

Failure behaviour (all fail closed):
  - secret unset or shorter than MIN_SECRET_LENGTH  -> ProfileNotConfigured (callers answer 404: the feature is off)
  - network error or timeout                        -> ProfileUnavailable   (callers answer a generic 502)
The secret, its length, a prefix or a hash are never logged and never appear in an exception message.
"""

import httpx

import config

REQUEST_TIMEOUT_SECONDS = 10.0
MIN_SECRET_LENGTH = 32


class ProfileNotConfigured(Exception):
    pass


class ProfileUnavailable(Exception):
    pass


class ProfileResult:
    def __init__(self, status: int, json_body, content: bytes, headers: dict):
        self.status = status
        self.json = json_body
        self.content = content
        self.headers = headers


def is_configured() -> bool:
    secret = config.PROFILE_SERVICE_SECRET
    return bool(secret) and len(secret) >= MIN_SECRET_LENGTH


def _do_request(method: str, url: str, headers: dict, json_body, timeout: float):
    """The single network call. Tests replace this function; nothing else in this module touches the network."""
    return httpx.request(method, url, headers=headers, json=json_body, timeout=timeout)


def call(method: str, path: str, viewer_user_id: int, json_body=None) -> ProfileResult:
    if not is_configured():
        raise ProfileNotConfigured()
    if isinstance(viewer_user_id, bool) or not isinstance(viewer_user_id, int) or viewer_user_id < 1:
        raise ValueError("viewer_user_id must be a positive integer from the session")
    headers = {
        "Authorization": f"Bearer {config.PROFILE_SERVICE_SECRET}",
        "X-Rithavo-User-Id": str(viewer_user_id),
        "Accept": "application/json, image/*",
    }
    try:
        resp = _do_request(method, f"{config.INTERNAL_SERVICE_BASE_URL}{path}", headers, json_body, REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        raise ProfileUnavailable() from None
    body = None
    if "application/json" in resp.headers.get("content-type", ""):
        try:
            body = resp.json()
        except ValueError:
            body = None
    return ProfileResult(resp.status_code, body, resp.content, {k.lower(): v for k, v in resp.headers.items()})
