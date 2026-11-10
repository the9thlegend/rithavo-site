"""
Database-enforced isolation (PROPOSAL, demonstrated on isolated local PostgreSQL only; not applied anywhere).

Why this exists: the CRM tables have RLS enabled with no policies, but the application connects as the
role that owns them (on Supabase the `postgres` role also has BYPASSRLS). Such a role is never filtered by
RLS, so today specialist isolation rests on application code alone (app/crm/access.py `Scope`).

Approach
  1. A dedicated login role `crm_app`: not the owner, NOSUPERUSER, NOBYPASSRLS, only the grants it needs.
  2. FORCE ROW LEVEL SECURITY on the business tables and explicit policies keyed on two per-transaction
     settings (`app.user_id`, `app.role`) that the application sets with `set_config(..., true)` right after
     the gate has authenticated the caller. Unset settings match nothing (fail closed).
  3. Three SECURITY DEFINER functions for the only legitimate cross-owner reads (masked duplicate probe,
     next reference number); they reveal ids only for the caller's own rows.

What it does NOT cover: the security tables (crm_staff, crm_mfa, crm_mfa_recovery_codes) stay app-trusted,
and the gate itself (who is a Founder) is still application logic: a compromised application process can
set app.role to founder. The policies defend against missing or wrong filters in queries, SQL injection into
a business-table query, and any non-application path to the data; they do not defend against code that
authenticates the wrong person.
"""

import contextvars
from contextlib import contextmanager

CURRENT = contextvars.ContextVar("crm_db_actor", default=None)
_ACTIVE = {"on": False}

BUSINESS_OWNER_TABLES = ("crm_companies", "crm_contacts", "crm_opportunities", "crm_followups")
CHILD_TABLES = {"crm_activities": "opportunity_id", "crm_founder_reviews": "opportunity_id"}
TRUSTED_TABLES = ("crm_staff", "crm_mfa", "crm_mfa_recovery_codes")
ALL_FORCED = BUSINESS_OWNER_TABLES + tuple(CHILD_TABLES) + ("crm_audit_log",)

POLICIES_SQL = """
-- CRM Lite 002 (PROPOSED, not applied): database-enforced owner isolation. Run as the table owner.
-- Prerequisite (infrastructure, Founder approval): CREATE ROLE crm_app LOGIN NOSUPERUSER NOBYPASSRLS ...;
BEGIN;
CREATE OR REPLACE FUNCTION crm_uid() RETURNS integer LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.user_id', true), '')::integer $$;
CREATE OR REPLACE FUNCTION crm_is_founder() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT coalesce(current_setting('app.role', true), '') = 'founder' $$;
""" + "".join(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;\n" for t in ALL_FORCED) + "".join(
    f"""DROP POLICY IF EXISTS crm_owner_all ON {t};
CREATE POLICY crm_owner_all ON {t} FOR ALL TO crm_app
  USING (crm_is_founder() OR owner_user_id = crm_uid())
  WITH CHECK (crm_is_founder() OR owner_user_id = crm_uid());
""" for t in BUSINESS_OWNER_TABLES) + "".join(
    f"""DROP POLICY IF EXISTS crm_child_all ON {t};
CREATE POLICY crm_child_all ON {t} FOR ALL TO crm_app
  USING (crm_is_founder() OR EXISTS (SELECT 1 FROM crm_opportunities o WHERE o.id = {t}.{col}))
  WITH CHECK (crm_is_founder() OR EXISTS (SELECT 1 FROM crm_opportunities o WHERE o.id = {t}.{col}));
""" for t, col in CHILD_TABLES.items()) + """
-- Security tables stay application-trusted: crm_app gets full access through an explicit policy.
""" + "".join(f"""DROP POLICY IF EXISTS crm_app_trusted ON {t};
CREATE POLICY crm_app_trusted ON {t} FOR ALL TO crm_app USING (true) WITH CHECK (true);
""" for t in TRUSTED_TABLES) + """
DROP POLICY IF EXISTS crm_audit_insert ON crm_audit_log;
DROP POLICY IF EXISTS crm_audit_select ON crm_audit_log;
CREATE POLICY crm_audit_insert ON crm_audit_log FOR INSERT TO crm_app WITH CHECK (true);
CREATE POLICY crm_audit_select ON crm_audit_log FOR SELECT TO crm_app
  USING (crm_is_founder() OR actor_user_id = crm_uid());

CREATE OR REPLACE FUNCTION crm_dup_probe(p_name_key text, p_email_key text)
RETURNS TABLE(kind text, row_id integer, company_id integer, mine boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT 'company'::text, CASE WHEN crm_is_founder() OR c.owner_user_id = crm_uid() THEN c.id END, NULL::integer,
         (c.owner_user_id = crm_uid())
    FROM crm_companies c WHERE p_name_key <> '' AND c.name_key = p_name_key AND c.archived = 0
  UNION ALL
  SELECT 'contact'::text, CASE WHEN crm_is_founder() OR k.owner_user_id = crm_uid() THEN k.id END,
         CASE WHEN crm_is_founder() OR k.owner_user_id = crm_uid() THEN k.company_id END,
         (k.owner_user_id = crm_uid())
    FROM crm_contacts k WHERE p_email_key <> '' AND k.email_key = p_email_key AND k.archived = 0
$$;
CREATE OR REPLACE FUNCTION crm_last_ref(p_prefix text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT max(ref) FROM crm_opportunities WHERE ref LIKE p_prefix || '%'
$$;
REVOKE ALL ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text) TO crm_app;
COMMIT;
"""


# Dedicated Preview database provisioning (owner-run, once). The application role is created WITHOUT a credential
# here; the credential is set out of band so it never appears in the repository.
PREVIEW_PROVISION_SQL = """
-- CRM Lite Preview database: least-privilege application role. Run as the table owner after 000 and 010.
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crm_app') THEN
    CREATE ROLE crm_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 8;
  END IF;
END
$do$;
ALTER ROLE crm_app SET statement_timeout = '15s';
ALTER ROLE crm_app SET lock_timeout = '10s';
ALTER ROLE crm_app SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE crm_app SET search_path = public;

-- Nothing for the Data API roles, now or by default.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated, service_role;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, service_role;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated, service_role;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM anon, authenticated, service_role;

-- crm_app: data access only. Not the owner, so it cannot disable RLS, alter policies or run DDL.
GRANT USAGE ON SCHEMA public TO crm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO crm_app;
REVOKE UPDATE, DELETE ON crm_activities, crm_audit_log FROM crm_app;

-- Defence in depth on the non-CRM application tables: RLS on, with an explicit policy only for crm_app.
DO $do$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename NOT LIKE 'crm\\_%' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS app_trusted ON public.%I', t);
    EXECUTE format('CREATE POLICY app_trusted ON public.%I FOR ALL TO crm_app USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$do$;
"""

POST_POLICY_SQL = """
-- After the policies and functions exist: restrict the SECURITY DEFINER functions to crm_app only.
REVOKE ALL ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text), crm_uid(), crm_is_founder(), crm_append_only()
  FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text), crm_uid(), crm_is_founder() TO crm_app;
ALTER FUNCTION crm_uid() SET search_path = public, pg_temp;
ALTER FUNCTION crm_is_founder() SET search_path = public, pg_temp;
ALTER FUNCTION crm_append_only() SET search_path = public, pg_temp;
"""


class LeastPrivilegeError(RuntimeError):
    """The connected database role is not the verified least-privilege application role. Category-only message."""


_LEAST_PRIVILEGE_SQL = (
    "SELECT r.rolname AS name, r.rolsuper AS su, r.rolbypassrls AS bypass, r.rolcreaterole AS createrole, "
    "r.rolcreatedb AS createdb, r.rolcanlogin AS canlogin, r.rolreplication AS replication, "
    "(SELECT pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.relname = 'crm_opportunities' "
    " AND c.relnamespace = 'public'::regnamespace) AS owner, "
    "(SELECT c.relforcerowsecurity FROM pg_class c WHERE c.relname = 'crm_opportunities' "
    " AND c.relnamespace = 'public'::regnamespace) AS forced, "
    "(SELECT count(*) FROM pg_policies WHERE left(tablename, 4) = 'crm_') AS policies, "
    "has_table_privilege(current_user, 'public.crm_opportunities', 'SELECT') AS can_select, "
    "has_table_privilege(current_user, 'public.crm_opportunities', 'INSERT') AS can_insert, "
    "has_table_privilege(current_user, 'public.crm_opportunities', 'UPDATE') AS can_update, "
    "has_table_privilege(current_user, 'public.crm_opportunities', 'TRUNCATE') AS can_truncate, "
    "has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_in_schema, "
    "(SELECT pg_has_role(current_user, pg_get_userbyid(c.relowner), 'USAGE') FROM pg_class c "
    " WHERE c.relname = 'crm_opportunities' AND c.relnamespace = 'public'::regnamespace) AS inherits_owner, "
    "has_function_privilege(current_user, 'public.crm_last_ref(text)', 'EXECUTE') AS can_run_ref_fn, "
    "has_function_privilege(current_user, 'public.crm_dup_probe(text, text)', 'EXECUTE') AS can_run_probe_fn "
    "FROM pg_roles r WHERE r.rolname = current_user"
)


def assert_least_privilege(db) -> dict:
    """Fail closed. Ask PostgreSQL itself, over the live connection, to confirm every attribute and privilege the
    application role must (and must not) have. Returns the confirmed facts, which are the only basis on which start-up
    may report `least_privilege=verified`. Raises LeastPrivilegeError on anything else, including a non-PostgreSQL
    database (SQLite can never verify this) and missing tables or policies."""
    if not getattr(db, "_is_postgres", False):
        raise LeastPrivilegeError("least privilege cannot be verified: the database is not PostgreSQL")
    with db.connect() as conn:
        me = conn.execute(_LEAST_PRIVILEGE_SQL).fetchone()
    if me is None:
        raise LeastPrivilegeError("least privilege cannot be verified: the connected role was not found")
    if me["owner"] is None:
        raise LeastPrivilegeError("CRM tables are not provisioned on this database")
    checks = {
        "role_is_crm_app": me["name"] == "crm_app",
        "not_superuser": not me["su"],
        "not_bypassrls": not me["bypass"],
        "no_createrole_createdb_replication": not (me["createrole"] or me["createdb"] or me["replication"]),
        "can_login": bool(me["canlogin"]),
        "not_table_owner": me["owner"] != me["name"] and not me["inherits_owner"],
        "rls_forced_on_crm_tables": bool(me["forced"]),
        "ownership_policies_present": (me["policies"] or 0) >= 9,
        "has_required_dml": bool(me["can_select"] and me["can_insert"] and me["can_update"]),
        "no_truncate": not me["can_truncate"],
        "no_create_in_schema": not me["can_create_in_schema"],
        "can_run_definer_functions": bool(me["can_run_ref_fn"] and me["can_run_probe_fn"]),
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        raise LeastPrivilegeError("least privilege NOT verified: " + ", ".join(failed))
    return {"role": me["name"], "checks": sorted(checks), "policies": me["policies"]}


def assert_auth_objects(db, extra_required=()):
    """Fail closed (CP-M3): the CRM authentication tables must exist with RLS enabled, a `crm_app` policy, exactly the
    planned privileges for the connected role (no DELETE on credentials or invites, no TRUNCATE, no REFERENCES or
    TRIGGER), and the database identity marker must be present. `extra_required` lets a read-only probe name a
    deliberately non-existent object to prove detection without any DDL. Returns the confirmed facts; raises
    LeastPrivilegeError (category-only text) otherwise. SQLite can never satisfy this."""
    from . import authmigration as am
    if not getattr(db, "_is_postgres", False):
        raise LeastPrivilegeError("auth objects cannot be verified: the database is not PostgreSQL")
    problems = []
    with db.connect() as conn:
        for t in tuple(am.AUTH_TABLES) + tuple(extra_required):
            r = conn.execute("SELECT to_regclass('public.' || ?) IS NOT NULL AS present", (t,)).fetchone()
            if not r["present"]:
                problems.append(f"missing_table:{t}")
                continue
            info = conn.execute(
                "SELECT c.relrowsecurity AS rls, "
                "EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = ? "
                "        AND 'crm_app' = ANY (p.roles)) AS policy FROM pg_class c WHERE c.oid = to_regclass('public.' || ?)",
                (t, t)).fetchone()
            if not info["rls"]:
                problems.append(f"rls_disabled:{t}")
            if not info["policy"]:
                problems.append(f"missing_policy:{t}")
            for priv in am.REQUIRED_PRIVILEGES.get(t, ()):
                ok = conn.execute("SELECT has_table_privilege(current_user, 'public.' || ?, ?) AS ok", (t, priv)).fetchone()["ok"]
                if not ok:
                    problems.append(f"missing_privilege:{t}:{priv}")
            for priv in am.FORBIDDEN_PRIVILEGES.get(t, ()):
                bad = conn.execute("SELECT has_table_privilege(current_user, 'public.' || ?, ?) AS ok", (t, priv)).fetchone()["ok"]
                if bad:
                    problems.append(f"excess_privilege:{t}:{priv}")
        seq = conn.execute("SELECT has_sequence_privilege(current_user, 'public.crm_invites_id_seq', 'USAGE') AS ok").fetchone()
        if not seq["ok"]:
            problems.append("missing_privilege:crm_invites_id_seq:USAGE")
        mk = conn.execute(
            "SELECT shobj_description((SELECT oid FROM pg_database WHERE datname = current_database()), 'pg_database') AS db, "
            "shobj_description((SELECT oid FROM pg_roles WHERE rolname = 'crm_app'), 'pg_authid') AS role").fetchone()
    marker = next((m for m in (mk["db"], mk["role"]) if m and m.startswith(am.MARKER_PREFIX)), None)
    if marker is None:
        problems.append("identity_marker_missing")
    if problems:
        raise LeastPrivilegeError("auth objects NOT verified: " + ", ".join(problems))
    return {"tables": list(am.AUTH_TABLES), "marker": marker}


def is_active() -> bool:
    return _ACTIVE["on"]


def set_actor(actor) -> None:
    CURRENT.set((actor.user_id, actor.role))


class BoundedValidatingPool:
    """Wraps the psycopg2 pool of a serverless instance for the pre-provisioned Preview database.

    * Bounded: callers wait (up to `wait` seconds) for a free connection instead of failing at once with
      PoolError("connection pool exhausted") when more requests than pool slots are in flight.
    * Validating: a pooled connection that the pooler or network closed while idle is discarded and replaced
      instead of surfacing as a 500 ("SSL connection has been closed unexpectedly").
    Only applied by `install`; the shared Database class is unchanged, so no other deployment is affected."""

    IDLE_VALIDATE_AFTER = 10.0  # seconds a connection may sit idle before it is checked on the next checkout

    def __init__(self, inner, size, wait=20.0):
        import threading
        self._inner, self._sem, self._wait = inner, threading.BoundedSemaphore(size), wait
        self._returned = {}  # id(conn) -> monotonic time it was last returned

    def getconn(self, *a, **k):
        if not self._sem.acquire(timeout=self._wait):
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="The database is busy. Try again.")
        try:
            for _ in range(4):
                conn = self._inner.getconn(*a, **k)
                try:
                    if conn.closed:
                        raise RuntimeError("closed")
                    import time
                    idle = time.monotonic() - self._returned.get(id(conn), 0.0)
                    if idle > self.IDLE_VALIDATE_AFTER:   # recently used connections are known good; skip the round trip
                        cur = conn.cursor()
                        cur.execute("SELECT 1")
                        cur.close()
                        conn.rollback()
                    return conn
                except Exception:
                    try:
                        self._inner.putconn(conn, close=True)
                    except Exception:
                        pass
            raise RuntimeError("could not obtain a healthy database connection")
        except BaseException:
            self._sem.release()
            raise

    def putconn(self, conn, *a, **k):
        import time
        self._returned[id(conn)] = time.monotonic()
        try:
            self._inner.putconn(conn, *a, **k)
        finally:
            self._sem.release()

    def closeall(self):
        return self._inner.closeall()


def install(db) -> None:
    """Make every transaction on this Database carry the authenticated caller into PostgreSQL."""
    if getattr(db, "_is_postgres", False) and not isinstance(db._pg_pool, BoundedValidatingPool):
        import config
        db._pg_pool = BoundedValidatingPool(db._get_pg_pool(), getattr(config, "DATABASE_POOL_MAX", 5))
    orig = db.connect

    @contextmanager
    def connect():
        with orig() as conn:
            who = CURRENT.get()
            if who is not None:
                conn.execute("SELECT set_config('app.user_id', ?, true), set_config('app.role', ?, true)",
                             (str(who[0]), who[1]))
            yield conn

    db.connect = connect
    _ACTIVE["on"] = True


def uninstall() -> None:
    _ACTIVE["on"] = False
