"""
Home/Explore/Admin Integration Phase — Super Admin bootstrap and
authorization (rithavo.com side). The account itself rides the existing
shared users table + password auth; super_admins membership is the one
new, explicit authorization fact this service owns.
"""

from app.super_admin import bootstrap_super_admin

from .conftest import login_via_magic_link


def test_bootstrap_creates_account_and_membership(db):
    bootstrap_super_admin(db, "admin@example.com", "correct horse battery staple")
    user = db.get_user_by_email("admin@example.com")
    assert user is not None
    assert user["password_hash"]
    assert db.is_super_admin(user["id"]) is True


def test_bootstrap_is_idempotent_no_duplicate_account(db):
    bootstrap_super_admin(db, "admin2@example.com", "password-one")
    bootstrap_super_admin(db, "admin2@example.com", "password-two")
    user = db.get_user_by_email("admin2@example.com")
    from app.auth import verify_password
    assert verify_password("password-two", user["password_hash"])


def test_bootstrap_does_nothing_when_email_unset(db):
    bootstrap_super_admin(db, None, "some-password")
    assert db.get_user_by_email("") is None


def test_bootstrap_does_nothing_when_password_unset(db):
    bootstrap_super_admin(db, "no-password-admin@example.com", None)
    assert db.get_user_by_email("no-password-admin@example.com") is None


def test_bootstrap_never_creates_a_passwordless_admin(db):
    # Both must be set together -- confirmed by the two tests above,
    # restated explicitly as the actual security property being tested.
    bootstrap_super_admin(db, "half-configured@example.com", "")
    assert db.get_user_by_email("half-configured@example.com") is None


def test_require_super_admin_accepts_a_bootstrapped_admin(app_and_client, db):
    app, client = app_and_client
    bootstrap_super_admin(db, "real-admin@example.com", "a-real-password")
    login_via_magic_link(client, app, "real-admin@example.com")
    resp = client.get("/api/admin/explore/stories")
    # Not 401/403 -- may still fail with 502/503 against an unconfigured
    # internal service secret in this test environment, which is a
    # separate, already-covered concern (test_routes_admin.py).
    assert resp.status_code not in (401, 403)


def test_require_super_admin_rejects_an_ordinary_signed_in_user(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "ordinary-user@example.com")
    resp = client.get("/api/admin/explore/stories")
    assert resp.status_code == 403
