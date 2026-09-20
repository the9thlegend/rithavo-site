"""
CI validity foundation — POST /career-intelligence/resume must not be
reachable with zero Career Intelligence purchase at all. Prior behavior
(documented in the route's own comment before this phase) deliberately
skipped every entitlement check; this closes that gap with a minimal,
VIEW-type guard (find_active_ci_entitlement) that does not touch resume
formatting/tailoring at all.
"""

from .conftest import login_via_magic_link


def test_resume_generation_is_blocked_with_no_ci_entitlement_at_all(app_and_client, db):
    app, client = app_and_client
    login_via_magic_link(client, app, "no-ci-resume@example.com")
    resp = client.post("/career-intelligence/resume", json={})
    assert resp.status_code == 402
    assert "Career Intelligence purchase is required" in resp.json()["detail"]
