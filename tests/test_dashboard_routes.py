"""
Pre-Launch Registration / Live Admin Command Center phase — the new
Super-Admin-only dashboard routes (app/routes_dashboard.py). Covers
exactly the changed surface: the auth boundary (401 signed-out, 403
ordinary member, 200 Super Admin), member-count/list accuracy, and
page-view/most-visited-page calculation correctness. Explore/News
Ingestion data itself is unchanged and already covered elsewhere.
"""

import config

from app.super_admin import bootstrap_super_admin

from .conftest import login_via_magic_link, seed_minimal_profile


def _make_super_admin(app, db, email="dash-admin@example.com", password="a-real-password"):
    bootstrap_super_admin(db, email, password)
    user_id = db.get_or_create_user(email)
    return user_id


# =====================================================================
# Auth boundary
# =====================================================================

def test_dashboard_summary_requires_sign_in(app_and_client):
    _, client = app_and_client
    resp = client.get("/admin/dashboard/summary")
    assert resp.status_code == 401


def test_dashboard_members_requires_sign_in(app_and_client):
    _, client = app_and_client
    resp = client.get("/admin/dashboard/members")
    assert resp.status_code == 401


def test_dashboard_summary_rejects_an_ordinary_signed_in_member(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "ordinary-dash-user@example.com")
    resp = client.get("/admin/dashboard/summary")
    assert resp.status_code == 403


def test_dashboard_members_rejects_an_ordinary_signed_in_member(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "ordinary-dash-user-2@example.com")
    resp = client.get("/admin/dashboard/members")
    assert resp.status_code == 403


def test_dashboard_summary_allows_the_super_admin(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)
    login_via_magic_link(client, app, "dash-admin@example.com")
    resp = client.get("/admin/dashboard/summary")
    assert resp.status_code == 200
    assert "summary_cards" in resp.json()


def test_dashboard_members_allows_the_super_admin(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)
    login_via_magic_link(client, app, "dash-admin@example.com")
    resp = client.get("/admin/dashboard/members")
    assert resp.status_code == 200
    assert "members" in resp.json()


# =====================================================================
# Member-count / list accuracy
# =====================================================================

def test_total_members_reflects_every_registered_user(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)
    login_via_magic_link(client, app, "member-one@example.com")
    login_via_magic_link(client, app, "member-two@example.com")
    login_via_magic_link(client, app, "dash-admin@example.com")

    resp = client.get("/admin/dashboard/summary?range=all")
    total = resp.json()["summary_cards"]["total_members"]
    # The three logged-in emails above (member-one, member-two, and the
    # super admin itself, which is also an ordinary `users` row).
    assert total == 3


def test_member_list_includes_email_and_registration_date(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)
    login_via_magic_link(client, app, "listed-member@example.com")
    login_via_magic_link(client, app, "dash-admin@example.com")

    resp = client.get("/admin/dashboard/members")
    members = resp.json()["members"]
    emails = {m["email"] for m in members}
    assert "listed-member@example.com" in emails
    listed = next(m for m in members if m["email"] == "listed-member@example.com")
    assert listed["registered_at"]


def test_member_list_reports_profile_completeness_when_a_profile_exists(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)
    user_id = login_via_magic_link(client, app, "profile-member@example.com")
    seed_minimal_profile(db, user_id, name="Profile Member")
    login_via_magic_link(client, app, "dash-admin@example.com")

    resp = client.get("/admin/dashboard/members")
    members = resp.json()["members"]
    listed = next(m for m in members if m["email"] == "profile-member@example.com")
    assert listed["profile_completeness_pct"] == 50
    assert listed["name"] == "Profile Member"


def test_member_list_gracefully_handles_no_profile_yet(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)
    login_via_magic_link(client, app, "no-profile-member@example.com")
    login_via_magic_link(client, app, "dash-admin@example.com")

    resp = client.get("/admin/dashboard/members")
    members = resp.json()["members"]
    listed = next(m for m in members if m["email"] == "no-profile-member@example.com")
    assert listed["profile_completeness_pct"] is None
    assert listed["name"] is None


# =====================================================================
# Page-view / most-visited-page calculation correctness
# =====================================================================

def test_page_views_and_most_visited_page_are_computed_correctly(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)

    client.post("/api/analytics/event", json={"event_type": "page_view", "path": "/"})
    client.post("/api/analytics/event", json={"event_type": "page_view", "path": "/"})
    client.post("/api/analytics/event", json={"event_type": "page_view", "path": "/career/"})

    login_via_magic_link(client, app, "dash-admin@example.com")
    resp = client.get("/admin/dashboard/summary?range=all")
    sc = resp.json()["summary_cards"]
    assert sc["page_views"] == 3
    assert sc["most_visited_page"] == "/"


def test_ci_and_ad_interest_counts_are_separate(app_and_client, db):
    app, client = app_and_client
    _make_super_admin(app, db)

    client.post("/api/analytics/event", json={"event_type": "ci_interest", "path": "/career/career-intelligence/"})
    client.post("/api/analytics/event", json={"event_type": "ci_interest", "path": "/career/career-intelligence/"})
    client.post("/api/analytics/event", json={"event_type": "ad_interest", "path": "/career/application-diagnostic/"})

    login_via_magic_link(client, app, "dash-admin@example.com")
    resp = client.get("/admin/dashboard/summary?range=all")
    sc = resp.json()["summary_cards"]
    assert sc["ci_interest"] == 2
    assert sc["ad_interest"] == 1


# =====================================================================
# Launch status reflects the real, unmodified launch-lock state
# =====================================================================

def test_launch_status_locked_when_launch_date_unset(app_and_client, db, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    _make_super_admin(app, db)
    login_via_magic_link(client, app, "dash-admin@example.com")

    resp = client.get("/admin/dashboard/summary")
    body = resp.json()
    assert body["summary_cards"]["launch_status"] == "LOCKED"
    assert body["system"]["launch_date"] == "NOT YET CONFIGURED"
    assert body["system"]["registration"] == "OPEN"


def test_launch_status_open_once_launch_date_has_passed(app_and_client, db, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", "2020-01-01T00:00:00+00:00")
    _make_super_admin(app, db)
    login_via_magic_link(client, app, "dash-admin@example.com")

    resp = client.get("/admin/dashboard/summary")
    body = resp.json()
    assert body["summary_cards"]["launch_status"] == "OPEN"
    assert body["system"]["purchases"] == "OPEN"
