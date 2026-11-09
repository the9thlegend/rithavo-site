-- Rithavo app base schema for the dedicated CRM Preview database (generated from app/db.py). Run as the owner.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS career_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    profile_json TEXT NOT NULL,
    trust_level TEXT NOT NULL DEFAULT 'UNVERIFIED',
    completeness_pct INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entitlements (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    product TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    payment_source TEXT NOT NULL,
    external_payment_reference TEXT,
    purchased_at TEXT NOT NULL,
    activated_at TEXT
);

CREATE TABLE IF NOT EXISTS capabilities (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    cluster_id INTEGER,
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    confidence TEXT,
    source TEXT NOT NULL,
    user_confirmed INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    first_derived_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    supersedes_id INTEGER
);

CREATE TABLE IF NOT EXISTS resumes (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    target_context TEXT NOT NULL DEFAULT '',
    source_file_ref TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    linked_diagnostic_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostics (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER REFERENCES users(id),
    ai_run_id TEXT NOT NULL UNIQUE,
    ai_model TEXT NOT NULL DEFAULT '',
    target_role_text TEXT NOT NULL DEFAULT '',
    target_jd_text TEXT NOT NULL DEFAULT '',
    overall_score INTEGER,
    resume_id INTEGER REFERENCES resumes(id),
    source_order_status TEXT NOT NULL DEFAULT '',
    diagnostic_version TEXT NOT NULL DEFAULT '',
    prompt_rubric_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostic_findings (
    id SERIAL PRIMARY KEY,
    diagnostic_id INTEGER NOT NULL REFERENCES diagnostics(id),
    dimension TEXT NOT NULL,
    finding_text TEXT NOT NULL DEFAULT '',
    evidence_excerpt TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL,
    promoted_to_capability_id INTEGER,
    promoted_to_evidence_id INTEGER
);

-- Phase 2B-1.7A: read-only presentation adapter for the sibling app's
-- Career Intelligence data model. Every field this service reads back
-- out of these tables is a VERBATIM, already-computed value — fit
-- bands, gap descriptions, recommendation text, readiness bands are
-- all written by the sibling's own Fit/Gap/Recommendation/Readiness
-- engines and never recomputed, rescored, or reinterpreted here. The
-- only "logic" this service adds (see app/career_intelligence.py) is
-- mechanical: which already-persisted row is "the active one" (a plain
-- status='ACTIVE' filter, the exact same convention every table below
-- already uses) and which of Stay/New Role/New Industry/Major
-- Transition a target's populated foreign keys correspond to (i.e.
-- reading which of target_role_id/target_industry_id is set — not a
-- scoring decision). No CI scoring, matching, synthesis, or
-- recommendation logic is reimplemented anywhere in this service.
-- Verbatim copies of the sibling's real schema; no-ops in production.
CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS industries (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS career_paths (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    source_context TEXT NOT NULL DEFAULT '',
    target_role_id INTEGER REFERENCES roles(id),
    target_industry_id INTEGER REFERENCES industries(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS career_direction_assessments (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    stated_direction_career_path_id INTEGER REFERENCES career_paths(id),
    stated_direction_fit_summary TEXT NOT NULL DEFAULT '',
    stated_direction_confidence TEXT,
    alternative_career_path_ids TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    computed_at TEXT NOT NULL,
    supersedes_id INTEGER
);

CREATE TABLE IF NOT EXISTS role_fits (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    role_id INTEGER NOT NULL REFERENCES roles(id),
    current_fit_band TEXT NOT NULL,
    transferable_fit_band TEXT NOT NULL,
    transition_effort_band TEXT NOT NULL,
    confidence TEXT,
    reasoning TEXT NOT NULL DEFAULT '',
    supporting_capability_ids TEXT NOT NULL DEFAULT '[]',
    gap_ids TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    computed_at TEXT NOT NULL,
    supersedes_id INTEGER
);

CREATE TABLE IF NOT EXISTS industry_fits (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    industry_id INTEGER NOT NULL REFERENCES industries(id),
    current_fit_band TEXT NOT NULL,
    transferable_fit_band TEXT NOT NULL,
    transition_effort_band TEXT NOT NULL,
    confidence TEXT,
    reasoning TEXT NOT NULL DEFAULT '',
    supporting_capability_ids TEXT NOT NULL DEFAULT '[]',
    gap_ids TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    computed_at TEXT NOT NULL,
    supersedes_id INTEGER
);

CREATE TABLE IF NOT EXISTS career_transitions (
    id SERIAL PRIMARY KEY,
    career_path_id INTEGER NOT NULL REFERENCES career_paths(id),
    role_fit_id INTEGER REFERENCES role_fits(id),
    industry_fit_id INTEGER REFERENCES industry_fits(id),
    transferable_capability_ids TEXT NOT NULL DEFAULT '[]',
    gap_ids TEXT NOT NULL DEFAULT '[]',
    difficulty_band TEXT NOT NULL,
    suggested_next_steps TEXT NOT NULL DEFAULT '[]',
    confidence TEXT,
    reasoning TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    computed_at TEXT NOT NULL,
    supersedes_id INTEGER
);

CREATE TABLE IF NOT EXISTS gaps (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    career_transition_id INTEGER REFERENCES career_transitions(id),
    gap_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'HELPFUL',
    depends_on_gap_ids TEXT NOT NULL DEFAULT '[]',
    applicable_stages TEXT NOT NULL DEFAULT '[]',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    recommended_action_type TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    supersedes_id INTEGER
);

CREATE TABLE IF NOT EXISTS learning_recommendations (
    id SERIAL PRIMARY KEY,
    gap_id INTEGER NOT NULL REFERENCES gaps(id),
    recommendation_type TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    importance_band TEXT NOT NULL,
    would_meaningfully_help INTEGER,
    would_meaningfully_help_explanation TEXT NOT NULL DEFAULT '',
    expected_outcome TEXT NOT NULL DEFAULT '',
    evidence_of_completion TEXT NOT NULL DEFAULT '',
    applicable_stages TEXT NOT NULL DEFAULT '[]',
    confidence TEXT,
    alternative_paths_considered TEXT NOT NULL DEFAULT '[]',
    decision TEXT NOT NULL DEFAULT 'PENDING',
    decided_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS readiness_assessments (
    id SERIAL PRIMARY KEY,
    career_transition_id INTEGER NOT NULL REFERENCES career_transitions(id),
    current_readiness_json TEXT NOT NULL,
    transition_readiness_json TEXT NOT NULL,
    target_readiness_json TEXT NOT NULL,
    overall_band TEXT NOT NULL,
    confidence TEXT,
    reasoning TEXT NOT NULL DEFAULT '',
    strengths TEXT NOT NULL DEFAULT '[]',
    transfers TEXT NOT NULL DEFAULT '[]',
    barrier_gap_ids TEXT NOT NULL DEFAULT '[]',
    next_step_recommendation_ids TEXT NOT NULL DEFAULT '[]',
    success_looks_like TEXT NOT NULL DEFAULT '[]',
    missing_information TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    computed_at TEXT NOT NULL,
    supersedes_id INTEGER
);

-- Phase P0.2 — the SMALLEST possible read-only mirror of the sibling's
-- card_settings table (owned by rithavo-career-profile, see its own
-- db.py for the full, real schema), just enough to answer "does this
-- person already have a Rithavo Card" for the Home page CTA. This
-- service never INSERTs, UPDATEs, or DELETEs a row here — the sibling's
-- existing get_or_create_card_settings_for_person remains the sole
-- Card writer. Deliberately omits every other real column (public_slug,
-- theme, discoverability, etc.) since none of them are needed for an
-- existence check; in production, against the real shared database,
-- this is a no-op (the table already exists with the full real shape).
CREATE TABLE IF NOT EXISTS card_settings (
    id SERIAL PRIMARY KEY,
    person_user_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS web_magic_link_tokens (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT
);

-- P0 (conventional sign-in rework): same single-use-token shape as
-- web_magic_link_tokens above, deliberately a separate table so a
-- password-reset token and a magic-link token can never be confused with
-- or consumed as each other even if (hypothetically) the same raw string
-- ever collided.
CREATE TABLE IF NOT EXISTS web_password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS education (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    degree TEXT NOT NULL DEFAULT '',
    institution TEXT NOT NULL DEFAULT '',
    field TEXT NOT NULL DEFAULT '',
    start_date TEXT,
    end_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experience_entries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    company TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    start_date TEXT,
    end_date TEXT,
    responsibilities TEXT NOT NULL DEFAULT '',
    achievements TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL DEFAULT '',
    function TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchases (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    product TEXT NOT NULL,
    amount_inr INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    qualifying_count_at_purchase INTEGER NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'PENDING',
    gateway TEXT NOT NULL DEFAULT 'test',
    gateway_reference TEXT UNIQUE,
    entitlement_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    refunded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_education_user ON education (user_id);
CREATE INDEX IF NOT EXISTS idx_experience_user ON experience_entries (user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases (user_id);

-- Home/Explore/Admin Integration Phase — genuinely new, owned by this
-- service (not shared with the sibling): explicit Super Admin
-- membership. A dedicated table, not a users.is_super_admin flag, so
-- authorization is always "is this user_id a row here," never "does
-- this email happen to match" — see app/super_admin.py.
CREATE TABLE IF NOT EXISTS super_admins (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    created_at TEXT NOT NULL
);

-- Pre-Launch Registration / Live Admin Command Center phase — the one
-- new table this phase adds. A closed, small vocabulary of event_type
-- values (enforced in code, see app/analytics.py), never a generic
-- events warehouse: page_view, registration_completed, login,
-- ci_interest, ad_interest. session_id is a random, first-party,
-- non-identifying cookie value (see app/analytics.py's own docstring)
-- -- present on every row, including for a logged-out visitor.
-- user_id is populated only once a visitor is authenticated; never the
-- other way around. No IP address, no fingerprinting signal, and no
-- free-form properties/metadata column -- every column here has one
-- fixed meaning, so this can never grow into an ad-hoc analytics
-- warehouse merely by adding new keys to a blob.
CREATE TABLE IF NOT EXISTS analytics_events (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    path TEXT,
    session_id TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id),
    referrer TEXT,
    device_category TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analytics_events_type_created ON analytics_events (event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_analytics_events_session ON analytics_events (session_id);
CREATE INDEX IF NOT EXISTS idx_analytics_events_path ON analytics_events (path);

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_updated_at TEXT;
ALTER TABLE entitlements ADD COLUMN IF NOT EXISTS diagnostic_id INTEGER REFERENCES diagnostics(id);
ALTER TABLE entitlements ADD COLUMN IF NOT EXISTS consumed_at TEXT;
ALTER TABLE entitlements ADD COLUMN IF NOT EXISTS purchase_id INTEGER REFERENCES purchases(id);
ALTER TABLE diagnostics ADD COLUMN IF NOT EXISTS job_id INTEGER;
ALTER TABLE diagnostics ADD COLUMN IF NOT EXISTS entitlement_id INTEGER REFERENCES entitlements(id);
ALTER TABLE diagnostics ADD COLUMN IF NOT EXISTS route TEXT;
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS content_json TEXT;
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS diagnostic_id INTEGER REFERENCES diagnostics(id);
ALTER TABLE purchases ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE diagnostics ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS resume_context TEXT;
ALTER TABLE purchases ADD COLUMN IF NOT EXISTS gateway_payment_id TEXT;
ALTER TABLE purchases ADD COLUMN IF NOT EXISTS customer_phone TEXT;
ALTER TABLE entitlements ADD COLUMN IF NOT EXISTS expires_at TEXT;
ALTER TABLE entitlements ADD COLUMN IF NOT EXISTS evaluations_used INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchases_idempotency_key ON purchases(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_diagnostics_idempotency_key ON diagnostics(profile_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
DROP INDEX IF EXISTS idx_purchases_idempotency_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchases_idempotency_key_v2 ON purchases(user_id, product, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchases_gateway_payment_id ON purchases(gateway_payment_id) WHERE gateway_payment_id IS NOT NULL;
