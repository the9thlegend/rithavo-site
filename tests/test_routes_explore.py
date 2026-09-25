"""
Home/Explore/Admin Integration Phase, corrected by the Product
Correction Phase — app/routes_explore.py. Explore is now
authenticated-only, with no customer-facing filters: this proxy calls
the sibling's real DB-paginated GET /internal/explore/relevant-feed
with this session's own user_id, and nothing else. The sibling's own
relevance/ranking correctness is covered in
rithavo-career-profile/tests/test_relevant_feed.py and
test_explore_relevance.py; these tests only confirm this service's own
authorization and pass-through/error-mapping behavior. internal_client
get is monkeypatched at the module boundary, never a real network call.
"""

import pytest

from app.internal_client import InternalServiceError

from .conftest import login_via_magic_link


# ---- Authentication is now required ----

def test_unauthenticated_visitor_cannot_reach_the_feed(app_and_client):
    _, client = app_and_client
    resp = client.get("/explore/stories")
    assert resp.status_code == 401


def test_unauthenticated_visitor_cannot_reach_story_detail(app_and_client):
    _, client = app_and_client
    resp = client.get("/explore/1")
    assert resp.status_code == 401


# ---- Pass-through to the relevance-based feed ----

def test_stories_proxies_with_the_callers_own_user_id(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "explore-feed-user@example.com")
    captured = {}

    def _fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"stories": [], "has_more": False, "next_page": 2}

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)

    resp = client.get("/explore/stories")
    assert resp.status_code == 200
    assert captured["path"] == "/internal/api/explore/relevant-feed"
    assert captured["params"]["user_id"] == user_id
    assert captured["params"]["page"] == 1
    assert captured["params"]["page_size"] == 12


def test_stories_never_forwards_a_client_supplied_user_id(app_and_client, db, monkeypatch):
    """CROSS-USER: even if a client tries to smuggle a different
    identity via a query parameter, this route defines no such
    parameter — only the caller's own session user_id is ever sent."""
    app, client = app_and_client
    real_user_id = login_via_magic_link(client, app, "explore-cross-user@example.com")
    captured = {}

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", lambda path, params=None: captured.update(params) or {})

    client.get("/explore/stories", params={"user_id": real_user_id + 9999})
    assert captured["user_id"] == real_user_id


def test_no_story_type_or_industry_filter_parameters_exist(app_and_client, db, monkeypatch):
    """Product Correction Phase: customer-facing filters are gone --
    this route accepts no story_type/industry parameters at all, and
    never forwards any to the sibling even if a client sends them."""
    app, client = app_and_client
    login_via_magic_link(client, app, "explore-no-filters-user@example.com")
    captured = {}

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", lambda path, params=None: captured.update(params) or {})

    client.get("/explore/stories", params={"story_type": "LAYOFFS_HIRING", "industry": "5"})
    assert "story_type" not in captured
    assert "industry" not in captured


def test_industries_proxy_endpoint_no_longer_exists(app_and_client, db):
    """The customer-facing industry filter selector was scrapped this
    phase along with its backing endpoint -- the path now only ever
    matches the story-detail catch-all, which correctly 422s since
    "industries" isn't a valid story id, rather than resolving to a
    real industries listing."""
    app, client = app_and_client
    login_via_magic_link(client, app, "explore-no-industries-endpoint@example.com")
    resp = client.get("/explore/industries")
    assert resp.status_code == 422


def test_story_detail_proxies_by_id_when_authenticated(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "explore-detail-user@example.com")
    captured = {}

    def _fake_get(path, params=None):
        captured["path"] = path
        return {"id": 42}

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)
    resp = client.get("/explore/42")
    assert resp.json() == {"id": 42}
    assert captured["path"] == "/internal/api/explore/published/42"   # the service-authenticated endpoint, not a public sibling route


def test_sibling_failure_maps_to_a_generic_error_not_a_500(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "explore-failure-user@example.com")

    def _raise(path, params=None):
        raise InternalServiceError("boom", status_code=502)

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", _raise)
    resp = client.get("/explore/stories")
    assert resp.status_code == 502
    assert "boom" not in resp.text  # never leak the sibling's raw error detail


# ---- Story images are served from rithavo.com (Explore Canonical Surface hardening) ----

def test_unauthenticated_visitor_cannot_reach_a_story_image(app_and_client):
    _, client = app_and_client
    assert client.get("/explore/1/image").status_code == 401


def test_story_image_is_fetched_server_to_server_and_served_from_rithavo(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "explore-image-user@example.com")
    captured = {}

    def _fake_bytes(path):
        captured["path"] = path
        return b"PNGbytes", "image/png"

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get_bytes", _fake_bytes)
    resp = client.get("/explore/42/image")
    assert resp.status_code == 200
    assert resp.content == b"PNGbytes"
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "private, max-age=300"
    assert captured["path"] == "/internal/api/explore/published/42/image"


def test_story_image_for_a_draft_or_missing_story_is_a_404_not_a_500(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "explore-image-404-user@example.com")

    def _raise(path):
        raise InternalServiceError("not published", status_code=404)

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get_bytes", _raise)
    assert client.get("/explore/7/image").status_code == 404


def test_story_image_sibling_outage_maps_to_a_generic_error(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "explore-image-outage-user@example.com")

    def _raise(path):
        raise InternalServiceError("boom", status_code=502)

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get_bytes", _raise)
    resp = client.get("/explore/7/image")
    assert resp.status_code == 502
    assert "boom" not in resp.text


def test_the_customer_explore_page_and_script_never_point_a_browser_at_the_sibling_service():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("explore-feed.js", "home/index.html"):
        text = (root / name).read_text(encoding="utf-8")
        assert "https://app.rithavo.com" not in text, name
        assert "app.rithavo.com/explore" not in text, name
