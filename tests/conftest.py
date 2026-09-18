import os
import re
from urllib.parse import urlparse

import pytest

from app.db import Database

# Phase 0.5 — opt-in Postgres backend for this same test suite. Unset by
# default, so the ordinary `pytest -q` run (SQLite) is completely
# unaffected. Mirrors the safety pattern already established in the
# sibling `rithavo-career-profile` repo's own Postgres-parity tests:
# an explicit localhost-only allowlist, because this fixture destructively
# resets its target schema (DROP SCHEMA public CASCADE) once per test and
# must never be pointed at anything but a disposable local instance.
POSTGRES_DSN = os.environ.get("RITHAVO_WEB_TEST_POSTGRES_DSN")
_ALLOWED_TEST_DB_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _dsn_host_is_allowed(dsn: str) -> bool:
    try:
        host = urlparse(dsn).hostname
    except ValueError:
        return False
    return host in _ALLOWED_TEST_DB_HOSTS


if POSTGRES_DSN and not _dsn_host_is_allowed(POSTGRES_DSN):
    raise RuntimeError(
        f"RITHAVO_WEB_TEST_POSTGRES_DSN's host is not in the allowed local-test list "
        f"{_ALLOWED_TEST_DB_HOSTS}. This suite destructively resets its target schema "
        "(DROP SCHEMA public CASCADE) and must only ever run against an explicit "
        "local/disposable Postgres instance — never Supabase, never staging, never "
        "anything remote, regardless of hostname."
    )


@pytest.fixture
def db(tmp_path):
    if POSTGRES_DSN:
        import psycopg2
        conn = psycopg2.connect(POSTGRES_DSN)
        conn.autocommit = True
        conn.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.close()
        database = Database(POSTGRES_DSN)
        database.init_schema()
        return database
    database = Database(tmp_path / "test.db")
    database.init_schema()
    return database


@pytest.fixture
def app_and_client(db):
    from app.main import app
    from fastapi.testclient import TestClient
    app.state.db = db
    app.state.email_sender.sent.clear()
    # The rate limiter lives on the module-level `app` singleton, so
    # without a reset here it would silently accumulate hits across every
    # test in the whole run (many of which intentionally reuse the same
    # handful of test emails) and eventually produce a spurious 429 that
    # has nothing to do with the test actually being run.
    app.state.auth_rate_limiter.reset()
    app.state.login_rate_limiter.reset()
    return app, TestClient(app)


def login_via_magic_link(client, app, email: str) -> int:
    """Same pattern as the sibling project's test helper: drive the real
    HTTP endpoints end-to-end (start -> read the token off the captured
    'sent' email -> verify) rather than reaching into internals, so these
    tests exercise exactly what a real client would."""
    email = email.strip().lower()
    app.state.email_sender.sent.clear()
    client.post("/auth/start", data={"email": email})
    assert app.state.email_sender.sent, "expected a magic-link email to be sent"
    body = app.state.email_sender.sent[-1]["body"]
    match = re.search(r"token=(\S+)", body)
    assert match, "expected a token= link in the sent email body"
    resp = client.get(f"/auth/verify?token={match.group(1)}", follow_redirects=False)
    assert resp.status_code == 302, f"expected verify to redirect, got {resp.status_code}: {resp.text[:200]}"
    return app.state.db.get_or_create_user(email)


def seed_minimal_profile(db, user_id: int, headline="", current_role="", previous_roles=None,
                          companies=None, industries=None, functions=None, name="Test User"):
    """Writes a plausible-shaped career_profiles.profile_json row directly
    (this service never creates these itself — they're owned by the
    sibling app — so tests simulate 'a profile already exists' the same
    way the real cross-service case works). Ported verbatim from
    rithavo-web-platform's conftest.py (Phase P0.2) — needed by
    tests/test_card_handoff.py."""
    import json as _json
    profile = {
        "identity": {
            "name": {"value": name}, "headline": {"value": headline},
            "location": {"value": ""}, "years_of_experience": {"value": ""},
        },
        "background": {
            "current_role": {"value": current_role},
            "previous_roles": previous_roles or [], "companies": companies or [],
            "industries": industries or [], "functions": functions or [],
        },
    }
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO career_profiles (user_id, profile_json, trust_level, completeness_pct, updated_at) "
            "VALUES (?, ?, 'UNVERIFIED', 50, ?)",
            (user_id, _json.dumps(profile), "2026-01-01T00:00:00+00:00"),
        )
    return profile
