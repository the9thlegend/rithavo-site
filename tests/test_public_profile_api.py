"""
R1 (site side): /c/{slug} and the public-profile / owner-management APIs. The profile service is replaced by a recording
fake, so nothing here touches a network, a real service or any real data. Covers S-R1-01 to S-R1-03, S-R1-07 to S-R1-09,
S-R1-11 and the flag-off / not-configured guarantees.
"""

import json

import httpx
import pytest

import config
from app import profile_client, routes_public_profile
from tests.conftest import login_via_magic_link

SECRET = "site-profile-secret-" + "s" * 24
OLD_SECRET = "site-old-internal-secret-" + "o" * 20
SLUG = "AbCdEfGh12345678"
OTHER_SLUG = "ZyXwVuTs98765432"

PUBLIC_ROUTES = [
    ("GET", f"/public-profile/{SLUG}"),
    ("GET", f"/public-profile/{SLUG}/photo"),
    ("POST", f"/public-profile/{SLUG}/follow"),
    ("POST", f"/public-profile/{SLUG}/unfollow"),
    ("POST", f"/public-profile/{SLUG}/share"),
]
MANAGEMENT_ROUTES = [
    ("GET", "/my-public-profile"),
    ("POST", "/my-public-profile"),
    ("GET", "/my-photos"),
    ("POST", "/my-photo"),
]


class FakeService:
    def __init__(self):
        self.calls = []
        self.responses = {}      # (method, path) -> httpx.Response or Exception
        self.default = None

    def __call__(self, method, url, headers, json_body, timeout):
        path = url.split(config.INTERNAL_SERVICE_BASE_URL, 1)[-1]
        self.calls.append({"method": method, "path": path, "headers": dict(headers), "json": json_body})
        outcome = self.responses.get((method, path), self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if outcome is not None else httpx.Response(200, json={"ok": True})


@pytest.fixture
def env(app_and_client, monkeypatch, caplog):
    app, client = app_and_client
    fake = FakeService()
    monkeypatch.setattr(config, "PROFILE_SERVICE_SECRET", SECRET)
    monkeypatch.setattr(config, "INTERNAL_SERVICE_SECRET", OLD_SECRET)
    monkeypatch.setattr(config, "PUBLIC_PROFILE_ENABLED", True)
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", True)
    monkeypatch.setattr(profile_client, "_do_request", fake)
    routes_public_profile.reset_limiters()
    caplog.set_level("DEBUG")
    user_id = login_via_magic_link(client, app, "member@example.test")
    return type("Env", (), {"app": app, "client": client, "fake": fake, "user_id": user_id, "caplog": caplog})()


def _request(env, method, path, **kw):
    return env.client.request(method, path, **kw)


# ---- flags and configuration ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", PUBLIC_ROUTES + MANAGEMENT_ROUTES)
def test_flags_off_means_404_for_everyone_and_no_service_call(env, monkeypatch, method, path):
    monkeypatch.setattr(config, "PUBLIC_PROFILE_ENABLED", False)
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", False)
    body = {"json": {}} if method == "POST" else {}
    assert _request(env, method, path, **body).status_code == 404
    env.client.cookies.clear()  # anonymous callers get the same 404: the feature's existence is not revealed
    assert _request(env, method, path, **body).status_code == 404
    assert env.fake.calls == []


def test_page_is_404_when_the_flag_is_off(env, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_PROFILE_ENABLED", False)
    r = env.client.get(f"/c/{SLUG}", follow_redirects=False)
    assert r.status_code == 404 and r.json() == {"detail": "Not found."}


@pytest.mark.parametrize("secret", [None, "", "short", "x" * 31])
@pytest.mark.parametrize("method,path", [PUBLIC_ROUTES[0], MANAGEMENT_ROUTES[0]])
def test_missing_or_short_profile_secret_behaves_like_a_disabled_feature(env, monkeypatch, method, path, secret):
    monkeypatch.setattr(config, "PROFILE_SERVICE_SECRET", secret)
    assert _request(env, method, path).status_code == 404
    assert env.client.get(f"/c/{SLUG}", follow_redirects=False).status_code == 404
    assert env.fake.calls == []


def test_the_two_flags_are_independent(env, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", False)
    assert _request(env, "GET", f"/public-profile/{SLUG}").status_code == 200
    assert _request(env, "GET", "/my-public-profile").status_code == 404
    monkeypatch.setattr(config, "PUBLIC_PROFILE_ENABLED", False)
    monkeypatch.setattr(config, "PROFILE_MANAGEMENT_ENABLED", True)
    assert _request(env, "GET", f"/public-profile/{SLUG}").status_code == 404
    assert _request(env, "GET", "/my-public-profile").status_code == 200


# ---- authentication ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", PUBLIC_ROUTES + MANAGEMENT_ROUTES)
def test_anonymous_callers_get_401_and_no_service_call(env, method, path):
    env.client.cookies.clear()
    body = {"json": {}} if method == "POST" else {}
    assert _request(env, method, path, **body).status_code == 401
    assert env.fake.calls == []


def test_anonymous_page_visitors_are_sent_to_sign_in_not_given_the_page(env):
    env.client.cookies.clear()
    r = env.client.get(f"/c/{SLUG}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/sign-in/?next=/c/{SLUG}"
    assert r.headers["cache-control"] == "private, no-store"


def test_viewer_id_comes_only_from_the_session_and_the_bearer_is_the_dedicated_secret(env):
    env.client.get(f"/public-profile/{SLUG}", headers={
        "X-Rithavo-User-Id": "999", "Authorization": "Bearer attacker", "x-forwarded-for": "9.9.9.9"})
    call = env.fake.calls[-1]
    assert call["headers"]["X-Rithavo-User-Id"] == str(env.user_id)
    assert call["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert OLD_SECRET not in json.dumps(call["headers"])
    assert "attacker" not in json.dumps(call["headers"])


def test_a_client_supplied_target_or_user_never_reaches_the_service(env):
    env.client.post(f"/public-profile/{SLUG}/follow?user_id=7&followee_id=8", json={"followee_id": 9, "user_id": 3})
    call = env.fake.calls[-1]
    assert call["path"] == f"/internal/api/cards/{SLUG}/follow" and call["json"] is None


# ---- relaying ------------------------------------------------------------------------------------------------------

def test_a_normal_read_is_relayed_and_never_cached(env):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.Response(
        200, json={"slug": SLUG, "kind": "person", "name": "Asha Rao", "sections": {}})
    r = env.client.get(f"/public-profile/{SLUG}")
    assert r.status_code == 200 and r.json()["name"] == "Asha Rao"
    assert r.headers["cache-control"] == "private, no-store" and r.headers["x-content-type-options"] == "nosniff"


def test_not_found_is_our_own_uniform_404(env):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.Response(404, json={"detail": "SERVICE-INTERNAL"})
    r = env.client.get(f"/public-profile/{SLUG}")
    assert r.status_code == 404 and r.json() == {"detail": "Not found."} and "SERVICE-INTERNAL" not in r.text


@pytest.mark.parametrize("status", [400, 403, 409, 422])
def test_client_errors_are_relayed_as_detail_and_code_only(env, status):
    env.fake.responses[("POST", f"/internal/api/cards/{SLUG}/share")] = httpx.Response(
        status, json={"detail": "Sharing needs a mutual follow.", "code": "not_allowed", "trace": "SECRET-TRACE"})
    r = env.client.post(f"/public-profile/{SLUG}/share", json={})
    assert r.status_code == status
    assert r.json() == {"detail": "Sharing needs a mutual follow.", "code": "not_allowed"}


@pytest.mark.parametrize("status", [401, 500, 502, 503])
def test_upstream_failures_become_a_generic_502_without_internals(env, status):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.Response(
        status, json={"detail": "Invalid or missing service credential.", "secret": SECRET})
    r = env.client.get(f"/public-profile/{SLUG}")
    assert r.status_code == 502 and r.json() == {"detail": "Profile service is temporarily unavailable."}
    assert "credential" not in r.text and SECRET not in r.text


def test_network_errors_become_a_generic_502(env):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.ConnectError("boom: http://internal/host")
    r = env.client.get(f"/public-profile/{SLUG}")
    assert r.status_code == 502 and "boom" not in r.text and "internal" not in r.text


def test_an_upstream_payload_containing_ids_or_contact_data_is_withheld(env):
    for bad in ({"name": "A", "user_id": 5}, {"name": "A", "sections": {"x": [{"followee_id": 3}]}},
                {"name": "A", "contact_email": "a@example.test"}, {"id": 9}):
        env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.Response(200, json=bad)
        r = env.client.get(f"/public-profile/{SLUG}")
        assert r.status_code == 502, bad
        assert "example.test" not in r.text


@pytest.mark.parametrize("bad", ["short", "has space in it", "../../etc/passwd", "a" * 65, "abc%2fdef1234567", "x" * 7])
def test_invalid_slugs_are_404_without_a_service_call(env, bad):
    for method, suffix in (("GET", ""), ("GET", "/photo"), ("POST", "/follow"), ("POST", "/unfollow"), ("POST", "/share")):
        r = env.client.request(method, f"/public-profile/{bad}{suffix}", **({"json": {}} if method == "POST" else {}))
        assert r.status_code in (404, 405)  # path-separator slugs never match the route at all
    assert env.fake.calls == []


# ---- photo ---------------------------------------------------------------------------------------------------------

def test_photo_is_relayed_with_alpha_flag_and_hardening_headers(env):
    png = b"\x89PNG\r\n\x1a\n" + b"data"
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}/photo")] = httpx.Response(
        200, content=png, headers={"content-type": "image/png", "x-rithavo-photo-alpha": "1"})
    r = env.client.get(f"/public-profile/{SLUG}/photo")
    assert r.status_code == 200 and r.content == png and r.headers["content-type"] == "image/png"
    assert r.headers["x-rithavo-photo-alpha"] == "1"
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_photo_alpha_flag_defaults_to_zero_and_other_types_are_refused(env):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}/photo")] = httpx.Response(
        200, content=b"jpeg", headers={"content-type": "image/jpeg"})
    assert env.client.get(f"/public-profile/{SLUG}/photo").headers["x-rithavo-photo-alpha"] == "0"
    for bad_type in ("text/html", "image/svg+xml", "application/octet-stream", ""):
        env.fake.responses[("GET", f"/internal/api/cards/{SLUG}/photo")] = httpx.Response(
            200, content=b"<script>alert(1)</script>", headers={"content-type": bad_type})
        assert env.client.get(f"/public-profile/{SLUG}/photo").status_code == 404


def test_photo_404_and_failures(env):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}/photo")] = httpx.Response(404, json={"detail": "x"})
    assert env.client.get(f"/public-profile/{SLUG}/photo").status_code == 404
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}/photo")] = httpx.ConnectError("down")
    assert env.client.get(f"/public-profile/{SLUG}/photo").status_code == 502


# ---- follow / unfollow / share -------------------------------------------------------------------------------------

def test_follow_unfollow_use_the_slug_path_and_post(env):
    env.client.post(f"/public-profile/{SLUG}/follow")
    env.client.post(f"/public-profile/{SLUG}/unfollow")
    assert [(c["method"], c["path"]) for c in env.fake.calls] == [
        ("POST", f"/internal/api/cards/{SLUG}/follow"), ("POST", f"/internal/api/cards/{SLUG}/unfollow")]


def test_state_changing_actions_are_not_available_as_get(env):
    for action in ("follow", "unfollow", "share"):
        assert env.client.get(f"/public-profile/{SLUG}/{action}").status_code == 405
    assert env.fake.calls == []


def test_share_relays_only_the_two_boolean_choices(env):
    env.client.post(f"/public-profile/{SLUG}/share", json={
        "share_email": True, "share_phone": "yes", "to_id": 5, "to_type": "business", "extra": "x"})
    assert env.fake.calls[-1]["json"] == {"share_email": True}
    env.client.post(f"/public-profile/{SLUG}/share")
    assert env.fake.calls[-1]["json"] == {}


def test_oversized_bodies_are_refused_before_any_service_call(env):
    huge = {"headline_override": "x" * 20_000}
    assert env.client.post("/my-public-profile", json=huge).status_code == 422
    assert env.client.post(f"/public-profile/{SLUG}/share", json={"share_email": True, "pad": "x" * 20_000}).status_code == 422
    assert env.fake.calls == []


# ---- owner management ----------------------------------------------------------------------------------------------

def test_management_reads_and_writes_are_relayed_and_owner_ids_are_allowed(env):
    payload = {"slug": SLUG, "capabilities": [{"id": 4, "name": "Strategy", "selected": True}],
               "contact_email": "me@example.test"}
    env.fake.responses[("GET", "/internal/api/me/card")] = httpx.Response(200, json=payload)
    r = env.client.get("/my-public-profile")
    assert r.status_code == 200 and r.json() == payload
    env.client.post("/my-public-profile", json={"headline_override": "Hello"})
    assert env.fake.calls[-1]["json"] == {"headline_override": "Hello"} and env.fake.calls[-1]["method"] == "POST"


def test_photo_selection_accepts_only_an_integer_id(env):
    for bad in ({"photo_id": "3"}, {"photo_id": True}, {"photo_id": 1.5}, {}, {"photo_id": None}):
        assert env.client.post("/my-photo", json=bad).status_code == 422
    assert env.fake.calls == []
    env.client.post("/my-photo", json={"photo_id": 3, "extra": "dropped"})
    assert env.fake.calls[-1]["json"] == {"photo_id": 3}
    assert env.fake.calls[-1]["path"] == "/internal/api/me/photo/select"


def test_management_errors_surface_the_trust_requirement_message(env):
    env.fake.responses[("POST", "/internal/api/me/card")] = httpx.Response(
        409, json={"detail": "Making your profile visible needs a confirmed profile photograph.", "code": "trust_required"})
    r = env.client.post("/my-public-profile", json={"is_discoverable": True})
    assert r.status_code == 409 and r.json()["code"] == "trust_required"


# ---- rate limiting -------------------------------------------------------------------------------------------------

def test_reads_are_rate_limited_per_viewer(env):
    for _ in range(120):
        assert env.client.get(f"/public-profile/{SLUG}").status_code == 200
    r = env.client.get(f"/public-profile/{SLUG}")
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    assert len(env.fake.calls) == 120  # the blocked request never reached the service


def test_slug_probing_engages_the_miss_limiter(env):
    env.fake.default = httpx.Response(404, json={"detail": "x"})
    statuses = [env.client.get(f"/public-profile/Probe{n:012d}").status_code for n in range(25)]
    assert statuses[:20] == [404] * 20 and statuses[20:] == [429] * 5
    assert len(env.fake.calls) == 20


def test_invalid_slugs_also_count_as_probes(env):
    statuses = [env.client.get(f"/public-profile/bad{n}").status_code for n in range(25)]
    assert statuses[:20] == [404] * 20 and statuses[20:] == [429] * 5
    assert env.fake.calls == []


def test_writes_are_rate_limited_per_viewer(env):
    for _ in range(30):
        assert env.client.post(f"/public-profile/{SLUG}/follow").status_code == 200
    assert env.client.post(f"/public-profile/{SLUG}/follow").status_code == 429


# ---- secrets and caching -------------------------------------------------------------------------------------------

def test_the_secret_never_appears_in_any_response_or_log(env):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.ConnectError(f"cannot reach with {SECRET}")
    probes = [env.client.get(f"/public-profile/{SLUG}"), env.client.get("/my-public-profile")]
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.Response(500, json={"detail": SECRET})
    probes.append(env.client.get(f"/public-profile/{SLUG}"))
    for r in probes:
        assert SECRET not in r.text and OLD_SECRET not in r.text
    assert SECRET not in env.caplog.text and OLD_SECRET not in env.caplog.text


def test_every_response_on_this_surface_is_no_store(env):
    env.fake.responses[("GET", f"/internal/api/cards/{SLUG}")] = httpx.Response(404, json={})
    responses = [
        env.client.get(f"/public-profile/{SLUG}"), env.client.get("/public-profile/bad"),
        env.client.post(f"/public-profile/{SLUG}/follow"), env.client.get("/my-public-profile"),
        env.client.post("/my-photo", json={"photo_id": "x"}),
    ]
    for r in responses:
        assert r.headers["cache-control"] == "private, no-store", r.request.url


def test_client_never_calls_the_service_when_not_configured(env, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_SERVICE_SECRET", None)
    with pytest.raises(profile_client.ProfileNotConfigured):
        profile_client.call("GET", "/internal/api/me/card", 1)
    monkeypatch.setattr(config, "PROFILE_SERVICE_SECRET", SECRET)
    for bad in (0, -1, True, "5", None):
        with pytest.raises(ValueError):
            profile_client.call("GET", "/internal/api/me/card", bad)
