"""
Database access for the Rithavo Web Platform (rithavo.com backend).

Two kinds of tables in SCHEMA below, deliberately distinguished in comments:

  - SHARED tables (users, career_profiles): these already exist in
    production, owned and created by the sibling `rithavo-career-profile`
    codebase (app.rithavo.com). This service only ever READS them in
    Phase 0. Their `CREATE TABLE IF NOT EXISTS` definitions here exist
    ONLY so local/test runs (SQLite, or a disposable local Postgres) can
    stand up a faithful replica to test against safely — in production,
    against the real shared database, these statements are no-ops
    (the tables already exist with these exact shapes) and never alter,
    drop, or rename anything.

  - NEW tables (education, experience_entries, web_magic_link_tokens):
    genuinely new, owned by this service, additive only.

Same SQLite/Postgres dual-backend proxy trick as the sibling project
(verified working there across many phases) — ported here as a small,
self-contained, independently-tested copy. Nothing here imports from or
calls into the sibling codebase at runtime.
"""

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional


class _PGCursorProxy:
    # Tables with no `id` column at all: career_profiles is keyed on
    # user_id (shared table); web_magic_link_tokens is keyed on
    # token_hash. Postgres has no equivalent to SQLite's harmless-if-unused
    # `cur.lastrowid` — appending `RETURNING id` to an insert into either
    # of these raises UndefinedColumn, so both must be excluded here.
    _TABLES_WITHOUT_ID = ("career_profiles", "web_magic_link_tokens")

    def __init__(self, real_cursor):
        self._cur = real_cursor
        self.lastrowid = None

    def execute(self, sql, params=()):
        translated = sql.replace("?", "%s")
        is_insert = translated.lstrip().upper().startswith("INSERT")
        targets_id_table = not any(f"INTO {t}" in translated for t in self._TABLES_WITHOUT_ID)
        needs_id = is_insert and "ON CONFLICT" not in translated and targets_id_table
        if needs_id:
            translated = translated.rstrip().rstrip(";") + " RETURNING id"
        self._cur.execute(translated, params)
        if needs_id:
            row = self._cur.fetchone()
            self.lastrowid = row["id"] if row else None
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        # Phase 2A: needed so callers can tell "my UPDATE actually changed
        # a row" from "it matched zero rows" — the definitive, backend-
        # portable way to detect a lost race on a guarded UPDATE (e.g.
        # `WHERE consumed_at IS NULL`), rather than trusting a separate
        # SELECT that could itself be stale by the time the UPDATE runs.
        return self._cur.rowcount


class _PGConnectionProxy:
    def __init__(self, real_conn):
        self._conn = real_conn

    def execute(self, sql, params=()):
        from psycopg2.extras import RealDictCursor
        cur = _PGCursorProxy(self._conn.cursor(cursor_factory=RealDictCursor))
        return cur.execute(sql, params)

    def executescript(self, sql):
        self._conn.cursor().execute(_sqlite_ddl_to_postgres(sql))

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _sqlite_ddl_to_postgres(sql: str) -> str:
    return sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- SHARED tables: read-mostly in this service, replica-for-testing only ----
#
# Phase 1 adds entitlements/capabilities/resumes/diagnostics/diagnostic_findings
# to this replica. entitlements/diagnostics/resumes move from read-only to
# read+INSERT+additive-UPDATE in this phase (this service now issues its own
# entitlements and writes its own diagnoses/resumes into these already-existing
# shared tables) — capabilities stays read-only (evidence grounding only).
# Every definition below is copied verbatim from the sibling app's real
# schema (re-verified against live production metadata in Phase 0.75); in
# production these CREATE TABLE IF NOT EXISTS statements are no-ops.
SCHEMA_SHARED_REPLICA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    target_context TEXT NOT NULL DEFAULT '',
    source_file_ref TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    linked_diagnostic_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS industries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS career_paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES users(id),
    source_context TEXT NOT NULL DEFAULT '',
    target_role_id INTEGER REFERENCES roles(id),
    target_industry_id INTEGER REFERENCES industries(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS career_direction_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""

# ---- NEW tables: owned by this service, additive ----
SCHEMA_NEW = """
CREATE TABLE IF NOT EXISTS web_magic_link_tokens (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS education (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""

# ---- Phase 1: additive columns on already-existing shared tables ----
# Every ALTER here is wrapped by _add_column_if_missing (SAVEPOINT-guarded,
# see the sibling app's own db.py for why that matters on Postgres) and is
# purely additive/nullable — no existing reader of these tables is affected
# by a new NULL-default column it doesn't select by name.
_ADDITIVE_COLUMNS = [
    # (table, column, ddl_type)
    ("entitlements", "diagnostic_id", "INTEGER REFERENCES diagnostics(id)"),
    ("entitlements", "consumed_at", "TEXT"),
    ("entitlements", "purchase_id", "INTEGER REFERENCES purchases(id)"),
    # job_id is deliberately a bare nullable column, not yet a foreign key:
    # no `jobs` table exists (job marketplace is a future phase) — Route B
    # (external pasted JD) always leaves this NULL; Route A would populate
    # it once a `jobs` table exists, at which point a FK could be added.
    ("diagnostics", "job_id", "INTEGER"),
    ("diagnostics", "entitlement_id", "INTEGER REFERENCES entitlements(id)"),
    ("diagnostics", "route", "TEXT"),
    ("resumes", "content_json", "TEXT"),
    ("resumes", "diagnostic_id", "INTEGER REFERENCES diagnostics(id)"),
    # Phase 2A: client-supplied (but server-enforced-unique) retry key —
    # a client that resends "start a purchase" after a timeout/duplicate
    # click must get back the SAME purchase row, never a second one.
    ("purchases", "idempotency_key", "TEXT"),
    # Phase 2B-1: same pattern, for the SAME reason, on the entitlement-
    # consuming side — a client that resends "submit this diagnosis"
    # after a timeout must get back the ALREADY-CREATED diagnosis rather
    # than a confusing "no credit" error (the entitlement is correctly
    # already consumed by then) or, worse, a second diagnosis.
    ("diagnostics", "idempotency_key", "TEXT"),
    # Phase 2B-1.7A: which of the three Rithavo resume contexts a given
    # resume row is — SAME_CAREER / NEW_TARGET (both new this phase, for
    # Career Intelligence resumes) or JOB_DIAGNOSIS (the pre-existing
    # Application Diagnosis resume, now explicitly tagged as such at
    # creation time instead of only being inferable from diagnostic_id
    # being non-null). Nullable/additive — existing rows created before
    # this column existed are simply NULL, exactly like every other
    # additive column above.
    ("resumes", "resume_context", "TEXT"),
    # Phase 2B-2: Razorpay's payment id, distinct from gateway_reference
    # (which now holds the Razorpay Order id, created before payment —
    # see begin_ad_purchase/begin_ci_purchase). Nullable until a payment
    # actually succeeds; UNIQUE (enforced via the additive index below)
    # so the same successful Razorpay payment can never confirm two
    # different purchase rows, even under a client-callback/webhook race.
    ("purchases", "gateway_payment_id", "TEXT"),
]

# Phase 2A: additive indexes, applied AFTER _ADDITIVE_COLUMNS (the column
# an index targets must already exist). A partial index — only rows that
# actually supplied a key are constrained — so the many purchases made
# without one (webhook-confirmed purchases that predate this feature, or
# callers that don't pass one) never collide with each other on NULL.
_ADDITIVE_INDEXES = [
    # Composite on (user_id, idempotency_key) — scoped per user, per the
    # design intent (two different users legitimately generating the same
    # key string, e.g. both client-side UUIDs colliding astronomically
    # rarely, must never collide with each other).
    ("idx_purchases_idempotency_key",
     "CREATE UNIQUE INDEX idx_purchases_idempotency_key ON purchases(user_id, idempotency_key) "
     "WHERE idempotency_key IS NOT NULL"),
    # Phase 2B-1: same scoping rationale as purchases' — diagnostics is
    # keyed on `profile_id` (== the owning user's id) rather than
    # `user_id`, but the pattern is identical.
    ("idx_diagnostics_idempotency_key",
     "CREATE UNIQUE INDEX idx_diagnostics_idempotency_key ON diagnostics(profile_id, idempotency_key) "
     "WHERE idempotency_key IS NOT NULL"),
    # Phase 2B-2: idx_purchases_idempotency_key above was scoped only by
    # (user_id, idempotency_key) — harmless while exactly one product
    # (APPLICATION_DIAGNOSIS) ever wrote to this table, but Career
    # Intelligence purchases now share it too. Replaced with a
    # product-scoped equivalent so the same idempotency_key string used
    # for two different products by the same user (unlikely, but no
    # longer impossible) can never collide. Safe to apply to the real
    # production table: every existing row has product='APPLICATION_DIAGNOSIS',
    # so no existing data could possibly violate the new, stricter constraint.
    ("drop_old_purchases_idempotency_key_index", "DROP INDEX IF EXISTS idx_purchases_idempotency_key"),
    ("idx_purchases_idempotency_key_v2",
     "CREATE UNIQUE INDEX idx_purchases_idempotency_key_v2 ON purchases(user_id, product, idempotency_key) "
     "WHERE idempotency_key IS NOT NULL"),
    # Phase 2B-2: see gateway_payment_id's own comment above.
    ("idx_purchases_gateway_payment_id",
     "CREATE UNIQUE INDEX idx_purchases_gateway_payment_id ON purchases(gateway_payment_id) "
     "WHERE gateway_payment_id IS NOT NULL"),
]


class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self._is_postgres = isinstance(db_path, str) and db_path.startswith(("postgres://", "postgresql://"))
        self._pg_pool = None

    def _get_pg_pool(self):
        if self._pg_pool is None:
            from psycopg2.pool import ThreadedConnectionPool
            import config
            # sslmode is pinned to 'require' here, in code — never left to
            # psycopg2's 'prefer' default and never 'disable' — per the
            # Phase 0.75 finding that the shared Supabase pooler does not
            # enforce TLS itself. If the DSN's own query string already
            # specifies sslmode, that explicit choice is respected instead
            # of being silently overridden.
            connect_kwargs = {"connect_timeout": getattr(config, "DATABASE_CONNECT_TIMEOUT_SECONDS", 10)}
            if "sslmode=" not in self.db_path:
                connect_kwargs["sslmode"] = getattr(config, "DATABASE_SSLMODE", "require")
            pool_min = getattr(config, "DATABASE_POOL_MIN", 1)
            pool_max = getattr(config, "DATABASE_POOL_MAX", 5)
            self._pg_pool = ThreadedConnectionPool(pool_min, pool_max, self.db_path, **connect_kwargs)
        return self._pg_pool

    @contextmanager
    def connect(self):
        if self._is_postgres:
            pool = self._get_pg_pool()
            raw_conn = pool.getconn()
            conn = _PGConnectionProxy(raw_conn)
            try:
                yield conn
                conn.commit()
            except Exception:
                raw_conn.rollback()
                raise
            finally:
                pool.putconn(raw_conn)
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Additive only, every CREATE TABLE `IF NOT EXISTS`, every ALTER
        TABLE SAVEPOINT-guarded (see _add_column_if_missing). Against the
        real shared production database, the SHARED-table CREATEs are
        no-ops (those tables already exist, created by the sibling app)
        and the additive ALTERs run exactly once (idempotent thereafter).
        Against local SQLite / a disposable test Postgres, this stands up
        a faithful replica so tests can run without ever touching real
        data. Order matters: SCHEMA_NEW (which creates `purchases`) must
        run before the additive-column pass, since two of those columns
        reference `purchases(id)`."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SHARED_REPLICA)
            conn.executescript(SCHEMA_NEW)
            for table, column, ddl_type in _ADDITIVE_COLUMNS:
                self._run_ddl_safely(conn, f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
            for _name, ddl in _ADDITIVE_INDEXES:
                self._run_ddl_safely(conn, ddl)

    def _run_ddl_safely(self, conn, ddl_sql: str) -> None:
        """Ported (same rationale) from the sibling app's own db.py: a
        bare try/except is not enough on Postgres because a failed
        statement inside init_schema()'s single transaction poisons every
        statement after it, silently rolling back the whole schema-setup
        call with no exception ever surfacing. The SAVEPOINT bounds the
        damage to just this one statement, on both backends. Generalized
        (Phase 2A) beyond just ADD COLUMN to also cover CREATE INDEX,
        which has the identical "already exists on a pre-migrated
        database" re-run problem but no portable IF NOT EXISTS everywhere
        an index name might already exist under a different definition."""
        conn.execute("SAVEPOINT run_ddl_safely")
        try:
            conn.execute(ddl_sql)
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT run_ddl_safely")
        else:
            conn.execute("RELEASE SAVEPOINT run_ddl_safely")

    # ---- identity (reads/writes the SHARED users table) ----

    def get_or_create_user(self, email: str) -> int:
        """Same resolution rule as the sibling app: one email, one
        users.id, forever — this is the entire mechanism by which the two
        independently-deployed services agree on 'the same Rithavo
        account' without sharing any code or any session."""
        email = email.strip().lower()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if row:
                return row["id"]
            cur = conn.execute(
                "INSERT INTO users (email, created_at) VALUES (?, ?)", (email, _now())
            )
            return cur.lastrowid

    def get_user_by_id(self, user_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    # ---- shared profile (READ ONLY from this service in Phase 0) ----

    def get_career_profile(self, user_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM career_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()

    # ---- Career Intelligence (READ ONLY presentation adapter — Phase
    #      2B-1.7A; see the schema comment above SCHEMA_SHARED_REPLICA's
    #      CI tables). Every query below is a plain status='ACTIVE' or
    #      foreign-key lookup, the exact same convention the sibling
    #      app's own db.py uses for these same tables — no scoring, no
    #      synthesis, nothing recomputed. ----

    def get_role(self, role_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()

    def get_industry(self, industry_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM industries WHERE id = ?", (industry_id,)).fetchone()

    def get_career_path(self, path_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM career_paths WHERE id = ?", (path_id,)).fetchone()

    def list_career_paths_for_user(self, user_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM career_paths WHERE profile_id = ? ORDER BY id", (user_id,)
            ).fetchall()

    def find_active_career_direction_assessment(self, user_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM career_direction_assessments WHERE profile_id = ? AND status = 'ACTIVE'",
                (user_id,),
            ).fetchone()

    def find_active_role_fit(self, user_id: int, role_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM role_fits WHERE profile_id = ? AND role_id = ? AND status = 'ACTIVE'",
                (user_id, role_id),
            ).fetchone()

    def find_active_industry_fit(self, user_id: int, industry_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM industry_fits WHERE profile_id = ? AND industry_id = ? AND status = 'ACTIVE'",
                (user_id, industry_id),
            ).fetchone()

    def find_active_career_transition_for_path(self, career_path_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM career_transitions WHERE career_path_id = ? AND status = 'ACTIVE'",
                (career_path_id,),
            ).fetchone()

    def list_active_gaps_for_transition(self, career_transition_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM gaps WHERE career_transition_id = ? AND status = 'ACTIVE' ORDER BY id",
                (career_transition_id,),
            ).fetchall()

    def list_recommendations_for_gap(self, gap_id: int) -> list:
        # "REJECTED never appears here" — the exact same customer-facing
        # exclusion rule the sibling app's own NextAction presentation
        # uses (app/intelligence/synthesis_engine.py), reproduced as a
        # literal filter, not a decision this service is making itself.
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM learning_recommendations WHERE gap_id = ? AND decision != 'REJECTED' ORDER BY id",
                (gap_id,),
            ).fetchall()

    def find_active_readiness_for_transition(self, career_transition_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM readiness_assessments WHERE career_transition_id = ? AND status = 'ACTIVE'",
                (career_transition_id,),
            ).fetchone()

    # ---- magic link (owned by this service) ----

    def record_magic_link_token(self, token_hash: str, email: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO web_magic_link_tokens (token_hash, email, created_at) VALUES (?, ?, ?)",
                (token_hash, email, _now()),
            )

    def consume_magic_link_token(self, token_hash: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT used_at FROM web_magic_link_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None or row["used_at"] is not None:
                return False
            conn.execute(
                "UPDATE web_magic_link_tokens SET used_at = ? WHERE token_hash = ?", (_now(), token_hash)
            )
            return True

    # ---- education (new, owned) ----

    def add_education(self, user_id: int, degree="", institution="", field="", start_date=None, end_date=None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO education (user_id, degree, institution, field, start_date, end_date, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, degree, institution, field, start_date, end_date, _now(), _now()),
            )
            return cur.lastrowid

    def get_education(self, education_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM education WHERE id = ?", (education_id,)).fetchone()

    def list_education_for_user(self, user_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM education WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()

    def update_education(self, education_id: int, fields: dict) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE education SET {cols}, updated_at = ? WHERE id = ?",
                (*fields.values(), _now(), education_id),
            )

    def delete_education(self, education_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM education WHERE id = ?", (education_id,))

    # ---- experience entries (new, owned) ----

    def add_experience_entry(self, user_id: int, **fields) -> int:
        cols = ["user_id", "company", "role", "start_date", "end_date",
                "responsibilities", "achievements", "industry", "function"]
        values = [user_id] + [fields.get(c, "" if c not in ("start_date", "end_date") else None) for c in cols[1:]]
        with self.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO experience_entries ({', '.join(cols)}, created_at, updated_at) "
                f"VALUES ({', '.join('?' for _ in cols)}, ?, ?)",
                (*values, _now(), _now()),
            )
            return cur.lastrowid

    def get_experience_entry(self, entry_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM experience_entries WHERE id = ?", (entry_id,)).fetchone()

    def list_experience_for_user(self, user_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM experience_entries WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()

    def update_experience_entry(self, entry_id: int, fields: dict) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE experience_entries SET {cols}, updated_at = ? WHERE id = ?",
                (*fields.values(), _now(), entry_id),
            )

    def delete_experience_entry(self, entry_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM experience_entries WHERE id = ?", (entry_id,))

    # ---- capabilities (SHARED table, read-only evidence grounding) ----

    def list_active_capabilities_for_user(self, user_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM capabilities WHERE profile_id = ? AND status = 'ACTIVE' ORDER BY id", (user_id,)
            ).fetchall()

    # ---- purchases (new, owned by this service) ----

    def _advisory_lock(self, conn, key: str) -> None:
        """Transaction-scoped advisory lock, Postgres only (a no-op on
        SQLite, where this repo's tests exercise the underlying counting/
        insert logic sequentially rather than via true concurrency — see
        tests/test_pricing.py for the real-concurrency proof against
        Postgres). Released automatically at COMMIT or ROLLBACK, i.e.
        exactly when this connect() block's `with` exits — so it holds for
        the entire read-then-write sequence it wraps, not just one
        statement."""
        if self._is_postgres:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(?)::bigint)", (key,))

    def count_qualifying_ad_purchases(self, user_id: int) -> int:
        """'Successful, non-refunded Application Diagnosis purchases only.'
        A single payment_status column (rather than a separate boolean
        flag) is the entire mechanism: ADMIN_GRANT, PENDING, and FAILED
        were never SUCCEEDED; a purchase that WAS SUCCEEDED and later
        refunded moves to the terminal REFUNDED status and drops out of
        this count automatically — no double-bookkeeping required."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM purchases WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSIS' "
                "AND payment_status = 'SUCCEEDED'", (user_id,),
            ).fetchone()
            return row["c"]

    def preview_ad_price(self, user_id: int) -> dict:
        """Read-only — answers 'what would this user pay right now' with
        NO side effect and NO row created. Distinct on purpose from
        begin_ad_purchase: a frontend showing 'Your price today: ₹319'
        (informational only, per the approved architecture) must not
        spam the purchases table with an abandoned row every time someone
        merely loads a pricing page."""
        count = self.count_qualifying_ad_purchases(user_id)
        from app.pricing import price_for_qualifying_count
        return {"qualifying_count": count, "amount_inr": price_for_qualifying_count(count)}

    def begin_ad_purchase(self, user_id: int, gateway: str = "test", idempotency_key: str = None) -> dict:
        """Atomically: lock this user's own purchase history, count their
        qualifying purchases, compute the price from that count, and
        insert a CREATED purchase row with that price locked in — all in
        one transaction. Two concurrent purchase attempts for the SAME
        user (double-click, two tabs) serialize on the advisory lock, so
        the second one always sees the first one's (not-yet-committed
        elsewhere) count correctly once it proceeds — neither can read a
        stale count and lock in a lower, already-used tier. Different
        users never contend with each other (the lock key is per-user).

        idempotency_key (Phase 2A): if the caller supplies one and a
        purchase with that exact key already exists for this user, that
        EXISTING purchase's data is returned unchanged — no new row, no
        re-priced retry — so a client retrying a timed-out "start
        purchase" request can't ever create two purchases for one intent.
        The existence check happens under the same per-user advisory
        lock as the count+insert, so two concurrent retries with the same
        key can't both pass the check before either commits."""
        from app.pricing import price_for_qualifying_count
        with self.connect() as conn:
            self._advisory_lock(conn, f"ad_purchase:{user_id}")
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM purchases WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSIS' "
                    "AND idempotency_key = ?",
                    (user_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return {
                        "purchase_id": existing["id"], "amount_inr": existing["amount_inr"],
                        "qualifying_count": existing["qualifying_count_at_purchase"],
                        "payment_status": existing["payment_status"], "replayed": True,
                    }
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM purchases WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSIS' "
                "AND payment_status = 'SUCCEEDED'", (user_id,),
            ).fetchone()["c"]
            price = price_for_qualifying_count(count)
            cur = conn.execute(
                "INSERT INTO purchases (user_id, product, amount_inr, currency, qualifying_count_at_purchase, "
                "payment_status, gateway, idempotency_key, created_at, updated_at) "
                "VALUES (?, 'APPLICATION_DIAGNOSIS', ?, 'INR', ?, 'CREATED', ?, ?, ?, ?)",
                (user_id, price, count, gateway, idempotency_key, _now(), _now()),
            )
            return {
                "purchase_id": cur.lastrowid, "amount_inr": price, "qualifying_count": count,
                "payment_status": "CREATED", "replayed": False,
            }

    def begin_ci_purchase(self, user_id: int, gateway: str = "test", idempotency_key: str = None) -> dict:
        """Career Intelligence's equivalent of begin_ad_purchase — same
        idempotency-key-dedup-under-advisory-lock guarantee, but with a
        flat price (no qualifying-count ladder; CI is ₹799 one-time,
        always). qualifying_count_at_purchase is stored as 0 — meaningless
        for CI, kept only because the column is NOT NULL on a shared
        table shape; count_qualifying_ad_purchases/price_for_qualifying_count
        are never called for this product."""
        from app.pricing import CAREER_INTELLIGENCE_PRICE_INR
        with self.connect() as conn:
            self._advisory_lock(conn, f"ci_purchase:{user_id}")
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM purchases WHERE user_id = ? AND product = 'CAREER_INTELLIGENCE' "
                    "AND idempotency_key = ?",
                    (user_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return {
                        "purchase_id": existing["id"], "amount_inr": existing["amount_inr"],
                        "payment_status": existing["payment_status"], "replayed": True,
                    }
            cur = conn.execute(
                "INSERT INTO purchases (user_id, product, amount_inr, currency, qualifying_count_at_purchase, "
                "payment_status, gateway, idempotency_key, created_at, updated_at) "
                "VALUES (?, 'CAREER_INTELLIGENCE', ?, 'INR', 0, 'CREATED', ?, ?, ?, ?)",
                (user_id, CAREER_INTELLIGENCE_PRICE_INR, gateway, idempotency_key, _now(), _now()),
            )
            return {
                "purchase_id": cur.lastrowid, "amount_inr": CAREER_INTELLIGENCE_PRICE_INR,
                "payment_status": "CREATED", "replayed": False,
            }

    def mark_purchase_pending(self, purchase_id: int, gateway_reference: str = None) -> None:
        """CREATED -> PENDING: a gateway payment intent/session now exists
        and the customer is expected to complete payment. Guarded so this
        can only ever fire from CREATED — calling it twice, or on a
        purchase already further along, is a silent no-op rather than an
        error, since nothing about later state should be clobbered by a
        stray duplicate call."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE purchases SET payment_status = 'PENDING', gateway_reference = COALESCE(?, gateway_reference), "
                "updated_at = ? WHERE id = ? AND payment_status = 'CREATED'",
                (gateway_reference, _now(), purchase_id),
            )

    def get_purchase(self, purchase_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()

    def get_purchase_by_gateway_reference(self, gateway_reference: str):
        """Phase 2B-2: how the Razorpay webhook resolves a purchase —
        purely from the Order id THIS server itself created and stored
        (gateway_reference), at PENDING time. Never resolves via anything
        the webhook payload's own identity-shaped fields (customer id,
        email, etc.) claim, exactly per 'never trust client-supplied
        identity' (this is a server-to-server call, not a browser
        session, but the same rule still applies — Razorpay's own
        webhook payload is still untrusted input until its signature is
        verified, and even then names only the payment, not our user)."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM purchases WHERE gateway_reference = ?", (gateway_reference,)
            ).fetchone()

    def list_purchases_for_user(self, user_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM purchases WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()

    def _confirm_purchase(self, purchase_id: int, gateway_reference: str, entitlement_product: str,
                           gateway_payment_id: str = None) -> dict:
        """Phase 2B-2: the shared core confirm_ad_purchase always had —
        extracted so Career Intelligence purchases (confirm_ci_purchase,
        below) get the EXACT same guarantees (idempotent, advisory-locked,
        gateway_reference-collision-checked) without a second, subtly-
        different implementation. confirm_ad_purchase's own public
        behavior/signature is unchanged; only entitlement_product (which
        it always hardcoded) is now a parameter.

        gateway_payment_id (Phase 2B-2, optional): Razorpay's payment id,
        distinct from gateway_reference (the Order id, set earlier at
        PENDING time — see begin_ad_purchase/begin_ci_purchase +
        mark_purchase_pending). Checked for uniqueness the same way
        gateway_reference already is, so the same successful Razorpay
        payment can never confirm two different purchase rows even if
        the client-side callback and the webhook both arrive."""
        with self.connect() as conn:
            self._advisory_lock(conn, f"confirm_purchase:{purchase_id}")
            purchase = conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
            if purchase is None:
                raise ValueError("purchase not found")
            if purchase["payment_status"] == "SUCCEEDED":
                return {
                    "purchase_id": purchase_id,
                    "entitlement_id": purchase["entitlement_id"],
                    "already_confirmed": True,
                }
            if purchase["payment_status"] not in ("CREATED", "PENDING"):
                raise ValueError(f"cannot confirm a purchase in status {purchase['payment_status']}")
            conflicting = conn.execute(
                "SELECT id FROM purchases WHERE gateway_reference = ? AND id != ?",
                (gateway_reference, purchase_id),
            ).fetchone()
            if conflicting is not None:
                raise ValueError("gateway_reference already used by a different purchase")
            if gateway_payment_id:
                payment_conflicting = conn.execute(
                    "SELECT id FROM purchases WHERE gateway_payment_id = ? AND id != ?",
                    (gateway_payment_id, purchase_id),
                ).fetchone()
                if payment_conflicting is not None:
                    raise ValueError("gateway_payment_id already used by a different purchase")

            ent_cur = conn.execute(
                "INSERT INTO entitlements (user_id, product, status, amount, currency, payment_source, "
                "external_payment_reference, purchased_at, activated_at, purchase_id) "
                "VALUES (?, ?, 'ACTIVE', ?, ?, 'rithavo_web_gateway', ?, ?, ?, ?)",
                (purchase["user_id"], entitlement_product, purchase["amount_inr"], purchase["currency"],
                 gateway_payment_id or gateway_reference, _now(), _now(), purchase_id),
            )
            entitlement_id = ent_cur.lastrowid
            conn.execute(
                "UPDATE purchases SET payment_status = 'SUCCEEDED', gateway_reference = ?, "
                "gateway_payment_id = COALESCE(?, gateway_payment_id), entitlement_id = ?, "
                "updated_at = ? WHERE id = ?",
                (gateway_reference, gateway_payment_id, entitlement_id, _now(), purchase_id),
            )
            return {"purchase_id": purchase_id, "entitlement_id": entitlement_id, "already_confirmed": False}

    def confirm_ad_purchase(self, purchase_id: int, gateway_reference: str, gateway_payment_id: str = None) -> dict:
        """Public entry point for Application Diagnosis — unchanged
        behavior/signature (gateway_payment_id is a new, optional,
        backward-compatible parameter). See _confirm_purchase above."""
        return self._confirm_purchase(purchase_id, gateway_reference, "APPLICATION_DIAGNOSTIC", gateway_payment_id)

    def confirm_ci_purchase(self, purchase_id: int, gateway_reference: str, gateway_payment_id: str = None) -> dict:
        """Public entry point for Career Intelligence. entitlement_product
        is 'career_intelligence' (lowercase, matching the sibling app's
        own PRODUCT_CAREER_INTELLIGENCE constant exactly — app/entitlements.py
        in rithavo-career-profile) rather than this service's own
        uppercase purchases.product convention, so an entitlement created
        here is recognized by the sibling's find_active_entitlement()
        too, via the one shared `entitlements` table — zero sibling code
        touched, just using its existing literal correctly."""
        return self._confirm_purchase(purchase_id, gateway_reference, "career_intelligence", gateway_payment_id)

    def admin_grant_ad_entitlement(self, user_id: int, reason: str = "ADMIN_GRANT", note: str = "") -> dict:
        """A founder/support-issued free entitlement — auditable as its own
        purchase row (payment_status='ADMIN_GRANT', amount 0) but, by
        construction, invisible to count_qualifying_ad_purchases (which
        only ever counts payment_status='SUCCEEDED'). This is the entire
        mechanism by which admin grants are guaranteed never to affect the
        discount ladder — there is no separate exclusion list to keep in
        sync with the pricing query.

        reason (Phase 2A) distinguishes WHY a non-commercial entitlement
        exists (e.g. "ADMIN_GRANT", "TEST_ENTITLEMENT", "INTERNAL_QA") for
        audit purposes, stored in the purchase's own `gateway` column —
        every value is equally excluded from the qualifying count (all of
        them are payment_status='ADMIN_GRANT', never 'SUCCEEDED'), so this
        is a bookkeeping distinction only, never a pricing one. This is
        deliberately NOT a promotional-pricing system."""
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO purchases (user_id, product, amount_inr, currency, qualifying_count_at_purchase, "
                "payment_status, gateway, created_at, updated_at) "
                "VALUES (?, 'APPLICATION_DIAGNOSIS', 0, 'INR', 0, 'ADMIN_GRANT', ?, ?, ?)",
                (user_id, reason, _now(), _now()),
            )
            purchase_id = cur.lastrowid
            ent_cur = conn.execute(
                "INSERT INTO entitlements (user_id, product, status, amount, currency, payment_source, "
                "external_payment_reference, purchased_at, activated_at, purchase_id) "
                "VALUES (?, 'APPLICATION_DIAGNOSTIC', 'ACTIVE', 0, 'INR', 'admin_grant', ?, ?, ?, ?)",
                (user_id, note, _now(), _now(), purchase_id),
            )
            entitlement_id = ent_cur.lastrowid
            conn.execute(
                "UPDATE purchases SET entitlement_id = ? WHERE id = ?", (entitlement_id, purchase_id)
            )
            return {"purchase_id": purchase_id, "entitlement_id": entitlement_id}

    def fail_ad_purchase(self, purchase_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE purchases SET payment_status = 'FAILED', updated_at = ? "
                "WHERE id = ? AND payment_status IN ('CREATED', 'PENDING')",
                (_now(), purchase_id),
            )

    def refund_ad_purchase(self, purchase_id: int, user_id: int = None) -> dict:
        """Refund policy (both branches preserve history, never delete it):
        entitlement not yet consumed -> REVOKE it, purchase -> REFUNDED.
        entitlement already consumed (diagnosis delivered) -> leave the
        entitlement, the diagnosis, and any resume exactly as they are;
        only the purchase's own status changes. Either way the purchase
        drops out of count_qualifying_ad_purchases immediately, since that
        count only ever looks at payment_status = 'SUCCEEDED'.

        user_id (Phase 2A, optional/defense-in-depth): no customer-facing
        HTTP route calls this yet — refunds are an internal/admin
        operation today — but a future admin surface must not be able to
        refund another user's purchase by ID substitution any more than
        any other resource here can. Passing the acting request's own
        user_id enforces that ownership check now, before such a route
        exists, rather than leaving it to be remembered later. Omit (as
        every current, internal caller does) only when the caller has
        already independently established the authority to act on any
        user's purchase (e.g. a verified operator-only tool)."""
        with self.connect() as conn:
            if user_id is not None:
                purchase = conn.execute(
                    "SELECT * FROM purchases WHERE id = ? AND user_id = ?", (purchase_id, user_id)
                ).fetchone()
            else:
                purchase = conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
            if purchase is None:
                raise ValueError("purchase not found")
            if purchase["payment_status"] != "SUCCEEDED":
                raise ValueError(f"cannot refund a purchase in status {purchase['payment_status']}")
            entitlement = None
            if purchase["entitlement_id"] is not None:
                entitlement = conn.execute(
                    "SELECT * FROM entitlements WHERE id = ?", (purchase["entitlement_id"],)
                ).fetchone()
            revoked = False
            if entitlement is not None and entitlement["consumed_at"] is None:
                conn.execute("UPDATE entitlements SET status = 'REVOKED' WHERE id = ?", (entitlement["id"],))
                revoked = True
            conn.execute(
                "UPDATE purchases SET payment_status = 'REFUNDED', refunded_at = ?, updated_at = ? WHERE id = ?",
                (_now(), _now(), purchase_id),
            )
            return {"purchase_id": purchase_id, "entitlement_revoked": revoked}

    # ---- entitlements (SHARED table; this service issues + consumes its
    #      own Application Diagnosis entitlements, never touches CI ones) ----

    def find_active_ad_entitlement(self, user_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM entitlements WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSTIC' "
                "AND status = 'ACTIVE' AND consumed_at IS NULL ORDER BY id LIMIT 1",
                (user_id,),
            ).fetchone()

    def get_entitlement(self, entitlement_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM entitlements WHERE id = ?", (entitlement_id,)).fetchone()

    def reserve_entitlement_for_consumption(self, entitlement_id: int, user_id: int) -> dict:
        """Phase 2A: the standalone, diagnosis-agnostic transactional
        consumption primitive Phase 2B is expected to build on — this
        does NOT create a diagnosis or touch the diagnostics table at
        all, only the entitlement's own consumed_at. (create_ad_diagnosis,
        shipped in Phase 1, already does its own atomic consume-and-
        diagnose in one call for the existing flow — this is a separate,
        narrower primitive for Phase 2B to use however it ends up wiring
        consumption to the real diagnosis workflow, not a replacement.)

        Correctness: on Postgres, `SELECT ... FOR UPDATE` takes a real
        row-level lock on this exact entitlement row for the duration of
        the transaction — a second concurrent call attempting the same
        SELECT ... FOR UPDATE blocks until the first transaction commits,
        then correctly observes consumed_at already set. This is the
        textbook-correct tool for "lock one already-existing row before
        mutating it" (as opposed to the advisory locks used elsewhere in
        this file for "serialize access to a not-yet-existing row's
        slot", e.g. the next purchase for a user). Ownership is enforced
        in the same query — a mismatched user_id makes the row
        invisible, indistinguishable from a nonexistent id, before any
        lock would even be requested on it.

        Belt-and-suspenders even without FOR UPDATE (SQLite has no
        equivalent): the final UPDATE's own `WHERE consumed_at IS NULL`
        guard is checked via `rowcount`, not the earlier SELECT — so even
        if two threads' SELECTs both raced past the NULL check, only the
        UPDATE that actually flips the row from NULL wins; the other
        sees rowcount=0 and correctly reports failure instead of
        claiming success it didn't earn.

        This function is a thin wrapper: the actual lock+check+mark-
        consumed logic lives in _reserve_entitlement_locked so it can be
        reused, unchanged, from inside create_ad_diagnosis's own larger
        transaction (Phase 2B-1) — the hard rule being that entitlement
        consumption must never be reimplemented a second time anywhere."""
        with self.connect() as conn:
            return self._reserve_entitlement_locked(conn, entitlement_id, user_id)

    def _reserve_entitlement_locked(self, conn, entitlement_id: int, user_id: int) -> dict:
        """The actual lock+check+mark-consumed core, operating on an
        ALREADY-OPEN connection/transaction — the caller decides whether
        and when to commit. See reserve_entitlement_for_consumption for
        the correctness argument (SELECT...FOR UPDATE on Postgres,
        rowcount-checked UPDATE on both backends)."""
        lock_suffix = " FOR UPDATE" if self._is_postgres else ""
        entitlement = conn.execute(
            f"SELECT * FROM entitlements WHERE id = ? AND user_id = ?{lock_suffix}",
            (entitlement_id, user_id),
        ).fetchone()
        if entitlement is None:
            return {"success": False, "reason": "not_found"}
        if entitlement["status"] != "ACTIVE":
            return {"success": False, "reason": "not_active"}
        if entitlement["consumed_at"] is not None:
            return {"success": False, "reason": "already_consumed"}
        cur = conn.execute(
            "UPDATE entitlements SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
            (_now(), entitlement_id),
        )
        if cur.rowcount != 1:
            return {"success": False, "reason": "already_consumed"}
        return {"success": True, "reason": "consumed"}

    # ---- diagnostics / diagnostic_findings (SHARED, previously-dormant
    #      tables; this service is their first live writer) ----

    def create_ad_diagnosis(self, user_id: int, entitlement_id: int, ai_run_id: str, target_jd_text: str,
                             overall_score: int, findings: list, route: str = "EXTERNAL_JD",
                             job_id: int = None, idempotency_key: str = None) -> dict:
        """Atomically consumes the given entitlement and creates the
        diagnosis + its findings, ALL in one transaction (one `with
        self.connect()` block — on Postgres this is one real database
        transaction, committed once at the end or rolled back entirely on
        any exception). This is the atomicity guarantee Phase 2B-1
        requires: the entitlement's consumed_at flip and the diagnostics/
        diagnostic_findings inserts either all land together or none of
        them do — there is no window where the entitlement is consumed
        but no diagnosis exists, or a diagnosis exists but the entitlement
        remains reusable.

        Entitlement consumption itself is NOT reimplemented here — it
        delegates to _reserve_entitlement_locked, the exact same
        lock+check+mark-consumed core reserve_entitlement_for_consumption
        uses on its own, just run inside THIS call's transaction instead
        of its own. Two concurrent submissions using the same entitlement
        (same JD or different JDs) can't both pass that check before
        either commits.

        idempotency_key (Phase 2B-1): if supplied and a diagnosis with
        that exact key already exists for this user, that diagnosis's id
        is returned unchanged — no new row, no re-attempted consumption —
        so a client retry after a lost response can never double-consume
        the entitlement or create a second diagnosis. Checked before the
        entitlement lock is even requested."""
        with self.connect() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT id FROM diagnostics WHERE profile_id = ? AND idempotency_key = ?",
                    (user_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return {"diagnostic_id": existing["id"], "replayed": True}

            reservation = self._reserve_entitlement_locked(conn, entitlement_id, user_id)
            if not reservation["success"]:
                # Genuine concurrent retry (not merely sequential): a
                # second request with the SAME idempotency_key arrived
                # before the first had committed, so the early check above
                # missed it. _reserve_entitlement_locked's SELECT...FOR
                # UPDATE only just unblocked because the FIRST request's
                # transaction committed — meaning if that's what happened
                # here, its diagnostics row is now visible. Check once
                # more before concluding this is a real failure, so both
                # concurrent callers end up returning the SAME successful
                # diagnostic_id instead of one succeeding and one erroring.
                if idempotency_key:
                    existing = conn.execute(
                        "SELECT id FROM diagnostics WHERE profile_id = ? AND idempotency_key = ?",
                        (user_id, idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        return {"diagnostic_id": existing["id"], "replayed": True}
                raise ValueError(f"entitlement not available ({reservation['reason']})")

            cur = conn.execute(
                "INSERT INTO diagnostics (profile_id, ai_run_id, ai_model, target_role_text, target_jd_text, "
                "overall_score, source_order_status, diagnostic_version, prompt_rubric_version, created_at, "
                "job_id, entitlement_id, route, idempotency_key) "
                "VALUES (?, ?, 'rule-based-v1', '', ?, ?, 'PAID', 'ad-v1', 'ad-rubric-v1', ?, ?, ?, ?, ?)",
                (user_id, ai_run_id, target_jd_text, overall_score, _now(), job_id, entitlement_id, route,
                 idempotency_key),
            )
            diagnostic_id = cur.lastrowid
            for f in findings:
                conn.execute(
                    "INSERT INTO diagnostic_findings (diagnostic_id, dimension, finding_text, "
                    "evidence_excerpt, classification) VALUES (?, ?, ?, ?, ?)",
                    (diagnostic_id, f["dimension"], f["finding_text"], f.get("evidence_excerpt", ""),
                     f["classification"]),
                )
            conn.execute(
                "UPDATE entitlements SET diagnostic_id = ? WHERE id = ?",
                (diagnostic_id, entitlement_id),
            )
            return {"diagnostic_id": diagnostic_id, "replayed": False}

    def get_diagnosis(self, diagnostic_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM diagnostics WHERE id = ?", (diagnostic_id,)).fetchone()

    def get_diagnosis_by_idempotency_key(self, user_id: int, idempotency_key: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM diagnostics WHERE profile_id = ? AND idempotency_key = ?",
                (user_id, idempotency_key),
            ).fetchone()

    def list_diagnoses_for_user(self, user_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM diagnostics WHERE profile_id = ? ORDER BY id DESC", (user_id,)
            ).fetchall()

    def list_findings_for_diagnosis(self, diagnostic_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM diagnostic_findings WHERE diagnostic_id = ? ORDER BY id", (diagnostic_id,)
            ).fetchall()

    # ---- resumes (SHARED table; immutable/versioned rows for the
    #      per-diagnosis tailored resume — never UPDATEd, only INSERTed) ----

    def create_resume_for_diagnosis(self, user_id: int, diagnostic_id: int, content: dict, label: str) -> int:
        import json
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO resumes (profile_id, target_context, source_file_ref, label, "
                "linked_diagnostic_ids, created_at, content_json, diagnostic_id, resume_context) "
                "VALUES (?, ?, '', ?, ?, ?, ?, ?, 'JOB_DIAGNOSIS')",
                (user_id, content.get("target_context", ""), label, json.dumps([diagnostic_id]), _now(),
                 json.dumps(content), diagnostic_id),
            )
            return cur.lastrowid

    def create_resume_for_career_intelligence(self, user_id: int, content: dict, label: str, resume_context: str) -> int:
        """Phase 2B-1.7A. resume_context is always 'SAME_CAREER' or
        'NEW_TARGET' here — 'JOB_DIAGNOSIS' only ever comes from
        create_resume_for_diagnosis above. Deliberately no diagnostic_id
        (this was never an Application Diagnosis) and no entitlement/
        purchase touched anywhere in this call path — a Career
        Intelligence resume is not gated by this service's own
        commerce tables at all."""
        import json
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO resumes (profile_id, target_context, source_file_ref, label, "
                "linked_diagnostic_ids, created_at, content_json, resume_context) "
                "VALUES (?, ?, '', ?, '[]', ?, ?, ?)",
                (user_id, content.get("target_context", ""), label, _now(), json.dumps(content), resume_context),
            )
            return cur.lastrowid

    def list_resumes_for_user_and_target(self, user_id: int, target_context: str) -> list:
        """Scopes CI resume versioning by (user, target label) — the
        closest available equivalent to list_resumes_for_diagnosis's
        per-diagnosis scoping, since a Career Intelligence resume has no
        diagnostic_id to key off."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM resumes WHERE profile_id = ? AND target_context = ? ORDER BY id",
                (user_id, target_context),
            ).fetchall()

    def get_resume(self, resume_id: int):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()

    def list_resumes_for_diagnosis(self, diagnostic_id: int) -> list:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM resumes WHERE diagnostic_id = ? ORDER BY id", (diagnostic_id,)
            ).fetchall()

    def list_resumes_for_user(self, user_id: int) -> list:
        """Phase 2B-1.7A: every resume row for this profile, not just the
        ones tied to an Application Diagnosis — the "Resumes" history tab
        needs the full picture. No new table, no schema change: `resumes`
        is already declared above. A row this service didn't create (e.g.
        one generated by the sibling app's Career Intelligence product)
        may have diagnostic_id/content_json NULL — callers must not
        assume every row is downloadable through this service's own
        /resume/{id}/download (that renders content_json, a column only
        this service's own writer populates)."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM resumes WHERE profile_id = ? ORDER BY id DESC", (user_id,)
            ).fetchall()
