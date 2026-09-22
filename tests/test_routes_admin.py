"""
Home/Explore/Admin Integration Phase — app/routes_admin.py. Covers
authorization (require_super_admin) and pass-through/error-mapping
only; the sibling's own CRUD/state-machine correctness is covered in
rithavo-career-profile/tests/test_internal_api.py. internal_client
get/post/post_file are monkeypatched at the module boundary.
"""

import pytest

from app.internal_client import InternalServiceError
from app.super_admin import bootstrap_super_admin

from .conftest import login_via_magic_link


@pytest.fixture
def admin_client(app_and_client, db):
    app, client = app_and_client
    bootstrap_super_admin(db, "dashboard-admin@example.com", "a-real-password")
    login_via_magic_link(client, app, "dashboard-admin@example.com")
    return app, client


# ---- Authorization ----

def test_unauthenticated_visitor_is_rejected(app_and_client):
    _, client = app_and_client
    resp = client.get("/api/admin/explore/stories")
    assert resp.status_code == 401


def test_ordinary_signed_in_user_is_rejected(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "not-an-admin@example.com")
    resp = client.get("/api/admin/explore/stories")
    assert resp.status_code == 403


# ---- Pass-through ----

def test_list_stories_proxies_to_sibling(admin_client, monkeypatch):
    app, client = admin_client
    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_get", lambda path, params=None: {"stories": [{"id": 1}]})
    resp = client.get("/api/admin/explore/stories")
    assert resp.status_code == 200
    assert resp.json() == {"stories": [{"id": 1}]}


def test_create_story_forwards_payload(admin_client, monkeypatch):
    app, client = admin_client
    captured = {}

    def _fake_post(path, json_body=None):
        captured["path"] = path
        captured["body"] = json_body
        return {"id": 1}

    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_post", _fake_post)
    resp = client.post("/api/admin/explore/stories", json={"headline": "H", "summary": "S"})
    assert resp.status_code == 200
    assert captured["path"] == "/internal/api/explore/stories"
    assert captured["body"] == {"headline": "H", "summary": "S"}


def test_publish_and_unpublish_proxy(admin_client, monkeypatch):
    app, client = admin_client
    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_post", lambda path, json_body=None: {"ok": True})
    assert client.post("/api/admin/explore/stories/1/publish").json() == {"ok": True}
    assert client.post("/api/admin/explore/stories/1/unpublish").json() == {"ok": True}


def test_verify_mentor_application_includes_admin_email(admin_client, monkeypatch):
    app, client = admin_client
    captured = {}

    def _fake_post(path, json_body=None):
        captured["path"] = path
        captured["body"] = json_body
        return {"status": "VERIFIED"}

    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_post", _fake_post)
    resp = client.post("/api/admin/mentors/applications/7/verify")
    assert resp.status_code == 200
    assert captured["path"] == "/internal/api/mentors/applications/7/verify"
    assert captured["body"]["reviewed_by"] == "dashboard-admin@example.com"


def test_sibling_failure_maps_to_error_without_leaking_detail(admin_client, monkeypatch):
    app, client = admin_client

    def _raise(path, params=None):
        raise InternalServiceError("internal detail that must not leak", status_code=503)

    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_get", _raise)
    resp = client.get("/api/admin/explore/stories")
    assert resp.status_code == 503
    assert "internal detail" not in resp.text


def test_search_users_proxies_query(admin_client, monkeypatch):
    app, client = admin_client
    captured = {}

    def _fake_get(path, params=None):
        captured["params"] = params
        return {"users": []}

    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)
    client.get("/api/admin/users", params={"q": "someone"})
    assert captured["params"]["q"] == "someone"
