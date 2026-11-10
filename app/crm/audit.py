"""Append-only audit writes for the CRM authentication layer (no business logic, no imports from store)."""

from datetime import datetime, timezone


def write(conn, actor_user_id, actor_role, action, entity=None, entity_id=None, outcome="ok", detail=None):
    # `ON CONFLICT DO NOTHING` (no conflict target; the table has no unique key besides id) makes the database layer
    # skip its automatic `RETURNING id`: under row-level security the returned row must also pass the audit SELECT
    # policy, which hides rows from a caller with no actor context (sign-in, invitation acceptance, throttle events).
    conn.execute(
        "INSERT INTO crm_audit_log (at, actor_user_id, actor_role, action, entity, entity_id, outcome, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (datetime.now(timezone.utc).isoformat(), actor_user_id, actor_role, action, entity, entity_id, outcome, detail))


def write_own(db, *args, **kwargs):
    """On its own connection so the row survives the exception that rolls back the request's transaction."""
    try:
        with db.connect() as conn:
            write(conn, *args, **kwargs)
    except Exception:
        pass
