"""
P0.5A-3 — server-to-server "fetch this user's current Card photo" access
token (rithavo.com side, issuing half; see the sibling
rithavo-career-profile's own app/photo_access.py, which verifies it).

Same shape and security philosophy as app/card_handoff.py's own token
(short-lived, itsdangerous-signed, purpose-scoped), but a DIFFERENT
dedicated secret and salt, and — unlike the Card handoff — this token
is never given to the browser at all: it is minted and consumed
entirely inside one server-to-server HTTP call this service's own
backend makes directly to app.rithavo.com, so there is deliberately no
single-use tracking table on either side for it (see the sibling
module's own docstring for why that's a considered choice, not a
missing one, for this narrower threat model).

fetch_selected_photo() is the ONLY place this service ever talks to the
sibling's photo route, and the ONLY place it ever touches
config.CARD_APP_BASE_URL for this purpose. Every failure mode (secret
unconfigured upstream by the caller, network error, timeout, non-200,
a response that isn't actually an image) collapses to a plain None —
the caller's job is to fall back to the initial-letter avatar, never to
surface a technical error or a broken-image icon.
"""

import httpx
from itsdangerous import URLSafeTimedSerializer

PHOTO_ACCESS_TOKEN_MAX_AGE_SECONDS = 45

# Short and fixed on purpose — this is a same-datacenter-class,
# synchronous backend call blocking one page load; a slow/unreachable
# sibling must fail fast, not hang the caller's own response.
_REQUEST_TIMEOUT_SECONDS = 5.0


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    # Distinct salt from card_handoff.py's "rithavo-card-handoff" — a
    # token minted for one purpose must never verify for the other.
    return URLSafeTimedSerializer(secret_key, salt="rithavo-photo-access")


def issue_photo_access_token(secret_key: str, user_id: int) -> str:
    """user_id must come from the caller's own already-authenticated
    session (see require_user in app/security.py) — never from anything
    a client could supply, exactly like issue_card_handoff_token."""
    return _serializer(secret_key).dumps({"user_id": user_id, "purpose": "photo_access"})


def fetch_selected_photo(card_app_base_url: str, token: str):
    """Returns (content_bytes, content_type) on success, or None on ANY
    failure. Server-to-server only — the browser never sees this
    request, this token, or app.rithavo.com."""
    try:
        resp = httpx.get(
            f"{card_app_base_url}/photo/internal-access",
            params={"token": token},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    content_type = resp.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        return None
    return resp.content, content_type
