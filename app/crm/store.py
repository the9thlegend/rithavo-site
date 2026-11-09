"""
CRM Lite business logic. Every function takes the authenticated `Actor`; every
read of a single record goes through `Scope.fetch`, every list through
`Scope.clause`. Every mutation writes its audit row in the same transaction.
Audit `detail` carries codes and ids only, never names, emails or free text.
"""

import re
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException

from . import mfa
from .access import Actor, Scope, audit_denied

STAGES = ["New Lead", "Contacted", "Qualified", "Discovery", "Founder Review", "Requirement Assessment",
          "Proposal Preparation", "Proposal Sent", "Negotiation", "Won", "Lost", "On Hold"]
CLOSED = ("Won", "Lost")
NEXT_STAGE = {
    "New Lead": "Contacted", "Contacted": "Qualified", "Qualified": "Discovery", "Discovery": "Founder Review",
    "Requirement Assessment": "Proposal Preparation", "Proposal Preparation": "Proposal Sent",
    "Proposal Sent": "Negotiation", "Negotiation": "Won",
}
PILLARS = ("career", "product_studio", "business_transformation")
SOURCES = ("referral", "website", "event", "outreach", "partner", "other")
LOST_REASONS = {
    "no_response": "No response", "not_a_fit": "Not a fit", "budget": "Budget",
    "chose_another_provider": "Chose another provider", "timing": "Timing",
    "duplicate_or_invalid": "Duplicate or invalid", "other": "Other",
}
HOLD_REASONS = {
    "client_requested_pause": "Client requested pause", "awaiting_client_information": "Awaiting client information",
    "budget_or_timing": "Budget or timing", "founder_decision_pending": "Founder decision pending", "other": "Other",
}
ACTIVITY_KINDS = ("note", "call", "email", "whatsapp", "meeting", "assessment")
MEANINGFUL_KINDS = ("call", "email", "whatsapp", "meeting")
NO_CONTACT_REVIEW_DAYS = 90
RETENTION_MONTHS = 12
MASKED_MESSAGE = "A matching record already exists and is owned by someone else. Ask the Founder."


# ---- helpers ----------------------------------------------------------------

def now_dt():
    return datetime.now(timezone.utc)


def now_iso():
    return now_dt().isoformat()


def d(row):
    return dict(row) if row is not None else None


def bad(msg, status=400):
    return HTTPException(status_code=status, detail=msg)


def text(value, name, *, maxlen=500, required=True, minlen=1):
    value = (value or "")
    if not isinstance(value, str):
        raise bad(f"{name} is not valid.")
    value = value.strip()
    if required and len(value) < minlen:
        raise bad(f"{name} is required." if minlen <= 1 else f"{name} needs at least {minlen} characters.")
    if len(value) > maxlen:
        raise bad(f"{name} is too long.")
    return value or None


def norm_key(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def parse_when(value, name="occurred_at"):
    if not value:
        return now_dt()
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise bad(f"{name} is not a valid date and time.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt > now_dt() + timedelta(minutes=5):
        raise bad(f"{name} cannot be in the future.")
    return dt


def parse_day(value, name):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise bad(f"{name} must be a date (YYYY-MM-DD).")


def audit(conn, actor, action, entity=None, entity_id=None, detail=None, outcome="ok"):
    conn.execute(
        "INSERT INTO crm_audit_log (at, actor_user_id, actor_role, action, entity, entity_id, outcome, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), actor.user_id if actor else None, actor.role if actor else None, action, entity, entity_id,
         outcome, detail))


def add_months(dt: datetime, months: int) -> datetime:
    y, m = divmod(dt.month - 1 + months, 12)
    year, month = dt.year + y, m + 1
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31,
                       31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def _owner_label_map(conn):
    out = {}
    for r in conn.execute("SELECT s.user_id, s.display_name FROM crm_staff s").fetchall():
        out[r["user_id"]] = r["display_name"]
    for r in conn.execute("SELECT user_id FROM super_admins").fetchall():
        out[r["user_id"]] = "Founder"
    return out


def _valid_owner(conn, user_id):
    if conn.execute("SELECT 1 FROM super_admins WHERE user_id = ?", (user_id,)).fetchone():
        return True
    return conn.execute("SELECT 1 FROM crm_staff WHERE user_id = ? AND active = 1", (user_id,)).fetchone() is not None


# ---- duplicates -------------------------------------------------------------

def find_matches(conn, name_key, email_key):
    """All records anywhere that match. INTERNAL: callers decide what may be revealed."""
    comps = []
    if name_key:
        comps = conn.execute("SELECT id, name, owner_user_id FROM crm_companies WHERE name_key = ? AND archived = 0",
                             (name_key,)).fetchall()
    conts = []
    if email_key:
        conts = conn.execute(
            "SELECT c.id, c.company_id, c.owner_user_id, co.name AS company_name FROM crm_contacts c "
            "JOIN crm_companies co ON co.id = c.company_id WHERE c.email_key = ? AND c.archived = 0",
            (email_key,)).fetchall()
    return comps, conts


def duplicate_outcome(conn, actor, company_name, email):
    """None if no duplicate; otherwise the HTTPException to raise. A specialist never
    learns anything about a record they do not own beyond the fixed message."""
    comps, conts = find_matches(conn, norm_key(company_name), (email or "").strip().lower())
    if not comps and not conts:
        return None
    if actor.is_founder:
        first = (comps or conts)[0]
        cid = first["id"] if comps else first["company_id"]
        return HTTPException(status_code=409, detail={
            "code": "duplicate_found", "company_id": cid, "message": "A matching company or contact already exists."})
    own_c = [c for c in comps if c["owner_user_id"] == actor.user_id]
    own_k = [c for c in conts if c["owner_user_id"] == actor.user_id]
    foreign = len(comps) - len(own_c) + len(conts) - len(own_k)
    if foreign:
        return HTTPException(status_code=409, detail={"code": "duplicate_masked", "message": MASKED_MESSAGE})
    cid = own_c[0]["id"] if own_c else own_k[0]["company_id"]
    return HTTPException(status_code=409, detail={
        "code": "duplicate_own", "company_id": cid, "message": "You already have a matching company or contact."})


def check_duplicate(db, actor, company_name, email):
    with db.connect() as conn:
        exc = duplicate_outcome(conn, actor, company_name or "", email or "")
    if exc is None:
        return {"duplicate": False}
    return {"duplicate": True, **exc.detail}


# ---- capture ----------------------------------------------------------------

def _next_ref(conn):
    year = now_dt().year
    row = conn.execute("SELECT ref FROM crm_opportunities WHERE ref LIKE ? ORDER BY ref DESC LIMIT 1",
                       (f"RIT-{year}-%",)).fetchone()
    n = int(row["ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"RIT-{year}-{n:03d}"


def capture_lead(db, actor, payload):
    """Company + contact + opportunity in one transaction. A lead IS an opportunity in New Lead."""
    company_id = payload.get("company_id")
    title = text(payload.get("title"), "Title", maxlen=160)
    pillar = payload.get("service_pillar")
    source = payload.get("source")
    if pillar not in PILLARS:
        raise bad("Choose a service pillar.")
    if source not in SOURCES:
        raise bad("Choose a source.")
    contact_name = text(payload.get("contact_name"), "Contact name", maxlen=120)
    email = text(payload.get("contact_email"), "Contact email", maxlen=200, required=False)
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise bad("Contact email is not valid.")
    for attempt in range(3):
        try:
            return _capture_once(db, actor, payload, company_id, title, pillar, source, contact_name, email)
        except HTTPException:
            raise
        except Exception as e:  # unique ref collision on a concurrent capture: retry
            if attempt == 2 or "ref" not in str(e).lower() and "unique" not in str(e).lower():
                raise
    raise bad("Could not allocate a reference. Try again.", 409)


def _capture_once(db, actor, payload, company_id, title, pillar, source, contact_name, email):
    t = now_iso()
    with db.connect() as conn:
        scope = Scope(db, actor)
        if company_id:
            company = scope.fetch(conn, "crm_companies", int(company_id), "company")
            if company["archived"]:
                raise bad("That company is archived.", 409)
            owner = company["owner_user_id"]
            cid = company["id"]
        else:
            name = text(payload.get("company_name"), "Company name", maxlen=160)
            exc = duplicate_outcome(conn, actor, name, email or "")
            if exc is not None:
                audit(conn, actor, "capture_duplicate", "company", None, exc.detail["code"], outcome="blocked")
                conn.commit()  # keep the 'blocked' audit row; the raise below would otherwise discard it
                raise exc
            owner = actor.user_id
            cur = conn.execute(
                "INSERT INTO crm_companies (owner_user_id, name, name_key, website, industry, created_by, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (owner, name, norm_key(name), text(payload.get("website"), "Website", maxlen=200, required=False),
                 text(payload.get("industry"), "Industry", maxlen=100, required=False), actor.user_id, t, t))
            cid = cur.lastrowid
        if company_id and email:
            comps, conts = find_matches(conn, "", email.lower())
            if conts:
                raise bad("A contact with that email already exists.", 409)
        k = conn.execute(
            "INSERT INTO crm_contacts (company_id, owner_user_id, full_name, role_title, email, email_key, phone, "
            "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, owner, contact_name, text(payload.get("contact_role"), "Role", maxlen=120, required=False), email,
             email.lower() if email else None, text(payload.get("contact_phone"), "Phone", maxlen=40, required=False),
             actor.user_id, t, t))
        ref = _next_ref(conn)
        o = conn.execute(
            "INSERT INTO crm_opportunities (ref, company_id, contact_id, owner_user_id, title, service_pillar, source, "
            "stage, stage_changed_at, requirement_summary, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'New Lead', ?, NULL, ?, ?, ?)",
            (ref, cid, k.lastrowid, owner, title, pillar, source, t, actor.user_id, t, t))
        audit(conn, actor, "opportunity_created", "opportunity", o.lastrowid, "stage=New Lead")
        return {"id": o.lastrowid, "ref": ref, "company_id": cid, "contact_id": k.lastrowid}


# ---- reads ------------------------------------------------------------------

def _opp_row(r, labels):
    x = dict(r)
    x["owner_label"] = labels.get(x["owner_user_id"], "Needs owner")
    return x


def list_opportunities(db, actor, stage=None, q=None, limit=200):
    scope = Scope(db, actor)
    where, params = scope.clause("o")
    sql = ("SELECT o.*, c.name AS company_name, k.full_name AS contact_name FROM crm_opportunities o "
           "JOIN crm_companies c ON c.id = o.company_id LEFT JOIN crm_contacts k ON k.id = o.contact_id "
           f"WHERE {where} AND o.archived = 0")
    if stage:
        if stage not in STAGES:
            raise bad("Unknown stage.")
        sql += " AND o.stage = ?"
        params = (*params, stage)
    sql += " ORDER BY o.updated_at DESC LIMIT ?"
    with db.connect() as conn:
        labels = _owner_label_map(conn)
        return [_opp_row(r, labels) for r in conn.execute(sql, (*params, int(limit))).fetchall()]


def get_opportunity(db, actor, opp_id):
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        labels = _owner_label_map(conn)
        out = _opp_row(o, labels)
        out["company"] = d(conn.execute("SELECT id, name, website, industry FROM crm_companies WHERE id = ?",
                                        (o["company_id"],)).fetchone())
        out["contact"] = d(conn.execute("SELECT id, full_name, role_title, email, phone FROM crm_contacts WHERE id = ?",
                                        (o["contact_id"],)).fetchone()) if o["contact_id"] else None
        out["activities"] = [dict(r) for r in conn.execute(
            "SELECT id, kind, summary, meaningful, occurred_at, created_by FROM crm_activities "
            "WHERE opportunity_id = ? ORDER BY occurred_at DESC, id DESC", (opp_id,)).fetchall()]
        out["followups"] = [dict(r) for r in conn.execute(
            "SELECT id, due_date, note, done_at FROM crm_followups WHERE opportunity_id = ? ORDER BY due_date",
            (opp_id,)).fetchall()]
        out["reviews"] = [dict(r) for r in conn.execute(
            "SELECT id, kind, state, requested_at, request_note, decided_at, decision, decision_note "
            "FROM crm_founder_reviews WHERE opportunity_id = ? ORDER BY id DESC", (opp_id,)).fetchall()]
        out["allowed_next"] = _allowed_next(actor, o)
        return out


def _allowed_next(actor, o):
    st = o["stage"]
    if st in CLOSED or st == "On Hold" or o["archived"]:
        return []
    if st == "Founder Review":
        return []
    nxt = NEXT_STAGE.get(st)
    if st == "Negotiation" or st == "Proposal Sent":
        return [x for x in (nxt, "Won") if x and (x != "Won" or actor.is_founder)]
    return [nxt] if nxt else []


def list_companies(db, actor, archived=False):
    scope = Scope(db, actor)
    where, params = scope.clause("c")
    with db.connect() as conn:
        labels = _owner_label_map(conn)
        rows = conn.execute(
            f"SELECT c.*, (SELECT COUNT(*) FROM crm_opportunities o WHERE o.company_id = c.id AND o.archived = 0) "
            f"AS opportunity_count, (SELECT COUNT(*) FROM crm_contacts k WHERE k.company_id = c.id AND k.archived = 0) "
            f"AS contact_count FROM crm_companies c WHERE {where} AND c.archived = ? ORDER BY c.name",
            (*params, 1 if archived else 0)).fetchall()
        return [_opp_row(r, labels) for r in rows]


def get_company(db, actor, company_id):
    scope = Scope(db, actor)
    with db.connect() as conn:
        c = scope.fetch(conn, "crm_companies", company_id, "company")
        labels = _owner_label_map(conn)
        out = _opp_row(c, labels)
        out["contacts"] = [dict(r) for r in conn.execute(
            "SELECT id, full_name, role_title, email, phone, last_meaningful_contact_at FROM crm_contacts "
            "WHERE company_id = ? AND archived = 0 ORDER BY full_name", (company_id,)).fetchall()]
        out["opportunities"] = [dict(r) for r in conn.execute(
            "SELECT id, ref, title, stage, updated_at FROM crm_opportunities WHERE company_id = ? AND archived = 0 "
            "ORDER BY updated_at DESC", (company_id,)).fetchall()]
        return out


def list_contacts(db, actor):
    scope = Scope(db, actor)
    where, params = scope.clause("k")
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT k.id, k.full_name, k.role_title, k.email, k.phone, k.company_id, c.name AS company_name, "
            f"k.last_meaningful_contact_at FROM crm_contacts k JOIN crm_companies c ON c.id = k.company_id "
            f"WHERE {where} AND k.archived = 0 ORDER BY k.full_name", params).fetchall()]


def add_contact(db, actor, company_id, payload):
    scope = Scope(db, actor)
    name = text(payload.get("full_name"), "Name", maxlen=120)
    email = text(payload.get("email"), "Email", maxlen=200, required=False)
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise bad("Email is not valid.")
    with db.connect() as conn:
        c = scope.fetch(conn, "crm_companies", company_id, "company")
        if email:
            _, conts = find_matches(conn, "", email.lower())
            if conts:
                raise bad("A contact with that email already exists.", 409)
        t = now_iso()
        cur = conn.execute(
            "INSERT INTO crm_contacts (company_id, owner_user_id, full_name, role_title, email, email_key, phone, "
            "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company_id, c["owner_user_id"], name, text(payload.get("role_title"), "Role", maxlen=120, required=False),
             email, email.lower() if email else None, text(payload.get("phone"), "Phone", maxlen=40, required=False),
             actor.user_id, t, t))
        audit(conn, actor, "contact_created", "contact", cur.lastrowid)
        return {"id": cur.lastrowid}


def update_opportunity(db, actor, opp_id, payload):
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["stage"] in CLOSED or o["archived"]:
            raise bad("This opportunity is closed.", 409)
        sets, vals = [], []
        if "title" in payload:
            sets.append("title = ?"); vals.append(text(payload["title"], "Title", maxlen=160))
        if "requirement_summary" in payload:
            sets.append("requirement_summary = ?")
            vals.append(text(payload["requirement_summary"], "Requirement summary", maxlen=4000, required=False))
        if "estimated_value_inr" in payload:
            v = payload["estimated_value_inr"]
            if v is not None and (not isinstance(v, int) or v < 0 or v > 10**10):
                raise bad("Estimated value is not valid.")
            sets.append("estimated_value_inr = ?"); vals.append(v)
        if "service_pillar" in payload:
            if payload["service_pillar"] not in PILLARS:
                raise bad("Choose a service pillar.")
            sets.append("service_pillar = ?"); vals.append(payload["service_pillar"])
        if "source" in payload:
            if payload["source"] not in SOURCES:
                raise bad("Choose a source.")
            sets.append("source = ?"); vals.append(payload["source"])
        if not sets:
            raise bad("Nothing to update.")
        sets.append("updated_at = ?"); vals.append(now_iso())
        conn.execute(f"UPDATE crm_opportunities SET {', '.join(sets)} WHERE id = ?", (*vals, opp_id))
        audit(conn, actor, "opportunity_updated", "opportunity", opp_id, ",".join(s.split(" ")[0] for s in sets[:-1]))
        return {"ok": True}


# ---- activities and follow-ups ----------------------------------------------

def _system_activity(conn, o, actor, kind, summary):
    t = now_iso()
    conn.execute(
        "INSERT INTO crm_activities (opportunity_id, company_id, contact_id, kind, summary, meaningful, occurred_at, "
        "created_by, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (o["id"], o["company_id"], o["contact_id"], kind, summary, t, actor.user_id, t))


def add_activity(db, actor, opp_id, payload):
    kind = payload.get("kind")
    if kind not in ACTIVITY_KINDS:
        raise bad("Choose an activity type.")
    meaningful = bool(payload.get("meaningful"))
    summary = text(payload.get("summary"), "Summary", maxlen=2000, minlen=3)
    if meaningful:
        if kind not in MEANINGFUL_KINDS:
            raise bad("Only a call, email, WhatsApp or meeting can count as meaningful contact.")
        if len(summary) < 20:
            raise bad("Describe what was discussed (at least 20 characters) for a meaningful contact.")
    when = parse_when(payload.get("occurred_at"))
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["archived"]:
            raise bad("This opportunity is archived.", 409)
        cur = conn.execute(
            "INSERT INTO crm_activities (opportunity_id, company_id, contact_id, kind, summary, meaningful, occurred_at, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (opp_id, o["company_id"], o["contact_id"], kind, summary, 1 if meaningful else 0, when.isoformat(),
             actor.user_id, now_iso()))
        if meaningful:
            w = when.isoformat()
            for table, rid in (("crm_opportunities", opp_id), ("crm_companies", o["company_id"]),
                               ("crm_contacts", o["contact_id"])):
                if rid is None:
                    continue
                conn.execute(
                    f"UPDATE {table} SET last_meaningful_contact_at = ? WHERE id = ? AND "
                    f"(last_meaningful_contact_at IS NULL OR last_meaningful_contact_at < ?)", (w, rid, w))
        conn.execute("UPDATE crm_opportunities SET updated_at = ? WHERE id = ?", (now_iso(), opp_id))
        audit(conn, actor, "activity_added", "opportunity", opp_id, f"kind={kind};meaningful={int(meaningful)}")
        return {"id": cur.lastrowid}


def add_followup(db, actor, opp_id, payload):
    due = parse_day(payload.get("due_date"), "Due date")
    note = text(payload.get("note"), "Note", maxlen=500, required=False)
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["stage"] in CLOSED or o["archived"]:
            raise bad("This opportunity is closed.", 409)
        cur = conn.execute(
            "INSERT INTO crm_followups (opportunity_id, owner_user_id, due_date, note, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (opp_id, o["owner_user_id"], due.isoformat(), note, actor.user_id, now_iso()))
        audit(conn, actor, "followup_created", "followup", cur.lastrowid)
        return {"id": cur.lastrowid}


def update_followup(db, actor, fu_id, payload):
    scope = Scope(db, actor)
    with db.connect() as conn:
        f = scope.fetch(conn, "crm_followups", fu_id, "followup")
        if payload.get("done"):
            conn.execute("UPDATE crm_followups SET done_at = ? WHERE id = ? AND done_at IS NULL", (now_iso(), fu_id))
            audit(conn, actor, "followup_done", "followup", fu_id)
        elif payload.get("due_date"):
            conn.execute("UPDATE crm_followups SET due_date = ? WHERE id = ?",
                         (parse_day(payload["due_date"], "Due date").isoformat(), fu_id))
            audit(conn, actor, "followup_rescheduled", "followup", fu_id)
        else:
            raise bad("Nothing to update.")
        return {"ok": True}


def list_followups(db, actor, include_done=False):
    scope = Scope(db, actor)
    where, params = scope.clause("f")
    sql = ("SELECT f.id, f.opportunity_id, f.due_date, f.note, f.done_at, f.owner_user_id, o.ref, o.title, o.stage, "
           f"c.name AS company_name FROM crm_followups f JOIN crm_opportunities o ON o.id = f.opportunity_id "
           f"JOIN crm_companies c ON c.id = o.company_id WHERE {where}")
    if not include_done:
        sql += " AND f.done_at IS NULL"
    sql += " ORDER BY f.due_date LIMIT 500"
    today = date.today().isoformat()
    with db.connect() as conn:
        labels = _owner_label_map(conn)
        out = []
        for r in conn.execute(sql, params).fetchall():
            x = dict(r)
            x["overdue"] = x["done_at"] is None and x["due_date"] < today
            x["owner_label"] = labels.get(x["owner_user_id"], "Needs owner")
            out.append(x)
        return out


# ---- stage movement ---------------------------------------------------------

def _set_stage(conn, actor, o, to, summary):
    t = now_iso()
    conn.execute("UPDATE crm_opportunities SET stage = ?, stage_changed_at = ?, updated_at = ? WHERE id = ?",
                 (to, t, t, o["id"]))
    _system_activity(conn, o, actor, "stage_change", summary)


def move_stage(db, actor, opp_id, to):
    if to not in STAGES:
        raise bad("Unknown stage.")
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        st = o["stage"]
        if o["archived"] or st in CLOSED:
            raise bad("This opportunity is closed.", 409)
        if st == "On Hold":
            raise bad("Resume the opportunity before moving it.", 409)
        if to in ("Lost", "On Hold"):
            raise bad("Use the Lost or On Hold action, which needs a reason.")
        if to == "Won":
            if not actor.is_founder:
                audit_denied(db, actor, "won_founder_only", "opportunity", opp_id)
                raise HTTPException(status_code=403, detail="Only the Founder can mark an opportunity as Won.")
            if st not in ("Proposal Sent", "Negotiation"):
                raise bad("An opportunity can be Won only after a proposal is sent.", 409)
            t = now_iso()
            conn.execute("UPDATE crm_opportunities SET stage = 'Won', stage_changed_at = ?, won_at = ?, updated_at = ? "
                         "WHERE id = ?", (t, t, t, opp_id))
            _system_activity(conn, o, actor, "decision", f"{st} -> Won")
            audit(conn, actor, "stage_won", "opportunity", opp_id)
            return {"stage": "Won"}
        if st == "Founder Review":
            raise bad("Waiting for the Founder's review.", 409)
        if to == "Requirement Assessment":
            raise bad("Only the Founder's approval moves an opportunity to Requirement Assessment.", 409)
        if NEXT_STAGE.get(st) != to:
            raise bad(f"Cannot move from {st} to {to}.", 409)
        if to == "Contacted":
            has_act = conn.execute("SELECT 1 FROM crm_activities WHERE opportunity_id = ? AND kind IN "
                                   "('call','email','whatsapp','meeting','note')", (opp_id,)).fetchone()
            has_fu = conn.execute("SELECT 1 FROM crm_followups WHERE opportunity_id = ? AND done_at IS NULL",
                                  (opp_id,)).fetchone()
            if not has_act or not has_fu:
                raise bad("Log an interaction and set a follow-up before marking as Contacted.", 409)
        if to == "Founder Review":
            if not (o["requirement_summary"] or "").strip():
                raise bad("Add a requirement summary before requesting Founder review.", 409)
            conn.execute(
                "INSERT INTO crm_founder_reviews (opportunity_id, kind, state, requested_by, requested_at) "
                "VALUES (?, 'opportunity', 'pending', ?, ?)", (opp_id, actor.user_id, now_iso()))
        if to == "Proposal Preparation":
            if not conn.execute("SELECT 1 FROM crm_activities WHERE opportunity_id = ? AND kind = 'assessment'",
                                (opp_id,)).fetchone():
                raise bad("Add an assessment note before Proposal Preparation.", 409)
        if to == "Proposal Sent" and o["proposal_status"] != "approved":
            raise bad("The Founder's commercial approval is needed before a proposal is sent.", 409)
        _set_stage(conn, actor, o, to, f"{st} -> {to}")
        audit(conn, actor, "stage_changed", "opportunity", opp_id, f"{st}>{to}")
        return {"stage": to}


def request_commercial_review(db, actor, opp_id, note):
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["stage"] != "Proposal Preparation":
            raise bad("Commercial review applies to Proposal Preparation.", 409)
        if conn.execute("SELECT 1 FROM crm_founder_reviews WHERE opportunity_id = ? AND kind = 'commercial' "
                        "AND state IN ('pending','held')", (opp_id,)).fetchone():
            raise bad("A commercial review is already waiting.", 409)
        conn.execute("INSERT INTO crm_founder_reviews (opportunity_id, kind, state, requested_by, requested_at, "
                     "request_note) VALUES (?, 'commercial', 'pending', ?, ?, ?)",
                     (opp_id, actor.user_id, now_iso(), text(note, "Note", maxlen=1000, required=False)))
        conn.execute("UPDATE crm_opportunities SET proposal_status = 'requested', updated_at = ? WHERE id = ?",
                     (now_iso(), opp_id))
        audit(conn, actor, "commercial_review_requested", "opportunity", opp_id)
        return {"ok": True}


# ---- Lost / On Hold (D-C5) --------------------------------------------------

def set_lost(db, actor, opp_id, reason_code, note):
    if reason_code not in LOST_REASONS:
        raise bad("Choose a reason.")
    note = text(note, "Note", maxlen=1000, minlen=5)
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["archived"] or o["stage"] in CLOSED:
            raise bad("This opportunity is already closed.", 409)
        before = o["stage_before_hold"] if o["stage"] == "On Hold" and o["stage_before_hold"] else o["stage"]
        t = now_iso()
        review_state = "confirmed" if actor.is_founder else "pending"
        conn.execute(
            "UPDATE crm_opportunities SET stage = 'Lost', stage_before_hold = ?, stage_changed_at = ?, reason_code = ?, "
            "reason_note = ?, hold_review_date = NULL, lost_hold_review = ?, updated_at = ? WHERE id = ?",
            (before, t, reason_code, note, review_state, t, opp_id))
        conn.execute("UPDATE crm_founder_reviews SET state = 'superseded' WHERE opportunity_id = ? AND kind = 'on_hold' "
                     "AND state = 'pending'", (opp_id,))
        if not actor.is_founder:
            conn.execute("INSERT INTO crm_founder_reviews (opportunity_id, kind, state, requested_by, requested_at, "
                         "request_note) VALUES (?, 'lost', 'pending', ?, ?, ?)", (opp_id, actor.user_id, t, reason_code))
        _system_activity(conn, o, actor, "decision", f"Lost: {LOST_REASONS[reason_code]}. {note}")
        audit(conn, actor, "set_lost", "opportunity", opp_id, f"reason={reason_code};from={before}")
        return {"stage": "Lost"}


def set_on_hold(db, actor, opp_id, reason_code, note, review_date):
    if reason_code not in HOLD_REASONS:
        raise bad("Choose a reason.")
    note = text(note, "Note", maxlen=1000, minlen=5)
    rd = parse_day(review_date, "Review date")
    if rd <= date.today() or rd > date.today() + timedelta(days=366):
        raise bad("Review date must be in the future and within a year.")
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["archived"] or o["stage"] in CLOSED or o["stage"] == "On Hold":
            raise bad("This opportunity cannot be put on hold.", 409)
        t = now_iso()
        review_state = "confirmed" if actor.is_founder else "pending"
        conn.execute(
            "UPDATE crm_opportunities SET stage = 'On Hold', stage_before_hold = ?, stage_changed_at = ?, reason_code = ?, "
            "reason_note = ?, hold_review_date = ?, lost_hold_review = ?, updated_at = ? WHERE id = ?",
            (o["stage"], t, reason_code, note, rd.isoformat(), review_state, t, opp_id))
        if not actor.is_founder:
            conn.execute("INSERT INTO crm_founder_reviews (opportunity_id, kind, state, requested_by, requested_at, "
                         "request_note) VALUES (?, 'on_hold', 'pending', ?, ?, ?)", (opp_id, actor.user_id, t, reason_code))
        _system_activity(conn, o, actor, "decision", f"On hold until {rd.isoformat()}: {HOLD_REASONS[reason_code]}. {note}")
        audit(conn, actor, "set_on_hold", "opportunity", opp_id, f"reason={reason_code};from={o['stage']}")
        return {"stage": "On Hold"}


def resume(db, actor, opp_id, note):
    note = text(note, "Note", maxlen=1000, minlen=5)
    scope = Scope(db, actor)
    with db.connect() as conn:
        o = scope.fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["stage"] != "On Hold":
            raise bad("Only an opportunity on hold can be resumed.", 409)
        back = o["stage_before_hold"] or "New Lead"
        t = now_iso()
        conn.execute(
            "UPDATE crm_opportunities SET stage = ?, stage_changed_at = ?, reason_code = NULL, reason_note = NULL, "
            "hold_review_date = NULL, lost_hold_review = 'none', updated_at = ? WHERE id = ?", (back, t, t, opp_id))
        conn.execute("UPDATE crm_founder_reviews SET state = 'resumed' WHERE opportunity_id = ? AND kind = 'on_hold' "
                     "AND state = 'pending'", (opp_id,))
        _system_activity(conn, o, actor, "decision", f"Resumed to {back}. {note}")
        audit(conn, actor, "resumed", "opportunity", opp_id, f"to={back}")
        return {"stage": back}


# ---- Founder review ---------------------------------------------------------

def review_queue(db, actor):
    with db.connect() as conn:
        labels = _owner_label_map(conn)
        rows = conn.execute(
            "SELECT r.id, r.kind, r.state, r.requested_at, r.request_note, o.id AS opportunity_id, o.ref, o.title, "
            "o.stage, o.stage_before_hold, o.reason_code, o.reason_note, o.hold_review_date, o.owner_user_id, "
            "o.requirement_summary, c.name AS company_name FROM crm_founder_reviews r "
            "JOIN crm_opportunities o ON o.id = r.opportunity_id JOIN crm_companies c ON c.id = o.company_id "
            "WHERE r.state IN ('pending','held') ORDER BY r.requested_at").fetchall()
        out = []
        for r in rows:
            x = dict(r)
            x["owner_label"] = labels.get(x["owner_user_id"], "Needs owner")
            out.append(x)
        return out


def my_review_status(db, actor):
    scope = Scope(db, actor)
    where, params = scope.clause("o")
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT r.id, r.kind, r.state, r.requested_at, r.decision, r.decision_note, o.ref, o.title FROM "
            f"crm_founder_reviews r JOIN crm_opportunities o ON o.id = r.opportunity_id WHERE {where} "
            "ORDER BY r.id DESC LIMIT 50", params).fetchall()]


def decide_review(db, actor, review_id, payload):
    decision = payload.get("decision")
    note = text(payload.get("note"), "Note", maxlen=1000, minlen=5)
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM crm_founder_reviews WHERE id = ?", (review_id,)).fetchone()
        if r is None or r["state"] not in ("pending", "held"):
            raise HTTPException(status_code=404, detail="Not found.")
        o = conn.execute("SELECT * FROM crm_opportunities WHERE id = ?", (r["opportunity_id"],)).fetchone()
        kind, t = r["kind"], now_iso()
        if kind in ("opportunity", "commercial"):
            if decision not in ("approve", "reject", "hold"):
                raise bad("Choose approve, reject or hold.")
            state = {"approve": "approved", "reject": "rejected", "hold": "held"}[decision]
            if kind == "opportunity":
                if o["stage"] != "Founder Review":
                    raise bad("This opportunity is no longer in Founder Review.", 409)
                if decision == "approve":
                    _set_stage(conn, actor, o, "Requirement Assessment", "Founder Review -> Requirement Assessment")
                elif decision == "reject":
                    _set_stage(conn, actor, o, "Discovery", "Founder Review -> Discovery (returned)")
            else:
                conn.execute("UPDATE crm_opportunities SET proposal_status = ?, updated_at = ? WHERE id = ?",
                             ({"approve": "approved", "reject": "rejected", "hold": "requested"}[decision], t, o["id"]))
        elif kind in ("lost", "on_hold"):
            if decision not in ("confirm", "override", "reopen"):
                raise bad("Choose confirm, override or reopen.")
            if decision == "confirm":
                conn.execute("UPDATE crm_opportunities SET lost_hold_review = 'confirmed', updated_at = ? WHERE id = ?",
                             (t, o["id"]))
                state = "confirmed"
            elif decision == "override":
                to_status = payload.get("to_status")
                code = payload.get("reason_code")
                if to_status == "lost":
                    if code not in LOST_REASONS:
                        raise bad("Choose a Lost reason.")
                    conn.execute("UPDATE crm_opportunities SET stage = 'Lost', reason_code = ?, reason_note = ?, "
                                 "hold_review_date = NULL, lost_hold_review = 'overridden', updated_at = ? WHERE id = ?",
                                 (code, note, t, o["id"]))
                elif to_status == "on_hold":
                    if code not in HOLD_REASONS:
                        raise bad("Choose an On Hold reason.")
                    rd = parse_day(payload.get("hold_review_date"), "Review date")
                    if rd <= date.today():
                        raise bad("Review date must be in the future.")
                    conn.execute("UPDATE crm_opportunities SET stage = 'On Hold', reason_code = ?, reason_note = ?, "
                                 "hold_review_date = ?, lost_hold_review = 'overridden', updated_at = ? WHERE id = ?",
                                 (code, note, rd.isoformat(), t, o["id"]))
                else:
                    raise bad("Override needs a target status of lost or on_hold.")
                state = "overridden"
            else:  # reopen
                target = payload.get("stage") or o["stage_before_hold"] or "New Lead"
                if target not in STAGES or target in ("Won", "Lost", "On Hold", "Founder Review"):
                    raise bad("Choose a stage to reopen into.")
                conn.execute(
                    "UPDATE crm_opportunities SET stage = ?, stage_changed_at = ?, reason_code = NULL, reason_note = NULL, "
                    "hold_review_date = NULL, lost_hold_review = 'none', updated_at = ? WHERE id = ?",
                    (target, t, t, o["id"]))
                state = "reopened"
            _system_activity(conn, o, actor, "decision", f"Founder review {state}. {note}")
        else:
            raise bad("Unsupported review.", 409)
        conn.execute(
            "UPDATE crm_founder_reviews SET state = ?, decided_by = ?, decided_at = ?, decision = ?, decision_note = ? "
            "WHERE id = ?", (state, actor.user_id, t, decision, note, review_id))
        audit(conn, actor, "review_decided", "review", review_id, f"kind={kind};decision={decision}")
        return {"state": state}


def reopen_lost(db, actor, opp_id, note, stage=None):
    note = text(note, "Note", maxlen=1000, minlen=5)
    with db.connect() as conn:
        o = Scope(db, actor).fetch(conn, "crm_opportunities", opp_id, "opportunity")
        if o["stage"] != "Lost":
            raise bad("Only a Lost opportunity can be reopened.", 409)
        target = stage or o["stage_before_hold"] or "New Lead"
        if target not in STAGES or target in ("Won", "Lost", "On Hold", "Founder Review"):
            raise bad("Choose a stage to reopen into.")
        t = now_iso()
        conn.execute("UPDATE crm_opportunities SET stage = ?, stage_changed_at = ?, reason_code = NULL, reason_note = NULL, "
                     "lost_hold_review = 'none', updated_at = ? WHERE id = ?", (target, t, t, opp_id))
        conn.execute("UPDATE crm_founder_reviews SET state = 'reopened', decided_by = ?, decided_at = ? WHERE "
                     "opportunity_id = ? AND kind = 'lost' AND state = 'pending'", (actor.user_id, t, opp_id))
        _system_activity(conn, o, actor, "decision", f"Reopened to {target}. {note}")
        audit(conn, actor, "reopened", "opportunity", opp_id, f"to={target}")
        return {"stage": target}


# ---- companies: archive and reassignment (Founder) --------------------------

def archive_company(db, actor, company_id):
    with db.connect() as conn:
        c = Scope(db, actor).fetch(conn, "crm_companies", company_id, "company")
        t = now_iso()
        conn.execute("UPDATE crm_companies SET archived = 1, archived_at = ?, updated_at = ? WHERE id = ?",
                     (t, t, company_id))
        conn.execute("UPDATE crm_contacts SET archived = 1 WHERE company_id = ?", (company_id,))
        conn.execute("UPDATE crm_opportunities SET archived = 1, updated_at = ? WHERE company_id = ?", (t, company_id))
        audit(conn, actor, "company_archived", "company", company_id)
        return {"ok": True}


def reassign_company(db, actor, company_id, new_owner_user_id):
    """Moves the whole bundle in one transaction: company, contacts, opportunities, open follow-ups
    (activities inherit through their opportunity and are never rewritten)."""
    with db.connect() as conn:
        c = Scope(db, actor).fetch(conn, "crm_companies", company_id, "company")
        if not _valid_owner(conn, new_owner_user_id):
            raise bad("The new owner must be the Founder or an active specialist.")
        t = now_iso()
        conn.execute("UPDATE crm_companies SET owner_user_id = ?, updated_at = ? WHERE id = ?",
                     (new_owner_user_id, t, company_id))
        k = conn.execute("UPDATE crm_contacts SET owner_user_id = ?, updated_at = ? WHERE company_id = ?",
                         (new_owner_user_id, t, company_id)).rowcount
        opps = conn.execute("SELECT id FROM crm_opportunities WHERE company_id = ?", (company_id,)).fetchall()
        conn.execute("UPDATE crm_opportunities SET owner_user_id = ?, updated_at = ? WHERE company_id = ?",
                     (new_owner_user_id, t, company_id))
        f = 0
        for r in opps:
            f += conn.execute("UPDATE crm_followups SET owner_user_id = ? WHERE opportunity_id = ?",
                              (new_owner_user_id, r["id"])).rowcount
            o = conn.execute("SELECT * FROM crm_opportunities WHERE id = ?", (r["id"],)).fetchone()
            _system_activity(conn, o, actor, "assignment", "Ownership reassigned")
        audit(conn, actor, "company_reassigned", "company", company_id,
              f"contacts={k};opportunities={len(opps)};followups={f}")
        return {"contacts": k, "opportunities": len(opps), "followups": f}


def needs_owner(db, actor):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, owner_user_id FROM crm_companies WHERE archived = 0 AND owner_user_id NOT IN "
            "(SELECT user_id FROM crm_staff WHERE active = 1 UNION SELECT user_id FROM super_admins) ORDER BY name"
        ).fetchall()]


# ---- search, overview, reports, retention -----------------------------------

def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(db, actor, q):
    q = (q or "").strip()
    if len(q) < 2:
        raise bad("Type at least two characters.")
    like = _like(q)
    scope = Scope(db, actor)
    w, p = scope.clause("c")
    w2, p2 = scope.clause("k")
    w3, p3 = scope.clause("o")
    with db.connect() as conn:
        return {
            "companies": [dict(r) for r in conn.execute(
                f"SELECT c.id, c.name FROM crm_companies c WHERE {w} AND c.archived = 0 AND LOWER(c.name) LIKE LOWER(?) ESCAPE '\\' "
                "ORDER BY c.name LIMIT 20", (*p, like)).fetchall()],
            "contacts": [dict(r) for r in conn.execute(
                f"SELECT k.id, k.full_name, k.company_id FROM crm_contacts k WHERE {w2} AND k.archived = 0 AND "
                "(LOWER(k.full_name) LIKE LOWER(?) ESCAPE '\\' OR LOWER(k.email) LIKE LOWER(?) ESCAPE '\\') ORDER BY k.full_name LIMIT 20",
                (*p2, like, like)).fetchall()],
            "opportunities": [dict(r) for r in conn.execute(
                f"SELECT o.id, o.ref, o.title, o.stage FROM crm_opportunities o WHERE {w3} AND o.archived = 0 AND "
                "(LOWER(o.title) LIKE LOWER(?) ESCAPE '\\' OR LOWER(o.ref) LIKE LOWER(?) ESCAPE '\\') ORDER BY o.ref LIMIT 20",
                (*p3, like, like)).fetchall()],
        }


def no_contact_queue(db, actor):
    """A-01: open opportunities with no meaningful contact at all, 90 days after creation."""
    cutoff = (now_dt() - timedelta(days=NO_CONTACT_REVIEW_DAYS)).isoformat()
    scope = Scope(db, actor)
    where, params = scope.clause("o")
    with db.connect() as conn:
        labels = _owner_label_map(conn)
        rows = conn.execute(
            "SELECT o.id, o.ref, o.title, o.stage, o.created_at, o.owner_user_id, c.name AS company_name FROM "
            f"crm_opportunities o JOIN crm_companies c ON c.id = o.company_id WHERE {where} AND o.archived = 0 AND "
            "o.stage NOT IN ('Won','Lost') AND o.last_meaningful_contact_at IS NULL AND o.created_at <= ? "
            "ORDER BY o.created_at", (*params, cutoff)).fetchall()
        return [{**dict(r), "owner_label": labels.get(r["owner_user_id"], "Needs owner")} for r in rows]


def request_archive(db, actor, opp_id, note):
    note = text(note, "Note", maxlen=1000, minlen=5)
    with db.connect() as conn:
        o = Scope(db, actor).fetch(conn, "crm_opportunities", opp_id, "opportunity")
        conn.execute("INSERT INTO crm_founder_reviews (opportunity_id, kind, state, requested_by, requested_at, "
                     "request_note) VALUES (?, 'archive', 'pending', ?, ?, ?)", (opp_id, actor.user_id, now_iso(), note))
        audit(conn, actor, "archive_requested", "opportunity", opp_id)
        return {"ok": True}


def retention_queue(db, actor):
    """Founder only. FLAGS records whose last meaningful contact is older than 12 months; deletes nothing."""
    limit_dt = add_months(now_dt(), -RETENTION_MONTHS).isoformat()
    with db.connect() as conn:
        labels = _owner_label_map(conn)
        rows = conn.execute(
            "SELECT id, name, owner_user_id, last_meaningful_contact_at FROM crm_companies WHERE archived = 0 AND "
            "last_meaningful_contact_at IS NOT NULL AND last_meaningful_contact_at < ? "
            "ORDER BY last_meaningful_contact_at", (limit_dt,)).fetchall()
        out = []
        for r in rows:
            last = datetime.fromisoformat(r["last_meaningful_contact_at"])
            out.append({"company_id": r["id"], "name": r["name"], "owner_label": labels.get(r["owner_user_id"], "Needs owner"),
                        "last_meaningful_contact_at": r["last_meaningful_contact_at"],
                        "retention_due_at": add_months(last, RETENTION_MONTHS).isoformat(),
                        "action": "flag_only"})
        return out


def overview(db, actor):
    scope = Scope(db, actor)
    where, params = scope.clause("o")
    wf, pf = scope.clause("f")
    today = date.today().isoformat()
    with db.connect() as conn:
        by_stage = {r["stage"]: r["n"] for r in conn.execute(
            f"SELECT stage, COUNT(*) AS n FROM crm_opportunities o WHERE {where} AND o.archived = 0 GROUP BY stage",
            params).fetchall()}
        due = conn.execute(f"SELECT COUNT(*) AS n FROM crm_followups f WHERE {wf} AND f.done_at IS NULL AND f.due_date = ?",
                           (*pf, today)).fetchone()["n"]
        overdue = conn.execute(f"SELECT COUNT(*) AS n FROM crm_followups f WHERE {wf} AND f.done_at IS NULL AND f.due_date < ?",
                               (*pf, today)).fetchone()["n"]
        hold_due = conn.execute(
            f"SELECT COUNT(*) AS n FROM crm_opportunities o WHERE {where} AND o.stage = 'On Hold' AND o.hold_review_date <= ?",
            (*params, today)).fetchone()["n"]
    out = {"by_stage": by_stage, "followups_due_today": due, "followups_overdue": overdue,
           "on_hold_review_due": hold_due, "no_contact_90d": len(no_contact_queue(db, actor)),
           "role": actor.role}
    if actor.is_founder:
        out["reviews_pending"] = len(review_queue(db, actor))
        out["needs_owner"] = len(needs_owner(db, actor))
        out["retention_due"] = len(retention_queue(db, actor))
    return out


def reports(db, actor):
    scope = Scope(db, actor)
    where, params = scope.clause("o")
    with db.connect() as conn:
        def grp(col, extra=""):
            return {str(r[col]): r["n"] for r in conn.execute(
                f"SELECT {col}, COUNT(*) AS n FROM crm_opportunities o WHERE {where} AND o.archived = 0 {extra} "
                f"GROUP BY {col}", params).fetchall()}
        total = conn.execute(f"SELECT COUNT(*) AS n FROM crm_opportunities o WHERE {where} AND o.archived = 0",
                             params).fetchone()["n"]
        won = conn.execute(f"SELECT COUNT(*) AS n FROM crm_opportunities o WHERE {where} AND o.stage = 'Won' AND o.archived = 0",
                           params).fetchone()["n"]
        open_val = conn.execute(
            f"SELECT COALESCE(SUM(estimated_value_inr), 0) AS v FROM crm_opportunities o WHERE {where} AND o.archived = 0 "
            "AND o.stage NOT IN ('Won','Lost','On Hold')", params).fetchone()["v"]
        rep = {
            "total": total, "won": won, "open_value_inr": open_val,
            "by_stage": grp("stage"), "by_source": grp("source"), "by_pillar": grp("service_pillar"),
            "lost_reasons": grp("reason_code", "AND o.stage = 'Lost'"),
            "scope": "all" if actor.is_founder else "own",
        }
        if actor.is_founder:
            labels = _owner_label_map(conn)
            rep["per_owner"] = {labels.get(r["owner_user_id"], "Needs owner"): r["n"] for r in conn.execute(
                "SELECT owner_user_id, COUNT(*) AS n FROM crm_opportunities WHERE archived = 0 GROUP BY owner_user_id"
            ).fetchall()}
        return rep


def export_csv(db, actor):
    def cell(v):
        s = "" if v is None else str(v)
        s = s.replace('"', '""')
        if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
            s = "'" + s
        return f'"{s}"'
    rows = list_opportunities(db, actor, limit=5000)
    cols = ["ref", "title", "company_name", "stage", "service_pillar", "source", "owner_label", "estimated_value_inr",
            "last_meaningful_contact_at", "created_at"]
    lines = [",".join(cols)] + [",".join(cell(r.get(c)) for c in cols) for r in rows]
    with db.connect() as conn:
        audit(conn, actor, "export", "opportunity", None, f"rows={len(rows)}")
    return "\n".join(lines) + "\n"


def audit_view(db, actor, limit=200):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, at, actor_user_id, actor_role, action, entity, entity_id, outcome, detail FROM crm_audit_log "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]


# ---- staff (Founder) --------------------------------------------------------

def list_staff(db, actor):
    with db.connect() as conn:
        out = []
        for r in conn.execute(
                "SELECT s.*, u.email FROM crm_staff s JOIN users u ON u.id = s.user_id ORDER BY s.display_name").fetchall():
            m = mfa.get_row(conn, r["user_id"])
            x = {k: r[k] for k in ("user_id", "display_name", "email", "active", "activated_at", "deactivated_at")}
            x["mfa_enrolled"] = mfa.is_enrolled(m)
            x["lock"] = mfa.lock_state(m)
            out.append(x)
        return out


def activate_staff(db, actor, email, display_name):
    email = text(email, "Email", maxlen=200).lower()
    name = text(display_name, "Display name", maxlen=80)
    user = db.get_user_by_email(email)
    if user is None:
        raise HTTPException(status_code=404, detail="No registered account has that email.")
    if db.is_super_admin(user["id"]):
        raise bad("That account is the Founder.", 409)
    t = now_iso()
    with db.connect() as conn:
        s = conn.execute("SELECT * FROM crm_staff WHERE user_id = ?", (user["id"],)).fetchone()
        if s is None:
            conn.execute("INSERT INTO crm_staff (user_id, display_name, active, activated_by, activated_at, created_at) "
                         "VALUES (?, ?, 1, ?, ?, ?)", (user["id"], name, actor.user_id, t, t))
        else:
            conn.execute("UPDATE crm_staff SET active = 1, display_name = ?, activated_by = ?, activated_at = ?, "
                         "deactivated_at = NULL, deactivated_by = NULL WHERE user_id = ?",
                         (name, actor.user_id, t, user["id"]))
        mfa.reset_user(conn, user["id"])  # reactivation always means fresh enrolment
        audit(conn, actor, "staff_activated", "staff", user["id"])
        return {"user_id": user["id"]}


def deactivate_staff(db, actor, user_id):
    with db.connect() as conn:
        s = conn.execute("SELECT * FROM crm_staff WHERE user_id = ?", (user_id,)).fetchone()
        if s is None:
            raise HTTPException(status_code=404, detail="Not found.")
        conn.execute("UPDATE crm_staff SET active = 0, deactivated_by = ?, deactivated_at = ? WHERE user_id = ?",
                     (actor.user_id, now_iso(), user_id))
        mfa.reset_user(conn, user_id)  # wipes secrets and bumps the epoch: sessions die on the next request
        n = conn.execute("SELECT COUNT(*) AS n FROM crm_companies WHERE owner_user_id = ? AND archived = 0",
                         (user_id,)).fetchone()["n"]
        audit(conn, actor, "staff_deactivated", "staff", user_id, f"companies_needing_owner={n}")
        return {"companies_needing_owner": n}


def reset_staff_mfa(db, actor, user_id, unlock_only=False):
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM crm_staff WHERE user_id = ?", (user_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Not found.")
        mfa.reset_user(conn, user_id, unlock_only=unlock_only)
        audit(conn, actor, "mfa_unlocked" if unlock_only else "mfa_reset", "staff", user_id)
        return {"ok": True}
