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
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
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
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT max(ref) FROM crm_opportunities WHERE ref LIKE p_prefix || '%'
$$;
REVOKE ALL ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text) TO crm_app;
COMMIT;
"""


def is_active() -> bool:
    return _ACTIVE["on"]


def set_actor(actor) -> None:
    CURRENT.set((actor.user_id, actor.role))


def install(db) -> None:
    """Make every transaction on this Database carry the authenticated caller into PostgreSQL."""
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
