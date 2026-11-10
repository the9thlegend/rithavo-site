"""Independent CRM credentials and sessions (scope 2.1, 2.2, 2.3; tests 6.1, 6.5). Synthetic data only."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from app.auth import hash_password
from app.crm import credentials, identity, session
from tests.crm_helpers import PASSWORD, Person, clock, crm, decode_session  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
GENERIC = {"detail": "Invalid email or password."}


# ---- login: one generic failure ------------------------------------------------------------------------------

def test_login_failures_are_identical_for_unknown_email_no_credential_and_wrong_password(crm):
    crm.founder()
    spec = crm.specialist("known@example.test", enrol=False)
    customer = Person(crm, "customer-only@example.test", "none")           # a customer identity, no CRM credential
    c = TestClient(crm.app)
    results = [
        c.post("/crm/auth/login", json={"email": "nobody@example.test", "password": PASSWORD}),
        c.post("/crm/auth/login", json={"email": customer.email, "password": PASSWORD}),
        c.post("/crm/auth/login", json={"email": "known@example.test", "password": "wrong-password-123"}),
        c.post("/crm/auth/login", json={"email": "", "password": ""}),
    ]
    for r in results:
        assert r.status_code == 401 and r.json() == GENERIC


@pytest.mark.parametrize("password,why", [
    ("short", "under 12"), ("a" * 201, "over 200"), ("password123", "common"), ("aaaaaaaaaaaa", "too few distinct characters"),
])
def test_password_policy_rejects_weak_passwords(password, why):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        credentials.check_policy(password, "someone@example.test")
    assert e.value.status_code == 422


def test_password_must_differ_from_the_email():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        credentials.check_policy("someone@example.test", "someone@example.test")


def test_crm_code_never_reads_the_customer_password_columns():
    for p in (ROOT / "app" / "crm").glob("*.py"):
        if p.name in ("schema.py", "authmigration.py", "rls.py"):
            continue
        code = p.read_text(encoding="utf-8")
        assert "password_updated_at" not in code, p.name
        assert "set_user_password" not in code and "get_user_by_email" not in code, p.name
        assert not re.search(r"users\b[^\n]{0,80}password_hash|password_hash[^\n]{0,80}\bFROM users", code), p.name


def test_crm_code_never_uses_the_public_session_key():
    for p in (ROOT / "app" / "crm").glob("*.py"):
        if p.name in ("schema.py", "authmigration.py", "rls.py"):
            continue
        code = p.read_text(encoding="utf-8")
        assert 'session.get("user_id")' not in code and 'session["user_id"]' not in code, p.name


# ---- customer sessions are not CRM sessions ------------------------------------------------------------------

def test_customer_password_session_gets_the_uniform_401_on_every_crm_route(crm):
    crm.founder()
    cust = Person(crm, "cust@example.test", "none")
    crm.db.set_user_password(cust.user_id, hash_password("customer-public-pass-1"))
    r = cust.client.post("/auth/login", json={"email": cust.email, "password": "customer-public-pass-1"})
    assert r.status_code == 200 and decode_session(cust.client).get("user_id") == cust.user_id   # a real customer session
    from app.crm.routes import classification
    anon = TestClient(crm.app)
    for (method, path), kind in classification().items():
        if kind == "public":
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        a = anon.request(method, url, json={})
        c = cust.client.request(method, url, json={})
        assert a.status_code == 401 and c.status_code == 401 and c.json() == a.json(), (method, path, c.status_code)


def test_founder_email_with_a_customer_session_still_needs_the_crm_login(crm):
    """A customer password or magic-link session for the Founder's own email is not a CRM session."""
    f = crm.founder()
    crm.db.set_user_password(f.user_id, hash_password("customer-public-pass-2"))
    other = TestClient(crm.app)
    assert other.post("/auth/login", json={"email": f.email, "password": "customer-public-pass-2"}).status_code == 200
    assert other.get("/crm/staff").status_code == 401
    assert other.get("/crm/me").status_code == 401


def test_crm_login_creates_no_customer_session_and_crm_logout_leaves_one_alone(crm):
    f = crm.founder()
    s = decode_session(f.client)
    assert "user_id" not in s and ("crm" in s)                                  # CRM keys only
    crm.db.set_user_password(f.user_id, hash_password("customer-public-pass-3"))
    f.client.post("/auth/login", json={"email": f.email, "password": "customer-public-pass-3"})   # also a customer session
    assert decode_session(f.client).get("user_id") == f.user_id
    assert f.post("/crm/auth/logout").status_code == 200
    after = decode_session(f.client)
    assert "crm" not in after and "crm_pre" not in after and after.get("user_id") == f.user_id    # customer session untouched
    assert f.get("/crm/staff").status_code == 401


def test_public_logout_leaves_no_usable_crm_session(crm):
    f = crm.founder()
    assert f.get("/crm/staff").status_code == 200
    assert f.client.post("/auth/logout").status_code == 200
    assert f.get("/crm/staff").status_code == 401


def test_customer_account_events_never_change_crm_credentials_or_sessions(crm):
    f = crm.founder()
    s = crm.specialist("steady@example.test")
    with crm.db.connect() as conn:
        before = dict(credentials.get(conn, s.user_id))
    crm.db.set_user_password(s.user_id, hash_password("customer-reset-pass-9"))       # customer password reset
    assert s.get("/crm/overview").status_code == 200                                   # CRM session unaffected
    with crm.db.connect() as conn:
        assert dict(credentials.get(conn, s.user_id)) == before
    fresh = TestClient(crm.app)
    assert fresh.post("/crm/auth/login", json={"email": s.email, "password": "customer-reset-pass-9"}).status_code == 401
    assert fresh.post("/crm/auth/login", json={"email": s.email, "password": PASSWORD}).status_code == 200


# ---- the password stage (crm_pre) ------------------------------------------------------------------------------

def test_password_stage_is_limited_and_expires_in_ten_minutes(crm):
    crm.founder()
    s = crm.specialist("prestage@example.test")
    p = Person(crm, s.email, "specialist").login()
    assert decode_session(p.client).get("crm_pre") and "crm" not in decode_session(p.client)
    r = p.get("/crm/overview")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_required"        # cannot read data
    assert p.get("/crm/me").json() == {"user_id": s.user_id, "email": s.email, "role": "specialist", "name": "prestage",
                                       "state": "active", "mfa": "verify", **{k: v for k, v in p.get("/crm/me").json().items()
                                                                              if k in ("stages", "lost_reasons", "hold_reasons", "pillars", "sources")}}
    crm.clock.advance(9 * 60 + 59)
    assert p.get("/crm/me").status_code == 200
    crm.clock.advance(2)                                                                 # 10 minutes and 1 second
    assert p.get("/crm/me").status_code == 401


# ---- Founder = Super Admin AND allowlisted (D3, D9) ----------------------------------------------------------

def test_super_admin_without_allowlist_and_allowlisted_without_super_admin_are_not_founders(crm, monkeypatch):
    allow_only = Person(crm, "founder@example.test", "founder").give_credential()      # allowlisted, no Super Admin row
    assert allow_only.client.post("/crm/auth/login", json={"email": allow_only.email, "password": PASSWORD}).status_code == 401
    admin_only = Person(crm, "otheradmin@example.test", "founder")
    crm.db.add_super_admin_if_missing(admin_only.user_id)
    admin_only.give_credential()
    assert admin_only.client.post("/crm/auth/login", json={"email": admin_only.email, "password": PASSWORD}).status_code == 401
    crm.db.add_super_admin_if_missing(allow_only.user_id)                               # now both conditions hold
    assert allow_only.client.post("/crm/auth/login", json={"email": allow_only.email, "password": PASSWORD}).status_code == 200


def test_removing_the_founder_from_the_allowlist_ends_the_session_on_the_next_request(crm, monkeypatch):
    f = crm.founder()
    assert f.get("/crm/staff").status_code == 200
    monkeypatch.setattr(config, "CRM_FOUNDER_EMAILS", ("system-founder@example.test",))
    r = f.get("/crm/staff")
    assert r.status_code == 401 and r.json() == {"detail": "Not signed in."}


def test_a_new_super_admin_gets_nothing_in_the_crm(crm):
    crm.founder()
    new_admin = Person(crm, "second.admin@example.test", "none")
    crm.db.add_super_admin_if_missing(new_admin.user_id)
    new_admin.give_credential()
    assert new_admin.client.post("/crm/auth/login", json={"email": new_admin.email, "password": PASSWORD}).status_code == 401
    assert new_admin.get("/crm/staff").status_code == 401


# ---- the gate re-reads the database on every request -----------------------------------------------------------

def test_credential_epoch_mismatch_ends_the_session(crm):
    s = crm.specialist("epoch@example.test")
    assert s.get("/crm/overview").status_code == 200
    with crm.db.connect() as conn:
        credentials.set_password(conn, s.user_id, "a-brand-new-password-1")
    assert s.get("/crm/overview").status_code == 401


def test_crm_session_cookie_holds_stamps_only(crm):
    s = crm.specialist("stamps@example.test")
    st = decode_session(s.client)["crm"]
    assert set(st) == {"uid", "cred", "mfa", "at", "last"} and all(isinstance(v, int) for v in st.values())
    assert PASSWORD not in str(decode_session(s.client))
