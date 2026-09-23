"""
Customer-Facing Profile Routing Correction — app/profile_proxy.py and
the /profile/edit routes in app/main.py. rithavo.com must never send a
customer to app.rithavo.com for Profile 2.0; this reverse-proxies the
sibling's real /profile and /profile/edit routes instead, authenticated
via the same shared INTERNAL_SERVICE_SECRET internal_client.py already
uses. The sibling's own Profile 2.0 correctness (experience CRUD,
completeness, DNA) is covered in rithavo-career-profile's own test
suite; these tests only confirm this service's own routing, link
rewriting, and pass-through behavior.
"""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import config
from app import profile_proxy as profile_proxy_module

from .conftest import login_via_magic_link

TEST_SECRET = "test-internal-service-secret"
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _service_secret(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_SERVICE_SECRET", TEST_SECRET)


@pytest.fixture
def app_and_client(db):
    from app.main import app
    app.state.db = db
    return app, TestClient(app)


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_sibling(monkeypatch, handler):
    """Replaces the real network call with an httpx.MockTransport so no
    request ever leaves the process -- `handler(request) -> httpx.Response`."""
    def _fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(profile_proxy_module.httpx, "AsyncClient", _fake_async_client)


# =====================================================================
# 1 & 2 — the static customer-facing page's link
# =====================================================================

def test_static_profile_page_links_to_rithavo_com_profile_edit():
    html = (REPO_ROOT / "profile" / "index.html").read_text(encoding="utf-8")
    assert 'href="/profile/edit"' in html


def test_static_profile_page_no_longer_links_to_app_rithavo_com():
    html = (REPO_ROOT / "profile" / "index.html").read_text(encoding="utf-8")
    assert "app.rithavo.com" not in html


def test_static_profile_page_navigation_otherwise_unchanged():
    """Only the one Edit-your-profile href should differ from before --
    everything else on the page (Education/Experience sections, their
    forms, the topbar mount) must be untouched."""
    html = (REPO_ROOT / "profile" / "index.html").read_text(encoding="utf-8")
    assert 'id="education-list"' in html
    assert 'id="experience-list"' in html
    assert 'RithavoApp.requireSession' in html
    assert html.count("<h2") == 2  # Education, Experience -- no new sections added


# =====================================================================
# 3 — the proxy reaches the sibling and the browser never leaves rithavo.com
# =====================================================================

def test_unauthenticated_visitor_is_redirected_to_sign_in_not_proxied(app_and_client):
    _, client = app_and_client
    resp = client.get("/profile/edit", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/sign-in/")


def test_authenticated_get_proxies_to_the_siblings_profile_view(app_and_client, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "proxy-view@example.com")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["user_id_header"] = request.headers.get("x-rithavo-user-id")
        html = '<a class="btn" href="/profile/edit">Edit profile</a>'
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _mock_sibling(monkeypatch, handler)
    resp = client.get("/profile/edit")
    assert resp.status_code == 200
    assert captured["url"] == f"{config.INTERNAL_SERVICE_BASE_URL}/profile"
    assert captured["auth"] == f"Bearer {TEST_SECRET}"
    assert captured["user_id_header"] == str(user_id)


def test_nested_path_maps_onto_the_siblings_matching_route(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "proxy-nested@example.com")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")

    _mock_sibling(monkeypatch, handler)
    client.get("/profile/edit/experience/5/edit")
    assert captured["path"] == "/profile/experience/5/edit"


def test_post_form_submission_is_forwarded_with_its_body(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "proxy-post@example.com")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.read()
        return httpx.Response(302, headers={"location": "/profile"})

    _mock_sibling(monkeypatch, handler)
    resp = client.post(
        "/profile/edit/experience/new", data={"title": "Engineer"}, follow_redirects=False,
    )
    assert captured["path"] == "/profile/experience/new"
    assert b"title=Engineer" in captured["body"]
    # And the sibling's own redirect target keeps the browser on rithavo.com,
    # remapped back into this same proxy rather than a bare, unroutable "/profile".
    assert resp.status_code == 302
    assert resp.headers["location"] == "/profile/edit"


def test_relative_links_in_returned_html_are_rewritten_to_stay_on_the_proxy(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "proxy-rewrite@example.com")

    def handler(request: httpx.Request) -> httpx.Response:
        html = (
            '<a href="/profile/edit">Edit profile</a>'
            '<form action="/profile/skills/add"></form>'
            '<a href="/profile/experience/9/edit">Edit</a>'
        )
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _mock_sibling(monkeypatch, handler)
    resp = client.get("/profile/edit")
    body = resp.text
    assert 'href="/profile/edit/edit"' in body
    assert 'action="/profile/edit/skills/add"' in body
    assert 'href="/profile/edit/experience/9/edit"' in body
    assert "app.rithavo.com" not in body


def test_sibling_service_unavailable_does_not_leak_a_raw_network_error(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "proxy-unavailable@example.com")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _mock_sibling(monkeypatch, handler)
    resp = client.get("/profile/edit")
    assert resp.status_code == 502
    assert "boom" not in resp.text


def test_no_career_profile_yet_falls_back_to_this_sites_own_onboarding(app_and_client, monkeypatch):
    """The sibling redirects a caller with no career_profiles row to its
    own /onboarding/start, which doesn't exist on rithavo.com and must
    never be exposed -- this proxy must land the browser on rithavo.com's
    own /onboarding/ instead, never a broken or foreign path."""
    app, client = app_and_client
    login_via_magic_link(client, app, "proxy-no-profile-yet@example.com")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/onboarding/start"})

    _mock_sibling(monkeypatch, handler)
    resp = client.get("/profile/edit", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/onboarding/"


def test_static_asset_reference_maps_to_the_siblings_static_path_not_profile(app_and_client, monkeypatch):
    """/static/style.css must map onto the sibling's bare /static/...
    (its actual stylesheet route), never /profile/static/... -- and the
    returned CSS must not be mistaken for HTML and rewritten."""
    app, client = app_and_client
    login_via_magic_link(client, app, "proxy-static-asset@example.com")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, headers={"content-type": "text/css"}, text="body { color: red; }")

    _mock_sibling(monkeypatch, handler)
    resp = client.get("/profile/edit/static/style.css")
    assert captured["path"] == "/static/style.css"
    assert resp.status_code == 200
    assert resp.text == "body { color: red; }"  # untouched -- not HTML, never rewritten


def test_stylesheet_link_in_returned_html_is_rewritten_to_the_proxy(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "proxy-static-link-rewrite@example.com")

    def handler(request: httpx.Request) -> httpx.Response:
        html = '<link rel="stylesheet" href="/static/style.css">'
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _mock_sibling(monkeypatch, handler)
    resp = client.get("/profile/edit")
    assert 'href="/profile/edit/static/style.css"' in resp.text
