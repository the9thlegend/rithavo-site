"""Database-enforced isolation (proposal), proven on a disposable local PostgreSQL. The application runs as the
non-owner, non-BYPASSRLS role `crm_app`; PostgreSQL itself filters every CRM business-table query. Skipped unless
RITHAVO_WEB_TEST_POSTGRES_DSN is set."""

import psycopg2
import pytest

from app.crm import access, rls, store
from tests.conftest import POSTGRES_DSN
from tests.crm_helpers import (Person, app_role_dsn, clock, column, crm, enforce_db_rls,  # noqa: F401
                               scalar)

pytestmark = pytest.mark.skipif(not POSTGRES_DSN, reason="needs a local disposable PostgreSQL")


_OPEN = []


@pytest.fixture
def env(crm, monkeypatch):
    if not hasattr(crm, "app_db"):
        enforce_db_rls(crm, monkeypatch)
    f = crm.founder()
    a = crm.specialist("rls.alice@example.test", "Alice")
    b = crm.specialist("rls.bala@example.test", "Bala")
    la = crm.lead(a, company="Alder Works")
    lb = crm.lead(b, company="Birch Mills")
    a.post(f"/crm/opportunities/{la['id']}/activities", {"kind": "note", "summary": "Alice's private note"})
    b.post(f"/crm/opportunities/{lb['id']}/activities", {"kind": "note", "summary": "Bala's private note"})
    yield crm, f, a, b, la, lb
    for c in _OPEN:  # raw connections must not hold locks into the next test's schema reset
        try:
            c.rollback(); c.close()
        except Exception:
            pass
    _OPEN.clear()


def as_app(user_id=None, role=None):
    """A raw connection as crm_app with the per-transaction context set the way the application sets it."""
    c = psycopg2.connect(app_role_dsn(POSTGRES_DSN))
    _OPEN.append(c)
    cur = c.cursor()
    if user_id is not None:
        cur.execute("SELECT set_config('app.user_id', %s, true), set_config('app.role', %s, true)", (str(user_id), role))
    return c, cur


def test_app_role_is_not_owner_not_superuser_not_bypassrls(env):
    c = psycopg2.connect(POSTGRES_DSN); _OPEN.append(c); cur = c.cursor()
    cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'crm_app'")
    assert cur.fetchone() == (False, False)
    cur.execute("SELECT count(*) FROM pg_tables WHERE tablename LIKE 'crm_%' AND tableowner = 'crm_app'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM pg_class WHERE relname LIKE 'crm_%' AND relkind='r' AND relforcerowsecurity")
    assert cur.fetchone()[0] >= 7  # the business tables and the audit log are FORCE'd


def test_database_alone_isolates_even_if_the_application_scope_is_removed(env, monkeypatch):
    crm, f, a, b, la, lb = env
    # Sabotage the application layer completely: no owner filter, no ownership check.
    monkeypatch.setattr(access.Scope, "clause", lambda self, alias="": ("1=1", ()))
    monkeypatch.setattr(access.Scope, "fetch",
                        lambda self, conn, table, row_id, entity: _plain_fetch(conn, table, row_id))
    assert [c["name"] for c in b.get("/crm/companies").json()] == ["Birch Mills"]
    assert [o["ref"] for o in b.get("/crm/opportunities").json()] == [lb["ref"]]
    assert b.get(f"/crm/opportunities/{la['id']}").status_code == 404
    assert b.get(f"/crm/companies/{la['company_id']}").status_code == 404
    assert b.post(f"/crm/opportunities/{la['id']}/stage", {"to": "Contacted"}).status_code == 404
    assert b.get("/crm/search?q=Alder").json() == {"companies": [], "contacts": [], "opportunities": []}
    assert b.get("/crm/reports").json()["total"] == 1
    assert {c["name"] for c in f.get("/crm/companies").json()} == {"Alder Works", "Birch Mills"}


def _plain_fetch(conn, table, row_id):
    from fastapi import HTTPException
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return row


def test_raw_sql_as_the_application_role_is_filtered_by_postgres(env):
    crm, f, a, b, la, lb = env
    c, cur = as_app(b.user_id, "specialist")
    for table in ("crm_companies", "crm_contacts", "crm_opportunities"):
        cur.execute(f"SELECT DISTINCT owner_user_id FROM {table}")  # no WHERE clause at all
        assert [r[0] for r in cur.fetchall()] == [b.user_id]
    cur.execute("SELECT count(*) FROM crm_opportunities WHERE 1=1 OR owner_user_id = %s", (a.user_id,))  # injection-shaped
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT summary FROM crm_activities")
    assert [r[0] for r in cur.fetchall()] == ["Bala's private note"]
    cur.execute("SELECT count(*) FROM crm_activities a WHERE a.opportunity_id = %s", (la["id"],))
    assert cur.fetchone()[0] == 0
    cur.execute("UPDATE crm_companies SET name = 'x'")
    assert cur.rowcount == 1  # only Bala's own row
    cur.execute("DELETE FROM crm_followups")
    cur.execute("INSERT INTO crm_audit_log (at, actor_user_id, action, outcome) VALUES ('t', %s, 'x', 'ok') RETURNING id", (b.user_id,))
    c.rollback()
    c, cur = as_app(b.user_id, "specialist")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):  # cannot create a row owned by someone else
        cur.execute("INSERT INTO crm_companies (owner_user_id, name, name_key, created_by, created_at, updated_at) "
                    "VALUES (%s, 'Stolen', 'stolen', 1, 't', 't')", (a.user_id,))
    c.rollback()
    c, cur = as_app(b.user_id, "specialist")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):  # nor hand a row to someone else
        cur.execute("UPDATE crm_companies SET owner_user_id = %s", (a.user_id,))
    c.rollback()


def test_no_context_matches_nothing_and_founder_context_sees_all(env):
    crm, f, a, b, la, lb = env
    c, cur = as_app()  # authenticated to the database but no caller set: fail closed
    for table in ("crm_companies", "crm_contacts", "crm_opportunities", "crm_followups", "crm_activities",
                  "crm_founder_reviews", "crm_audit_log"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0, table
    c, cur = as_app(f.user_id, "founder")
    cur.execute("SELECT count(*) FROM crm_companies")
    assert cur.fetchone()[0] == 2


def test_application_role_cannot_switch_rls_off(env):
    crm, f, a, b, la, lb = env
    c, cur = as_app(b.user_id, "specialist")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("ALTER TABLE crm_companies DISABLE ROW LEVEL SECURITY")
    c.rollback(); c, cur = as_app(b.user_id, "specialist")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("DROP POLICY crm_owner_all ON crm_companies")
    c.rollback(); c, cur = as_app(b.user_id, "specialist")
    cur.execute("SET row_security = off")  # for a non-BYPASSRLS role this raises instead of bypassing
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("SELECT * FROM crm_companies")


def test_audit_log_is_readable_only_for_own_actions_by_specialists(env):
    crm, f, a, b, la, lb = env
    c, cur = as_app(b.user_id, "specialist")
    cur.execute("SELECT DISTINCT actor_user_id FROM crm_audit_log")
    assert {r[0] for r in cur.fetchall()} <= {b.user_id}


def test_duplicate_probe_returns_ids_only_for_own_rows(env):
    crm, f, a, b, la, lb = env
    c, cur = as_app(b.user_id, "specialist")
    cur.execute("SELECT kind, row_id, company_id, mine FROM crm_dup_probe('alderworks', '')")
    assert cur.fetchall() == [("company", None, None, False)]  # exists, but no id and not mine
    cur.execute("SELECT kind, row_id, mine FROM crm_dup_probe('birchmills', '')")
    assert cur.fetchall() == [("company", lb["company_id"], True)]
    c2, cur2 = as_app(f.user_id, "founder")
    cur2.execute("SELECT row_id FROM crm_dup_probe('alderworks', '')")
    assert cur2.fetchall() == [(la["company_id"],)]
