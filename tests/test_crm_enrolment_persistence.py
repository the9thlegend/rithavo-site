"""
Root-cause reproduction and regression tests for "Start MFA setup first." during Founder enrolment.

Two serverless instances are simulated by swapping `app.state.db` between requests while one browser (one
cookie jar, same signed session secret) keeps talking to the app. Separate databases = the hosted Preview
(each instance has its own throwaway SQLite file). One shared database = the intended persistence layer.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.crm import diag, schema, totp
from app.db import Database
from tests.crm_helpers import PASSWORD, _b32decode, clock, crm  # noqa: F401  (fixtures)


def _second_instance(env, tmp_path):
    """A second instance with its OWN database, seeded identically (same user ids), as the Preview seed does."""
    other = Database(tmp_path / "instance_b.db")
    other.init_schema()
    schema.ensure_schema(other)
    return other


def _seed_same_user(env, other, email):
    from app.crm import credentials, mfa
    uid = other.get_or_create_user(email)
    other.add_super_admin_if_missing(uid)
    with other.connect() as conn:                     # the same seeded credential, so epochs and the cookie line up
        credentials.set_password(conn, uid, PASSWORD, set_by=uid)
        mfa.ensure_row(conn, uid)
    return uid


def test_root_cause_separate_instance_databases_lose_the_pending_secret(crm, tmp_path):
    """Start on instance A, confirm on instance B (own DB): the exact hosted symptom."""
    from tests.crm_helpers import Person
    db_a, app = crm.db, crm.app
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    db_b = _second_instance(crm, tmp_path)
    assert _seed_same_user(crm, db_b, "founder@example.test") == p.user_id  # same internal id on both instances

    r = p.client.post("/crm/mfa/enrol/start")             # instance A
    assert r.status_code == 200
    p.secret = _b32decode(r.json()["secret"])

    app.state.db = db_b                                   # the next request lands on instance B
    r = p.client.post("/crm/mfa/enrol/confirm", json={"code": p.code()})
    assert r.status_code == 409 and r.json()["detail"] == "Start MFA setup first."   # reproduced

    app.state.db = db_a                                   # back on A the pending secret is intact and valid
    r = p.client.post("/crm/mfa/enrol/confirm", json={"code": p.code()})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10


def test_shared_persistence_layer_survives_instance_change(crm):
    """Two instances over ONE database: pending setup state written by one is read by the other."""
    from tests.crm_helpers import Person
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    app = crm.app
    shared_a = crm.db
    shared_b = Database(shared_a.db_path)  # a second Database object over the same store

    r = p.client.post("/crm/mfa/enrol/start")             # "instance A"
    p.secret = _b32decode(r.json()["secret"])
    app.state.db = shared_b                               # "instance B", same persistence layer
    r = p.client.post("/crm/mfa/enrol/confirm", json={"code": p.code()})
    assert r.status_code == 200, r.text
    app.state.db = shared_a
    assert p.client.get("/crm/me").json()["mfa"] == "ok"  # and A sees the enrolment B completed


def test_setup_state_survives_separate_http_requests(crm):
    from tests.crm_helpers import Person
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    r = p.client.post("/crm/mfa/enrol/start")
    assert r.status_code == 200
    with crm.db.connect() as conn:
        row = conn.execute("SELECT pending_secret_enc, secret_enc FROM crm_mfa WHERE user_id = ?", (p.user_id,)).fetchone()
    assert row["pending_secret_enc"] and not row["secret_enc"]
    assert p.client.get("/crm/me").json()["mfa"] == "enrol"            # an unrelated request in between
    p.secret = _b32decode(r.json()["secret"])
    assert p.client.post("/crm/mfa/enrol/confirm", json={"code": p.code()}).status_code == 200


def test_confirm_without_start_is_the_documented_error(crm):
    from tests.crm_helpers import Person
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    r = p.client.post("/crm/mfa/enrol/confirm", json={"code": "123456"})
    assert r.status_code == 409 and r.json()["detail"] == "Start MFA setup first."
    assert p.client.get("/crm/me").json()["mfa"] == "enrol"            # nothing was enrolled


def test_invalid_otp_rejected_and_enrolment_not_completed(crm):
    from tests.crm_helpers import Person
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    p.secret = _b32decode(p.client.post("/crm/mfa/enrol/start").json()["secret"])
    wrong = totp.hotp(p.secret, totp.step_for() + 50)
    r = p.client.post("/crm/mfa/enrol/confirm", json={"code": wrong})
    assert r.status_code == 401 and r.json()["detail"] == "That code is not correct."
    assert p.client.get("/crm/me").json()["mfa"] == "enrol"
    # the pending secret is still there, so the right code still works
    assert p.client.post("/crm/mfa/enrol/confirm", json={"code": p.code()}).status_code == 200


def test_completed_enrolment_cannot_be_replayed(crm):
    from tests.crm_helpers import Person
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    p.secret = _b32decode(p.client.post("/crm/mfa/enrol/start").json()["secret"])
    code = p.code()
    assert p.client.post("/crm/mfa/enrol/confirm", json={"code": code}).status_code == 200
    again = p.client.post("/crm/mfa/enrol/confirm", json={"code": code})
    assert again.status_code == 409 and again.json()["detail"] == "MFA is already set up."
    assert p.client.post("/crm/mfa/enrol/start").status_code == 409     # cannot restart over a live enrolment
    # a fresh session cannot reuse the same code to pass verification (replay guard on the enrolment step)
    other = TestClient(crm.app)
    other.post("/auth/login", json={"email": p.email, "password": PASSWORD})
    assert other.post("/crm/mfa/verify", json={"code": code}).status_code == 401


def test_founder_and_specialist_gates(crm):
    from tests.crm_helpers import Person
    from app.crm import store
    founder = crm.founder()
    s = crm.specialist("spec@example.test", "Spec", enrol=False, founder=crm._founder_actor())
    assert s.get("/crm/overview").status_code == 401 and s.get("/crm/overview").json()["detail"]["code"] == "mfa_enrol_required"
    assert founder.get("/crm/staff").status_code == 200
    assert s.get("/crm/staff").status_code in (401, 403)
    s.enrol()
    assert s.get("/crm/overview").status_code == 200
    assert s.get("/crm/staff").status_code == 403                         # specialist is never a Founder
    stranger = Person(crm, "stranger@example.test", "x")                  # a customer identity with no CRM role
    assert stranger.post("/crm/mfa/enrol/start").status_code == 401       # no CRM session: cannot even begin enrolment
    assert stranger.client.post("/crm/auth/login", json={"email": stranger.email, "password": PASSWORD}).status_code == 401


def test_diagnostics_log_stage_user_pending_and_instance_but_no_secrets(crm, caplog):
    from tests.crm_helpers import Person
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    with caplog.at_level(logging.INFO, logger="rithavo_web.crm.diag"):
        start = p.client.post("/crm/mfa/enrol/start")
        secret_text = start.json()["secret"]
        p.secret = _b32decode(secret_text)
        code = p.code()
        p.client.post("/crm/mfa/enrol/confirm", json={"code": "000000"})
        p.client.post("/crm/mfa/enrol/confirm", json={"code": code})
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "rithavo_web.crm.diag")
    assert "stage=enrol_start" in text and "stage=enrol_confirm" in text
    assert f"user={p.user_id}" in text and f"instance={diag.INSTANCE_ID}" in text
    assert "pending=true" in text and "outcome=ok" in text and "category=invalid_code" in text
    # never: setup secret, OTPs, recovery codes, cookies, passwords
    for forbidden in (secret_text, code, "000000", PASSWORD, "session="):
        assert forbidden not in text


def test_diag_marks_missing_pending_state_distinctly(crm, caplog, tmp_path):
    from tests.crm_helpers import Person
    p = Person(crm, "founder@example.test", "founder")
    crm.db.add_super_admin_if_missing(p.user_id)
    p.give_credential().login()
    with caplog.at_level(logging.INFO, logger="rithavo_web.crm.diag"):
        p.client.post("/crm/mfa/enrol/confirm", json={"code": "123456"})
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "rithavo_web.crm.diag")
    assert "pending=false" in text and "category=no_pending_setup" in text and "db=sqlite" in text
