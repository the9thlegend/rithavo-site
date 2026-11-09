"""CRM Lite: owner isolation, masked duplicates, bundles, workflow, Lost/On Hold, retention, audit."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.crm import store
from tests.crm_helpers import scalar, column, Person, clock, crm  # noqa: F401


def two_specialists(crm):
    f = crm.founder()
    a = crm.specialist("alice.s@example.test", "Alice")
    b = crm.specialist("bala.s@example.test", "Bala")
    return f, a, b


# ---- isolation (D-C1) --------------------------------------------------------

def test_foreign_records_are_404_on_every_route_and_lists_exclude_them(crm):
    f, a, b = two_specialists(crm)
    lead = crm.lead(a, company="Kestrel Logistics")
    oid, cid = lead["id"], lead["company_id"]
    a.post(f"/crm/opportunities/{oid}/followups", {"due_date": "2030-01-01", "note": "call"})
    fu_id = a.get("/crm/followups").json()[0]["id"]
    for method, path, body in (
            ("GET", f"/crm/opportunities/{oid}", None), ("PATCH", f"/crm/opportunities/{oid}", {"title": "x"}),
            ("POST", f"/crm/opportunities/{oid}/stage", {"to": "Contacted"}),
            ("POST", f"/crm/opportunities/{oid}/activities", {"kind": "note", "summary": "hello there"}),
            ("POST", f"/crm/opportunities/{oid}/followups", {"due_date": "2030-01-01"}),
            ("POST", f"/crm/opportunities/{oid}/lost", {"reason_code": "budget", "note": "no budget left"}),
            ("POST", f"/crm/opportunities/{oid}/on-hold", {"reason_code": "other", "note": "pause it",
                                                            "hold_review_date": (date.today() + timedelta(days=30)).isoformat()}),
            ("POST", f"/crm/opportunities/{oid}/resume", {"note": "resuming now"}),
            ("POST", f"/crm/opportunities/{oid}/commercial-review", {}),
            ("POST", f"/crm/opportunities/{oid}/request-archive", {"note": "please archive"}),
            ("GET", f"/crm/companies/{cid}", None), ("POST", f"/crm/companies/{cid}/contacts", {"full_name": "Z Z"}),
            ("PATCH", f"/crm/followups/{fu_id}", {"done": True})):
        r = b.client.request(method, path, json=body)
        assert r.status_code == 404, (method, path, r.status_code)
        assert r.json() == {"detail": "Not found."}
    # ... indistinguishable from a record that does not exist
    assert b.get("/crm/opportunities/99999").json() == {"detail": "Not found."}
    # Lists, search, counts and reports contain nothing of A's.
    assert b.get("/crm/opportunities").json() == []
    assert b.get("/crm/companies").json() == []
    assert b.get("/crm/contacts").json() == []
    assert b.get("/crm/followups").json() == []
    s = b.get("/crm/search?q=Kestrel").json()
    assert s == {"companies": [], "contacts": [], "opportunities": []}
    assert b.get("/crm/overview").json()["by_stage"] == {}
    rep = b.get("/crm/reports").json()
    assert rep["total"] == 0 and rep["scope"] == "own" and "per_owner" not in rep
    # A, the owner, and the Founder see it.
    assert a.get(f"/crm/opportunities/{oid}").status_code == 200
    assert a.get("/crm/search?q=Kestrel").json()["companies"][0]["id"] == cid
    assert f.get(f"/crm/opportunities/{oid}").status_code == 200
    assert f.get("/crm/reports").json()["per_owner"] == {"Alice": 1}
    # Foreign access attempts are audited as denials.
    denied = [r for r in f.get("/crm/audit").json() if r["outcome"] == "denied" and r["action"] == "foreign_record"]
    from app.crm import rls
    if rls.is_active():
        # Database-enforced mode: PostgreSQL hides the foreign row before the application can see it, so the
        # application-level denial audit never fires. Known trade-off, documented in the RLS proposal.
        assert denied == []
    else:
        assert len(denied) >= 10


def test_founder_sees_everything(crm):
    f, a, b = two_specialists(crm)
    crm.lead(a, company="Aster Foods")
    crm.lead(b, company="Basil Labs")
    assert {c["name"] for c in f.get("/crm/companies").json()} == {"Aster Foods", "Basil Labs"}
    assert len(f.get("/crm/opportunities").json()) == 2
    assert f.get("/crm/overview").json()["by_stage"] == {"New Lead": 2}


def test_specialist_report_totals_equal_sum_of_own_rows_only(crm):
    f, a, b = two_specialists(crm)
    for i in range(3):
        crm.lead(a, company=f"Co A{i}", pillar="career", source="website")
    crm.lead(b, company="Co B", pillar="product_studio", source="event")
    ra = a.get("/crm/reports").json()
    assert ra["total"] == 3 and ra["by_pillar"] == {"career": 3} and ra["by_source"] == {"website": 3}
    assert b.get("/crm/reports").json()["total"] == 1
    assert f.get("/crm/reports").json()["total"] == 4


# ---- masked duplicates -------------------------------------------------------

def test_duplicate_warning_reveals_nothing_about_another_owners_record(crm):
    f, a, b = two_specialists(crm)
    crm.lead(a, company="Quill & Quarry Pvt Ltd", contact="Meera Joshi", email="meera@quillquarry.example.test",
             title="Secret engagement title", pillar="business_transformation")
    expected = {"code": "duplicate_masked", "message": store.MASKED_MESSAGE}
    # Matched by company name (different formatting) and by contact email: identical masked body.
    for payload in ({"company_name": "quill and quarry pvt ltd".replace("and", "&"), "contact_email": ""},
                    {"company_name": "Another Name", "contact_email": "MEERA@quillquarry.example.test"}):
        r = b.post("/crm/duplicate-check", payload)
        assert r.json() == {"duplicate": True, **expected}
        raw = json.dumps(r.json()).lower()
        for secret in ("quill", "meera", "alice", "secret engagement", "business_transformation", "owner_user_id"):
            assert secret not in raw
    r = b.post("/crm/leads", {"company_name": "Quill & Quarry Pvt Ltd", "contact_name": "X Y",
                              "contact_email": "x@y.example.test", "title": "t", "service_pillar": "career",
                              "source": "other"})
    assert r.status_code == 409 and r.json()["detail"] == expected
    # Nothing was created for B.
    assert b.get("/crm/companies").json() == []
    # The owner is told about their own record; the Founder sees the match.
    own = a.post("/crm/duplicate-check", {"company_name": "Quill & Quarry Pvt Ltd"}).json()
    assert own["code"] == "duplicate_own"
    assert f.post("/crm/duplicate-check", {"company_name": "Quill & Quarry Pvt Ltd"}).json()["code"] == "duplicate_found"
    assert b.post("/crm/duplicate-check", {"company_name": "Totally New Co"}).json() == {"duplicate": False}


# ---- ownership bundle and reassignment (A-02) --------------------------------

def test_bundle_inherits_owner_and_reassignment_moves_everything(crm):
    f, a, b = two_specialists(crm)
    lead = crm.lead(a, company="Harbor Analytics")
    cid, oid = lead["company_id"], lead["id"]
    assert a.post(f"/crm/companies/{cid}/contacts", {"full_name": "Ravi Menon", "email": "ravi@harbor.example.test"}).status_code == 200
    a.post(f"/crm/opportunities/{oid}/activities", {"kind": "note", "summary": "kickoff notes"})
    a.post(f"/crm/opportunities/{oid}/followups", {"due_date": "2030-02-02", "note": "check in"})
    lead2 = crm.lead(a, company="Harbor Analytics", title="Second opp", **{}) if False else None
    with crm.db.connect() as conn:  # every row of the bundle carries owner A
        owners = {scalar(conn.execute(f"SELECT DISTINCT owner_user_id FROM {t}").fetchall()[0]) for t in
                  ("crm_companies", "crm_contacts", "crm_opportunities", "crm_followups")}
        acts_before = scalar(conn.execute("SELECT COUNT(*) FROM crm_activities").fetchone())
    assert owners == {a.user_id}
    # A specialist cannot reassign; the Founder cannot assign to an inactive or unknown user.
    assert a.post(f"/crm/companies/{cid}/reassign", {"owner_user_id": b.user_id}).status_code == 403
    assert f.post(f"/crm/companies/{cid}/reassign", {"owner_user_id": 99999}).status_code == 400
    r = f.post(f"/crm/companies/{cid}/reassign", {"owner_user_id": b.user_id})
    assert r.status_code == 200 and r.json() == {"contacts": 2, "opportunities": 1, "followups": 1}
    with crm.db.connect() as conn:
        for t in ("crm_companies", "crm_contacts", "crm_opportunities", "crm_followups"):
            assert set(column(conn.execute(f"SELECT DISTINCT owner_user_id FROM {t}").fetchall())) == {b.user_id}, t
    # A lost access completely; B gained it all, including history.
    assert a.get(f"/crm/opportunities/{oid}").status_code == 404
    assert a.get(f"/crm/companies/{cid}").status_code == 404
    assert a.get("/crm/followups").json() == []
    detail = b.get(f"/crm/opportunities/{oid}").json()
    assert detail["owner_label"] == "Bala" and any(x["summary"] == "kickoff notes" for x in detail["activities"])
    assert len(b.get("/crm/followups").json()) == 1
    # Existing activities were not rewritten (append-only); one assignment entry was added.
    with crm.db.connect() as conn:
        assert scalar(conn.execute("SELECT COUNT(*) FROM crm_activities").fetchone()) == acts_before + 1


# ---- workflow guards ---------------------------------------------------------

def test_full_stage_path_with_server_side_guards(crm):
    f, a, b = two_specialists(crm)
    oid = crm.lead(a)["id"]
    stage = lambda p, to: p.post(f"/crm/opportunities/{oid}/stage", {"to": to})
    assert stage(a, "Qualified").status_code == 409            # cannot skip
    assert stage(a, "Contacted").status_code == 409            # no interaction or follow-up yet
    a.post(f"/crm/opportunities/{oid}/activities", {"kind": "call", "summary": "Intro call, no answer"})
    assert stage(a, "Contacted").status_code == 409            # still no follow-up
    a.post(f"/crm/opportunities/{oid}/followups", {"due_date": "2030-03-03"})
    assert stage(a, "Contacted").status_code == 200
    assert stage(a, "Qualified").status_code == 200 and stage(a, "Discovery").status_code == 200
    assert stage(a, "Founder Review").status_code == 409       # no requirement summary
    a.patch(f"/crm/opportunities/{oid}", {"requirement_summary": "Needs a customer portal and a CRM integration."})
    assert stage(a, "Founder Review").status_code == 200
    assert stage(a, "Requirement Assessment").status_code == 409  # specialists cannot approve
    review = f.get("/crm/reviews").json()
    assert len(review) == 1 and review[0]["kind"] == "opportunity" and review[0]["owner_label"] == "Alice"
    assert a.post(f"/crm/reviews/{review[0]['id']}/decide", {"decision": "approve", "note": "fine"}).status_code == 403
    assert f.post(f"/crm/reviews/{review[0]['id']}/decide", {"decision": "approve", "note": "no"}).status_code == 400  # note too short
    assert f.post(f"/crm/reviews/{review[0]['id']}/decide", {"decision": "approve", "note": "Good fit, proceed."}).status_code == 200
    assert a.get(f"/crm/opportunities/{oid}").json()["stage"] == "Requirement Assessment"
    assert stage(a, "Proposal Preparation").status_code == 409  # needs an assessment note
    a.post(f"/crm/opportunities/{oid}/activities", {"kind": "assessment", "summary": "Scope: 3 modules, 8 weeks"})
    assert stage(a, "Proposal Preparation").status_code == 200
    assert stage(a, "Proposal Sent").status_code == 409         # needs commercial approval
    assert a.post(f"/crm/opportunities/{oid}/commercial-review", {"note": "pricing attached"}).status_code == 200
    com = [r for r in f.get("/crm/reviews").json() if r["kind"] == "commercial"][0]
    assert f.post(f"/crm/reviews/{com['id']}/decide", {"decision": "approve", "note": "Pricing approved."}).status_code == 200
    assert stage(a, "Proposal Sent").status_code == 200
    assert stage(a, "Won").status_code == 403                   # Founder only
    assert stage(a, "Negotiation").status_code == 200
    assert stage(a, "Won").status_code == 403
    assert stage(f, "Won").status_code == 200
    assert stage(f, "Contacted").status_code == 409             # closed
    assert a.patch(f"/crm/opportunities/{oid}", {"title": "x"}).status_code == 409


def test_founder_rejection_returns_to_discovery(crm):
    f, a, b = two_specialists(crm)
    oid = crm.lead(a)["id"]
    a.post(f"/crm/opportunities/{oid}/activities", {"kind": "note", "summary": "first contact notes"})
    a.post(f"/crm/opportunities/{oid}/followups", {"due_date": "2030-03-03"})
    for to in ("Contacted", "Qualified", "Discovery"):
        a.post(f"/crm/opportunities/{oid}/stage", {"to": to})
    a.patch(f"/crm/opportunities/{oid}", {"requirement_summary": "Vague scope."})
    a.post(f"/crm/opportunities/{oid}/stage", {"to": "Founder Review"})
    rid = f.get("/crm/reviews").json()[0]["id"]
    f.post(f"/crm/reviews/{rid}/decide", {"decision": "reject", "note": "Needs sharper scope first."})
    assert a.get(f"/crm/opportunities/{oid}").json()["stage"] == "Discovery"
    assert a.get("/crm/reviews/mine").json()[0]["state"] == "rejected"


# ---- Lost / On Hold (D-C5, A-07) ---------------------------------------------

def test_specialist_lost_requires_reason_and_founder_can_override_or_reopen(crm):
    f, a, b = two_specialists(crm)
    oid = crm.lead(a)["id"]
    lost = lambda **k: a.post(f"/crm/opportunities/{oid}/lost", k)
    assert lost(note="client went quiet").status_code == 400                       # reason mandatory
    assert lost(reason_code="made_up", note="client went quiet").status_code == 400
    assert lost(reason_code="no_response", note="").status_code == 400             # note mandatory
    assert lost(reason_code="no_response", note="client went quiet").status_code == 200
    d = a.get(f"/crm/opportunities/{oid}").json()
    assert d["stage"] == "Lost" and d["reason_code"] == "no_response" and d["lost_hold_review"] == "pending"
    q = f.get("/crm/reviews").json()
    assert q[0]["kind"] == "lost" and q[0]["reason_code"] == "no_response" and q[0]["stage_before_hold"] == "New Lead"
    # The specialist cannot reopen, move, edit or put on hold a Lost opportunity.
    assert a.post(f"/crm/opportunities/{oid}/reopen", {"note": "reopen it please"}).status_code == 403
    assert a.post(f"/crm/opportunities/{oid}/stage", {"to": "Contacted"}).status_code == 409
    assert a.post(f"/crm/opportunities/{oid}/on-hold", {"reason_code": "other", "note": "pause it",
                                                        "hold_review_date": (date.today() + timedelta(days=30)).isoformat()}).status_code == 409
    # Founder overrides Lost to On Hold, then reopens.
    review_id = q[0]["id"]
    tomorrow = (date.today() + timedelta(days=30)).isoformat()
    r = f.post(f"/crm/reviews/{review_id}/decide", {"decision": "override", "note": "Client asked to wait.",
                                                    "to_status": "on_hold", "reason_code": "client_requested_pause",
                                                    "hold_review_date": tomorrow})
    assert r.status_code == 200 and r.json()["state"] == "overridden"
    assert a.get(f"/crm/opportunities/{oid}").json()["stage"] == "On Hold"
    assert a.post(f"/crm/opportunities/{oid}/resume", {"note": "client is back"}).status_code == 200
    assert a.get(f"/crm/opportunities/{oid}").json()["stage"] == "New Lead"
    # Lost again, this time the Founder reopens it directly.
    lost(reason_code="timing", note="not this quarter")
    assert f.post(f"/crm/opportunities/{oid}/reopen", {"note": "Reopening at client request."}).status_code == 200
    assert a.get(f"/crm/opportunities/{oid}").json()["stage"] == "New Lead"
    assert a.get(f"/crm/opportunities/{oid}").json()["reason_code"] is None


def test_on_hold_needs_review_date_and_founder_confirms(crm):
    f, a, b = two_specialists(crm)
    oid = crm.lead(a)["id"]
    hold = lambda **k: a.post(f"/crm/opportunities/{oid}/on-hold", k)
    nxt = (date.today() + timedelta(days=14)).isoformat()
    assert hold(reason_code="budget_or_timing", note="waiting for budget").status_code == 400       # no date
    assert hold(reason_code="budget_or_timing", note="waiting for budget", hold_review_date="2001-01-01").status_code == 400
    assert hold(reason_code="budget_or_timing", note="waiting for budget", hold_review_date="soon").status_code == 400
    assert hold(reason_code="no_response", note="waiting for budget", hold_review_date=nxt).status_code == 400  # a Lost reason
    assert hold(reason_code="budget_or_timing", note="waiting for budget", hold_review_date=nxt).status_code == 200
    assert a.post(f"/crm/opportunities/{oid}/stage", {"to": "Contacted"}).status_code == 409  # resume first
    rid = f.get("/crm/reviews").json()[0]["id"]
    assert f.post(f"/crm/reviews/{rid}/decide", {"decision": "confirm", "note": "Agreed."}).json()["state"] == "confirmed"
    assert f.get("/crm/reviews").json() == []
    assert a.get(f"/crm/opportunities/{oid}").json()["lost_hold_review"] == "confirmed"


def test_hold_review_due_is_surfaced_in_overview(crm):
    f, a, b = two_specialists(crm)
    oid = crm.lead(a)["id"]
    a.post(f"/crm/opportunities/{oid}/on-hold", {"reason_code": "other", "note": "short pause", "hold_review_date":
                                                 (date.today() + timedelta(days=3)).isoformat()})
    assert a.get("/crm/overview").json()["on_hold_review_due"] == 0
    with crm.db.connect() as conn:
        conn.execute("UPDATE crm_opportunities SET hold_review_date = ? WHERE id = ?", ("2020-01-01", oid))
    assert a.get("/crm/overview").json()["on_hold_review_due"] == 1
    assert b.get("/crm/overview").json()["on_hold_review_due"] == 0


# ---- activities: append-only, meaningful contact (A-06) ----------------------

def test_activities_and_audit_are_append_only(crm):
    f, a, b = two_specialists(crm)
    oid = crm.lead(a)["id"]
    a.post(f"/crm/opportunities/{oid}/activities", {"kind": "note", "summary": "a first note"})
    import sqlite3
    for sql in ("UPDATE crm_activities SET summary = 'x'", "DELETE FROM crm_activities",
                "UPDATE crm_audit_log SET detail = 'x'", "DELETE FROM crm_audit_log"):
        with pytest.raises(Exception, match="append-only"):
            with crm.db.connect() as conn:
                conn.execute(sql)
    # No route edits or deletes an activity.
    for method in ("PATCH", "PUT", "DELETE"):
        assert a.client.request(method, f"/crm/opportunities/{oid}/activities").status_code in (404, 405)


def test_meaningful_contact_rules_and_clock(crm):
    f, a, b = two_specialists(crm)
    lead = crm.lead(a)
    oid, cid = lead["id"], lead["company_id"]
    act = lambda **k: a.post(f"/crm/opportunities/{oid}/activities", k)
    assert a.get(f"/crm/opportunities/{oid}").json()["last_meaningful_contact_at"] is None  # creation never counts
    assert act(kind="note", summary="internal planning note for the team", meaningful=True).status_code == 400
    assert act(kind="call", summary="too short", meaningful=True).status_code == 400
    assert act(kind="email", summary="Sent proposal outline, no reply yet", occurred_at="2999-01-01T00:00:00Z").status_code == 400
    assert act(kind="call", summary="Unanswered call, left voicemail").status_code == 200  # not flagged: not meaningful
    assert a.get(f"/crm/opportunities/{oid}").json()["last_meaningful_contact_at"] is None
    when = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert act(kind="meeting", summary="Met the client and agreed the discovery scope", meaningful=True,
               occurred_at=when).status_code == 200
    got = a.get(f"/crm/opportunities/{oid}").json()["last_meaningful_contact_at"]
    assert got == datetime.fromisoformat(when).astimezone(timezone.utc).isoformat()
    assert a.get(f"/crm/companies/{cid}").json()["last_meaningful_contact_at"] == got
    # Stage changes, Lost and On Hold do not move the clock.
    a.post(f"/crm/opportunities/{oid}/on-hold", {"reason_code": "other", "note": "pause", "hold_review_date":
                                                 (date.today() + timedelta(days=9)).isoformat()})
    assert a.get(f"/crm/opportunities/{oid}").json()["last_meaningful_contact_at"] == got


# ---- 90-day queue and retention flags (A-01, D-C3) ---------------------------

def test_no_meaningful_contact_queue_and_retention_flags_never_delete(crm):
    f, a, b = two_specialists(crm)
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    never = crm.lead(a, company="Never Contacted Co")
    contacted = crm.lead(a, company="Contacted Co")
    b_never = crm.lead(b, company="Bala Quiet Co")
    stale = crm.lead(a, company="Stale Contact Co")
    with crm.db.connect() as conn:
        for l in (never, contacted, b_never, stale):
            conn.execute("UPDATE crm_opportunities SET created_at = ? WHERE id = ?", (old, l["id"]))
    a.post(f"/crm/opportunities/{contacted['id']}/activities", {"kind": "call", "summary": "Two-way call on the scope",
                                                                "meaningful": True})
    q = a.get("/crm/review/no-contact").json()
    assert {x["company_name"] for x in q} == {"Never Contacted Co", "Stale Contact Co"}  # own only, B's excluded
    assert {x["company_name"] for x in f.get("/crm/review/no-contact").json()} == {
        "Never Contacted Co", "Stale Contact Co", "Bala Quiet Co"}
    assert a.get("/crm/overview").json()["no_contact_90d"] == 2
    # Retention: only records with a meaningful contact older than 12 months are flagged; Founder only.
    thirteen = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    with crm.db.connect() as conn:
        conn.execute("UPDATE crm_companies SET last_meaningful_contact_at = ? WHERE id = ?", (thirteen, stale["company_id"]))
        before = scalar(conn.execute("SELECT COUNT(*) FROM crm_companies").fetchone())
    assert a.get("/crm/retention").status_code == 403
    rq = f.get("/crm/retention").json()
    assert [x["name"] for x in rq] == ["Stale Contact Co"] and rq[0]["action"] == "flag_only"
    due = datetime.fromisoformat(rq[0]["retention_due_at"])
    assert due.year == datetime.fromisoformat(thirteen).year + 1
    with crm.db.connect() as conn:  # flag only: nothing deleted, nothing archived
        assert scalar(conn.execute("SELECT COUNT(*) FROM crm_companies").fetchone()) == before
        assert scalar(conn.execute("SELECT COUNT(*) FROM crm_companies WHERE archived = 1").fetchone()) == 0
    # No purge or delete endpoint exists.
    from app.crm.routes import router
    assert not [r for r in router.routes if "DELETE" in r.methods or "purge" in r.path]


# ---- audit (criterion 21) ----------------------------------------------------

def test_mutations_are_audited_atomically_and_without_personal_values(crm):
    f, a, b = two_specialists(crm)
    lead = crm.lead(a, company="Zephyr Quartz Industries", contact="Ishaan Kulkarni",
                    email="ishaan.k@zephyrquartz.example.test", title="Distinctive Bespoke Title")
    oid = lead["id"]
    a.post(f"/crm/opportunities/{oid}/activities", {"kind": "note", "summary": "Distinctive private note text"})
    a.post(f"/crm/opportunities/{oid}/lost", {"reason_code": "budget", "note": "Distinctive private lost note"})
    rows = f.get("/crm/audit").json()
    text = json.dumps(rows).lower()
    for pii in ("zephyr", "ishaan", "distinctive", "bespoke", "zephyrquartz", "@"):
        assert pii not in text, pii
    actions = {r["action"] for r in rows}
    assert {"opportunity_created", "activity_added", "set_lost", "mfa_enrolled", "staff_activated"} <= actions
    # A failed operation leaves no audit row behind (same transaction).
    n = len(rows)
    assert a.post(f"/crm/opportunities/{oid}/stage", {"to": "Contacted"}).status_code == 409
    after = f.get("/crm/audit").json()
    assert not [r for r in after if r["action"] == "stage_changed"]
    # Only the Founder reads the audit log; export is audited and neutralises formulas.
    assert a.get("/crm/audit").status_code == 403
    crm.lead(f, company="=cmd|' /C calc'!A0", contact="Eve Test", title="+SUM(1)")
    csv = f.get("/crm/export.csv")
    assert csv.status_code == 200 and "\"'=cmd" in csv.text and "\"'+SUM" in csv.text
    assert any(r["action"] == "export" for r in f.get("/crm/audit").json())
    assert a.get("/crm/export.csv").status_code == 403


def test_search_is_case_insensitive_and_escapes_wildcards(crm):
    f, a, b = two_specialists(crm)
    crm.lead(a, company="Kestrel Logistics", contact="Priya Nair")
    assert a.get("/crm/search?q=kESTREL").json()["companies"][0]["name"] == "Kestrel Logistics"
    assert a.get("/crm/search?q=priya").json()["contacts"]
    assert a.get("/crm/search?q=%25%25").json() == {"companies": [], "contacts": [], "opportunities": []}  # literal %, not a wildcard
    assert a.get("/crm/search?q=k").status_code == 400
