"""One-time Founder bootstrap (scope 2.4, 3a; tests 6.2). Synthetic data only."""

import hashlib
import logging
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import config
from app.crm import authmigration, credentials, identity, invites, mfa, ratelimit
from app.crm.routes import classification
from tests.crm_helpers import PASSWORD, Person, clock, crm, decode_session  # noqa: F401

NEW_PW = "a-fresh-founder-password-1"
GENERIC = {"detail": "This link is not valid."}


def owner_issue(env, email="founder@example.test", ttl=None, created_by="owner_db"):
    """What the owner-run helper's SQL produces: a hashed single-use row for the Founder's identity."""
    uid = env.db.get_or_create_user(email)
    env.db.add_super_admin_if_missing(uid)
    with env.db.connect() as conn:
        return uid, invites.issue(conn, user_id=uid, email=email, purpose=invites.FOUNDER_BOOTSTRAP, created_by=created_by,
                                  ttl=ttl or config.CRM_BOOTSTRAP_TTL_SECONDS)


def accept(client, token, password=NEW_PW):
    return client.post("/crm/auth/accept", json={"token": token, "password": password})


def test_raw_token_is_never_stored_logged_or_audited(crm, caplog):
    uid, token = owner_issue(crm)
    c = TestClient(crm.app)
    with caplog.at_level(logging.DEBUG):
        r = accept(c, token)
    assert r.status_code == 200
    with crm.db.connect() as conn:
        dump = " ".join(str(dict(x)) for x in conn.execute("SELECT * FROM crm_invites").fetchall())
        dump += " ".join(str(dict(x)) for x in conn.execute("SELECT * FROM crm_audit_log").fetchall())
        row = conn.execute("SELECT * FROM crm_invites").fetchone()
    assert token not in dump and row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in r.text and token not in "\n".join(m.getMessage() for m in caplog.records)
    assert token not in str(decode_session(c))


def test_acceptance_sets_the_credential_bumps_the_epoch_clears_mfa_and_forces_enrolment(crm):
    f = crm.founder()                                           # an existing enrolled Founder (e.g. lost device)
    old_session = f
    with crm.db.connect() as conn:
        before_cred = credentials.get(conn, f.user_id)["credential_epoch"]
        before_mfa = mfa.get_row(conn, f.user_id)["session_epoch"]
    _uid, token = owner_issue(crm)
    c = TestClient(crm.app)
    assert accept(c, token).status_code == 200
    with crm.db.connect() as conn:
        cred = credentials.get(conn, f.user_id)
        m = mfa.get_row(conn, f.user_id)
        codes = conn.execute("SELECT COUNT(*) AS n FROM crm_mfa_recovery_codes WHERE user_id = ?", (f.user_id,)).fetchone()["n"]
    assert cred["credential_epoch"] > before_cred and m["session_epoch"] > before_mfa
    assert m["secret_enc"] is None and codes == 0
    assert old_session.get("/crm/staff").status_code == 401                              # the old session ended
    f2 = Person(crm, f.email, "founder")
    assert f2.client.post("/crm/auth/login", json={"email": f.email, "password": PASSWORD}).status_code == 401   # old password gone
    assert f2.client.post("/crm/auth/login", json={"email": f.email, "password": NEW_PW}).status_code == 200
    r = f2.get("/crm/staff")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_enrol_required"   # enrolment before any data
    f2.enrol()
    assert f2.get("/crm/staff").status_code == 200


def test_single_use_sequential_and_concurrent(crm):
    _uid, token = owner_issue(crm)
    assert accept(TestClient(crm.app), token).status_code == 200
    again = accept(TestClient(crm.app), token, "another-password-for-me-1")
    assert again.status_code == 400 and again.json() == GENERIC
    _uid, token2 = owner_issue(crm)                              # a fresh token for the concurrency check
    barrier = threading.Barrier(2)

    def go(pw):
        c = TestClient(crm.app)
        barrier.wait()
        return accept(c, token2, pw).status_code

    with ThreadPoolExecutor(2) as ex:
        codes = list(ex.map(go, ["concurrent-password-one-1", "concurrent-password-two-2"]))
    assert sorted(codes) == [200, 400]


def test_expiry_boundary_is_exactly_30_minutes(crm):
    _uid, t1 = owner_issue(crm)
    crm.clock.advance(30 * 60 - 1)
    assert accept(TestClient(crm.app), t1).status_code == 200                        # 29:59 accepted
    _uid, t2 = owner_issue(crm)
    crm.clock.advance(30 * 60)
    r = accept(TestClient(crm.app), t2)                                              # 30:00 rejected
    assert r.status_code == 400 and r.json() == GENERIC


def test_every_kind_of_bad_token_gets_the_identical_response(crm):
    uid, used = owner_issue(crm)
    accept(TestClient(crm.app), used)
    uid, expired = owner_issue(crm)
    crm.clock.advance(31 * 60)
    uid, revoked = owner_issue(crm)
    with crm.db.connect() as conn:
        invites.revoke_open(conn, uid, invites.FOUNDER_BOOTSTRAP)
    bad = [used, expired, revoked, "not-a-token", "", "x" * 500]
    seen = set()
    for t in bad:
        r = accept(TestClient(crm.app), t)
        seen.add((r.status_code, r.text))
    assert seen == {(400, '{"detail":"This link is not valid."}')}


def test_only_an_allowlisted_super_admin_can_accept_a_bootstrap_token(crm):
    # a token row for someone who is not an allowlisted Super Admin (an owner mistake) must not work
    for email, admin, allow in (("stranger@example.test", False, False), ("founder@example.test", False, True),
                                ("otheradmin@example.test", True, False)):
        uid = crm.db.get_or_create_user(email)
        if admin:
            crm.db.add_super_admin_if_missing(uid)
        with crm.db.connect() as conn:
            tok = invites.issue(conn, user_id=uid, email=email, purpose=invites.FOUNDER_BOOTSTRAP, created_by="owner_db",
                                ttl=1800)
        r = accept(TestClient(crm.app), tok)
        assert r.status_code == 400 and r.json() == GENERIC, email
        with crm.db.connect() as conn:
            assert not credentials.has_password(credentials.get(conn, uid))


def test_a_new_issuance_revokes_the_previous_token(crm):
    uid, first = owner_issue(crm)
    uid, second = owner_issue(crm)
    assert accept(TestClient(crm.app), first).status_code == 400
    assert accept(TestClient(crm.app), second).status_code == 200


def test_no_endpoint_issues_or_claims_a_bootstrap_token(crm):
    for (method, path), kind in classification().items():
        assert "bootstrap" not in path and "claim" not in path, path
    anon = TestClient(crm.app)
    for path in ("/crm/auth/bootstrap", "/crm/bootstrap", "/crm/auth/claim", "/crm/staff/founder"):
        assert anon.post(path, json={}).status_code in (404, 405)
    # and a Founder, even signed in, cannot create a bootstrap invitation through the staff API
    f = crm.founder()
    r = f.post("/crm/staff/invite", {"email": "founder@example.test", "display_name": "Me"})
    assert r.status_code == 409


def test_the_owner_sql_issues_a_token_the_application_accepts(crm):
    uid = crm.db.get_or_create_user("founder@example.test")
    crm.db.add_super_admin_if_missing(uid)
    from app.crm.invites import hash_token, new_token
    token = new_token()
    out = authmigration.bootstrap_sql("founder@example.test", hash_token(token), int(_time.time()), "2026-01-01T00:00:00+00:00", 1800)
    assert token not in out["sql"] and hash_token(token) in out["sql"]
    assert "not a Super Admin" in out["guard"]
    with crm.db.connect() as conn:                                      # the portable statements run on SQLite too
        for stmt in out["statements"].strip().split(";\n"):
            if stmt.strip():
                conn.execute(stmt)
    assert accept(TestClient(crm.app), token).status_code == 200
    with crm.db.connect() as conn:
        acts = [r["action"] for r in conn.execute("SELECT action FROM crm_audit_log ORDER BY id").fetchall()]
    assert acts[0] == "founder_bootstrap_issued" and "founder_bootstrap_accepted" in acts and "founder_credential_set" in acts


def test_the_owner_sql_issues_nothing_for_an_email_that_is_not_a_super_admin(crm):
    crm.db.get_or_create_user("notadmin@example.test")
    out = authmigration.bootstrap_sql("notadmin@example.test", "deadbeef" * 8, int(_time.time()), "2026-01-01T00:00:00+00:00", 1800)
    with crm.db.connect() as conn:
        for stmt in out["statements"].strip().split(";\n"):
            if stmt.strip():
                conn.execute(stmt)
        assert conn.execute("SELECT COUNT(*) AS n FROM crm_invites").fetchone()["n"] == 0


def test_acceptance_enforces_the_password_policy_without_burning_the_token(crm):
    _uid, token = owner_issue(crm)
    c = TestClient(crm.app)
    assert accept(c, token, "short").status_code == 422
    assert accept(c, token, NEW_PW).status_code == 200                      # the token is still good after a policy failure


# ---- rate limits (scope 2.5) ---------------------------------------------------------------------------------------

def _from(ip, monkeypatch=None):
    return {"x-vercel-forwarded-for": ip}


def test_bootstrap_source_limit_5_then_429_even_with_a_valid_token(crm, monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", True)
    _uid, token = owner_issue(crm)
    c = TestClient(crm.app)
    h = _from("203.0.113.5")
    for i in range(5):
        assert c.post("/crm/auth/accept", json={"token": "wrong-token-%d" % i, "password": NEW_PW}, headers=h).status_code == 400
    # the 6th unknown token from the same source: the invitation limit is 10, so still 400 ...
    assert c.post("/crm/auth/accept", json={"token": "wrong-6", "password": NEW_PW}, headers=h).status_code == 400
    # ... but the VALID bootstrap token from that source is now refused (stricter bootstrap limit) with a generic 429
    r = c.post("/crm/auth/accept", json={"token": token, "password": NEW_PW}, headers=h)
    assert r.status_code == 429 and "bootstrap" not in r.text.lower() and "dimension" not in r.text.lower()
    # another source can still use it
    assert c.post("/crm/auth/accept", json={"token": token, "password": NEW_PW}, headers=_from("203.0.113.9")).status_code == 200


def test_global_brake_revokes_the_open_token_and_audits(crm, monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", True)
    _uid, token = owner_issue(crm)
    c = TestClient(crm.app)
    for i in range(10):                                           # ten failures from ten different sources
        c.post("/crm/auth/accept", json={"token": "bad-%d" % i, "password": NEW_PW}, headers=_from("198.51.100.%d" % (i + 1)))
    with crm.db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM crm_invites WHERE revoked_at IS NOT NULL").fetchone()["n"]
        acts = [r["action"] for r in conn.execute("SELECT action FROM crm_audit_log").fetchall()]
    assert n == 1 and "founder_bootstrap_revoked_rate_limit" in acts
    r = c.post("/crm/auth/accept", json={"token": token, "password": NEW_PW}, headers=_from("192.0.2.77"))
    assert r.status_code == 400                                   # revoked: generic failure; the owner must issue a new one
    uid, fresh = owner_issue(crm)                                 # recovery: re-issue works after the window
    crm.clock.advance(31 * 60)
    uid, fresh = owner_issue(crm)
    assert c.post("/crm/auth/accept", json={"token": fresh, "password": NEW_PW}, headers=_from("192.0.2.78")).status_code == 200


def test_limits_are_shared_between_two_instances(crm, monkeypatch):
    from app.db import Database
    monkeypatch.setattr(config, "ON_VERCEL", True)
    other = Database(crm.db.db_path)
    h = _from("203.0.113.50")
    c = TestClient(crm.app)
    for i in range(3):
        c.post("/crm/auth/accept", json={"token": "a-%d" % i, "password": NEW_PW}, headers=h)
    crm.app.state.db = other                                      # the next requests land on a second instance
    for i in range(2):
        c.post("/crm/auth/accept", json={"token": "b-%d" % i, "password": NEW_PW}, headers=h)
    assert ratelimit.count(other, "bootstrap_source", "203.0.113.50") == 5
    assert ratelimit.count(crm.db, "bootstrap_source", "203.0.113.50") == 5
