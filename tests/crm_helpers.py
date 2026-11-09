"""Shared fixtures and helpers for the CRM Lite tests. Synthetic data only."""

import types

import pytest
from fastapi.testclient import TestClient

import config
from app.auth import hash_password
from app.crm import access, mfa, schema, totp

PASSWORD = "synthetic-test-password-1"


class Clock:
    """Controllable clock for TOTP steps, lockouts, idle and absolute session limits."""

    def __init__(self):
        import time
        self.now = time.time()

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    shim = types.SimpleNamespace(time=lambda: c.now)
    monkeypatch.setattr(access, "time", shim)
    monkeypatch.setattr(mfa, "time", shim)
    monkeypatch.setattr(totp, "time", shim)
    return c


@pytest.fixture
def crm(app_and_client, db, monkeypatch, clock):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(config, "CRM_ENABLED", True)
    monkeypatch.setattr(config, "CRM_MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "CRM_ALLOW_SCHEMA_ON_POSTGRES", True, raising=False)
    schema.ensure_schema(db)
    app, _ = app_and_client
    return CrmEnv(app, db, clock)


class Person:
    def __init__(self, env, email, role):
        self.env, self.email, self.role = env, email, role
        self.client = TestClient(env.app)
        self.user_id = env.db.get_or_create_user(email)
        env.db.set_user_password(self.user_id, hash_password(PASSWORD))
        self.secret = None
        self.recovery = None

    def login(self):
        self.env.app.state.login_rate_limiter.reset()
        r = self.client.post("/auth/login", json={"email": self.email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return self

    def code(self, offset_steps=0):
        return totp.hotp(self.secret, totp.step_for() + offset_steps)

    def enrol(self):
        r = self.client.post("/crm/mfa/enrol/start")
        assert r.status_code == 200, r.text
        self.secret = _b32decode(r.json()["secret"])
        r = self.client.post("/crm/mfa/enrol/confirm", json={"code": self.code()})
        assert r.status_code == 200, r.text
        self.recovery = r.json()["recovery_codes"]
        return self

    def verify(self):
        # A fresh step each time so the replay guard never trips on legitimate re-verification.
        self.env.clock.advance(30)
        r = self.client.post("/crm/mfa/verify", json={"code": self.code()})
        assert r.status_code == 200, r.text
        return self

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def post(self, path, json=None):
        return self.client.post(path, json=json if json is not None else {})

    def patch(self, path, json=None):
        return self.client.patch(path, json=json if json is not None else {})


def _b32decode(s):
    import base64
    return base64.b32decode(s + "=" * (-len(s) % 8))


class CrmEnv:
    def __init__(self, app, db, clock):
        self.app, self.db, self.clock = app, db, clock
        self._n = 0

    def founder(self, email="founder@example.test"):
        p = Person(self, email, "founder")
        self.db.add_super_admin_if_missing(p.user_id)
        return p.login().enrol()

    def specialist(self, email, name=None, activate=True, enrol=True, founder=None):
        p = Person(self, email, "specialist")
        if activate:
            from app.crm import store
            f = founder or self._founder_actor()
            store.activate_staff(self.db, f, email, name or email.split("@")[0])
        p.login()
        if enrol and activate:
            p.enrol()
        return p

    def _founder_actor(self):
        uid = self.db.get_or_create_user("system-founder@example.test")
        self.db.add_super_admin_if_missing(uid)
        return access.Actor(uid, "founder", "system-founder@example.test", "Founder", True)

    def lead(self, person, company="Northwind Textiles", contact="Asha Verma", email=None, title="Brand refresh",
             pillar="product_studio", source="referral", **extra):
        self._n += 1
        payload = {"company_name": company, "contact_name": contact,
                   "contact_email": email if email is not None else f"contact{self._n}@client.example.test",
                   "title": title, "service_pillar": pillar, "source": source, **extra}
        r = person.post("/crm/leads", payload)
        assert r.status_code == 200, r.text
        return r.json()


def scalar(row):
    """First column of a row on either backend (sqlite3.Row or a Postgres dict row)."""
    return list(dict(row).values())[0]


def column(rows):
    return [scalar(r) for r in rows]
