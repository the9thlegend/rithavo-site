"""
Home/Explore/Admin Integration Phase — app/routes_explore.py, the
public Explore proxy. The sibling's own already-tested feed/pagination/
filtering logic (rithavo-career-profile/tests/test_explore_feed.py)
is not re-tested here; these tests only confirm this service's own
pass-through/error-mapping behavior. internal_client.get is
monkeypatched at the module boundary, never a real network call.
"""

import pytest

from app.internal_client import InternalServiceError


def test_stories_proxies_query_params(app_and_client, monkeypatch):
    _, client = app_and_client
    captured = {}

    def _fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"stories": [], "has_more": False, "next_page": 2}

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)

    resp = client.get("/api/explore/stories", params={"page": 2, "story_type": "LAYOFFS_HIRING"})
    assert resp.status_code == 200
    assert captured["path"] == "/explore/stories"
    assert captured["params"]["page"] == 2
    assert captured["params"]["story_type"] == "LAYOFFS_HIRING"


def test_stories_defaults_to_page_one_with_no_filters(app_and_client, monkeypatch):
    _, client = app_and_client
    captured = {}

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", lambda path, params=None: captured.update(params) or {})

    client.get("/api/explore/stories")
    assert captured["page"] == 1
    assert captured["story_type"] == ""
    assert captured["industry"] == ""


def test_industries_proxies_to_sibling(app_and_client, monkeypatch):
    _, client = app_and_client
    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", lambda path, params=None: {"tree": [{"parent": {"name": "Tech"}}]})
    resp = client.get("/api/explore/industries")
    assert resp.json()["tree"][0]["parent"]["name"] == "Tech"


def test_story_detail_proxies_by_id(app_and_client, monkeypatch):
    _, client = app_and_client
    captured = {}

    def _fake_get(path, params=None):
        captured["path"] = path
        return {"id": 42}

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", _fake_get)
    resp = client.get("/api/explore/42")
    assert resp.json() == {"id": 42}
    assert captured["path"] == "/explore/42/json"


def test_sibling_failure_maps_to_a_generic_error_not_a_500(app_and_client, monkeypatch):
    _, client = app_and_client

    def _raise(path, params=None):
        raise InternalServiceError("boom", status_code=502)

    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", _raise)
    resp = client.get("/api/explore/stories")
    assert resp.status_code == 502
    assert "boom" not in resp.text  # never leak the sibling's raw error detail


def test_no_auth_required_to_read_the_feed(app_and_client, monkeypatch):
    """Matches the source's own public nature -- Explore reading
    requires no login on either side."""
    _, client = app_and_client
    import app.routes_explore as mod
    monkeypatch.setattr(mod, "internal_get", lambda path, params=None: {"stories": []})
    resp = client.get("/api/explore/stories")
    assert resp.status_code == 200
