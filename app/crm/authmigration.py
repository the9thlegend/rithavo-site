"""
Preview migration 030 (CRM independent authentication): generated text, the database identity marker, and the
start-up object checker's expectations. Nothing here runs by itself; SQL is applied only by the owner with the
migration tool, and only to the Preview project.

Identity marker (scope 4, mandatory requirement 2): the marker is `COMMENT ON DATABASE` (fallback `COMMENT ON ROLE
crm_app` if the platform refuses a database comment). It is NEVER written over an unexpected non-empty comment: the
migration aborts with the exact conflict, before anything else in the transaction. The original comments are
recorded before applying, and the restore statements below put back exactly what was there.
"""

from . import schema

EXPECTED_MARKER = "rithavo-crm-preview:sbejvcosjgtwchsafrng"
MARKER_PREFIX = "rithavo-crm-"
AUTH_TABLES = schema.AUTH_TABLE_NAMES
ROLE = "crm_app"


def lit(value):
    """SQL string literal (standard_conforming_strings on) or NULL."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


# ---- decision model (mirrors the SQL; used by the pre-flight and by tests) -------------------------------------

def plan_marker(db_comment, role_comment, expected=EXPECTED_MARKER, *, db_comment_allowed=True):
    """Decide what the SQL will do. Returns {"action": "set"|"noop"|"abort", "target": "database"|"role"|None,
    "conflict": str|None}. A non-empty comment that is not exactly `expected` always aborts, on either target."""
    def empty(v):
        return v is None or v == ""
    if db_comment_allowed:
        if not empty(db_comment) and db_comment != expected:
            return {"action": "abort", "target": "database", "conflict": f"database comment is {db_comment!r}"}
        return {"action": "noop" if db_comment == expected else "set", "target": "database", "conflict": None}
    if not empty(role_comment) and role_comment != expected:
        return {"action": "abort", "target": "role", "conflict": f"role comment is {role_comment!r}"}
    return {"action": "noop" if role_comment == expected else "set", "target": "role", "conflict": None}


def restore_sql(before, applied):
    """Statements that put back exactly the recorded original metadata. `before` = {"database": str|None,
    "role": str|None}; `applied` = {"action": ..., "target": ...} as returned by plan_marker. A noop changed nothing,
    so nothing is restored."""
    if applied.get("action") != "set":
        return "-- marker was already in place: nothing to restore\n"
    if applied["target"] == "database":
        return f"COMMENT ON DATABASE postgres IS {lit(before.get('database'))};\n"
    return f"COMMENT ON ROLE {ROLE} IS {lit(before.get('role'))};\n"


# ---- SQL ------------------------------------------------------------------------------------------------------

def marker_body(expected=EXPECTED_MARKER, *, force_role=False):
    """PL/pgSQL statements (no DECLARE/BEGIN wrapper needed beyond the variables listed in marker_declare)."""
    e = lit(expected)
    role_part = f"""
    role_oid := (SELECT oid FROM pg_roles WHERE rolname = '{ROLE}');
    IF role_oid IS NULL THEN RAISE EXCEPTION 'identity marker: role {ROLE} does not exist'; END IF;
    rcur := shobj_description(role_oid, 'pg_authid');
    IF rcur IS NOT NULL AND rcur <> '' AND rcur <> expected THEN
      RAISE EXCEPTION 'identity marker conflict: unexpected role comment: %', rcur;
    END IF;
    EXECUTE format('COMMENT ON ROLE {ROLE} IS %L', expected);"""
    db_oid_expr = "(SELECT oid FROM pg_database WHERE datname = current_database())"
    if force_role:
        return f"expected := {e};{role_part}\n"
    return f"""expected := {e};
  cur := shobj_description({db_oid_expr}, 'pg_database');
  IF cur IS NOT NULL AND cur <> '' AND cur <> expected THEN
    RAISE EXCEPTION 'identity marker conflict: unexpected database comment: %', cur;
  END IF;
  BEGIN
    EXECUTE format('COMMENT ON DATABASE %I IS %L', current_database(), expected);
  EXCEPTION WHEN insufficient_privilege THEN{role_part.replace(chr(10) + '    ', chr(10) + '      ')}
  END;
"""


MARKER_DECLARE = "expected text; cur text; rcur text; role_oid oid;"


def marker_block(expected=EXPECTED_MARKER):
    return f"DO $marker$\nDECLARE {MARKER_DECLARE}\nBEGIN\n  {marker_body(expected)}END\n$marker$;\n"


def tables_and_grants_sql():
    pg = schema._pg(schema.AUTH_TABLES)
    t = ", ".join(AUTH_TABLES)
    out = [pg]
    for name in AUTH_TABLES:
        out.append(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;\n")
        out.append(f"DROP POLICY IF EXISTS crm_app_trusted ON {name};\n")
        out.append(f"CREATE POLICY crm_app_trusted ON {name} FOR ALL TO {ROLE} USING (true) WITH CHECK (true);\n")
    out.append(f"REVOKE ALL ON {t} FROM anon, authenticated, service_role;\n")
    out.append(f"GRANT SELECT, INSERT, UPDATE ON crm_credentials, crm_invites TO {ROLE};\n")
    out.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON crm_rate_limits TO {ROLE};\n")
    out.append(f"GRANT USAGE, SELECT ON SEQUENCE crm_invites_id_seq TO {ROLE};\n")
    return "".join(out)


def migration_sql():
    """030: the marker check FIRST (a conflict aborts before any change), then the three tables, RLS, policies, grants.
    No BEGIN/COMMIT: the migration tool wraps the text in one transaction. Additive only."""
    return ("-- CRM 030: independent authentication tables (Preview project rithavo-crm-preview ONLY).\n"
            "-- Additive. No existing table is altered. The identity marker is checked first and never overwrites an\n"
            "-- unexpected non-empty comment. Never run against Production.\n"
            + marker_block() + tables_and_grants_sql())


# ---- what the start-up checker requires (see rls.assert_auth_objects) -----------------------------------------

REQUIRED_PRIVILEGES = {
    "crm_credentials": {"SELECT", "INSERT", "UPDATE"},
    "crm_invites": {"SELECT", "INSERT", "UPDATE"},
    "crm_rate_limits": {"SELECT", "INSERT", "UPDATE", "DELETE"},
}
FORBIDDEN_PRIVILEGES = {
    "crm_credentials": {"DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"},
    "crm_invites": {"DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"},
    "crm_rate_limits": {"TRUNCATE", "REFERENCES", "TRIGGER"},
}


# ---- one-time Founder bootstrap (owner-run) ----------------------------------------------------------------------

def bootstrap_sql(email, token_hash, now_epoch, now_iso, ttl):
    """Owner-run issuance of a single-use Founder bootstrap token. Only the HASH is in this text. Returns
    {"guard": PostgreSQL-only DO block, "statements": portable statements, "sql": guard + statements}.
    The guard aborts unless the email is a Super Admin (the application re-checks the allowlist at acceptance)."""
    e = lit(email.strip().lower())
    guard = (f"DO $guard$ BEGIN IF NOT EXISTS (SELECT 1 FROM users u JOIN super_admins sa ON sa.user_id = u.id "
             f"WHERE u.email = {e}) THEN RAISE EXCEPTION 'bootstrap refused: that email is not a Super Admin'; END IF; "
             f"END $guard$;\n")
    statements = (
        f"UPDATE crm_invites SET revoked_at = {int(now_epoch)} WHERE purpose = 'founder_bootstrap' "
        f"AND used_at IS NULL AND revoked_at IS NULL;\n"
        f"INSERT INTO crm_invites (token_hash, user_id, email, purpose, created_by, created_at, expires_at) "
        f"SELECT {lit(token_hash)}, u.id, u.email, 'founder_bootstrap', 'owner_db', {lit(now_iso)}, {int(now_epoch) + int(ttl)} "
        f"FROM users u JOIN super_admins sa ON sa.user_id = u.id WHERE u.email = {e};\n"
        f"INSERT INTO crm_audit_log (at, actor_user_id, actor_role, action, entity, entity_id, outcome, detail) "
        f"SELECT {lit(now_iso)}, NULL, 'owner_db', 'founder_bootstrap_issued', 'staff', u.id, 'ok', NULL "
        f"FROM users u WHERE u.email = {e};\n")
    return {"guard": guard, "statements": statements, "sql": guard + statements}


# ---- identity-marker self-test (live, transaction rolled back) ---------------------------------------------------

def selftest_sql(case, expected=EXPECTED_MARKER):
    """A DO block that exercises one marker path inside a transaction and ends with RAISE EXCEPTION, so NOTHING is
    kept. The outcome is read from the exception text: 'SELFTEST <case> PASS ...' or 'SELFTEST <case> FAIL ...'.
    Cases: db_empty_set, db_already_set, db_conflict, role_empty_set, role_conflict, rollback_database,
    rollback_role. Conflict cases temporarily put an unexpected value in place (also rolled back)."""
    dbc = "shobj_description((SELECT oid FROM pg_database WHERE datname = current_database()), 'pg_database')"
    rc = f"shobj_description((SELECT oid FROM pg_roles WHERE rolname = '{ROLE}'), 'pg_authid')"

    def setdb(v):
        return f"EXECUTE format('COMMENT ON DATABASE %I IS %L', current_database(), {v});"

    def setrole(v):
        return f"EXECUTE format('COMMENT ON ROLE {ROLE} IS %L', {v});"

    head = f"DO $t$\nDECLARE {MARKER_DECLARE} msg text; r1 text; r2 text;\nBEGIN\n"
    tail = "END\n$t$;\n"

    def run(force_role):
        body = marker_body(expected, force_role=force_role)
        return f"  BEGIN\n    {body}\n  EXCEPTION WHEN raise_exception THEN msg := SQLERRM;\n  END;\n"

    ok = lambda extra: f"IF msg IS NULL AND {extra} THEN msg := 'PASS'; ELSE msg := 'FAIL ' || coalesce(msg, 'no error'); END IF;\n"
    if case == "db_empty_set":
        s = f"  {setdb('NULL::text')}\n" + run(False) + f"  r1 := {dbc};\n  " + ok(f"r1 = {lit(expected)}")
    elif case == "db_already_set":
        s = f"  {setdb(lit(expected))}\n" + run(False) + f"  r1 := {dbc};\n  " + ok(f"r1 = {lit(expected)}")
    elif case == "db_conflict":
        s = (f"  {setdb(lit('someone-elses-comment'))}\n" + run(False) + f"  r1 := {dbc};\n"
             "  IF msg LIKE '%identity marker conflict: unexpected database comment: someone-elses-comment%' "
             "AND r1 = 'someone-elses-comment' THEN msg := 'PASS conflict reported exactly, comment untouched'; "
             "ELSE msg := 'FAIL ' || coalesce(msg, 'no error'); END IF;\n")
    elif case == "role_empty_set":
        s = (f"  {setrole('NULL::text')}\n  r2 := {dbc};\n" + run(True) + f"  r1 := {rc};\n  "
             + ok(f"r1 = {lit(expected)} AND {dbc} IS NOT DISTINCT FROM r2"))
    elif case == "role_conflict":
        s = (f"  {setrole(lit('another-role-comment'))}\n" + run(True) + f"  r1 := {rc};\n"
             "  IF msg LIKE '%identity marker conflict: unexpected role comment: another-role-comment%' "
             "AND r1 = 'another-role-comment' THEN msg := 'PASS role conflict reported exactly, comment untouched'; "
             "ELSE msg := 'FAIL ' || coalesce(msg, 'no error'); END IF;\n")
    elif case == "rollback_database":
        s = (f"  {setdb('NULL::text')}\n" + run(False) + f"  {setdb('NULL::text')}\n  r1 := {dbc};\n"
             "  IF r1 IS NULL THEN msg := 'PASS restore returned the database comment to NULL exactly'; "
             "ELSE msg := 'FAIL ' || r1; END IF;\n")
    elif case == "rollback_role":
        s = (f"  {setrole('NULL::text')}\n" + run(True) + f"  {setrole('NULL::text')}\n  r1 := {rc};\n"
             "  IF r1 IS NULL THEN msg := 'PASS restore returned the role comment to NULL exactly'; "
             "ELSE msg := 'FAIL ' || r1; END IF;\n")
    else:
        raise ValueError(case)
    return head + s + f"  RAISE EXCEPTION 'SELFTEST {case} %', msg;\n" + tail


# ---- guarded drop of the three tables (separate, deliberate, Preview only) --------------------------------------

PREVIEW_PROJECT_REF = "sbejvcosjgtwchsafrng"
PRODUCTION_PROJECT_REF = "todgmhddihfgqeofzudb"
EXPECTED_FINGERPRINT = "3b3f07ef"
_T3 = "'crm_credentials'::regclass, 'crm_invites'::regclass, 'crm_rate_limits'::regclass"


def drop_sql(*, expected_credentials=-1, expected_invites=-1, expected_rate_limits=-1, max_companies=-1,
             max_opportunities=-1, approved_at_epoch=0, approved_at_iso="", dry_run=True):
    """The guarded drop. Every default is the REFUSE value: an unfilled script aborts. Every guard is a RAISE
    EXCEPTION inside one DO block (one transaction), so any mismatch leaves everything in place. A dry run passes
    every guard and then raises 'DRY RUN OK ...', which rolls back; only a real run reaches the DROP."""
    final = ("RAISE EXCEPTION 'DRY RUN OK: every guard passed; credentials=%, invites=%, rate_limits=%', c1, c2, c3;"
             if dry_run else "DROP TABLE crm_rate_limits, crm_invites, crm_credentials;")
    return f"""DO $drop$
DECLARE
  exp_cred bigint := {int(expected_credentials)}; exp_inv bigint := {int(expected_invites)};
  exp_rl bigint := {int(expected_rate_limits)}; max_co bigint := {int(max_companies)}; max_op bigint := {int(max_opportunities)};
  approved_epoch bigint := {int(approved_at_epoch)}; approved_iso text := {lit(approved_at_iso)};
  expected text := {lit(EXPECTED_MARKER)}; dbc text; rcur text; n bigint; c1 bigint; c2 bigint; c3 bigint;
BEGIN
  -- 0. approval parameters must be filled (the defaults refuse)
  IF exp_cred < 0 OR exp_inv < 0 OR exp_rl < 0 OR max_co < 0 OR max_op < 0 OR approved_epoch <= 0 OR approved_iso = '' THEN
    RAISE EXCEPTION 'drop refused: approval parameters are not filled';
  END IF;
  -- 2. database identity: owner role, crm_app present, exact identity marker (database or role comment)
  IF current_user <> 'postgres' THEN RAISE EXCEPTION 'drop refused: not connected as the owner'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN RAISE EXCEPTION 'drop refused: role {ROLE} not present'; END IF;
  dbc := shobj_description((SELECT oid FROM pg_database WHERE datname = current_database()), 'pg_database');
  rcur := shobj_description((SELECT oid FROM pg_roles WHERE rolname = '{ROLE}'), 'pg_authid');
  IF dbc IS DISTINCT FROM expected AND rcur IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'drop refused: identity marker is not %', expected;
  END IF;
  -- 4. synthetic-only data
  IF EXISTS (SELECT 1 FROM users WHERE email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: a non-synthetic user exists'; END IF;
  IF EXISTS (SELECT 1 FROM crm_contacts WHERE email IS NOT NULL AND email NOT LIKE '%.example.test') THEN RAISE EXCEPTION 'drop refused: a non-synthetic contact email exists'; END IF;
  IF EXISTS (SELECT 1 FROM crm_staff s JOIN users u ON u.id = s.user_id WHERE u.email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: staff row for a non-synthetic user'; END IF;
  IF EXISTS (SELECT 1 FROM crm_credentials c JOIN users u ON u.id = c.user_id WHERE u.email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: credential for a non-synthetic user'; END IF;
  IF EXISTS (SELECT 1 FROM crm_invites WHERE email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: invite for a non-synthetic email'; END IF;
  -- 5. unexpected CRM data: exact approved counts, maxima, nothing newer than the approval
  SELECT count(*) INTO c1 FROM crm_credentials; SELECT count(*) INTO c2 FROM crm_invites; SELECT count(*) INTO c3 FROM crm_rate_limits;
  IF c1 <> exp_cred OR c2 <> exp_inv OR c3 <> exp_rl THEN
    RAISE EXCEPTION 'drop refused: row counts differ from the approval (credentials %, invites %, rate_limits %)', c1, c2, c3;
  END IF;
  SELECT count(*) INTO n FROM crm_companies; IF n > max_co THEN RAISE EXCEPTION 'drop refused: more companies than approved (%)', n; END IF;
  SELECT count(*) INTO n FROM crm_opportunities; IF n > max_op THEN RAISE EXCEPTION 'drop refused: more opportunities than approved (%)', n; END IF;
  IF EXISTS (SELECT 1 FROM crm_invites WHERE created_at > approved_iso) OR EXISTS (SELECT 1 FROM crm_credentials WHERE set_at > approved_iso)
     OR EXISTS (SELECT 1 FROM crm_rate_limits WHERE window_start > approved_epoch) THEN
    RAISE EXCEPTION 'drop refused: rows newer than the approval exist';
  END IF;
  -- 6. no dependents, no active crm_app work
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE contype = 'f' AND confrelid IN ({_T3}))
     OR EXISTS (SELECT 1 FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid WHERE d.refobjid IN ({_T3})) THEN
    RAISE EXCEPTION 'drop refused: something depends on these tables';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename = '{ROLE}' AND state <> 'idle' AND pid <> pg_backend_pid()) THEN
    RAISE EXCEPTION 'drop refused: crm_app has an active transaction';
  END IF;
  -- 7. all of the above and the drop are one transaction
  {final}
END
$drop$;
"""


def drop_preflight(connection_target, *, project_ref, project_name):
    """Runner-side checks done BEFORE any SQL is sent. `connection_target` = (host, port, dbname, user). Returns the
    fingerprint; raises ValueError (category text) on any mismatch."""
    import hashlib
    host, port, dbname, user = connection_target
    for part in (project_ref, user, host):
        if PRODUCTION_PROJECT_REF in (part or ""):
            raise ValueError("refused: the Production project")
    if project_ref != PREVIEW_PROJECT_REF:
        raise ValueError("refused: project reference is not the Preview project")
    if project_name != "rithavo-crm-preview":
        raise ValueError("refused: project name is not rithavo-crm-preview")
    fp = hashlib.sha256(f"{host}|{port}|{dbname}".encode()).hexdigest()[:8]
    if fp != EXPECTED_FINGERPRINT:
        raise ValueError("refused: database fingerprint does not match")
    return fp
