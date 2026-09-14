"""
P0.5A-3 — server-to-server Card-photo access tests (rithavo.com side).

This service only ever mints the short-lived photo-access token and
proxies the sibling's response through — it never stores, caches, or
duplicates photo bytes itself (see app/photo_access.py's module
docstring). The sibling's own test suite
(rithavo-career-profile/tests/test_photo_access.py) covers token
verification and the actual photo lookup/serving.

fetch_selected_photo (the ONLY place this service ever talks to the
sibling) is monkeypatched at the network boundary for every test here —
never a real network call in this suite, the same way the payment
gateway boundary is swapped for a test double elsewhere in this repo.
"""

import config
import pytest
from itsdangerous import URLSafeTimedSerializer

from app.photo_access import fetch_selected_photo, issue_photo_access_token

from .conftest import login_via_magic_link

TEST_SECRET = "test-photo-access-secret"


@pytest.fixture(autouse=True)
def _photo_access_secret(monkeypatch):
    """Every test in this file gets a deterministic secret explicitly,
    the way a real deployment would via its own
    RITHAVO_PHOTO_ACCESS_SECRET env var. The one test that needs it
    actually unset overrides this itself."""
    monkeypatch.setattr(config, "PHOTO_ACCESS_SECRET", TEST_SECRET)


def _decode_token(token: str, secret: str = TEST_SECRET) -> dict:
    return URLSafeTimedSerializer(secret, salt="rithavo-photo-access").loads(token, max_age=45)


# =====================================================================
# issue_photo_access_token
# =====================================================================

def test_token_carries_exactly_the_user_id_and_purpose():
    """The token must carry only what's strictly required — never
    email, name, or any other identifying/profile detail."""
    token = issue_photo_access_token(TEST_SECRET, 4242)
    decoded = _decode_token(token)
    assert decoded == {"user_id": 4242, "purpose": "photo_access"}


# =====================================================================
# fetch_selected_photo — unit level, httpx boundary mocked
# =====================================================================

class _FakeResponse:
    def __init__(self, status_code, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


def test_fetch_returns_bytes_and_content_type_on_success(monkeypatch):
    import app.photo_access as photo_access_module
    monkeypatch.setattr(
        photo_access_module.httpx, "get",
        lambda url, params, timeout: _FakeResponse(200, b"fake-image-bytes", {"content-type": "image/jpeg"}),
    )
    result = fetch_selected_photo("https://app.rithavo.com", "sometoken")
    assert result == (b"fake-image-bytes", "image/jpeg")


def test_fetch_returns_none_on_non_200(monkeypatch):
    import app.photo_access as photo_access_module
    monkeypatch.setattr(photo_access_module.httpx, "get", lambda url, params, timeout: _FakeResponse(404))
    assert fetch_selected_photo("https://app.rithavo.com", "sometoken") is None


def test_fetch_returns_none_when_response_is_not_an_image(monkeypatch):
    import app.photo_access as photo_access_module
    monkeypatch.setattr(
        photo_access_module.httpx, "get",
        lambda url, params, timeout: _FakeResponse(200, b"<html>oops</html>", {"content-type": "text/html"}),
    )
    assert fetch_selected_photo("https://app.rithavo.com", "sometoken") is None


def test_fetch_returns_none_on_network_error_or_timeout(monkeypatch):
    import httpx

    import app.photo_access as photo_access_module

    def _raise(*a, **k):
        raise httpx.ConnectTimeout("simulated timeout")

    monkeypatch.setattr(photo_access_module.httpx, "get", _raise)
    assert fetch_selected_photo("https://app.rithavo.com", "sometoken") is None


# =====================================================================
# GET /profile/photo — endpoint level
# =====================================================================

def test_unauthenticated_request_is_rejected(app_and_client):
    app, client = app_and_client
    resp = client.get("/profile/photo")
    assert resp.status_code == 401


def test_secret_unconfigured_fails_closed(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "photo-endpoint-nosecret@example.com")
    monkeypatch.setattr(config, "PHOTO_ACCESS_SECRET", None)
    resp = client.get("/profile/photo")
    assert resp.status_code == 404


def test_successful_fetch_streams_the_image_through(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "photo-endpoint-success@example.com")
    import app.main as main_module
    monkeypatch.setattr(main_module, "fetch_selected_photo", lambda base_url, token: (b"real-bytes", "image/png"))
    resp = client.get("/profile/photo")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"real-bytes"


def test_sibling_failure_falls_back_to_404_not_500(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "photo-endpoint-sibling-down@example.com")
    import app.main as main_module
    monkeypatch.setattr(main_module, "fetch_selected_photo", lambda base_url, token: None)
    resp = client.get("/profile/photo")
    assert resp.status_code == 404


def test_token_always_carries_the_authenticated_sessions_own_user_id(app_and_client, monkeypatch):
    """CROSS-USER: even if a client tries to smuggle a different
    identity via a query parameter, the route defines no such
    parameter — the only user_id that can ever end up inside the
    minted token is the caller's own session_user_id. Verified by
    capturing the actual token passed to fetch_selected_photo and
    decoding it, rather than only inspecting the route's source."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "photo-endpoint-cross-user@example.com")

    captured = {}

    def _capture(base_url, token):
        captured["token"] = token
        return (b"bytes", "image/png")

    import app.main as main_module
    monkeypatch.setattr(main_module, "fetch_selected_photo", _capture)

    resp = client.get("/profile/photo", params={"user_id": user_id + 9999})
    assert resp.status_code == 200
    decoded = _decode_token(captured["token"])
    assert decoded["user_id"] == user_id
