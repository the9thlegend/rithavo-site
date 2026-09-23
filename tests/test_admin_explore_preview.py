"""
Super Admin Final Correction phase — the Explore story Preview must
stay entirely within rithavo.com. GET /api/admin/explore/stories/{id}
(already existed, used elsewhere in routes_admin.py) is what the
in-page preview modal calls instead of navigating to
app.rithavo.com/explore/{id} — this file proves that route still works
for both statuses, and that the served admin page itself contains no
navigation link to app.rithavo.com.
"""

from app.super_admin import bootstrap_super_admin
from .conftest import login_via_magic_link


def _login_admin(app, client, db):
    bootstrap_super_admin(db, "preview-admin@example.com", "a-real-password")
    login_via_magic_link(client, app, "preview-admin@example.com")


def test_admin_page_contains_no_navigation_link_to_the_sibling_service(app_and_client, db):
    """The one remaining acceptable reference to app.rithavo.com is an
    <img> src for thumbnail bytes (same established pattern the
    customer-facing Home feed already uses) -- never an <a href> or
    target=_blank that would navigate the admin's own browser there."""
    app, client = app_and_client
    _login_admin(app, client, db)
    body = client.get("/admin/").text
    assert 'target="_blank"' not in body
    assert 'href="https://app.rithavo.com' not in body
    assert "href='https://app.rithavo.com" not in body


def test_preview_proxy_works_for_a_published_story(app_and_client, db, monkeypatch):
    app, client = app_and_client
    _login_admin(app, client, db)

    def _fake_get(path, params=None):
        return {"id": 1, "headline": "Published Story", "status": "PUBLISHED", "story_type": "GLOBAL",
                "story_type_label": "Global", "summary": "s", "body": "", "why_it_matters": "",
                "source_name": "", "source_url": "", "published_at": None, "has_image": False, "industries": []}

    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)
    resp = client.get("/api/admin/explore/stories/1")
    assert resp.status_code == 200
    assert resp.json()["headline"] == "Published Story"


def test_preview_proxy_works_for_a_draft_story_too(app_and_client, db, monkeypatch):
    """Unlike the customer-facing /api/explore/{id} (PUBLISHED-only),
    the admin's own preview must work for a DRAFT story as well --
    reviewing a story before publishing it is the whole point."""
    app, client = app_and_client
    _login_admin(app, client, db)

    def _fake_get(path, params=None):
        return {"id": 2, "headline": "Draft Story", "status": "DRAFT", "story_type": "GLOBAL",
                "story_type_label": "Global", "summary": "s", "body": "", "why_it_matters": "",
                "source_name": "", "source_url": "", "published_at": None, "has_image": False, "industries": []}

    import app.routes_admin as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)
    resp = client.get("/api/admin/explore/stories/2")
    assert resp.status_code == 200
    assert resp.json()["status"] == "DRAFT"


def test_non_admin_cannot_use_the_preview_proxy(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "not-an-admin-preview@example.com")
    resp = client.get("/api/admin/explore/stories/1")
    assert resp.status_code == 403
