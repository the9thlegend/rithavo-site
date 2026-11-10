"""Preview database wiring: configuration guards, provisioning SQL, fail-closed behaviour. No network, no secrets."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.crm import rls

ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "app" / "crm" / "sql" / "preview"


def _config_in_subprocess(env_extra, expr):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RITHAVO_") and k != "VERCEL_ENV"}
    env.update(env_extra)
    code = f"import config; print({expr})"
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, r'{ROOT}'); {code}"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode, (r.stdout + r.stderr).strip()


PREVIEW = {"VERCEL_ENV": "preview"}
OK_URL = "postgresql://crm_app.sbejvcosjgtwchsafrng:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres"




def test_preview_never_reads_the_production_url_variable():
    rc, out = _config_in_subprocess({**PREVIEW, "RITHAVO_WEB_DATABASE_URL": OK_URL}, "config.DATABASE_URL")
    assert (rc, out) == (0, "None")




def test_provisioning_sql_files_match_generated_text_and_hold_no_credentials():
    body = (rls.PREVIEW_PROVISION_SQL + rls.POLICIES_SQL.replace("BEGIN;\n", "", 1).rsplit("COMMIT;", 1)[0]
            + rls.POST_POLICY_SQL)
    on_disk = (SQL / "020_roles_rls.sql").read_text(encoding="utf-8")
    assert on_disk == body
    assert "PASSWORD" not in on_disk.upper() and "SCRAM" not in on_disk.upper()
    assert "NOBYPASSRLS" in on_disk and "NOSUPERUSER" in on_disk and "CONNECTION LIMIT" in on_disk
    assert "statement_timeout" in on_disk
    assert on_disk.count("SECURITY DEFINER SET search_path = public, pg_temp") == 2   # both SECURITY DEFINER functions
    assert on_disk.count("SET search_path = public, pg_temp") == 5                    # plus the three helper functions
    assert "SECURITY DEFINER SET search_path = public AS" not in on_disk
    for f in ("000_app_base.sql", "010_crm.sql", "020_roles_rls.sql"):
        assert (SQL / f).exists()
        text = (SQL / f).read_text(encoding="utf-8")
        assert "postgresql://" not in text and "sb_" not in text


def test_startup_skips_ddl_only_when_preprovisioned():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "if not config.DB_PREPROVISIONED:" in main
    assert main.index("if not config.DB_PREPROVISIONED:") < main.index("app.state.db.init_schema()")


def test_reference_allocation_takes_a_transaction_advisory_lock_on_postgres():
    from app.crm import store
    calls = []

    class Cur:
        def fetchone(self): return None

    class PgConn:
        _conn = object()
        def execute(self, sql, params=()):
            calls.append(sql)
            return Cur()

    assert store._next_ref(PgConn()).endswith("-001")
    assert calls[0].startswith("SELECT pg_advisory_xact_lock")


class _FakeInnerPool:
    def __init__(self, conns):
        self.conns, self.put = list(conns), []

    def getconn(self):
        return self.conns.pop(0)

    def putconn(self, conn, close=False):
        self.put.append((conn, close))


class _FakeConn:
    def __init__(self, closed=False, fails=False):
        self.closed, self.fails, self.rolled = closed, fails, 0

    def cursor(self):
        outer = self

        class C:
            def execute(self, sql):
                if outer.fails:
                    raise RuntimeError("SSL connection has been closed unexpectedly")

            def close(self): pass
        return C()

    def rollback(self): self.rolled += 1


def test_pool_discards_dead_connections_and_returns_a_healthy_one():
    dead1, dead2, good = _FakeConn(closed=True), _FakeConn(fails=True), _FakeConn()
    inner = _FakeInnerPool([dead1, dead2, good])
    pool = rls.BoundedValidatingPool(inner, size=2)
    assert pool.getconn() is good
    assert [(c is dead1, close) for c, close in inner.put] == [(True, True), (False, True)]
    pool.putconn(good)


def test_pool_waits_for_a_free_slot_then_gives_a_clear_503_instead_of_pool_exhausted():
    from fastapi import HTTPException
    inner = _FakeInnerPool([_FakeConn(), _FakeConn()])
    pool = rls.BoundedValidatingPool(inner, size=1, wait=0.2)
    first = pool.getconn()
    with pytest.raises(HTTPException) as e:
        pool.getconn()
    assert e.value.status_code == 503
    pool.putconn(first)
    assert pool.getconn() is not None   # slot is free again


def test_install_wraps_a_postgres_pool_only():
    class Db:
        _is_postgres = True
        _pg_pool = None
        def _get_pg_pool(self): return _FakeInnerPool([])
        def connect(self): raise AssertionError
    d = Db()
    rls.install(d)
    assert isinstance(d._pg_pool, rls.BoundedValidatingPool)
    rls.uninstall()
