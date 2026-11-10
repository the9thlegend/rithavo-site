"""CRM Lite: Production safety, schema, scope boundaries, seed refusal. No database is touched but
disposable SQLite files."""

import pathlib
import re

import pytest

import config
from app.crm import schema, seed, setup, totp
from app.db import Database
from tests.crm_helpers import scalar, column, clock, crm  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent


def tables(db):
    with db.connect() as conn:
        if db._is_postgres:
            return set(column(conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()))
        return set(column(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()))


def test_flag_off_creates_no_crm_tables(app_and_client, db):
    assert not [t for t in tables(db) if t.startswith("crm_")]


def test_ensure_schema_refuses_a_non_isolated_postgres(monkeypatch):
    class FakePg:
        _is_postgres = True
    monkeypatch.setattr(config, "ISOLATED_DEPLOYMENT", False)
    monkeypatch.setattr(config, "CRM_ALLOW_SCHEMA_ON_POSTGRES", False, raising=False)
    with pytest.raises(RuntimeError, match="non-isolated"):
        schema.ensure_schema(FakePg())


def test_init_failure_keeps_crm_disabled_and_site_up(monkeypatch, tmp_path):
    from fastapi import FastAPI
    monkeypatch.setattr(config, "CRM_ENABLED", True)
    monkeypatch.setattr(setup, "ensure_schema", lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
    app = FastAPI()
    app.state.db = Database(tmp_path / "x.db")
    setup.init_crm(app)
    assert config.CRM_ENABLED is False


def test_mfa_key_fails_closed_outside_isolated_deployments(monkeypatch):
    monkeypatch.setattr(config, "CRM_MFA_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "ISOLATED_DEPLOYMENT", False)
    monkeypatch.setattr(totp.config, "CRM_ALLOW_DERIVED_KEY", False, raising=False)
    with pytest.raises(totp.MfaKeyUnavailable):
        totp.encrypt_secret(b"x" * 20)
    monkeypatch.setattr(config, "ISOLATED_DEPLOYMENT", True)  # Preview/dev only: derived key
    assert totp.decrypt_secret(totp.encrypt_secret(b"y" * 20)) == b"y" * 20


def test_key_rotation_keeps_old_secrets_and_recovery_codes_valid(monkeypatch):
    from cryptography.fernet import Fernet
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(config, "CRM_MFA_ENCRYPTION_KEY", old)
    token = totp.encrypt_secret(b"s" * 20)
    stored = totp.recovery_hash("ABCD-EFGH-JKLM")
    monkeypatch.setattr(config, "CRM_MFA_ENCRYPTION_KEY", f"{new},{old}")
    assert totp.decrypt_secret(token) == b"s" * 20
    assert stored in totp.recovery_hash_candidates("abcd efgh jklm")


def test_migration_file_matches_generated_sql_and_has_rls_without_policies():
    on_disk = (ROOT / "app" / "crm" / "sql" / "001_crm_lite.sql").read_text(encoding="utf-8")
    assert on_disk == schema.postgres_migration_sql()
    for t in schema.ALL_TABLES:
        assert f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;" in on_disk
    assert len(schema.ALL_TABLES) == 10
    assert "CREATE POLICY" not in on_disk.upper()
    assert "DROP TABLE" not in on_disk.upper().replace("-- ROLLBACK: DROP TABLE", "")


def test_no_project_or_customer_data_anywhere_in_the_crm_package():
    ddl = schema.TABLES.lower()
    for forbidden in ("project", "handoff"):
        assert forbidden not in ddl
    # schema.py and rls.py are owner-run SQL text (they name pg_roles, anon, catalog views); the CRM's runtime
    # queries in every other module are what this checks.
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app" / "crm").glob("*.py")
                     if p.name not in ("schema.py", "rls.py", "authmigration.py"))
    for table in ("career_profiles", "purchases", "entitlements", "businesses", "card_settings", "mentor",
                  "diagnostics", "resumes"):
        assert not re.search(rf"\b(FROM|JOIN|INTO|UPDATE)\s+{table}\b", code), table
    # The only non-CRM tables the CRM reads are identity (users) and Founder membership (super_admins).
    touched = set(re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_]+)", code))
    assert {t for t in touched if not t.startswith("crm_")} <= {"users", "super_admins"}


def test_no_new_host_or_redirect_or_external_call_in_the_crm_package():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app" / "crm").glob("*.py"))
    for needle in ("http://", "https://", "requests.", "httpx", "smtplib", "urllib.request"):
        assert needle not in code.replace("otpauth://", ""), needle


def test_seed_refuses_anything_that_could_be_production(monkeypatch, tmp_path):
    class FakePg:
        _is_postgres = True
    monkeypatch.setattr(config, "ISOLATED_DEPLOYMENT", False)
    monkeypatch.setattr(config, "CRM_ALLOW_SCHEMA_ON_POSTGRES", False, raising=False)
    with pytest.raises(seed.SeedRefused):
        seed.seed_synthetic(FakePg(), "a-long-synthetic-password")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://example.test/db")
    with pytest.raises(seed.SeedRefused):
        seed.seed_synthetic(Database(tmp_path / "y.db"), "a-long-synthetic-password")


def test_seed_builds_a_realistic_synthetic_dataset(crm, db):
    r = seed.seed_synthetic(db, "synthetic-seed-password")
    assert r["seeded"] and len(r["specialists"]) == 4
    assert seed.seed_synthetic(db, "synthetic-seed-password")["seeded"] is False  # idempotent
    with db.connect() as conn:
        emails = column(conn.execute("SELECT email FROM users").fetchall())
    assert emails and all(e.endswith("@example.test") for e in emails)


def test_proposed_rls_policy_file_matches_generated_text_and_is_not_applied_by_code():
    from app.crm import rls
    on_disk = (ROOT / "app" / "crm" / "sql" / "002_crm_rls_policies_PROPOSED.sql").read_text(encoding="utf-8")
    assert on_disk == rls.POLICIES_SQL
    # No code path executes it outside tests: ensure_schema and init_crm never reference the policies.
    for name in ("schema.py", "setup.py", "seed.py", "routes.py"):
        assert "POLICIES_SQL" not in (ROOT / "app" / "crm" / name).read_text(encoding="utf-8")
