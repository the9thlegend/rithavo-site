"""
CRM Lite schema (ten `crm_` tables). Additive only; never touches an
existing table. Applied by `ensure_schema` only on isolated deployments and
tests; Production receives it solely through the separately approved SQL
file (`sql/001_crm_lite.sql`, generated from this module).

No project, handoff or customer-data columns exist by design (D-C4, D-C1).
"""

TABLES = """
CREATE TABLE IF NOT EXISTS crm_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL REFERENCES crm_opportunities(id),
    owner_user_id INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    note TEXT,
    done_at TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_founder_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""


# CRM independent authentication tables (scope Revision 3). Kept separate from TABLES so migration 001 is unchanged
# and the Preview migration 030 can add exactly these three tables.
AUTH_TABLES = """
CREATE TABLE IF NOT EXISTS crm_credentials (
    user_id INTEGER PRIMARY KEY,
    password_hash TEXT,
    credential_epoch INTEGER NOT NULL DEFAULT 1,
    set_at TEXT,
    set_by INTEGER
);
CREATE TABLE IF NOT EXISTS crm_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""
AUTH_TABLE_NAMES = ("crm_credentials", "crm_invites", "crm_rate_limits")

APPEND_ONLY_TABLES = ("crm_activities", "crm_audit_log")
ALL_TABLES = (
    "crm_staff", "crm_mfa", "crm_mfa_recovery_codes", "crm_companies", "crm_contacts",
    "crm_opportunities", "crm_activities", "crm_followups", "crm_founder_reviews", "crm_audit_log",
)

SQLITE_TRIGGERS = "".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;
""" for t in APPEND_ONLY_TABLES)

POSTGRES_TRIGGERS = """
CREATE OR REPLACE FUNCTION crm_append_only() RETURNS trigger AS $fn$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$fn$ LANGUAGE plpgsql;
""" + "".join(
    f"""
DROP TRIGGER IF EXISTS {t}_append_only ON {t};
CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t}
FOR EACH ROW EXECUTE FUNCTION crm_append_only();
""" for t in APPEND_ONLY_TABLES)

_TABLE_LIST = ", ".join(ALL_TABLES)

# RLS ON with NO policies: PostgREST roles (anon, authenticated) can read and
# write nothing. The application connects as the table owner, which bypasses
# RLS, so isolation between specialists is enforced in the application layer.
POSTGRES_RLS = "".join(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;\n" for t in ALL_TABLES) + f"""
DO $rls$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON {_TABLE_LIST} FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON {_TABLE_LIST} FROM authenticated;
  END IF;
END
$rls$;
"""


def _pg(ddl: str) -> str:
    return ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def postgres_migration_sql() -> str:
    """Full migration text (PostgreSQL). Never executed by code on Production."""
    return (
        "-- CRM Lite 001: ten crm_ tables, append-only triggers, RLS on with no policies.\n"
        "-- Rollback: DROP TABLE the ten crm_ tables and function crm_append_only().\n"
        "-- NOT APPLIED to Production. Requires Founder approval and a same-day restore-tested backup (B-01, B-09).\n"
        "BEGIN;\n" + _pg(TABLES) + POSTGRES_TRIGGERS + POSTGRES_RLS + "COMMIT;\n"
    )


def ensure_schema(db) -> None:
    """Create the CRM tables on an isolated database (SQLite, a disposable local
    Postgres, or a Preview Postgres). Refuses a Production Postgres: that path is
    migration-only."""
    import config
    if db._is_postgres and not config.ISOLATED_DEPLOYMENT and not getattr(config, "CRM_ALLOW_SCHEMA_ON_POSTGRES", False):
        raise RuntimeError(
            "Refusing to create CRM tables on a non-isolated Postgres database. Use the approved migration file.")
    with db.connect() as conn:
        if db._is_postgres:
            conn.executescript(_pg(TABLES))
            conn.executescript(_pg(AUTH_TABLES))
            conn.executescript(POSTGRES_TRIGGERS)
            conn.executescript(POSTGRES_RLS)
        else:
            conn.executescript(TABLES)
            conn.executescript(AUTH_TABLES)
            conn.executescript(SQLITE_TRIGGERS)
