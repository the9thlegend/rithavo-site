"""CRM Lite on a real (disposable, localhost-only) PostgreSQL: RLS on with no policies, anonymous and
authenticated roles denied, application role, the migration file itself. Skipped unless
RITHAVO_WEB_TEST_POSTGRES_DSN is set (the suite's existing opt-in; conftest enforces a localhost host)."""

import pathlib

import pytest

from app.crm import schema
from tests.crm_helpers import clock, crm, column, scalar  # noqa: F401
from tests.conftest import POSTGRES_DSN

pytestmark = pytest.mark.skipif(not POSTGRES_DSN, reason="needs RITHAVO_WEB_TEST_POSTGRES_DSN (local disposable Postgres)")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def raw():
    import psycopg2
    c = psycopg2.connect(POSTGRES_DSN)
    c.autocommit = True
    return c


def ensure_role(cur, name):
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if not cur.fetchone():
        cur.execute(f"CREATE ROLE {name} NOLOGIN")


def test_rls_enabled_on_all_ten_tables_with_no_policies(crm):
    c = raw(); cur = c.cursor()
    cur.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relname LIKE 'crm_%' AND relkind = 'r'")
    rows = dict(cur.fetchall())
    assert set(rows) == set(schema.ALL_TABLES) and all(rows.values())
    cur.execute("SELECT count(*) FROM pg_policies WHERE tablename LIKE 'crm_%'")
    assert cur.fetchone()[0] == 0


def test_app_role_is_the_table_owner_and_works(crm):
    f = crm.founder()
    s = crm.specialist("rls.spec@example.test")
    crm.lead(s)
    c = raw(); cur = c.cursor()
    cur.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE tablename LIKE 'crm_%'")
    owners = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT current_user")
    assert owners == [cur.fetchone()[0]]  # the connecting (application) role owns every CRM table
    cur.execute("SELECT count(*) FROM crm_opportunities")
    assert cur.fetchone()[0] == 1  # the application role reads and writes through RLS (owner bypass)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_anonymous_and_authenticated_roles_are_denied(crm, role):
    s = crm.specialist("rls.spec2@example.test")
    crm.lead(s)
    c = raw(); cur = c.cursor()
    ensure_role(cur, role)
    cur.execute(f"GRANT USAGE ON SCHEMA public TO {role}")  # as on Supabase, so the tables are visible by name
    # 1) Privileges revoked by the migration: a plain read is a permission error.
    cur.execute(f"SET ROLE {role}")
    import psycopg2
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("SELECT * FROM crm_companies")
    cur.execute("RESET ROLE")
    # 2) Even if someone later re-grants table privileges, RLS with no policy still returns nothing and refuses writes.
    cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON crm_companies, crm_audit_log, crm_mfa TO {role}")
    try:
        cur.execute(f"SET ROLE {role}")
        for table in ("crm_companies", "crm_audit_log", "crm_mfa"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO crm_companies (owner_user_id, name, name_key, created_by, created_at, updated_at) "
                        "VALUES (1, 'x', 'x', 1, 'x', 'x')")
        cur.execute("UPDATE crm_companies SET name = 'hacked'")
        assert cur.rowcount == 0
        cur.execute("DELETE FROM crm_companies")
        assert cur.rowcount == 0
    finally:
        cur.execute("RESET ROLE")
        cur.execute(f"REVOKE ALL ON crm_companies, crm_audit_log, crm_mfa FROM {role}")
    cur.execute("SELECT count(*) FROM crm_companies")
    assert cur.fetchone()[0] == 1


def test_migration_file_applies_cleanly_and_is_idempotent(crm):
    c = raw(); cur = c.cursor()
    cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    sql = (ROOT / "app" / "crm" / "sql" / "001_crm_lite.sql").read_text(encoding="utf-8")
    cur.execute(sql)
    cur.execute(sql)  # re-running changes nothing and does not fail
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'crm_%'")
    assert cur.fetchone()[0] == 10
    cur.execute("SELECT count(*) FROM pg_class WHERE relname LIKE 'crm_%' AND relkind='r' AND relrowsecurity")
    assert cur.fetchone()[0] == 10
    cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE '%append_only'")
    assert cur.fetchone()[0] == 2
    # Rollback script from the file header works and leaves nothing behind.
    cur.execute("DROP TABLE crm_audit_log, crm_activities, crm_founder_reviews, crm_followups, crm_opportunities, "
                "crm_contacts, crm_companies, crm_mfa_recovery_codes, crm_mfa, crm_staff; DROP FUNCTION crm_append_only();")
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'crm_%'")
    assert cur.fetchone()[0] == 0
