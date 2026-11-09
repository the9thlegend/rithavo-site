-- CRM Lite 001: ten crm_ tables, append-only triggers, RLS on with no policies.
-- Rollback: DROP TABLE the ten crm_ tables and function crm_append_only().
-- NOT APPLIED to Production. Requires Founder approval and a same-day restore-tested backup (B-01, B-09).

CREATE TABLE IF NOT EXISTS crm_staff (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    activated_by INTEGER,
    activated_at TEXT,
    deactivated_by INTEGER,
    deactivated_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_mfa (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    secret_enc TEXT,
    pending_secret_enc TEXT,
    enrolled_at TEXT,
    last_used_step INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    locked_until BIGINT NOT NULL DEFAULT 0,
    hard_locked INTEGER NOT NULL DEFAULT 0,
    session_epoch INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_mfa_recovery_codes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_companies (
    id SERIAL PRIMARY KEY,
    owner_user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    website TEXT,
    industry TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    last_meaningful_contact_at TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_contacts (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES crm_companies(id),
    owner_user_id INTEGER NOT NULL,
    full_name TEXT NOT NULL,
    role_title TEXT,
    email TEXT,
    email_key TEXT,
    phone TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    last_meaningful_contact_at TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_opportunities (
    id SERIAL PRIMARY KEY,
    ref TEXT NOT NULL UNIQUE,
    company_id INTEGER NOT NULL REFERENCES crm_companies(id),
    contact_id INTEGER REFERENCES crm_contacts(id),
    owner_user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    service_pillar TEXT NOT NULL,
    source TEXT NOT NULL,
    stage TEXT NOT NULL,
    stage_changed_at TEXT NOT NULL,
    stage_before_hold TEXT,
    requirement_summary TEXT,
    estimated_value_inr INTEGER,
    proposal_status TEXT NOT NULL DEFAULT 'none',
    reason_code TEXT,
    reason_note TEXT,
    hold_review_date TEXT,
    lost_hold_review TEXT NOT NULL DEFAULT 'none',
    last_meaningful_contact_at TEXT,
    won_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_activities (
    id SERIAL PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES crm_opportunities(id),
    company_id INTEGER NOT NULL,
    contact_id INTEGER,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    meaningful INTEGER NOT NULL DEFAULT 0,
    occurred_at TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_followups (
    id SERIAL PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES crm_opportunities(id),
    owner_user_id INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    note TEXT,
    done_at TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_founder_reviews (
    id SERIAL PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES crm_opportunities(id),
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    requested_by INTEGER NOT NULL,
    requested_at TEXT NOT NULL,
    request_note TEXT,
    decided_by INTEGER,
    decided_at TEXT,
    decision TEXT,
    decision_note TEXT
);
CREATE TABLE IF NOT EXISTS crm_audit_log (
    id SERIAL PRIMARY KEY,
    at TEXT NOT NULL,
    actor_user_id INTEGER,
    actor_role TEXT,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id INTEGER,
    outcome TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_crm_companies_owner ON crm_companies(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_crm_companies_key ON crm_companies(name_key);
CREATE INDEX IF NOT EXISTS idx_crm_contacts_owner ON crm_contacts(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_crm_contacts_email ON crm_contacts(email_key);
CREATE INDEX IF NOT EXISTS idx_crm_opps_owner ON crm_opportunities(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_crm_acts_opp ON crm_activities(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_crm_fu_owner_due ON crm_followups(owner_user_id, due_date);
CREATE INDEX IF NOT EXISTS idx_crm_rev_state ON crm_founder_reviews(state);
CREATE INDEX IF NOT EXISTS idx_crm_audit_at ON crm_audit_log(at);

CREATE OR REPLACE FUNCTION crm_append_only() RETURNS trigger AS $fn$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS crm_activities_append_only ON crm_activities;
CREATE TRIGGER crm_activities_append_only BEFORE UPDATE OR DELETE ON crm_activities
FOR EACH ROW EXECUTE FUNCTION crm_append_only();

DROP TRIGGER IF EXISTS crm_audit_log_append_only ON crm_audit_log;
CREATE TRIGGER crm_audit_log_append_only BEFORE UPDATE OR DELETE ON crm_audit_log
FOR EACH ROW EXECUTE FUNCTION crm_append_only();
ALTER TABLE crm_staff ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_mfa ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_mfa_recovery_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_opportunities ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_followups ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_founder_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_audit_log ENABLE ROW LEVEL SECURITY;

DO $rls$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON crm_staff, crm_mfa, crm_mfa_recovery_codes, crm_companies, crm_contacts, crm_opportunities, crm_activities, crm_followups, crm_founder_reviews, crm_audit_log FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON crm_staff, crm_mfa, crm_mfa_recovery_codes, crm_companies, crm_contacts, crm_opportunities, crm_activities, crm_followups, crm_founder_reviews, crm_audit_log FROM authenticated;
  END IF;
END
$rls$;
