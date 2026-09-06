"""
Phase 2B-1.6/2B-1.7A — regression coverage for the "/api" prefix mismatch
class of bug found in this engagement: the emailed magic-link URL (built
from request.base_url) needs the "/api" prefix in production; the
post-verify redirect target needs the OPPOSITE — it must NEVER carry
"/api", since (as of Phase 2B-1.7A) it points at a static customer-facing
page (rithavo-site/home/index.html), not an API route. Both are built
from the request's own scheme+netloc/root_path rather than a hardcoded
"https://rithavo.com", so both keep working correctly under the exact
wrapping api/index.py applies in production, not just in the unprefixed
local/test shape.

This test reproduces that wrapping inline (rather than importing
rithavo-site's api/index.py, which lives in a separate repo/deployment)
so a future change to either the redirect or the link-building code
can't silently reintroduce a 404 (or, for the redirect, an incorrect
/api/home/) in production while every existing test — which never sets
root_path — keeps passing.
"""

import re

from fastapi.testclient import TestClient

from app.main import app


async def _api_prefixed(scope, receive, send):
    """Mirrors rithavo-site/api/index.py's wrapper: strips a leading
    "/api" from the path and sets root_path accordingly."""
    if scope["type"] == "http" and scope["path"].startswith("/api"):
        scope = dict(scope)
        scope["path"] = scope["path"][len("/api"):] or "/"
        scope["root_path"] = "/api"
    await app(scope, receive, send)


def test_magic_link_email_points_at_the_api_prefixed_verify_url(db):
    app.state.db = db
    app.state.email_sender.sent.clear()
    client = TestClient(_api_prefixed, base_url="https://rithavo.com")
    client.post("/api/auth/start", data={"email": "prefix-check@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    match = re.search(r"(https://\S+)", body)
    assert match, "expected a link in the email body"
    assert match.group(1).startswith("https://rithavo.com/api/auth/verify?token=")


def test_post_verify_redirect_targets_the_static_onboarding_page_never_under_api(db):
    """P0: a brand-new user (no career_profiles row) lands on /onboarding/,
    not /home/ — see test_the_same_redirect_target_is_used_in_local_dev_and_tests
    below for the existing-profile case, which still lands on /home/.
    Either way, the redirect must never carry the "/api" prefix this same
    request arrived under — that's the actual invariant this file exists
    to protect, unaffected by which static page it now points at."""
    app.state.db = db
    app.state.email_sender.sent.clear()
    app.state.auth_rate_limiter.reset()
    client = TestClient(_api_prefixed, base_url="https://rithavo.com")
    client.post("/api/auth/start", data={"email": "prefix-redirect-check@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    resp = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    # /onboarding/ is a static file served outside /api entirely — the
    # redirect must never carry the "/api" prefix this same request
    # arrived under.
    assert resp.headers["location"] == "https://rithavo.com/onboarding/"


def test_the_same_redirect_target_is_used_in_local_dev_and_tests(app_and_client, db):
    """The destination must not depend on any "/api" concept — local dev
    and this very test suite have none, and must still land on the same
    place. This user already has a career_profiles row (P0 onboarding
    only applies to brand-new users), so the target is /home/."""
    app, client = app_and_client
    app.state.email_sender.sent.clear()
    user_id = db.get_or_create_user("local-redirect-check@example.com")
    db.upsert_career_profile(user_id, {"identity": {}, "background": {}})
    client.post("/auth/start", data={"email": "local-redirect-check@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://testserver/home/"


def test_a_brand_new_user_with_no_profile_lands_on_onboarding_locally(app_and_client):
    """Same invariant as the two tests above, for the no-profile branch,
    without the "/api" wrapping — the P0 redirect logic itself."""
    app, client = app_and_client
    app.state.email_sender.sent.clear()
    client.post("/auth/start", data={"email": "brand-new-user@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://testserver/onboarding/"
