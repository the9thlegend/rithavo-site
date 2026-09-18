"""
P0 (conventional sign-in rework) — email + password authentication,
alongside the existing, unmodified magic-link flow. Covers exactly the
changed surface: password hashing/verification, first-time setup via the
reset-link flow (an existing magic-link-only member has no password
until they use it), successful/failed login, rate limiting,
account-enumeration protection, and that the existing magic-link and
protected-route behavior are unaffected.
"""

import re

import pytest

from app.auth import hash_password, verify_password
from app.db import Database

from .conftest import login_via_magic_link


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "password-auth-test.db")
    database.init_schema()
    return database


@pytest.fixture
def app_and_client(db):
    from app.main import app
    from fastapi.testclient import TestClient
    app.state.db = db
    app.state.email_sender.sent.clear()
    app.state.auth_rate_limiter.reset()
    app.state.login_rate_limiter.reset()
    return app, TestClient(app)


def _reset_token_from_last_email(app):
    body = app.state.email_sender.sent[-1]["body"]
    match = re.search(r"token=(\S+)", body)
    assert match, "expected a token= link in the sent email body"
    return match.group(1)


# =====================================================================
# Password hashing (unit level)
# =====================================================================

def test_password_hash_never_stores_plaintext():
    stored = hash_password("correct-horse-battery-staple")
    assert "correct-horse-battery-staple" not in stored
    assert stored.startswith("scrypt$")


def test_password_verify_accepts_correct_and_rejects_wrong():
    stored = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", stored) is True
    assert verify_password("wrong-password", stored) is False


def test_password_verify_is_safe_against_malformed_stored_hash():
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False


# =====================================================================
# New password setup for an existing (magic-link-only) member
# =====================================================================

def test_existing_magic_link_member_can_set_a_password_via_reset_link(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "existing-member@example.com")
    assert db.get_user_by_id(user_id)["password_hash"] is None

    resp = client.post("/auth/password/forgot", json={"email": "existing-member@example.com"})
    assert resp.status_code == 200
    token = _reset_token_from_last_email(app)

    resp = client.post("/auth/password/reset", json={"token": token, "password": "a-new-password-123"})
    assert resp.status_code == 200
    assert resp.json()["redirect"]

    updated = db.get_user_by_id(user_id)
    assert updated["password_hash"] is not None
    assert verify_password("a-new-password-123", updated["password_hash"])


def test_setting_a_password_never_creates_a_second_users_row(app_and_client, db):
    """The critical existing-member safety requirement: no duplicate
    identity, no new users row, existing profile/session identity
    untouched."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "no-duplicate@example.com")

    with db.connect() as conn:
        count_before = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    client.post("/auth/password/forgot", json={"email": "no-duplicate@example.com"})
    token = _reset_token_from_last_email(app)
    client.post("/auth/password/reset", json={"token": token, "password": "a-new-password-123"})

    with db.connect() as conn:
        count_after = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    assert count_after == count_before
    assert db.get_or_create_user("no-duplicate@example.com") == user_id


def test_password_reset_requires_a_minimum_length(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "short-password@example.com")
    client.post("/auth/password/forgot", json={"email": "short-password@example.com"})
    token = _reset_token_from_last_email(app)

    resp = client.post("/auth/password/reset", json={"token": token, "password": "short"})
    assert resp.status_code == 422


# =====================================================================
# Login
# =====================================================================

def _set_password(app, client, db, email, password):
    login_via_magic_link(client, app, email)
    client.post("/auth/password/forgot", json={"email": email})
    token = _reset_token_from_last_email(app)
    client.post("/auth/password/reset", json={"token": token, "password": password})
    client.post("/auth/logout")


def test_successful_password_login(app_and_client, db):
    app, client = app_and_client
    _set_password(app, client, db, "login-success@example.com", "correct-password-1")

    resp = client.post("/auth/login", json={"email": "login-success@example.com", "password": "correct-password-1"})
    assert resp.status_code == 200
    assert resp.json()["redirect"]


def test_incorrect_password_is_rejected_with_a_generic_error(app_and_client, db):
    app, client = app_and_client
    _set_password(app, client, db, "login-wrong@example.com", "correct-password-1")

    resp = client.post("/auth/login", json={"email": "login-wrong@example.com", "password": "wrong-password"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


def test_login_for_unknown_email_gives_the_same_generic_error_as_wrong_password(app_and_client, db):
    """Account-enumeration protection: an attacker must not be able to
    tell "no such account" apart from "wrong password" from the response
    alone."""
    app, client = app_and_client
    _set_password(app, client, db, "enum-check@example.com", "correct-password-1")

    resp_unknown = client.post("/auth/login", json={"email": "never-signed-up@example.com", "password": "anything"})
    resp_wrong = client.post("/auth/login", json={"email": "enum-check@example.com", "password": "wrong-password"})
    assert resp_unknown.status_code == resp_wrong.status_code == 401
    assert resp_unknown.json()["detail"] == resp_wrong.json()["detail"]


def test_login_for_magic_link_only_member_with_no_password_gives_the_same_generic_error(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "no-password-set@example.com")

    resp = client.post("/auth/login", json={"email": "no-password-set@example.com", "password": "anything"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


def test_login_rate_limiting(app_and_client, db):
    app, client = app_and_client
    _set_password(app, client, db, "rate-limit-check@example.com", "correct-password-1")

    for _ in range(5):
        resp = client.post("/auth/login", json={"email": "rate-limit-check@example.com", "password": "wrong"})
        assert resp.status_code == 401
    resp = client.post("/auth/login", json={"email": "rate-limit-check@example.com", "password": "wrong"})
    assert resp.status_code == 429


def test_password_forgot_gives_the_same_generic_message_regardless_of_account_existence(app_and_client, db):
    app, client = app_and_client
    resp_known = client.post("/auth/password/forgot", json={"email": "existing-member@example.com"})
    resp_unknown = client.post("/auth/password/forgot", json={"email": "never-existed@example.com"})
    assert resp_known.status_code == resp_unknown.status_code == 200
    assert resp_known.json()["message"] == resp_unknown.json()["message"]


# =====================================================================
# Existing magic-link flow + protected routes unaffected
# =====================================================================

def test_existing_magic_link_flow_still_works_unmodified(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "still-works@example.com")
    assert user_id == db.get_or_create_user("still-works@example.com")


def test_protected_route_still_requires_a_session(app_and_client, db):
    app, client = app_and_client
    resp = client.get("/career-intelligence")
    assert resp.status_code == 401


def test_protected_route_works_after_password_login(app_and_client, db):
    app, client = app_and_client
    _set_password(app, client, db, "protected-route-check@example.com", "correct-password-1")
    client.post("/auth/login", json={"email": "protected-route-check@example.com", "password": "correct-password-1"})

    resp = client.get("/career-intelligence")
    assert resp.status_code == 200
