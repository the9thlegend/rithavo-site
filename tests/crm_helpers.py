"""Shared fixtures and helpers for the CRM Lite tests. Synthetic data only."""

import base64
import os
import types

import pytest
from fastapi.testclient import TestClient

import config
from app.auth import hash_password
from app.crm import access, credentials, identity, invites, mfa, ratelimit, schema, session, totp

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
    monkeypatch.setattr(session, "time", shim)
    monkeypatch.setattr(invites, "time", shim)
    monkeypatch.setattr(ratelimit, "time", shim)
    return c


@pytest.fixture
def crm(app_and_client, db, monkeypatch, clock):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(config, "CRM_ENABLED", True)
    monkeypatch.setattr(config, "CRM_MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "CRM_ALLOW_SCHEMA_ON_POSTGRES", True, raising=False)
    monkeypatch.setattr(config, "CRM_FOUNDER_EMAILS", ("founder@example.test", "system-founder@example.test"))
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY_PREVIOUS", None)
    schema.ensure_schema(db)
    app, _ = app_and_client
    env = CrmEnv(app, db, clock)
    if os.environ.get("RITHAVO_CRM_TEST_RLS") == "1":
        enforce_db_rls(env, monkeypatch)
    return env


class Person:
    """One CRM user with their own cookie jar. Setup shortcuts write the database directly (what the owner-run
    bootstrap and the Founder's invite plus activation produce); the sign-in, MFA and data calls are the real API."""

    def __init__(self, env, email, role):
        self.env, self.email, self.role = env, email, role
        self.client = TestClient(env.app)
        self.user_id = env.db.get_or_create_user(email)
        self.secret = None
        self.recovery = None
        self.password = PASSWORD

    def give_credential(self, password=PASSWORD):
        with self.env.db.connect() as conn:
            credentials.set_password(conn, self.user_id, password, set_by=self.user_id)
            mfa.ensure_row(conn, self.user_id)
        self.password = password
        return self

    def login(self, password=None):
        r = self.client.post("/crm/auth/login", json={"email": self.email, "password": password or self.password})
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

    def founder(self, email="founder@example.test", enrol=True):
        p = Person(self, email, "founder")
        self.db.add_super_admin_if_missing(p.user_id)
        p.give_credential()
        p.login()
        return p.enrol() if enrol else p

    def specialist(self, email, name=None, activate=True, enrol=True, founder=None):
        """A specialist set up the way the real flow ends: staff row (active if `activate`), own CRM credential, and
        (optionally) an enrolled authenticator."""
        p = Person(self, email, "specialist")
        f = founder or self._founder_actor()
        t = "2026-01-01T00:00:00+00:00"
        with self.db.connect() as conn:
            conn.execute("INSERT INTO crm_staff (user_id, display_name, active, activated_by, activated_at, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (p.user_id, name or email.split("@")[0], 1 if activate else 0,
                                                       f.user_id if activate else None, t if activate else None, t))
        p.give_credential()
        p.login()
        if enrol:
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


APP_ROLE = "crm_app"
APP_ROLE_PASSWORD = "localtest-crm-app-only"


def app_role_dsn(dsn):
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(dsn)
    return urlunsplit((p.scheme, f"{APP_ROLE}:{APP_ROLE_PASSWORD}@{p.hostname}:{p.port}", p.path, p.query, ""))


def create_app_role(superuser_dsn):
    """The proposed least-privilege application role: not the owner, no superuser, no BYPASSRLS."""
    import psycopg2
    c = psycopg2.connect(superuser_dsn)
    c.autocommit = True
    cur = c.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
    if not cur.fetchone():
        cur.execute(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
                    f"PASSWORD '{APP_ROLE_PASSWORD}'")
    cur.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    cur.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    cur.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    c.close()


def enforce_db_rls(env, monkeypatch):
    """Run the application as `crm_app` with the proposed policies installed, so every CRM query is filtered by
    PostgreSQL itself. `env.db` stays the superuser connection used only for test set-up and assertions."""
    from app.crm import rls
    from app.db import Database
    from tests.conftest import POSTGRES_DSN
    assert POSTGRES_DSN, "RITHAVO_CRM_TEST_RLS needs RITHAVO_WEB_TEST_POSTGRES_DSN"
    create_app_role(POSTGRES_DSN)
    import psycopg2
    c = psycopg2.connect(POSTGRES_DSN)
    c.autocommit = True
    c.cursor().execute(rls.POLICIES_SQL)
    from app.crm import authmigration
    c.cursor().execute(authmigration.tables_and_grants_sql())
    c.close()
    app_db = Database(app_role_dsn(POSTGRES_DSN))
    rls.install(app_db)
    env.app.state.db = app_db
    env.app_db = app_db
    monkeypatch.setattr(rls, "_ACTIVE", {"on": True})


# ---- helpers for the independent-authentication suites -------------------------------------------------------

def decode_session(client):
    """The Starlette signed session cookie as a dict (payload only; the signature is not checked here)."""
    import json
    raw = client.cookies.get("session")
    if not raw:
        return {}
    payload = raw.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def non_crm_counts(db):
    """Row counts of every table that is not a CRM table (SQLite or PostgreSQL)."""
    with db.connect() as conn:
        if getattr(conn, "_conn", None) is None:
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        else:
            names = [r["tablename"] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()]
        out = {}
        for n in sorted(names):
            if n.startswith("crm_") or n.startswith("sqlite_"):
                continue
            out[n] = list(dict(conn.execute(f"SELECT COUNT(*) AS n FROM {n}").fetchone()).values())[0]
        return out


def make_awaiting(env, email, name=None):
    """A staff member in the `awaiting_activation` state, by direct setup: inactive row, accepted invitation,
    credential, enrolled authenticator. Returns (Person, credential_epoch, mfa_epoch). The Person has NO live session."""
    from app.crm import invites
    p = Person(env, email, "specialist")
    t = "2026-01-01T00:00:00+00:00"
    with env.db.connect() as conn:
        conn.execute("INSERT INTO crm_staff (user_id, display_name, active, created_at) VALUES (?, ?, 0, ?)",
                     (p.user_id, name or email.split("@")[0], t))
        tok = invites.issue(conn, user_id=p.user_id, email=email, purpose=invites.STAFF_INVITE, created_by="1", ttl=86400)
        invites.consume(conn, tok, (invites.STAFF_INVITE,))
    p.give_credential()
    p.login()
    p.enrol()
    p.client = TestClient(env.app)       # drop the session: tests start from a clean browser
    with env.db.connect() as conn:
        cred = credentials.get(conn, p.user_id)["credential_epoch"]
        m = mfa.get_row(conn, p.user_id)["session_epoch"]
    return p, cred, m
