"""MFA recovery: session handling and the account-state transition (scope 2.8b; mandatory requirement 1; tests 6.10).
After a successful recovery only the recovering user's `crm_pre` stamp is updated (new MFA epoch, original expiry kept),
that stage is limited to the onboarding allowlist, every other session of the person ends on every instance, and the
person must enrol and confirm a new authenticator (a specialist must also be re-activated by the Founder). Synthetic data."""

import re

import pytest
from fastapi.testclient import TestClient

import config
from app.crm import identity, mfa, totp
from app.crm.routes import classification
from app.db import Database
from tests.crm_helpers import PASSWORD, Person, clock, crm, decode_session, make_awaiting  # noqa: F401


def _epoch(crm, user_id):
    with crm.db.connect() as conn:
        return mfa.get_row(conn, user_id)["session_epoch"]


def _recovering_session(crm, person):
    """A fresh browser that has done the password step only."""
    q = Person(crm, person.email, person.role)
    q.login()
    return q


# ---- 1, 2: only the recovering crm_pre is updated; its original expiry is kept ---------------------------------

def test_only_the_recovering_pre_stamp_is_updated_and_its_original_expiry_is_kept(crm):
    s = crm.specialist("rec1@example.test")
    q = _recovering_session(crm, s)
    before = decode_session(q.client)["crm_pre"]
    crm.clock.advance(5 * 60)
    r = q.post("/crm/mfa/recover", {"code": s.recovery[0]})
    assert r.status_code == 200 and r.json()["next"] == "enrol"
    after = decode_session(q.client)
    assert "crm" not in after                                                            # no verified stage appears
    new_epoch = _epoch(crm, s.user_id)
    assert after["crm_pre"] == {**before, "mfa": new_epoch}                                # ONLY the MFA epoch changed
    assert after["crm_pre"]["at"] == before["at"] and new_epoch == before["mfa"] + 1        # original start kept, epoch bumped
    assert set(after) == {"crm_pre"}                                                      # nothing else was written to the session


def test_the_lifetime_is_not_extended_by_recovery(crm):
    s = crm.specialist("rec2@example.test")
    q = _recovering_session(crm, s)                      # password at t0
    crm.clock.advance(9 * 60)
    assert q.post("/crm/mfa/recover", {"code": s.recovery[0]}).status_code == 200      # t0 + 9:00, still inside the stage
    assert q.get("/crm/me").status_code == 200
    crm.clock.advance(61)                                                                # t0 + 10:01
    assert q.get("/crm/me").status_code == 401                                           # NOT extended to 10 minutes after recovery
    assert q.post("/crm/mfa/enrol/start").status_code == 401


def test_recovery_after_the_stage_has_expired_is_refused(crm):
    s = crm.specialist("rec3@example.test")
    q = _recovering_session(crm, s)
    crm.clock.advance(10 * 60 + 1)
    assert q.post("/crm/mfa/recover", {"code": s.recovery[0]}).status_code == 401
    with crm.db.connect() as conn:
        assert mfa.is_enrolled(mfa.get_row(conn, s.user_id))                              # nothing was consumed or cleared


def test_recovery_is_only_available_from_the_password_stage(crm):
    s = crm.specialist("rec4@example.test")                                               # s holds a verified (full) session
    r = s.post("/crm/mfa/recover", {"code": s.recovery[0]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "use_password_stage"
    with crm.db.connect() as conn:
        assert mfa.is_enrolled(mfa.get_row(conn, s.user_id))
    assert TestClient(crm.app).post("/crm/mfa/recover", json={"code": s.recovery[0]}).status_code == 401


# ---- 3: the surviving session is limited to the onboarding allowlist ------------------------------------------

def test_the_surviving_session_cannot_reach_data_or_staff_management(crm):
    f = crm.founder()
    s = crm.specialist("rec5@example.test")
    q = _recovering_session(crm, s)
    assert q.post("/crm/mfa/recover", {"code": s.recovery[0]}).status_code == 200
    for (method, path), kind in classification().items():
        if kind not in ("active", "founder"):
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        r = q.client.request(method, url, json={})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "awaiting_activation", (method, path, r.status_code)
    assert q.get("/crm/me").json() == {"state": "credentialed", "mfa": "enrol"}            # own state only
    assert q.post("/crm/mfa/enrol/start").status_code == 200                               # the allowed onboarding operations work
    assert q.post("/crm/auth/logout").status_code == 200


def test_the_surviving_session_of_the_founder_cannot_read_data_before_enrolment(crm):
    f = crm.founder()
    q = Person(crm, f.email, "founder").login()
    assert q.post("/crm/mfa/recover", {"code": f.recovery[0]}).status_code == 200
    for method, url in (("GET", "/crm/overview"), ("GET", "/crm/staff"), ("GET", "/crm/audit"), ("GET", "/crm/export.csv"),
                        ("POST", "/crm/leads"), ("POST", f"/crm/staff/{f.user_id}/reset-mfa")):
        r = q.client.request(method, url, json={})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_enrol_required", (method, url, r.status_code)
    q.enrol()
    assert q.get("/crm/staff").status_code == 200                                         # access only after a confirmed authenticator
    with crm.db.connect() as conn:
        acts = [r["action"] for r in conn.execute("SELECT action FROM crm_audit_log").fetchall()]
    assert "founder_mfa_recovered" in acts and "staff_returned_to_awaiting_activation" not in acts


# ---- 4: every other session of the person ends, on every instance --------------------------------------------

def test_other_sessions_end_on_every_instance_and_the_recovering_one_survives(crm):
    f = crm.founder()
    s = crm.specialist("rec6@example.test")                                                # session A: verified, on instance 1
    other_pre = _recovering_session(crm, s)                                                 # session B: password stage only
    recovering = _recovering_session(crm, s)                                                # session C: does the recovery
    assert s.get("/crm/overview").status_code == 200
    instance2 = Database(crm.db.db_path)
    first = crm.app.state.db
    crm.app.state.db = instance2                                                            # the recovery lands on a second instance
    assert recovering.post("/crm/mfa/recover", {"code": s.recovery[0]}).status_code == 200
    crm.app.state.db = first                                                                # and the others are checked on the first
    assert s.get("/crm/overview").status_code == 401                                       # the verified session ended
    assert other_pre.get("/crm/me").status_code == 401                                     # the other password-stage session ended
    assert recovering.get("/crm/me").status_code == 200                                    # the recovering session survives
    crm.app.state.db = instance2
    assert s.get("/crm/overview").status_code == 401 and other_pre.get("/crm/me").status_code == 401
    crm.app.state.db = first


# ---- 5: enrol and confirm before regaining access; a specialist is re-activated -------------------------------

def test_active_specialist_is_returned_to_inactive_and_needs_enrolment_and_reactivation(crm):
    from tests.test_crm_invitations import sign_up_through_the_real_flow
    f = crm.founder()
    s = sign_up_through_the_real_flow(crm, f, "rec7@example.test")                         # the real invite, accept, enrol flow
    row = [x for x in f.get("/crm/staff").json() if x["user_id"] == s.user_id][0]
    assert f.post(f"/crm/staff/{s.user_id}/activate", {"credential_epoch": row["credential_epoch"], "mfa_epoch": row["mfa_epoch"]}).status_code == 200
    row = [x for x in f.get("/crm/staff").json() if x["user_id"] == s.user_id][0]
    assert row["state"] == "active"
    before_epochs = {"credential_epoch": row["credential_epoch"], "mfa_epoch": row["mfa_epoch"]}      # what was reviewed pre-recovery
    q = Person(crm, s.email, "specialist")
    q.password = s.password
    q.login()
    res = q.post("/crm/mfa/recover", {"code": s.recovery[0]})
    assert res.status_code == 200 and res.json()["returned_to_inactive"] is True
    row = [x for x in f.get("/crm/staff").json() if x["user_id"] == s.user_id][0]
    assert row["state"] == "credentialed" and row["active"] == 0 and row["mfa_recovered_review"] is True
    assert q.get("/crm/overview").status_code == 403
    q.enrol()                                                                              # new authenticator, confirmed
    assert q.get("/crm/overview").status_code == 403                                       # still no data: not re-activated
    row2 = [x for x in f.get("/crm/staff").json() if x["user_id"] == s.user_id][0]
    assert row2["state"] == "awaiting_activation" and row2["mfa_recovered_review"] is True
    stale = f.post(f"/crm/staff/{s.user_id}/activate", before_epochs)                       # a pending activation with the old epochs
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "state_changed_retry"
    ok = f.post(f"/crm/staff/{s.user_id}/activate", {"credential_epoch": row2["credential_epoch"], "mfa_epoch": row2["mfa_epoch"]})
    assert ok.status_code == 200 and q.get("/crm/overview").status_code == 200
    assert [x for x in f.get("/crm/staff").json() if x["user_id"] == s.user_id][0]["mfa_recovered_review"] is False


def test_an_awaiting_person_who_recovers_goes_back_to_credentialed_and_stays_inactive(crm):
    f = crm.founder()
    p, cred, m = make_awaiting(crm, "rec8@example.test")
    q = Person(crm, p.email, "specialist").login()
    code = None
    with crm.db.connect() as conn:                                                          # a recovery code from the person's enrolment
        code = totp.new_recovery_codes()[0]
        conn.execute("INSERT INTO crm_mfa_recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, 'now')",
                     (p.user_id, totp.recovery_hash(code)))
    assert q.post("/crm/mfa/recover", {"code": code}).status_code == 200
    with crm.db.connect() as conn:
        assert identity.staff_state(conn, p.user_id)[1] == "credentialed"
    assert q.get("/crm/overview").status_code == 403
    assert f.post(f"/crm/staff/{p.user_id}/activate", {"credential_epoch": cred, "mfa_epoch": m}).status_code == 409   # pending activation fails
    assert q.get("/crm/me").json() == {"state": "credentialed", "mfa": "enrol"}


def test_founder_reset_of_a_specialist_follows_the_same_transition(crm):
    f = crm.founder()
    s = crm.specialist("rec9@example.test")
    r = f.post(f"/crm/staff/{s.user_id}/reset-mfa")
    assert r.status_code == 200 and r.json()["returned_to_inactive"] is True
    q = _recovering_session(crm, s)
    assert q.get("/crm/overview").status_code == 403
    q.enrol()
    assert q.get("/crm/overview").status_code == 403


# ---- recovery code hygiene -------------------------------------------------------------------------------------------

def test_recovery_codes_are_single_use_and_all_remaining_codes_are_deleted(crm):
    s = crm.specialist("rec10@example.test")
    q = _recovering_session(crm, s)
    assert q.post("/crm/mfa/recover", {"code": s.recovery[0]}).status_code == 200
    with crm.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM crm_mfa_recovery_codes WHERE user_id = ?", (s.user_id,)).fetchone()["n"] == 0
    q2 = _recovering_session(crm, s)
    assert q2.post("/crm/mfa/recover", {"code": s.recovery[1]}).status_code == 401           # the others are gone too
    q.enrol()
    assert len(q.recovery) == 10 and not set(q.recovery) & set(s.recovery)                   # a new set, shown once


def test_recovery_attempts_are_rate_limited_per_source_and_audited(crm, monkeypatch):
    from app.crm import ratelimit
    monkeypatch.setattr(config, "ON_VERCEL", True)
    s = crm.specialist("rec11@example.test")
    q = _recovering_session(crm, s)
    for _ in range(30):                                   # thirty failed codes seen from one source (any accounts)
        ratelimit.fail(crm.db, [("mfa_source", "203.0.113.77")])
    r = q.client.post("/crm/mfa/recover", json={"code": s.recovery[0]}, headers={"x-vercel-forwarded-for": "203.0.113.77"})
    assert r.status_code == 429 and "dimension" not in r.text and "Retry-After" in r.headers
    with crm.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM crm_audit_log WHERE action = 'rate_limited'").fetchone()["n"] >= 1
        assert mfa.is_enrolled(mfa.get_row(conn, s.user_id))                               # the limited request changed nothing
    ok = q.client.post("/crm/mfa/recover", json={"code": s.recovery[0]}, headers={"x-vercel-forwarded-for": "203.0.113.78"})
    assert ok.status_code == 200                                                          # another source is unaffected
