"""
Home/Explore/Admin Integration Phase — app/routes_mentor.py. Covers
authorization (require_user) and that user_id is always taken from the
caller's own session, never a client-supplied field — the sibling's own
lifecycle correctness is covered in
rithavo-career-profile/tests/test_internal_api.py.
"""

from app.internal_client import InternalServiceError

from .conftest import login_via_magic_link


def test_unauthenticated_visitor_is_rejected(app_and_client):
    _, client = app_and_client
    assert client.get("/api/mentor/discover").status_code == 401
    assert client.get("/api/mentor/status").status_code == 401
    assert client.get("/api/mentor/sessions").status_code == 401
    assert client.post("/api/mentor/apply", json={}).status_code == 401


def test_status_uses_the_callers_own_session_user_id(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "mentor-status-check@example.com")
    captured = {}

    def _fake_get(path, params=None):
        captured["params"] = params
        return {"is_verified_mentor": False, "application": None}

    import app.routes_mentor as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)
    client.get("/api/mentor/status")
    assert captured["params"]["user_id"] == user_id


def test_apply_ignores_any_client_supplied_user_id(app_and_client, db, monkeypatch):
    """CROSS-USER: even if a client tries to smuggle a different
    identity in the JSON body, the route always overwrites user_id with
    the caller's own session value."""
    app, client = app_and_client
    real_user_id = login_via_magic_link(client, app, "apply-cross-user@example.com")
    captured = {}

    def _fake_post(path, json_body=None):
        captured["body"] = json_body
        return {"id": 1}

    import app.routes_mentor as mod
    monkeypatch.setattr(mod, "internal_post", _fake_post)
    client.post("/api/mentor/apply", json={"years_of_experience": 12, "user_id": real_user_id + 9999})
    assert captured["body"]["user_id"] == real_user_id


def test_request_session_forwards_mentor_and_caller_ids(app_and_client, db, monkeypatch):
    app, client = app_and_client
    mentee_id = login_via_magic_link(client, app, "requesting-mentee@example.com")
    captured = {}

    def _fake_post(path, json_body=None):
        captured["path"] = path
        captured["body"] = json_body
        return {"id": 5}

    import app.routes_mentor as mod
    monkeypatch.setattr(mod, "internal_post", _fake_post)
    client.post("/api/mentor/42/request-session")
    assert captured["path"] == "/internal/api/mentors/42/request-session"
    assert captured["body"] == {"user_id": mentee_id}


def test_schedule_forwards_scheduled_at(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "scheduler@example.com")
    captured = {}

    def _fake_post(path, json_body=None):
        captured["body"] = json_body
        return {"ok": True}

    import app.routes_mentor as mod
    monkeypatch.setattr(mod, "internal_post", _fake_post)
    client.post("/api/mentor/sessions/1/schedule", json={"scheduled_at": "2026-02-01T10:00:00+00:00"})
    assert captured["body"]["scheduled_at"] == "2026-02-01T10:00:00+00:00"


def test_sibling_failure_maps_to_error_without_leaking_detail(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "mentor-failure-check@example.com")

    def _raise(path, params=None):
        raise InternalServiceError("internal detail that must not leak", status_code=502)

    import app.routes_mentor as mod
    monkeypatch.setattr(mod, "internal_get", _raise)
    resp = client.get("/api/mentor/discover")
    assert resp.status_code == 502
    assert "internal detail" not in resp.text
