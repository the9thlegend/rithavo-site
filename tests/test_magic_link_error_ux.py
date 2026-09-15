"""
P0.5 auth hardening — GET /auth/verify's failure branch used to return
raw JSON ({"error": "..."}), which a browser renders as plain text
since this route is exactly what the emailed link points at. It now
redirects to /sign-in/?auth_error=<code> instead, where the existing
banner UI renders a proper Rithavo-styled message. auth.py itself
(token generation/verification/consumption) is completely unchanged —
only the failure *presentation* in main.py and the sign-in page's own
banner handling changed.
"""

import re

import config
import pytest
from itsdangerous import URLSafeTimedSerializer

from .conftest import login_via_magic_link, seed_minimal_profile

TEST_SECRET = "test-session-secret-for-auth-error-ux"


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    monkeypatch.setattr(config, "SESSION_SECRET", TEST_SECRET)


def _issue_test_token(email: str, secret: str = TEST_SECRET) -> str:
    return URLSafeTimedSerializer(secret, salt="rithavo-web-magic-link").dumps(
        {"email": email, "nonce": "test-nonce"},
    )


def test_malformed_token_redirects_to_sign_in_with_invalid_code(app_and_client):
    app, client = app_and_client
    resp = client.get("/auth/verify?token=not-a-real-token-at-all", follow_redirects=False)
    assert resp.status_code == 302  # a redirect, never a JSON body
    location = resp.headers["location"]
    assert location.endswith("/sign-in/?auth_error=invalid")


def test_tampered_signature_redirects_with_invalid_code(app_and_client):
    app, client = app_and_client
    token = _issue_test_token("tampered@example.com", secret="a-different-wrong-secret")
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/sign-in/?auth_error=invalid")


def test_expired_token_redirects_with_expired_code(app_and_client):
    import app.main as main_module

    app, client = app_and_client
    token = _issue_test_token("expired-link@example.com")
    original_max_age = None
    import app.auth as auth_module
    original_max_age = auth_module.TOKEN_MAX_AGE_SECONDS
    try:
        auth_module.TOKEN_MAX_AGE_SECONDS = -1
        resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    finally:
        auth_module.TOKEN_MAX_AGE_SECONDS = original_max_age
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/sign-in/?auth_error=expired")


def test_already_used_token_redirects_with_used_code(app_and_client, db):
    app, client = app_and_client
    app.state.email_sender.sent.clear()
    client.post("/auth/start", data={"email": "reuse-check@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)

    first = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert first.status_code == 302  # consumed successfully, onboarding/home

    second = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert second.status_code == 302
    assert second.headers["location"].endswith("/sign-in/?auth_error=used")


def test_failure_redirect_never_contains_the_raw_token_or_exception_text(app_and_client):
    app, client = app_and_client
    token = _issue_test_token("no-leak@example.com", secret="wrong-secret")
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    location = resp.headers["location"]
    assert token not in location
    assert "invalid" in location or "expired" in location or "used" in location
    # Only the closed set of display codes — never raw exception wording.
    assert "signature" not in location.lower()
    assert "itsdangerous" not in location.lower()


def test_successful_verification_is_completely_unaffected(app_and_client, db):
    """The success path (real profile-exists routing) must remain byte-
    for-byte identical to before this change."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "still-works@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")

    app.state.email_sender.sent.clear()
    client.post("/auth/start", data={"email": "still-works@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/home/")


def test_sign_in_page_renders_the_correct_banner_copy_for_each_error_code():
    """Static content check on the actual served page — the three
    documented codes each map to a distinct, non-technical message, and
    an unrecognized code still falls back to a generic one rather than
    breaking."""
    import pathlib
    html = pathlib.Path(__file__).resolve().parent.parent.joinpath("sign-in", "index.html").read_text(encoding="utf-8")
    assert "auth_error" in html
    assert "expired" in html and "has expired" in html
    assert "used" in html and "already been used" in html
    assert "invalid" in html and "isn't valid" in html
    assert "didn't work" in html  # generic fallback for an unrecognized code
