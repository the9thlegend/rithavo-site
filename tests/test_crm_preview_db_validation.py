"""Pre-provisioned Preview database: URL validation, fail-closed least-privilege verification, safe start-up.
No secrets: every URL below is fake. The optional live test runs only when a Preview test URL is supplied via the
environment (never stored in the repository)."""

import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI

import config
from app.crm import diag, rls, setup
from app.db import Database

ROOT = Path(__file__).resolve().parent.parent
REF = "abcdefghijklmnopqrst"           # a fake, non-production project reference
GOOD = f"postgresql://crm_app.{REF}:s3cretpw@aws-0-us-east-1.pooler.supabase.com:6543/postgres"


def v(raw):
    return config.validate_preview_database_url(raw)


# ---- URL validation -------------------------------------------------------------------------------------------

def test_valid_postgresql_urls_are_accepted_unchanged():
    assert v(GOOD) == GOOD
    assert v(GOOD.replace("postgresql://", "postgres://")).startswith("postgres://")
    assert v("postgresql://crm_app:pw@db.abcdefghijklmnopqrst.supabase.co:5432/postgres").startswith("postgresql://crm_app:")


@pytest.mark.parametrize("raw", [" " + GOOD, GOOD + " ", "  " + GOOD + "\n", "\t" + GOOD + "\r\n"])
def test_accidental_leading_and_trailing_whitespace_is_trimmed(raw):
    assert v(raw) == GOOD


@pytest.mark.parametrize("raw", [f'"{GOOD}"', f"'{GOOD}'", f"`{GOOD}`", f'"{GOOD}', f"{GOOD}'"])
def test_quoted_urls_are_rejected_not_normalised(raw):
    with pytest.raises(config.PreviewDatabaseUrlError, match="surrounded_by_quotes"):
        v(raw)


@pytest.mark.parametrize("raw", [f"psql {GOOD}", f"psql '{GOOD}' -c 'select 1'", f"export X={GOOD} postgresql://x", "postgresql://crm_app:p w@h/db"])
def test_shell_prefixes_and_internal_whitespace_are_rejected(raw):
    with pytest.raises(config.PreviewDatabaseUrlError):
        v(raw)


@pytest.mark.parametrize("raw", ["/tmp/rithavo_preview.db", "C:\\temp\\preview.db", "./storage/web_platform.db", "sqlite:///tmp/x.db",
                                 "file:preview.db", "mysql://crm_app:pw@h:3306/db", "postgresql+psycopg2://crm_app:pw@h/db",
                                 "POSTGRESQL://crm_app:pw@h/db", "jdbc:postgresql://h/db", "rithavo_preview.db"])
def test_sqlite_paths_and_non_postgresql_values_are_rejected(raw):
    with pytest.raises(config.PreviewDatabaseUrlError, match="scheme_is_not_postgresql"):
        v(raw)


@pytest.mark.parametrize("raw", [None, "", "   ", "\n"])
def test_missing_or_empty_is_rejected(raw):
    with pytest.raises(config.PreviewDatabaseUrlError, match="missing_or_empty"):
        v(raw)


@pytest.mark.parametrize("raw", ["postgresql://", "postgresql:///postgres", "postgresql://crm_app@h/postgres",
                                 "postgresql://crm_app:pw@h:notaport/postgres", "postgresql://crm_app:pw@h:6543",
                                 "postgresql://crm_app:pw@h:6543/", "postgres:/crm_app:pw@h/db", "postgresql:crm_app:pw@h/db"])
def test_malformed_urls_are_rejected(raw):
    with pytest.raises(config.PreviewDatabaseUrlError):
        v(raw)


@pytest.mark.parametrize("raw", [
    "postgresql://crm_app.todgmhddihfgqeofzudb:pw@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres",
    "postgresql://crm_app:pw@db.todgmhddihfgqeofzudb.supabase.co:5432/postgres",
    "postgresql://postgres.todgmhddihfgqeofzudb:pw@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres",
])
def test_production_project_is_rejected(raw):
    with pytest.raises(config.PreviewDatabaseUrlError, match="production_project"):
        v(raw)


@pytest.mark.parametrize("user", ["postgres", f"postgres.{REF}", "supabase_admin", "authenticator", "service_role", "crm_app_admin", "crm_apps", "anon"])
def test_any_role_other_than_crm_app_is_rejected(user):
    with pytest.raises(config.PreviewDatabaseUrlError, match="role_must_be_crm_app"):
        v(f"postgresql://{user}:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres")


def test_error_messages_never_contain_the_value_user_host_or_password():
    bad = [f'"{GOOD}"', f"psql {GOOD}", "/tmp/secret-path/x.db", GOOD.replace("crm_app", "postgres"),
           GOOD.replace(REF, "todgmhddihfgqeofzudb")]
    for raw in bad:
        with pytest.raises(config.PreviewDatabaseUrlError) as e:
            v(raw)
        text = str(e.value)
        for needle in ("s3cretpw", REF, "pooler.supabase.com", "secret-path", "aws-0"):
            assert needle not in text


# ---- config: pre-provisioned mode never falls back to SQLite --------------------------------------------------------

def _config(env_extra, expr="config.DB_PREPROVISIONED"):
    env = {k: val for k, val in os.environ.items() if not k.startswith("RITHAVO_") and k != "VERCEL_ENV"}
    env.update(env_extra)
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, r'{ROOT}'); import config; print({expr})"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode, (r.stdout + r.stderr)


PREVIEW = {"VERCEL_ENV": "preview", "RITHAVO_PREVIEW_DB_PREPROVISIONED": "1"}


def test_config_accepts_a_valid_url_and_trims_whitespace():
    assert _config({**PREVIEW, "RITHAVO_PREVIEW_DATABASE_URL": GOOD}) == (0, "True\n")
    rc, out = _config({**PREVIEW, "RITHAVO_PREVIEW_DATABASE_URL": "  " + GOOD + "\n"}, "config.DATABASE_URL == '%s'" % GOOD)
    assert (rc, out.strip()) == (0, "True")


@pytest.mark.parametrize("value,category", [
    (f'"{GOOD}"', "surrounded_by_quotes"), (f"psql {GOOD}", "contains_whitespace_or_shell_prefix"),
    ("/tmp/rithavo_preview.db", "scheme_is_not_postgresql"), ("", "missing_or_empty"),
    ("postgresql://crm_app.todgmhddihfgqeofzudb:pw@h:6543/postgres", "production_project"),
    (GOOD.replace("crm_app", "postgres"), "role_must_be_crm_app"),
])
def test_config_stops_startup_with_a_category_only_error_instead_of_using_sqlite(value, category):
    rc, out = _config({**PREVIEW, "RITHAVO_PREVIEW_DATABASE_URL": value})
    assert rc != 0 and category in out
    for needle in ("s3cretpw", REF, "pooler.supabase.com"):
        assert needle not in out


def test_config_requires_a_url_when_preprovisioned_is_set_on_preview():
    rc, out = _config({**PREVIEW})
    assert rc != 0 and "missing_or_empty" in out


def test_flag_alone_is_ignored_outside_preview_and_without_the_flag_nothing_changes():
    assert _config({"RITHAVO_PREVIEW_DB_PREPROVISIONED": "1"}) == (0, "False\n")                       # not a Preview deployment
    assert _config({"VERCEL_ENV": "preview", "RITHAVO_PREVIEW_DATABASE_URL": GOOD}) == (0, "False\n")  # flag absent
    assert _config({"VERCEL_ENV": "production", **{"RITHAVO_PREVIEW_DB_PREPROVISIONED": "1"}}) == (0, "False\n")


# ---- least-privilege verification fails closed -------------------------------------------------------------------

GOOD_ROW = {"name": "crm_app", "su": False, "bypass": False, "createrole": False, "createdb": False, "canlogin": True,
            "replication": False, "owner": "postgres", "forced": True, "policies": 11, "can_select": True,
            "can_insert": True, "can_update": True, "can_truncate": False, "can_create_in_schema": False,
            "inherits_owner": False, "can_run_ref_fn": True, "can_run_probe_fn": True}


class _Conn:
    def __init__(self, row): self.row = row
    def execute(self, *_a, **_k): return self
    def fetchone(self): return self.row
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakePg:
    _is_postgres = True
    def __init__(self, row): self.row = row
    def connect(self): return _Conn(self.row)


def test_sqlite_can_never_produce_a_least_privilege_verification(db):
    with pytest.raises(rls.LeastPrivilegeError, match="not PostgreSQL"):
        rls.assert_least_privilege(db)


def test_postgresql_confirmation_returns_the_verified_facts():
    facts = rls.assert_least_privilege(_FakePg(GOOD_ROW))
    assert facts["role"] == "crm_app" and "not_bypassrls" in facts["checks"] and len(facts["checks"]) == 12


@pytest.mark.parametrize("override,failing", [
    ({"name": "postgres"}, "role_is_crm_app"), ({"name": "other_app"}, "role_is_crm_app"),
    ({"su": True}, "not_superuser"), ({"bypass": True}, "not_bypassrls"),
    ({"createrole": True}, "no_createrole_createdb_replication"), ({"createdb": True}, "no_createrole_createdb_replication"),
    ({"replication": True}, "no_createrole_createdb_replication"), ({"canlogin": False}, "can_login"),
    ({"owner": "crm_app"}, "not_table_owner"), ({"inherits_owner": True}, "not_table_owner"),
    ({"forced": False}, "rls_forced_on_crm_tables"), ({"policies": 0}, "ownership_policies_present"),
    ({"policies": 8}, "ownership_policies_present"), ({"can_select": False}, "has_required_dml"),
    ({"can_insert": False}, "has_required_dml"), ({"can_update": False}, "has_required_dml"),
    ({"can_truncate": True}, "no_truncate"), ({"can_create_in_schema": True}, "no_create_in_schema"),
    ({"can_run_ref_fn": False}, "can_run_definer_functions"), ({"can_run_probe_fn": False}, "can_run_definer_functions"),
])
def test_every_missing_or_excess_privilege_fails_verification(override, failing):
    with pytest.raises(rls.LeastPrivilegeError) as e:
        rls.assert_least_privilege(_FakePg({**GOOD_ROW, **override}))
    assert failing in str(e.value)


def test_unprovisioned_and_unknown_roles_fail_closed():
    with pytest.raises(rls.LeastPrivilegeError, match="not provisioned"):
        rls.assert_least_privilege(_FakePg({**GOOD_ROW, "owner": None}))
    with pytest.raises(rls.LeastPrivilegeError, match="not found"):
        rls.assert_least_privilege(_FakePg(None))


# ---- start-up never claims verification it did not get ------------------------------------------------------------

def _boot_lines(caplog):
    return "\n".join(r.getMessage() for r in caplog.records if r.name == "rithavo_web.crm.diag" and "crm_boot" in r.getMessage())


def test_boot_says_verified_only_with_postgresql_confirmation(caplog, db):
    with caplog.at_level(logging.INFO, logger="rithavo_web.crm.diag"):
        diag.boot(db, provisioned=True, verified=None)                                # no confirmation
        diag.boot(db, provisioned=True, verified={"role": "crm_app"})                 # confirmation dict but SQLite database
        pg = _FakePg(GOOD_ROW); pg.db_path = "postgresql://crm_app:pw@h:6543/postgres"
        diag.boot(pg, provisioned=True, verified=rls.assert_least_privilege(pg))     # real PostgreSQL confirmation
    lines = _boot_lines(caplog).splitlines()
    assert "least_privilege=not_verified" in lines[0] and "least_privilege=not_verified" in lines[1]
    assert "least_privilege=verified" in lines[2] and "role=crm_app" in lines[2]
    assert "pw@" not in "\n".join(lines) and "postgresql://" not in "\n".join(lines)


def test_init_crm_with_preprovisioned_flag_on_sqlite_keeps_the_crm_off_and_never_reports_verified(monkeypatch, caplog, db):
    monkeypatch.setattr(config, "CRM_ENABLED", True)
    monkeypatch.setattr(config, "DB_PREPROVISIONED", True)
    monkeypatch.setattr(config, "CRM_SEED_ENABLED", False)
    app = FastAPI()
    app.state.db = db                                                                   # SQLite
    with caplog.at_level(logging.INFO):
        setup.init_crm(app)
    assert config.CRM_ENABLED is False
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "least_privilege=verified" not in text and "not PostgreSQL" in text


def test_init_crm_failure_logs_no_connection_details(monkeypatch, caplog):
    class Boom:
        _is_postgres = True
        def connect(self):
            raise RuntimeError('connection to server at "db-host.example" failed: password authentication failed for user "crm_app"')
    monkeypatch.setattr(config, "CRM_ENABLED", True)
    monkeypatch.setattr(config, "DB_PREPROVISIONED", True)
    monkeypatch.setattr(config, "CRM_SEED_ENABLED", False)
    app = FastAPI(); app.state.db = Boom()
    with caplog.at_level(logging.INFO):
        setup.init_crm(app)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert config.CRM_ENABLED is False and "RuntimeError" in text
    assert "db-host.example" not in text and "password authentication" not in text


# ---- live: successful connection as crm_app (opt-in; the URL is supplied by the environment, never stored) --------------

@pytest.mark.skipif(not os.environ.get("RITHAVO_PREVIEW_TEST_DATABASE_URL"), reason="no Preview test database URL in the environment")
def test_live_connection_as_crm_app_is_confirmed_by_postgresql():
    url = config.validate_preview_database_url(os.environ["RITHAVO_PREVIEW_TEST_DATABASE_URL"])
    live = Database(url)
    facts = rls.assert_least_privilege(live)
    assert facts["role"] == "crm_app" and facts["policies"] >= 9
    with live.connect() as conn:
        assert conn.execute("SELECT current_user AS u").fetchone()["u"] == "crm_app"
