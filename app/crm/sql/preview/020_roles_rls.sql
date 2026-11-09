
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
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename NOT LIKE 'crm\_%' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS app_trusted ON public.%I', t);
    EXECUTE format('CREATE POLICY app_trusted ON public.%I FOR ALL TO crm_app USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$do$;

-- CRM Lite 002 (PROPOSED, not applied): database-enforced owner isolation. Run as the table owner.
-- Prerequisite (infrastructure, Founder approval): CREATE ROLE crm_app LOGIN NOSUPERUSER NOBYPASSRLS ...;
CREATE OR REPLACE FUNCTION crm_uid() RETURNS integer LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.user_id', true), '')::integer $$;
CREATE OR REPLACE FUNCTION crm_is_founder() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT coalesce(current_setting('app.role', true), '') = 'founder' $$;
ALTER TABLE crm_companies FORCE ROW LEVEL SECURITY;
ALTER TABLE crm_contacts FORCE ROW LEVEL SECURITY;
ALTER TABLE crm_opportunities FORCE ROW LEVEL SECURITY;
ALTER TABLE crm_followups FORCE ROW LEVEL SECURITY;
ALTER TABLE crm_activities FORCE ROW LEVEL SECURITY;
ALTER TABLE crm_founder_reviews FORCE ROW LEVEL SECURITY;
ALTER TABLE crm_audit_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_owner_all ON crm_companies;
CREATE POLICY crm_owner_all ON crm_companies FOR ALL TO crm_app
  USING (crm_is_founder() OR owner_user_id = crm_uid())
  WITH CHECK (crm_is_founder() OR owner_user_id = crm_uid());
DROP POLICY IF EXISTS crm_owner_all ON crm_contacts;
CREATE POLICY crm_owner_all ON crm_contacts FOR ALL TO crm_app
  USING (crm_is_founder() OR owner_user_id = crm_uid())
  WITH CHECK (crm_is_founder() OR owner_user_id = crm_uid());
DROP POLICY IF EXISTS crm_owner_all ON crm_opportunities;
CREATE POLICY crm_owner_all ON crm_opportunities FOR ALL TO crm_app
  USING (crm_is_founder() OR owner_user_id = crm_uid())
  WITH CHECK (crm_is_founder() OR owner_user_id = crm_uid());
DROP POLICY IF EXISTS crm_owner_all ON crm_followups;
CREATE POLICY crm_owner_all ON crm_followups FOR ALL TO crm_app
  USING (crm_is_founder() OR owner_user_id = crm_uid())
  WITH CHECK (crm_is_founder() OR owner_user_id = crm_uid());
DROP POLICY IF EXISTS crm_child_all ON crm_activities;
CREATE POLICY crm_child_all ON crm_activities FOR ALL TO crm_app
  USING (crm_is_founder() OR EXISTS (SELECT 1 FROM crm_opportunities o WHERE o.id = crm_activities.opportunity_id))
  WITH CHECK (crm_is_founder() OR EXISTS (SELECT 1 FROM crm_opportunities o WHERE o.id = crm_activities.opportunity_id));
DROP POLICY IF EXISTS crm_child_all ON crm_founder_reviews;
CREATE POLICY crm_child_all ON crm_founder_reviews FOR ALL TO crm_app
  USING (crm_is_founder() OR EXISTS (SELECT 1 FROM crm_opportunities o WHERE o.id = crm_founder_reviews.opportunity_id))
  WITH CHECK (crm_is_founder() OR EXISTS (SELECT 1 FROM crm_opportunities o WHERE o.id = crm_founder_reviews.opportunity_id));

-- Security tables stay application-trusted: crm_app gets full access through an explicit policy.
DROP POLICY IF EXISTS crm_app_trusted ON crm_staff;
CREATE POLICY crm_app_trusted ON crm_staff FOR ALL TO crm_app USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS crm_app_trusted ON crm_mfa;
CREATE POLICY crm_app_trusted ON crm_mfa FOR ALL TO crm_app USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS crm_app_trusted ON crm_mfa_recovery_codes;
CREATE POLICY crm_app_trusted ON crm_mfa_recovery_codes FOR ALL TO crm_app USING (true) WITH CHECK (true);

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

-- After the policies and functions exist: restrict the SECURITY DEFINER functions to crm_app only.
REVOKE ALL ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text), crm_uid(), crm_is_founder(), crm_append_only()
  FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION crm_dup_probe(text, text), crm_last_ref(text), crm_uid(), crm_is_founder() TO crm_app;
