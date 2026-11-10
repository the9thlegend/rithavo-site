-- CRM 030: independent authentication tables (Preview project rithavo-crm-preview ONLY).
-- Additive. No existing table is altered. The identity marker is checked first and never overwrites an
-- unexpected non-empty comment. Marker target: role comment. Never run against Production.
DO $marker$
DECLARE expected text; cur text; rcur text; role_oid oid;
BEGIN
  expected := 'rithavo-crm-preview:sbejvcosjgtwchsafrng';
    role_oid := (SELECT oid FROM pg_roles WHERE rolname = 'crm_app');
    IF role_oid IS NULL THEN RAISE EXCEPTION 'identity marker: role crm_app does not exist'; END IF;
    rcur := shobj_description(role_oid, 'pg_authid');
    IF rcur IS NOT NULL AND rcur <> '' AND rcur <> expected THEN
      RAISE EXCEPTION 'identity marker conflict: unexpected role comment: %', rcur;
    END IF;
    EXECUTE format('COMMENT ON ROLE crm_app IS %L', expected);
END
$marker$;

CREATE TABLE IF NOT EXISTS crm_credentials (
    user_id INTEGER PRIMARY KEY,
    password_hash TEXT,
    credential_epoch INTEGER NOT NULL DEFAULT 1,
    set_at TEXT,
    set_by INTEGER
);
CREATE TABLE IF NOT EXISTS crm_invites (
    id SERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    purpose TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    expires_at BIGINT NOT NULL,
    used_at BIGINT,
    revoked_at BIGINT
);
CREATE TABLE IF NOT EXISTS crm_rate_limits (
    key_hash TEXT NOT NULL,
    window_start BIGINT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_hash, window_start)
);
CREATE INDEX IF NOT EXISTS idx_crm_invites_user ON crm_invites(user_id);
CREATE INDEX IF NOT EXISTS idx_crm_rl_window ON crm_rate_limits(window_start);
ALTER TABLE crm_credentials ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_app_trusted ON crm_credentials;
CREATE POLICY crm_app_trusted ON crm_credentials FOR ALL TO crm_app USING (true) WITH CHECK (true);
ALTER TABLE crm_invites ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_app_trusted ON crm_invites;
CREATE POLICY crm_app_trusted ON crm_invites FOR ALL TO crm_app USING (true) WITH CHECK (true);
ALTER TABLE crm_rate_limits ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_app_trusted ON crm_rate_limits;
CREATE POLICY crm_app_trusted ON crm_rate_limits FOR ALL TO crm_app USING (true) WITH CHECK (true);
REVOKE ALL ON crm_credentials, crm_invites, crm_rate_limits FROM anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE ON crm_credentials, crm_invites TO crm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON crm_rate_limits TO crm_app;
GRANT USAGE, SELECT ON SEQUENCE crm_invites_id_seq TO crm_app;
