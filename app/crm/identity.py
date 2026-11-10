"""
Who is this person, as far as the CRM is concerned (scope 2.3, 2.8). The ONLY module that reads the shared
`users` and `super_admins` tables (so a later dedicated CRM connection with column-limited grants stays small):
`users(id, email)` and `super_admins(user_id)`. Never the customer password columns.

Founder = a Super Admin row AND an email on the Founder allowlist. Specialist = a `crm_staff` row.
Account state (specialists), derived from the database on every request:
  invited -> credentialed -> awaiting_activation -> active, or deactivated.
"""

from dataclasses import dataclass
from typing import Optional

import config

from . import credentials, mfa


@dataclass
class Identity:
    user_id: int
    email: str
    role: Optional[str]            # "founder" | "specialist" | None
    state: Optional[str]           # specialists only: see module docstring; "founder" for the Founder
    display_name: str
    staff: Optional[dict] = None


def user_by_email(db, email):
    with db.connect() as conn:
        return conn.execute("SELECT id, email FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()


def user_id_by_email(conn, email):
    r = conn.execute("SELECT id FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
    return r["id"] if r else None


def user_by_id(conn, user_id):
    return conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()


def ensure_user(db, email):
    """The existing identity creation path: inserts one users row (email, created time) and nothing else."""
    return db.get_or_create_user(email)


def is_allowlisted(email):
    return (email or "").strip().lower() in config.CRM_FOUNDER_EMAILS


def is_super_admin(conn, user_id):
    return conn.execute("SELECT 1 FROM super_admins WHERE user_id = ?", (user_id,)).fetchone() is not None


def is_founder(conn, user_id, email=None):
    if email is None:
        u = user_by_id(conn, user_id)
        email = u["email"] if u else None
    return bool(email) and is_allowlisted(email) and is_super_admin(conn, user_id)


def founder_ids(conn):
    rows = conn.execute("SELECT u.id AS id, u.email AS email FROM super_admins sa JOIN users u ON u.id = sa.user_id").fetchall()
    return [r["id"] for r in rows if is_allowlisted(r["email"])]


def staff_state(conn, user_id):
    s = conn.execute("SELECT * FROM crm_staff WHERE user_id = ?", (user_id,)).fetchone()
    if s is None:
        return None, None
    if s["active"]:
        return s, "active"
    if s["deactivated_at"]:
        return s, "deactivated"
    if not credentials.has_password(credentials.get(conn, user_id)):
        return s, "invited"
    return s, ("awaiting_activation" if mfa.is_enrolled(mfa.get_row(conn, user_id)) else "credentialed")


def resolve(conn, user_id):
    u = user_by_id(conn, user_id)
    if u is None:
        return None
    if is_founder(conn, user_id, u["email"]):
        return Identity(user_id, u["email"], "founder", "founder", "Founder")
    s, state = staff_state(conn, user_id)
    if s is None:
        return Identity(user_id, u["email"], None, None, "")
    return Identity(user_id, u["email"], "specialist", state, s["display_name"], dict(s))
