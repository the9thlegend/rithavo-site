"""
Pre-Launch Registration / Live Admin Command Center phase — the public,
unauthenticated client-side analytics beacon (POST /api/analytics/event)
and the two server-recorded events (registration_completed, login) that
ride the existing auth routes instead of trusting a client beacon for
them. Covers exactly this new surface -- the existing auth flows
themselves (magic link, password login) are unchanged and already
covered by test_magic_link_error_ux.py / test_password_auth.py.
"""

from .conftest import login_via_magic_link


def _event_count(db, event_type):
    with db.connect() as conn:
        return conn.execute(
            "SELECT count(*) AS c FROM analytics_events WHERE event_type = ?", (event_type,)
        ).fetchone()["c"]


# =====================================================================
# POST /api/analytics/event
# =====================================================================

def test_beacon_accepts_a_valid_event_type(app_and_client, db):
    _, client = app_and_client
    resp = client.post("/api/analytics/event", json={"event_type": "page_view", "path": "/"})
    assert resp.status_code == 200
    assert _event_count(db, "page_view") == 1


def test_beacon_silently_ignores_an_unrecognized_event_type(app_and_client, db):
    """Never surfaces as an error to the visitor -- and never lands in
    the table, since that would defeat the closed-vocabulary design."""
    _, client = app_and_client
    resp = client.post("/api/analytics/event", json={"event_type": "purchase_completed", "path": "/"})
    assert resp.status_code == 200
    with db.connect() as conn:
        total = conn.execute("SELECT count(*) AS c FROM analytics_events").fetchone()["c"]
    assert total == 0


def test_beacon_sets_a_first_party_session_cookie_on_first_call(app_and_client):
    _, client = app_and_client
    resp = client.post("/api/analytics/event", json={"event_type": "page_view", "path": "/"})
    assert "rv_sid" in resp.cookies


def test_beacon_never_trusts_a_client_supplied_session_or_user_id(app_and_client, db):
    """The public beacon body only ever accepts event_type/path -- a
    forged session_id or user_id in the request is simply not a field
    the route reads, so it can't be smuggled in this way."""
    _, client = app_and_client
    resp = client.post(
        "/api/analytics/event",
        json={"event_type": "page_view", "path": "/", "session_id": "attacker-chosen", "user_id": 999999},
    )
    assert resp.status_code == 200
    with db.connect() as conn:
        row = conn.execute(
            "SELECT session_id, user_id FROM analytics_events WHERE event_type = 'page_view'"
        ).fetchone()
    assert row["session_id"] != "attacker-chosen"
    assert row["user_id"] is None


def test_beacon_attaches_the_real_user_id_once_authenticated(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "beacon-user@example.com")
    client.post("/api/analytics/event", json={"event_type": "ci_interest", "path": "/career/career-intelligence/"})
    with db.connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM analytics_events WHERE event_type = 'ci_interest'"
        ).fetchone()
    assert row["user_id"] == user_id


# =====================================================================
# Server-recorded registration_completed / login
# =====================================================================

def test_first_magic_link_verification_records_registration_completed(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "brand-new-member@example.com")
    assert _event_count(db, "registration_completed") == 1
    assert _event_count(db, "login") == 0


def test_repeat_magic_link_verification_records_login_not_registration(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "returning-member@example.com")
    login_via_magic_link(client, app, "returning-member@example.com")
    assert _event_count(db, "registration_completed") == 1
    assert _event_count(db, "login") == 1


def test_password_login_records_a_login_event(app_and_client, db):
    from app.auth import hash_password
    app, client = app_and_client
    user_id = db.get_or_create_user("password-login-member@example.com")
    db.set_user_password(user_id, hash_password("a-real-password"))

    resp = client.post(
        "/auth/login",
        json={"email": "password-login-member@example.com", "password": "a-real-password"},
    )
    assert resp.status_code == 200
    assert _event_count(db, "login") == 1
    assert _event_count(db, "registration_completed") == 0


def test_analytics_recording_failure_never_breaks_login(app_and_client, db, monkeypatch):
    """_record_session_event swallows its own exceptions -- a broken
    analytics write must never turn into a failed sign-in."""
    app, client = app_and_client

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated analytics failure")

    monkeypatch.setattr(db, "record_analytics_event", _boom)
    resp = client.post("/auth/start", data={"email": "resilient-member@example.com"})
    assert resp.status_code == 200
    import re
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 302
