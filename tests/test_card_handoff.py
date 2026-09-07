"""
Phase P0.2 — Profile -> Rithavo Card handoff tests (rithavo.com side).

This service only ever mints the short-lived handoff token and answers
a read-only "does a Card already exist" question — it never creates,
edits, or renders Card data (see app/card_handoff.py's module
docstring). The sibling app's own test suite
(rithavo-career-profile/tests/test_card_handoff.py) covers verification,
single-use consumption, and session establishment on that side.
"""

import config
import pytest
from itsdangerous import URLSafeTimedSerializer

from .conftest import login_via_magic_link, seed_minimal_profile

TEST_SECRET = "test-card-handoff-secret"


@pytest.fixture(autouse=True)
def _card_handoff_secret(monkeypatch):
    """Phase P0.2A: config.CARD_HANDOFF_SECRET has no fallback of its
    own anymore (unset means None, which must fail closed) — every test
    in this file gets a deterministic value explicitly, the way a real
    deployment would via its own RITHAVO_CARD_HANDOFF_SECRET env var.
    The one test that needs it actually unset overrides this itself."""
    monkeypatch.setattr(config, "CARD_HANDOFF_SECRET", TEST_SECRET)


def _decode_token(token: str, secret: str = TEST_SECRET) -> dict:
    return URLSafeTimedSerializer(secret, salt="rithavo-card-handoff").loads(token, max_age=90)


# =====================================================================
# POST /card/continue
# =====================================================================

def test_unauthenticated_user_cannot_initiate_handoff(app_and_client):
    """Test #8."""
    app, client = app_and_client
    resp = client.post("/card/continue")
    assert resp.status_code == 401


def test_authenticated_user_without_a_profile_gets_a_clear_error(app_and_client):
    app, client = app_and_client
    login_via_magic_link(client, app, "no-profile-yet@example.com")
    resp = client.post("/card/continue")
    assert resp.status_code == 400


def test_missing_secret_fails_closed_not_silently_insecure(app_and_client, db, monkeypatch):
    """Phase P0.2A test #1: an unconfigured production deployment must
    refuse to hand off at all, never sign a token with a fallback."""
    monkeypatch.setattr(config, "CARD_HANDOFF_SECRET", None)
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "no-secret-configured@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")

    resp = client.post("/card/continue")
    assert resp.status_code == 503


def test_authenticated_user_with_a_profile_can_initiate_handoff(app_and_client, db):
    """Test #1: returns a handoff_url pointing at the sibling app and a
    token that decodes, under the SAME secret+salt the sibling uses, to
    exactly this user's shared users.id and nothing else."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "has-profile@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")

    resp = client.post("/card/continue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["handoff_url"] == f"{config.CARD_APP_BASE_URL}/handoff/card"

    decoded = _decode_token(body["token"])
    assert decoded["user_id"] == user_id
    assert decoded["purpose"] == "card_handoff"


def test_token_never_contains_more_than_the_minimum(app_and_client, db):
    """The token must carry only what's strictly required to identify
    the shared users.id — never email, name, headline, or any other
    profile/contact detail, and never a credential or session secret."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "minimal-token@example.com")
    seed_minimal_profile(db, user_id, headline="Should Not Leak", name="Should Not Leak Either")

    resp = client.post("/card/continue")
    decoded = _decode_token(resp.json()["token"])
    assert set(decoded.keys()) == {"user_id", "purpose", "nonce"}
    assert "Should Not Leak" not in resp.text


def test_two_handoffs_for_the_same_user_mint_different_tokens(app_and_client, db):
    """Each call mints an independent token (via its own random nonce),
    consistent with the token being single-use on the sibling's side —
    this service never assumes there's only ever one live token."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "two-handoffs@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")

    token_a = client.post("/card/continue").json()["token"]
    token_b = client.post("/card/continue").json()["token"]
    assert token_a != token_b


# =====================================================================
# GET /card/status
# =====================================================================

def test_card_status_requires_auth(app_and_client):
    app, client = app_and_client
    resp = client.get("/card/status")
    assert resp.status_code == 401


def test_card_status_false_when_no_card_row_exists(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "no-card-yet@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")

    resp = client.get("/card/status")
    assert resp.status_code == 200
    assert resp.json() == {"has_card": False}


def test_card_status_true_when_a_card_row_exists(app_and_client, db):
    """Never written by this service in production — seeded directly
    here to simulate the sibling having already created one, the same
    convention seed_minimal_profile already uses for career_profiles."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "has-card@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")
    with db.connect() as conn:
        conn.execute("INSERT INTO card_settings (person_user_id) VALUES (?)", (user_id,))

    resp = client.get("/card/status")
    assert resp.status_code == 200
    assert resp.json() == {"has_card": True}


def test_card_status_never_reports_another_users_card(app_and_client, db):
    """Test #14 (cross-user authorization), read side: a Card row
    belonging to a different person must never flip this user's status
    to True."""
    app, client = app_and_client
    other_user_id = db.get_or_create_user("other-person@example.com")
    with db.connect() as conn:
        conn.execute("INSERT INTO card_settings (person_user_id) VALUES (?)", (other_user_id,))

    user_id = login_via_magic_link(client, app, "unrelated-person@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer")

    resp = client.get("/card/status")
    assert resp.json() == {"has_card": False}
