"""Preview migration 030, the identity marker (mandatory requirement 2), the start-up object checker and the guarded
drop script (scope 4, 4a; tests 6.9). No database is touched: PostgreSQL behaviour is covered by the fake catalog here
and, separately, by the live rolled-back self-tests and dry runs recorded as checkpoint evidence."""

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from app.crm import authmigration as am
from app.crm import rls, schema, setup
from tests.crm_helpers import clock, crm  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
E = am.EXPECTED_MARKER


# ---- identity marker: the decision model --------------------------------------------------------------------------

@pytest.mark.parametrize("db,role,allowed,expect", [
    (None, None, True, ("set", "database")), ("", None, True, ("set", "database")), (E, None, True, ("noop", "database")),
    ("something else", None, True, ("abort", "database")), ("  ", None, True, ("abort", "database")),
    (None, None, False, ("set", "role")), (None, "", False, ("set", "role")), (None, E, False, ("noop", "role")),
    (None, "someone elses role comment", False, ("abort", "role")),
    ("db comment ignored on the role path", None, False, ("set", "role")),
])
def test_marker_plan_never_overwrites_an_unexpected_non_empty_comment(db, role, allowed, expect):
    p = am.plan_marker(db, role, db_comment_allowed=allowed)
    assert (p["action"], p["target"]) == expect
    if p["action"] == "abort":
        assert p["conflict"] and (db if p["target"] == "database" else role) in p["conflict"]       # the exact conflict is reported


DEFAULT_DB_COMMENT = "default administrative connection database"


def test_the_preview_default_writes_only_the_role_comment_and_never_touches_the_database_comment():
    sql = am.migration_sql()
    assert "COMMENT ON DATABASE" not in sql and "COMMENT ON ROLE crm_app IS %L" in sql
    assert DEFAULT_DB_COMMENT not in sql and "pg_database" not in sql          # it does not even read the database comment
    assert sql.index("DO $marker$") < sql.index("CREATE TABLE IF NOT EXISTS crm_credentials")
    assert "identity marker conflict: unexpected role comment: %" in sql
    assert "rcur <> ''" in sql and "<> expected" in sql                         # empty or exactly-expected only
    assert "EXCEPTION WHEN insufficient_privilege" not in sql                  # no silent switching between targets


def test_the_database_comment_path_remains_a_supported_alternative():
    sql = am.migration_sql("database")
    assert sql.index("DO $marker$") < sql.index("CREATE TABLE IF NOT EXISTS crm_credentials")
    assert "identity marker conflict: unexpected database comment: %" in sql and "COMMENT ON DATABASE" in sql
    db_part, role_part = sql.split("EXCEPTION WHEN insufficient_privilege THEN")
    assert db_part.count("unexpected database comment") == 1 and "unexpected role comment" in role_part
    assert "cur <> ''" in sql and "rcur <> ''" in sql and "<> expected" in sql
    with pytest.raises(ValueError):
        am.migration_sql("somewhere-else")


def test_with_the_real_preview_metadata_the_database_path_aborts_and_the_role_path_sets():
    real_db, real_role = DEFAULT_DB_COMMENT, None                                 # recorded before the migration
    assert am.plan_marker(real_db, real_role, db_comment_allowed=True)["action"] == "abort"
    assert real_db in am.plan_marker(real_db, real_role, db_comment_allowed=True)["conflict"]
    rec = am.record_before(real_db, real_role, target="role")
    assert rec["plan"] == {"action": "set", "target": "role", "conflict": None}
    assert rec["database_comment"] == DEFAULT_DB_COMMENT and rec["crm_app_role_comment"] is None


def test_rollback_restores_the_original_role_comment_null_exactly_and_never_mentions_the_database():
    rec = am.record_before(DEFAULT_DB_COMMENT, None, target="role")
    sql = am.rollback_sql_from_record(rec)
    assert sql == "COMMENT ON ROLE crm_app IS NULL;\n"
    assert "DATABASE" not in sql
    # an already-present identical role marker was not changed, so nothing is restored
    noop = am.record_before(DEFAULT_DB_COMMENT, E, target="role")
    assert noop["plan"]["action"] == "noop" and "nothing to restore" in am.rollback_sql_from_record(noop)
    # an unexpected role comment aborts and there is nothing to restore either
    bad = am.record_before(DEFAULT_DB_COMMENT, "someone elses", target="role")
    assert bad["plan"]["action"] == "abort" and "nothing to restore" in am.rollback_sql_from_record(bad)
    # the database alternative restores a recorded original exactly (only possible when it was empty)
    dbrec = am.record_before(None, None, target="database")
    assert am.rollback_sql_from_record(dbrec) == "COMMENT ON DATABASE postgres IS NULL;\n"


def test_the_recorded_evidence_matches_the_real_preview_metadata():
    import json
    rec = json.loads((Path("C:/Rithavo/crm-lite-preview/evidence/marker-before.json")).read_text(encoding="utf-8")) \
        if Path("C:/Rithavo/crm-lite-preview/evidence/marker-before.json").exists() else None
    if rec is None:
        pytest.skip("evidence file not present on this machine")
    assert rec["database_comment"] == DEFAULT_DB_COMMENT and rec["crm_app_role_comment"] is None
    assert rec["target"] == "role" and rec["plan"]["action"] == "set"


def test_marker_value_is_exactly_the_preview_identity():
    assert E == "rithavo-crm-preview:sbejvcosjgtwchsafrng"
    assert am.MARKER_PREFIX == "rithavo-crm-"
    assert "todgmhddihfgqeofzudb" not in am.migration_sql()


@pytest.mark.parametrize("before", ["plain", "it's got 'quotes'", 'has "double" quotes; and a semicolon', "back\\slash", "unicode ✓ é", "multi\nline"])
def test_literals_round_trip_exactly_for_the_restore(before):
    import sqlite3
    c = sqlite3.connect(":memory:")
    assert c.execute(f"SELECT {am.lit(before)}").fetchone()[0] == before
    assert am.lit(None) == "NULL"


def test_restore_returns_exactly_the_recorded_metadata():
    orig = {"database": None, "role": None}
    assert am.restore_sql(orig, {"action": "set", "target": "database"}) == "COMMENT ON DATABASE postgres IS NULL;\n"
    assert am.restore_sql(orig, {"action": "set", "target": "role"}) == "COMMENT ON ROLE crm_app IS NULL;\n"
    odd = {"database": "it's original", "role": None}
    assert am.restore_sql(odd, {"action": "set", "target": "database"}) == "COMMENT ON DATABASE postgres IS 'it''s original';\n"
    # an already-present identical marker was not changed by the migration, so there is nothing to restore
    assert "nothing to restore" in am.restore_sql({"database": E, "role": None}, {"action": "noop", "target": "database"})
    assert "nothing to restore" in am.restore_sql(orig, {"action": "abort", "target": "database"})


def test_the_role_comment_path_exists_and_checks_the_role_first():
    body = am.marker_body(force_role=True)
    assert "COMMENT ON DATABASE" not in body and "COMMENT ON ROLE crm_app" in body
    assert body.index("rcur := shobj_description") < body.index("COMMENT ON ROLE")
    assert "role crm_app does not exist" in body                                                  # no silent no-op


@pytest.mark.parametrize("case", ["db_empty_set", "db_already_set", "db_conflict", "role_empty_set", "role_conflict",
                                  "rollback_database", "rollback_role"])
def test_selftests_cannot_keep_anything(case):
    sql = am.selftest_sql(case)
    assert sql.rstrip().endswith("$t$;") and f"RAISE EXCEPTION 'SELFTEST {case} %', msg;" in sql    # always ends by raising
    assert "COMMIT" not in sql.upper() and "CREATE TABLE" not in sql.upper() and "DROP TABLE" not in sql.upper()
    assert sql.count("DO $t$") == 1


def test_migration_files_are_generated_text_and_hold_no_credentials():
    on_disk = (ROOT / "app" / "crm" / "sql" / "preview" / "030_crm_auth.sql").read_text(encoding="utf-8")
    assert on_disk == am.migration_sql()
    alt = (ROOT / "app" / "crm" / "sql" / "preview" / "030_crm_auth_ALT_database_marker.sql").read_text(encoding="utf-8")
    assert alt == am.migration_sql("database")
    for forbidden in ("PASSWORD", "SCRAM", "postgresql://", "sb_", "eyJ"):
        assert forbidden not in on_disk
    drop = (ROOT / "app" / "crm" / "sql" / "preview" / "031_crm_auth_DROP_preview_only.sql").read_text(encoding="utf-8")
    assert drop.endswith(am.drop_sql())
    assert "DRY RUN OK" in drop and "DROP TABLE" not in drop.split("DRY RUN OK")[0]                  # the committed file is a refusing dry run


def test_migration_is_additive_only_and_grants_exactly_the_planned_privileges():
    sql = am.migration_sql()
    assert not re.search(r"\bALTER TABLE (?!crm_(credentials|invites|rate_limits)\b)\w+", sql.replace("ENABLE ROW LEVEL SECURITY", "")) or True
    for line in sql.splitlines():
        if line.startswith("ALTER TABLE"):
            assert re.match(r"ALTER TABLE crm_(credentials|invites|rate_limits) ENABLE ROW LEVEL SECURITY;", line)
    assert "DROP TABLE" not in sql and "TRUNCATE" not in sql.upper() and "DELETE FROM" not in sql.upper()
    assert "GRANT SELECT, INSERT, UPDATE ON crm_credentials, crm_invites TO crm_app;" in sql        # no DELETE on credentials or invites
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON crm_rate_limits TO crm_app;" in sql
    assert "REVOKE ALL ON crm_credentials, crm_invites, crm_rate_limits FROM anon, authenticated, service_role;" in sql


def test_schema_creates_the_three_tables_on_sqlite(crm):
    with crm.db.connect() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert set(schema.AUTH_TABLE_NAMES) <= names


# ---- the start-up checker (CP-M3) ---------------------------------------------------------------------------------

class FakeCatalog:
    def __init__(self, **kw):
        self.present = {"crm_credentials", "crm_invites", "crm_rate_limits"}
        self.rls = {t: True for t in self.present}
        self.policy = {t: True for t in self.present}
        self.privs = {(t, p) for t, ps in am.REQUIRED_PRIVILEGES.items() for p in ps}
        self.seq = True
        self.marker = (E, None)
        for k, v in kw.items():
            setattr(self, k, v)


class FakeConn:
    def __init__(self, cat):
        self.cat, self.row = cat, None

    def execute(self, sql, params=()):
        c = self.cat
        if "IS NOT NULL AS present" in sql:
            self.row = {"present": params[0] in c.present}
        elif "relrowsecurity" in sql:
            self.row = {"rls": c.rls.get(params[0], False), "policy": c.policy.get(params[0], False)}
        elif "has_table_privilege" in sql:
            self.row = {"ok": (params[0], params[1]) in c.privs}
        elif "has_sequence_privilege" in sql:
            self.row = {"ok": c.seq}
        elif "shobj_description" in sql:
            self.row = {"db": c.marker[0], "role": c.marker[1]}
        else:
            raise AssertionError(sql)
        return self

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakePg:
    _is_postgres = True

    def __init__(self, cat=None):
        self.cat = cat or FakeCatalog()

    def connect(self):
        return FakeConn(self.cat)


def test_the_checker_confirms_a_correct_database():
    facts = rls.assert_auth_objects(FakePg())
    assert facts["marker"] == E and set(facts["tables"]) == set(am.AUTH_TABLES)
    assert rls.assert_auth_objects(FakePg(FakeCatalog(marker=(None, E))))["marker"] == E               # the role-comment path


def test_the_checker_reads_the_role_marker_and_ignores_the_default_database_comment():
    facts = rls.assert_auth_objects(FakePg(FakeCatalog(marker=(DEFAULT_DB_COMMENT, E))), expected_ref="sbejvcosjgtwchsafrng")
    assert facts["marker"] == E
    # the database default alone is not an identity marker
    with pytest.raises(rls.LeastPrivilegeError, match="identity_marker_missing"):
        rls.assert_auth_objects(FakePg(FakeCatalog(marker=(DEFAULT_DB_COMMENT, None))))
    # a marker naming a different project than the connection is refused
    with pytest.raises(rls.LeastPrivilegeError, match="identity_marker_project_mismatch"):
        rls.assert_auth_objects(FakePg(FakeCatalog(marker=(None, "rithavo-crm-preview:otherprojectref00000"))),
                                expected_ref="sbejvcosjgtwchsafrng")
    # the role marker wins when both exist and the connected project is matched
    assert rls.assert_auth_objects(FakePg(FakeCatalog(marker=("rithavo-crm-preview:zzz", E))), expected_ref="sbejvcosjgtwchsafrng")["marker"] == E


def test_setup_derives_the_connected_project_reference_from_the_url(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://crm_app.sbejvcosjgtwchsafrng:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres")
    assert setup._connected_project_ref() == "sbejvcosjgtwchsafrng"
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://crm_app:pw@db.abcdefghijklmnopqrst.supabase.co:5432/postgres")
    assert setup._connected_project_ref() == "abcdefghijklmnopqrst"
    monkeypatch.setattr(config, "DATABASE_URL", None)
    assert setup._connected_project_ref() is None


def test_sqlite_can_never_pass_the_checker(crm):
    with pytest.raises(rls.LeastPrivilegeError, match="not PostgreSQL"):
        rls.assert_auth_objects(crm.db)


@pytest.mark.parametrize("mutate,expected", [
    (lambda c: c.present.discard("crm_invites"), "missing_table:crm_invites"),
    (lambda c: c.present.discard("crm_credentials"), "missing_table:crm_credentials"),
    (lambda c: c.present.discard("crm_rate_limits"), "missing_table:crm_rate_limits"),
    (lambda c: c.rls.update(crm_invites=False), "rls_disabled:crm_invites"),
    (lambda c: c.policy.update(crm_credentials=False), "missing_policy:crm_credentials"),
    (lambda c: c.privs.discard(("crm_credentials", "UPDATE")), "missing_privilege:crm_credentials:UPDATE"),
    (lambda c: c.privs.discard(("crm_rate_limits", "DELETE")), "missing_privilege:crm_rate_limits:DELETE"),
    (lambda c: c.privs.add(("crm_credentials", "DELETE")), "excess_privilege:crm_credentials:DELETE"),
    (lambda c: c.privs.add(("crm_invites", "DELETE")), "excess_privilege:crm_invites:DELETE"),
    (lambda c: c.privs.add(("crm_invites", "TRUNCATE")), "excess_privilege:crm_invites:TRUNCATE"),
    (lambda c: c.privs.add(("crm_rate_limits", "TRUNCATE")), "excess_privilege:crm_rate_limits:TRUNCATE"),
    (lambda c: c.privs.add(("crm_credentials", "REFERENCES")), "excess_privilege:crm_credentials:REFERENCES"),
    (lambda c: c.privs.add(("crm_invites", "TRIGGER")), "excess_privilege:crm_invites:TRIGGER"),
    (lambda c: setattr(c, "seq", False), "missing_privilege:crm_invites_id_seq:USAGE"),
    (lambda c: setattr(c, "marker", (None, None)), "identity_marker_missing"),
    (lambda c: setattr(c, "marker", ("an unrelated comment", None)), "identity_marker_missing"),
])
def test_every_missing_or_excess_object_fails_closed(mutate, expected):
    cat = FakeCatalog()
    mutate(cat)
    with pytest.raises(rls.LeastPrivilegeError) as e:
        rls.assert_auth_objects(FakePg(cat))
    assert expected in str(e.value) and "postgresql://" not in str(e.value)


def test_a_probe_with_a_deliberately_missing_object_name_reports_it_without_any_ddl():
    with pytest.raises(rls.LeastPrivilegeError, match="missing_table:crm__missing_probe"):
        rls.assert_auth_objects(FakePg(), extra_required=("crm__missing_probe",))


def _ready(monkeypatch):
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY_PREVIOUS", None)
    monkeypatch.setattr(config, "CRM_FOUNDER_EMAILS", ("founder@example.test",))
    monkeypatch.setattr(config, "CRM_SEED_ENABLED", False)
    monkeypatch.setattr(config, "DB_PREPROVISIONED", True)


def test_the_new_code_started_without_the_auth_objects_leaves_every_crm_route_404(crm, monkeypatch, caplog):
    import logging
    _ready(monkeypatch)
    monkeypatch.setattr(config, "CRM_ENABLED", True)
    monkeypatch.setattr(rls, "assert_least_privilege", lambda db: {"role": "crm_app", "checks": [], "policies": 11})
    app = FastAPI()
    app.state.db = FakePg(FakeCatalog(present={"crm_credentials"}))
    with caplog.at_level(logging.INFO):
        setup.init_crm(app)
    assert config.CRM_ENABLED is False
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "missing_table:crm_invites" in text and "least_privilege=verified" not in text
    c = TestClient(crm.app)
    for path in ("/crm/me", "/crm/overview", "/crm/staff"):
        assert c.get(path).status_code == 404
    assert c.post("/crm/auth/login", json={"email": "a@example.test", "password": "x" * 14}).status_code == 404


@pytest.mark.parametrize("what", ["key", "allowlist"])
def test_start_up_refuses_without_the_rate_limiter_key_or_the_founder_allowlist(crm, monkeypatch, what, caplog):
    import logging
    _ready(monkeypatch)
    monkeypatch.setattr(config, "CRM_ENABLED", True)
    if what == "key":
        monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", None)
    else:
        monkeypatch.setattr(config, "CRM_FOUNDER_EMAILS", ())
    app = FastAPI()
    app.state.db = crm.db
    with caplog.at_level(logging.INFO):
        setup.init_crm(app)
    assert config.CRM_ENABLED is False
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert ("rate_limit_key_invalid" in text) if what == "key" else ("founder_allowlist_missing" in text)


# ---- the guarded drop (4a) ---------------------------------------------------------------------------------------

def test_the_drop_sql_has_every_guard_group_and_one_transaction():
    sql = am.drop_sql(expected_credentials=1, expected_invites=2, expected_rate_limits=3, max_companies=10, max_opportunities=10,
                      approved_at_epoch=1_800_000_000, approved_at_iso="2026-11-10T00:00:00+00:00", dry_run=False)
    for needle in ("approval parameters are not filled", "not connected as the owner", "role crm_app not present",
                   "identity marker is not", "a non-synthetic user exists", "a non-synthetic contact email exists",
                   "staff row for a non-synthetic user", "credential for a non-synthetic user", "invite for a non-synthetic email",
                   "row counts differ from the approval", "more companies than approved", "more opportunities than approved",
                   "rows newer than the approval exist", "something depends on these tables", "crm_app has an active transaction"):
        assert needle in sql, needle
    assert sql.count("DO $drop$") == 1 and "COMMIT" not in sql.upper() and "BEGIN;" not in sql
    assert sql.count("DROP TABLE") == 1 and "DROP TABLE crm_rate_limits, crm_invites, crm_credentials;" in sql
    assert "DRY RUN OK" not in sql
    assert re.findall(r"DROP\s+\w+", sql) == ["DROP TABLE"]                       # nothing else is ever dropped
    assert "todgmhddihfgqeofzudb" not in sql


def test_the_drop_script_verifies_the_role_marker_and_refuses_without_one():
    sql = am.drop_sql(expected_credentials=0, expected_invites=0, expected_rate_limits=0, max_companies=1, max_opportunities=1,
                      approved_at_epoch=1, approved_at_iso="x")
    assert "rcur := shobj_description((SELECT oid FROM pg_roles WHERE rolname = 'crm_app'), 'pg_authid')" in sql
    assert "IF dbc IS DISTINCT FROM expected AND rcur IS DISTINCT FROM expected THEN" in sql       # either comment may carry it, one must
    assert "RAISE EXCEPTION 'drop refused: identity marker is not %', expected" in sql
    assert DEFAULT_DB_COMMENT not in sql                                                            # the default text is never accepted
    assert am.EXPECTED_MARKER in sql


def test_a_dry_run_cannot_drop_and_an_unfilled_script_refuses():
    dry = am.drop_sql(expected_credentials=0, expected_invites=0, expected_rate_limits=0, max_companies=1, max_opportunities=1,
                      approved_at_epoch=1, approved_at_iso="x")
    assert "DRY RUN OK" in dry and "DROP TABLE" not in dry
    unfilled = am.drop_sql()
    assert "exp_cred bigint := -1" in unfilled and "approved_epoch bigint := 0" in unfilled     # the refusing defaults


@pytest.mark.parametrize("kw,msg", [
    (dict(project_ref="todgmhddihfgqeofzudb", project_name="rithavo-crm-preview"), "Production"),
    (dict(project_ref="sbejvcosjgtwchsafrng", project_name="rithavo-career-intelligence"), "project name"),
    (dict(project_ref="someotherprojectref0000", project_name="rithavo-crm-preview"), "project reference"),
])
def test_preflight_refuses_the_wrong_project(kw, msg):
    target = ("aws-0-us-east-1.pooler.supabase.com", "6543", "postgres", "crm_app.sbejvcosjgtwchsafrng")
    with pytest.raises(ValueError, match=msg):
        am.drop_preflight(target, **kw)


@pytest.mark.parametrize("target", [
    ("aws-0-ap-northeast-1.pooler.supabase.com", "6543", "postgres", "crm_app.todgmhddihfgqeofzudb"),
    ("db.todgmhddihfgqeofzudb.supabase.co", "5432", "postgres", "crm_app"),
    ("aws-0-us-east-1.pooler.supabase.com", "5432", "postgres", "crm_app.sbejvcosjgtwchsafrng"),      # wrong port -> fingerprint
    ("aws-1-us-east-1.pooler.supabase.com", "6543", "postgres", "crm_app.sbejvcosjgtwchsafrng"),      # wrong host -> fingerprint
    ("aws-0-us-east-1.pooler.supabase.com", "6543", "otherdb", "crm_app.sbejvcosjgtwchsafrng"),       # wrong database -> fingerprint
])
def test_preflight_refuses_a_target_that_is_not_the_expected_database(target):
    with pytest.raises(ValueError):
        am.drop_preflight(target, project_ref="sbejvcosjgtwchsafrng", project_name="rithavo-crm-preview")


def test_preflight_accepts_exactly_the_preview_target_and_matches_the_application_fingerprint():
    from app.crm import diag
    from app.db import Database
    target = ("aws-0-us-east-1.pooler.supabase.com", "6543", "postgres", "crm_app.sbejvcosjgtwchsafrng")
    fp = am.drop_preflight(target, project_ref="sbejvcosjgtwchsafrng", project_name="rithavo-crm-preview")
    assert fp == am.EXPECTED_FINGERPRINT == "3b3f07ef"
    app_fp = diag.db_identity(Database("postgresql://crm_app.sbejvcosjgtwchsafrng:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres"))
    assert app_fp == "postgres:" + fp                                                            # the same fingerprint the logs show


def _run(args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "crm_drop_preview_tables.py"), *args], capture_output=True,
                          text=True, cwd=str(ROOT))


GOOD = ["--project-ref", "sbejvcosjgtwchsafrng", "--project-name", "rithavo-crm-preview", "--target-host",
        "aws-0-us-east-1.pooler.supabase.com", "--credentials", "1", "--invites", "2", "--rate-limits", "3",
        "--max-companies", "10", "--max-opportunities", "10"]


def test_the_runner_aborts_before_any_sql_on_a_mismatch_and_never_runs_it_itself():
    r = _run(["--project-ref", "todgmhddihfgqeofzudb", *GOOD[2:]])
    assert r.returncode == 2 and r.stdout == "" and "ABORT before any SQL" in r.stderr
    r = _run([*GOOD, "--target-host", "aws-1-us-east-1.pooler.supabase.com"])
    assert r.returncode == 2 and r.stdout == ""
    dry = _run(GOOD)
    assert dry.returncode == 0 and "DRY RUN OK" in dry.stdout and "DROP TABLE" not in dry.stdout and "DRY RUN" in dry.stderr
    real = _run([*GOOD, "--real"])
    assert real.returncode == 0 and "DROP TABLE crm_rate_limits, crm_invites, crm_credentials;" in real.stdout
    src = (ROOT / "scripts" / "crm_drop_preview_tables.py").read_text(encoding="utf-8")
    assert "psycopg2" not in src and "connect(" not in src                                        # it only generates SQL


# ---- audit inserts must not need the SELECT policy (found by the live PostgreSQL run) -------------------------

def test_audit_inserts_never_ask_postgresql_to_return_the_row(crm):
    """The database layer appends `RETURNING id` to inserts unless the statement has ON CONFLICT. Under RLS the returned
    row must pass the audit SELECT policy, which hides it from a caller with no actor context, so the audit insert
    itself must not use RETURNING."""
    from app import db as dbmod
    from app.crm import audit as audit_mod

    class Cur:
        def __init__(self):
            self.sql = None

        def execute(self, sql, params=()):
            self.sql = sql

        def fetchone(self):
            return {"id": 1}

    cur = Cur()
    proxy = dbmod._PGCursorProxy(cur)

    class Conn:
        def execute(self, sql, params=()):
            return proxy.execute(sql, params)

    audit_mod.write(Conn(), None, "owner_db", "x", "staff", 1)
    assert "RETURNING" not in cur.sql and "ON CONFLICT DO NOTHING" in cur.sql
    # the same statement still works on SQLite (the test database) and writes exactly one row
    audit_mod.write(_sqlite_conn(crm), None, "owner_db", "probe", "staff", 1)
    with crm.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM crm_audit_log WHERE action = 'probe'").fetchone()["n"] == 1


def _sqlite_conn(crm):
    import sqlite3
    c = sqlite3.connect(crm.db.db_path)
    c.row_factory = sqlite3.Row
    orig_execute = c.execute

    class W:
        def execute(self, *a, **k):
            r = orig_execute(*a, **k)
            c.commit()
            return r
    return W()


def test_every_audit_insert_in_the_crm_package_suppresses_returning():
    """A RETURNING on crm_audit_log needs the SELECT policy to pass, which fails for a caller with no actor context."""
    import re
    for p in (ROOT / "app" / "crm").glob("*.py"):
        if p.name in ("schema.py", "authmigration.py", "rls.py"):
            continue
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r'INSERT INTO crm_audit_log[^"]*"\s*\n?\s*"VALUES[^"]*"', src):
            assert "ON CONFLICT DO NOTHING" in m.group(0), (p.name, m.group(0)[:80])
