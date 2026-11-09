"""
Synthetic CRM data for Preview and local development ONLY. Fictional names, `.test`
addresses (reserved, never routable). Refuses to run against anything that could be
Production, and never reads or copies real customer or lead data.
"""

from datetime import date, datetime, timedelta, timezone

import config

from ..auth import hash_password
from . import rls, store
from .access import Actor

FOUNDER_EMAIL = "founder.preview@example.test"
SPECIALISTS = (
    ("aarav.preview@example.test", "Aarav (synthetic)"),
    ("meera.preview@example.test", "Meera (synthetic)"),
    ("kabir.preview@example.test", "Kabir (synthetic)"),
    ("diya.preview@example.test", "Diya (synthetic)"),
)


class SeedRefused(RuntimeError):
    pass


def _guard(db):
    if db._is_postgres and not config.ISOLATED_DEPLOYMENT and not getattr(config, "CRM_ALLOW_SCHEMA_ON_POSTGRES", False):
        raise SeedRefused("Refusing to seed a non-isolated Postgres database.")
    if config.DATABASE_URL and not config.ISOLATED_DEPLOYMENT:
        raise SeedRefused("Refusing to seed while a production database URL is configured.")


def seed_synthetic(db, password: str):
    _guard(db)
    # Seeding writes rows for several owners, so it runs with the Founder database context (a no-op on SQLite and
    # on an owner connection). The context is reset afterwards so it can never leak into request handling.
    token = rls.CURRENT.set((0, "founder"))
    try:
        return _seed(db, password)
    finally:
        rls.CURRENT.reset(token)


def _seed(db, password: str):
    if not password or len(password) < 12:
        raise SeedRefused("A password of at least 12 characters is required.")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM crm_staff LIMIT 1").fetchone():
            return {"seeded": False, "reason": "already seeded"}
    pw = hash_password(password)
    fid = db.get_or_create_user(FOUNDER_EMAIL)
    db.set_user_password(fid, pw)
    db.add_super_admin_if_missing(fid)
    founder = Actor(fid, "founder", FOUNDER_EMAIL, "Founder", True)
    ids = {}
    for email, name in SPECIALISTS:
        uid = db.get_or_create_user(email)
        db.set_user_password(uid, pw)
        store.activate_staff(db, founder, email, name)
        ids[email] = Actor(uid, "specialist", email, name, True)
    a, m, k, d = (ids[e] for e, _ in SPECIALISTS)
    spec = [
        (a, "Cedar Valley Textiles", "Nisha Rao", "Brand and storefront refresh", "product_studio", "referral"),
        (a, "Lantern Health Collective", "Dr. Omkar Patil", "Operations playbook", "business_transformation", "event"),
        (m, "Riverbend Logistics", "Sana Qureshi", "Dispatch dashboard", "product_studio", "website"),
        (m, "Tamarind Learning Labs", "Vikram Shah", "Mentor programme design", "career", "partner"),
        (k, "Pinecrest Finance", "Leela Iyer", "Process automation", "business_transformation", "outreach"),
        (d, "Saffron Row Foods", "Arjun Mehta", "Ordering app", "product_studio", "referral"),
    ]
    made = []
    for actor, company, contact, title, pillar, source in spec:
        r = store.capture_lead(db, actor, {
            "company_name": company, "contact_name": contact, "contact_email": f"{contact.split()[-1].lower()}@{company.split()[0].lower()}.example.test",
            "title": title, "service_pillar": pillar, "source": source})
        made.append((actor, r))
    today = date.today()
    # Varied, realistic states.
    ca, ra = made[0]
    store.add_activity(db, ca, ra["id"], {"kind": "call", "summary": "Intro call: confirmed the storefront scope and budget range", "meaningful": True})
    store.add_followup(db, ca, ra["id"], {"due_date": (today + timedelta(days=2)).isoformat(), "note": "Send scope outline"})
    store.move_stage(db, ca, ra["id"], "Contacted")
    cb, rb = made[1]
    store.add_followup(db, cb, rb["id"], {"due_date": (today - timedelta(days=3)).isoformat(), "note": "Chase for the workshop date"})
    cc, rc = made[2]
    store.add_activity(db, cc, rc["id"], {"kind": "meeting", "summary": "Workshop with the dispatch lead, agreed the pilot lane", "meaningful": True})
    store.add_followup(db, cc, rc["id"], {"due_date": today.isoformat(), "note": "Share pilot plan"})
    store.move_stage(db, cc, rc["id"], "Contacted")
    cd, rd = made[3]
    store.set_on_hold(db, cd, rd["id"], "budget_or_timing", "Client re-plans budget next quarter", (today + timedelta(days=45)).isoformat())
    ce, re_ = made[4]
    store.set_lost(db, ce, re_["id"], "chose_another_provider", "Went with an in-house team")
    # One record with no meaningful contact for 100 days (the 90-day review queue).
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    cf, rf = made[5]
    with db.connect() as conn:
        conn.execute("UPDATE crm_opportunities SET created_at = ? WHERE id = ?", (old, rf["id"]))
    return {"seeded": True, "founder": FOUNDER_EMAIL, "specialists": [e for e, _ in SPECIALISTS]}
