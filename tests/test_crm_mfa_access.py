"""CRM Lite (Rithavo Nexus): gate, MFA, lockout, sessions, staff lifecycle under the independent authentication
architecture. Synthetic data only. Traceability: every test from the previous baseline has an equivalent here or in
test_crm_auth_independent.py / test_crm_invitations.py / test_crm_recovery_sessions.py (see evidence/traceability)."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.crm import mfa, totp
from app.crm.routes import classification
from app.db import Database
from tests.crm_helpers import PASSWORD, Person, clock, crm  # noqa: F401


# ---- TOTP implementation (RFC 6238 published vectors, SHA-1, 8 digits) -------

def test_totp_matches_rfc6238_vectors():
    secret = b"12345678901234567890"
    for ts, expected in ((59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
                         (1234567890, "89005924"), (2000000000, "69279037")):
        assert totp.hotp(secret, int(ts // 30), digits=8) == expected


def test_totp_window_is_plus_minus_one_step():
    s = totp.new_secret()
    ts = 1_700_000_000
    for off, ok in ((-2, False), (-1, True), (0, True), (1, True), (2, False)):
        got = totp.matching_step(s, totp.hotp(s, totp.step_for(ts) + off), ts)
        assert (got is not None) is ok


# ---- the gate ---------------------------------------------------------------

def test_flag_off_every_route_is_404(app_and_client, db):
    app, client = app_and_client
    for path in ("/crm/me", "/crm/overview", "/crm/opportunities", "/crm/staff"):
        assert client.get(path).status_code == 404
    assert client.post("/crm/auth/login", json={"email": "a@example.test", "password": "x" * 14}).status_code == 404


def test_anonymous_and_customer_only_sessions_get_the_same_401(crm):
    """Replaces the old 'registered customer gets 403': a customer session is simply 'no CRM session'."""
    anon = TestClient(crm.app)
    r_anon = anon.get("/crm/overview")
    assert r_anon.status_code == 401
    customer = Person(crm, "registered@example.test", "none")
    customer.client.post("/auth/login", json={"email": customer.email, "password": PASSWORD})   # public login, no password set
    r_cust = customer.get("/crm/overview")
    assert r_cust.status_code == 401 and r_cust.json() == r_anon.json()
    assert customer.get("/crm/me").status_code == 401
    assert customer.post("/crm/mfa/enrol/start").status_code == 401


def test_mfa_is_enforced_before_any_crm_data_route(crm):
    f = crm.founder()
    s = crm.specialist("spec1@example.test", enrol=False)
    r = s.get("/crm/overview")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_enrol_required"
    s.enrol()  # enrolment also verifies this session
    assert s.get("/crm/overview").status_code == 200
    # A brand-new session for an enrolled user has the password stage only.
    s2 = Person(crm, "spec1@example.test", "specialist").login()
    r = s2.get("/crm/opportunities")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_required"
    s2.secret = s.secret
    s2.verify()
    assert s2.get("/crm/opportunities").status_code == 200
    # The Founder is held to the same rule.
    f2 = Person(crm, "founder@example.test", "founder").login()
    assert f2.get("/crm/staff").status_code == 401


def test_every_data_route_requires_mfa(crm):
    """Walk the whole router: with a password-stage-only active user, no data route answers."""
    crm.founder()
    s = crm.specialist("spec2@example.test", enrol=False)
    f2 = Person(crm, "founder@example.test", "founder").login()
    for (method, path), kind in classification().items():
        if kind not in ("active", "founder"):
            continue
        url = path.replace("{opp_id}", "1").replace("{company_id}", "1").replace("{fu_id}", "1") \
                  .replace("{review_id}", "1").replace("{user_id}", "1")
        for client in (s, f2):
            resp = client.client.request(method, url, json={})
            assert resp.status_code in (401, 429, 423), (method, url, resp.status_code)


# ---- enrolment, replay, secrets at rest, recovery ----------------------------

def test_secret_and_recovery_codes_are_protected_at_rest(crm):
    f = crm.founder()
    with crm.db.connect() as conn:
        row = conn.execute("SELECT * FROM crm_mfa WHERE user_id = ?", (f.user_id,)).fetchone()
        codes = conn.execute("SELECT code_hash FROM crm_mfa_recovery_codes WHERE user_id = ?", (f.user_id,)).fetchall()
    b32 = totp.b32(f.secret)
    assert b32 not in row["secret_enc"] and f.secret not in row["secret_enc"].encode()
    assert totp.decrypt_secret(row["secret_enc"]) == f.secret
    assert len(codes) == 10
    for c in f.recovery:
        assert all(c.replace("-", "") not in h["code_hash"] for h in codes)
    with crm.db.connect() as conn:
        dump = " ".join(str(dict(r)) for r in conn.execute("SELECT * FROM crm_audit_log").fetchall())
    assert b32 not in dump


def test_wrong_code_rejected_and_replay_refused(crm):
    s = crm.specialist("spec3@example.test")
    s2 = Person(crm, "spec3@example.test", "specialist").login()
    s2.secret = s.secret
    assert s2.post("/crm/mfa/verify", {"code": "000000"}).status_code == 401
    crm.clock.advance(30)
    code = s2.code()
    assert s2.post("/crm/mfa/verify", {"code": code}).status_code == 200
    # The same step again (a replay, e.g. an observed code) is refused.
    s3 = Person(crm, "spec3@example.test", "specialist").login()
    assert s3.post("/crm/mfa/verify", {"code": code}).status_code == 401
    # Two steps in the past is outside the window.
    crm.clock.advance(120)
    s3.login()
    assert s3.post("/crm/mfa/verify", {"code": totp.hotp(s.secret, totp.step_for() - 2)}).status_code == 401


def test_recovery_code_is_single_use_and_forces_reenrolment(crm):
    f = crm.founder()
    s = crm.specialist("spec4@example.test")
    s2 = Person(crm, "spec4@example.test", "specialist").login()
    code = s.recovery[0]
    assert s2.post("/crm/mfa/recover", {"code": code}).status_code == 200
    # Recovery returns an active specialist to inactive: no data until re-enrolled AND re-activated.
    r = s2.get("/crm/overview")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "awaiting_activation"
    with crm.db.connect() as conn:
        assert mfa.get_row(conn, s.user_id)["secret_enc"] is None
    s2.enrol()                                   # the surviving password stage allows exactly this
    assert s2.get("/crm/overview").status_code == 403           # still inactive until the Founder activates
    assert s.get("/crm/overview").status_code == 401            # the old full session's epoch is stale
    s3 = Person(crm, "spec4@example.test", "specialist").login()
    assert s3.post("/crm/mfa/recover", {"code": code}).status_code == 401    # a used code is gone


# ---- lockout: database-backed, shared across instances -----------------------

def test_lockout_5_soft_10_hard_and_founder_unlock(crm):
    f = crm.founder()
    s = crm.specialist("spec5@example.test")
    t = Person(crm, "spec5@example.test", "specialist").login()
    for i in range(4):
        assert t.post("/crm/mfa/verify", {"code": "000000"}).status_code == 401
    assert t.post("/crm/mfa/verify", {"code": "000000"}).status_code == 401  # 5th failure sets the lock
    t.secret = s.secret
    crm.clock.advance(30)
    r = t.post("/crm/mfa/verify", {"code": t.code()})  # even a correct code is refused while locked
    assert r.status_code == 429
    crm.clock.advance(15 * 60 + 1)
    for i in range(5):  # failures 6..10
        t.login()
        t.post("/crm/mfa/verify", {"code": "000000"})
        crm.clock.advance(15 * 60 + 1)
    t.login()
    crm.clock.advance(30)
    r = t.post("/crm/mfa/verify", {"code": t.code()})
    assert r.status_code == 423  # hard lock: only the Founder can clear it
    f.login(); f.verify()  # the Founder's own session expired during the simulated waiting
    assert f.post(f"/crm/staff/{s.user_id}/unlock").status_code == 200
    t.login()
    crm.clock.advance(30)
    assert t.post("/crm/mfa/verify", {"code": t.code()}).status_code == 200
    # A specialist cannot unlock anyone.
    assert t.post(f"/crm/staff/{s.user_id}/unlock").status_code == 403


def test_lockout_is_shared_between_instances(crm, tmp_path):
    """A second Database object (a second serverless instance) sees and extends the same counters."""
    s = crm.specialist("spec6@example.test")
    other = Database(crm.db.db_path)
    for i in range(3):
        with pytest.raises(HTTPException):
            mfa.verify_code(crm.db, s.user_id, "000000")
    for i in range(2):
        with pytest.raises(HTTPException):
            mfa.verify_code(other, s.user_id, "000000")
    crm.clock.advance(30)
    with pytest.raises(HTTPException) as e:
        mfa.verify_code(crm.db, s.user_id, totp.hotp(s.secret, totp.step_for()))
    assert e.value.status_code == 429  # lock set by failures split across two instances
    with pytest.raises(HTTPException) as e:
        mfa.verify_code(other, s.user_id, totp.hotp(s.secret, totp.step_for()))
    assert e.value.status_code == 429


def test_replay_guard_is_atomic_across_instances(crm):
    s = crm.specialist("spec7@example.test")
    other = Database(crm.db.db_path)
    crm.clock.advance(30)
    code = totp.hotp(s.secret, totp.step_for())
    assert mfa.verify_code(crm.db, s.user_id, code)
    with pytest.raises(HTTPException) as e:
        mfa.verify_code(other, s.user_id, code)
    assert e.value.status_code == 401


# ---- session lifecycle -------------------------------------------------------

def test_idle_timeout_30_minutes_and_activity_extends(crm):
    s = crm.specialist("spec8@example.test")
    crm.clock.advance(25 * 60)
    assert s.get("/crm/overview").status_code == 200  # activity refreshes the idle stamp
    crm.clock.advance(25 * 60)
    assert s.get("/crm/overview").status_code == 200
    crm.clock.advance(31 * 60)
    r = s.get("/crm/overview")
    assert r.status_code == 401 and r.json() == {"detail": "Not signed in."}   # signed out: password and code again


def test_absolute_limit_8_hours_even_with_continuous_activity(crm):
    s = crm.specialist("spec9@example.test")
    for _ in range(16):  # 16 x 29 min = 7h44
        crm.clock.advance(29 * 60)
        assert s.get("/crm/overview").status_code == 200
    crm.clock.advance(29 * 60)  # 8h13 since MFA
    assert s.get("/crm/overview").status_code == 401


def test_password_change_invalidates_every_other_crm_session(crm):
    s = crm.specialist("spec10@example.test")
    other = Person(crm, "spec10@example.test", "specialist").login()
    other.secret = s.secret
    other.verify()
    assert s.get("/crm/overview").status_code == 200 and other.get("/crm/overview").status_code == 200
    r = s.post("/crm/auth/change-password", {"current": PASSWORD, "new": "another-synthetic-pass-2"})
    assert r.status_code == 200
    assert s.get("/crm/overview").status_code == 401           # the changing session must sign in again
    assert other.get("/crm/overview").status_code == 401       # every other session ends (credential epoch)
    assert Person(crm, "spec10@example.test", "specialist").post(
        "/crm/auth/login", {"email": "spec10@example.test", "password": PASSWORD}).status_code == 401
    assert Person(crm, "spec10@example.test", "specialist").post(
        "/crm/auth/login", {"email": "spec10@example.test", "password": "another-synthetic-pass-2"}).status_code == 200


def test_session_epoch_bump_invalidates_existing_cookie(crm):
    f = crm.founder()
    s = crm.specialist("spec11@example.test")
    assert s.get("/crm/overview").status_code == 200
    assert f.post(f"/crm/staff/{s.user_id}/reset-mfa").status_code == 200
    r = s.get("/crm/overview")
    assert r.status_code == 401 and r.json() == {"detail": "Not signed in."}


# ---- staff lifecycle ---------------------------------------------------------

def test_deactivation_takes_effect_on_the_next_request(crm):
    f = crm.founder()
    s = crm.specialist("spec12@example.test")
    crm.lead(s)
    assert s.get("/crm/opportunities").status_code == 200
    r = f.post(f"/crm/staff/{s.user_id}/deactivate")
    assert r.status_code == 200 and r.json()["companies_needing_owner"] == 1
    r = s.get("/crm/opportunities")                         # same cookie, next request
    assert r.status_code == 401 and r.json() == {"detail": "Not signed in."}
    assert s.get("/crm/me").status_code == 401
    assert [c["name"] for c in f.get("/crm/needs-owner").json()] == ["Northwind Textiles"]


def test_specialist_is_refused_every_founder_route(crm):
    crm.founder()
    s = crm.specialist("spec15@example.test")
    for (method, path), kind in classification().items():
        if kind != "founder":
            continue
        url = path.replace("{opp_id}", "1").replace("{company_id}", "1").replace("{review_id}", "1") \
                  .replace("{user_id}", "1")
        r = s.client.request(method, url, json={})
        assert r.status_code == 403, (method, path, r.status_code)
