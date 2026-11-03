"""
The Rithavo Card is served from another Rithavo host, so by the one-domain rule the cross-host handoff is OFF by default:
nothing is minted, nothing names the other host, nobody is sent there. Profile functionality is untouched.
"""


import config

from .conftest import login_via_magic_link, seed_minimal_profile


def test_the_handoff_switch_is_off_unless_explicitly_set():
    assert config.CARD_HANDOFF_ENABLED is False


def test_signed_out_is_still_refused_before_anything_else(app_and_client):
    app, client = app_and_client
    assert client.post("/card/continue").status_code == 401


def test_signed_in_user_with_a_profile_gets_nothing_minted_and_no_other_host(app_and_client, db, monkeypatch):
    monkeypatch.setattr(config, "CARD_HANDOFF_SECRET", "a-secret-that-would-otherwise-allow-minting")
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "profile-no-card@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")
    resp = client.post("/card/continue")
    assert resp.status_code == 404
    text = resp.text + str(dict(resp.headers))
    assert "token" not in resp.json() and "handoff_url" not in text and "rithavo.com" not in text.replace("on rithavo.com", "")
    assert "location" not in {k.lower() for k in resp.headers}


def test_card_status_still_answers_for_the_home_page_wording(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "status-only@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")
    assert client.get("/card/status").json() == {"has_card": False}


def test_the_profile_api_still_works_with_the_handoff_off(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "profile-works@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")
    assert client.get("/profile").status_code == 200
