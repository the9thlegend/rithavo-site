"""Founder-controlled invitations, the pre-activation API boundary, activation checks and activation races
(scope 2.4, 2.8, 2.8a; tests 6.3). Synthetic data only."""

import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import config
from app.crm import credentials, identity, invites, mfa
from app.crm.routes import PUBLIC_ROUTES, classification
from tests.crm_helpers import PASSWORD, Person, clock, crm, decode_session, make_awaiting, non_crm_counts  # noqa: F401

PW = "an-invited-persons-password-1"


def invite(f, email="invitee@example.test", name="Invitee"):
    r = f.post("/crm/staff/invite", {"email": email, "display_name": name})
    assert r.status_code == 200, r.text
    return r.json()


def token_of(link):
    return link.split("token=")[1]


def sign_up_through_the_real_flow(crm, f, email="invitee@example.test"):
    body = invite(f, email)
    p = Person(crm, email, "specialist")
    assert p.client.post("/crm/auth/accept", json={"token": token_of(body["link"]), "password": PW}).status_code == 200
    p.password = PW
    p.login()
    p.enrol()
    return p


# ---- creating an invitation grants nothing ------------------------------------------------------------------

def test_inviting_adds_one_identity_row_and_nothing_else_outside_the_crm(crm):
    f = crm.founder()
    before = non_crm_counts(crm.db)
    body = invite(f)
    after = non_crm_counts(crm.db)
    diff = {t: (before.get(t, 0), n) for t, n in after.items() if n != before.get(t, 0)}
    assert diff == {"users": (before["users"], before["users"] + 1)}              # one users row, no entitlement, purchase, profile...
    for t in ("entitlements", "purchases", "career_profiles", "card_settings", "diagnostics", "resumes", "analytics_events",
              "super_admins", "web_magic_link_tokens", "web_password_reset_tokens"):
        assert after.get(t, 0) == before.get(t, 0), t
    assert body["delivery"] == "link" and "token=" in body["link"]
    assert decode_session(f.client).get("user_id") is None                            # no customer session appears


def test_inviting_an_existing_customer_changes_nothing_of_theirs(crm):
    f = crm.founder()
    cust = Person(crm, "cust.staff@example.test", "none")
    from app.auth import hash_password
    crm.db.set_user_password(cust.user_id, hash_password("customer-public-pass-5"))
    cust.client.post("/auth/login", json={"email": cust.email, "password": "customer-public-pass-5"})
    before = non_crm_counts(crm.db)
    with crm.db.connect() as conn:
        pw_before = conn.execute("SELECT password_hash FROM users WHERE id = ?", (cust.user_id,)).fetchone()[0]
    invite(f, cust.email)
    assert non_crm_counts(crm.db) == before                                            # not even a new users row
    with crm.db.connect() as conn:
        assert conn.execute("SELECT password_hash FROM users WHERE id = ?", (cust.user_id,)).fetchone()[0] == pw_before
    assert cust.client.get("/auth/logout") is not None
    assert cust.client.post("/auth/login", json={"email": cust.email, "password": "customer-public-pass-5"}).status_code == 200


def test_accepting_an_invitation_creates_no_customer_session(crm):
    f = crm.founder()
    body = invite(f)
    c = TestClient(crm.app)
    r = c.post("/crm/auth/accept", json={"token": token_of(body["link"]), "password": PW})
    assert r.status_code == 200 and not c.cookies.get("session")                       # no cookie at all
    with crm.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM analytics_events").fetchone()[0] == 0


def test_invitation_refused_for_the_allowlisted_founder_email(crm):
    f = crm.founder()
    for email in ("founder@example.test", "SYSTEM-FOUNDER@example.test"):
        r = f.post("/crm/staff/invite", {"email": email, "display_name": "X"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "founder_email"


def test_invite_link_is_never_logged_and_is_shown_once(crm, caplog):
    import logging
    f = crm.founder()
    with caplog.at_level(logging.DEBUG):
        body = invite(f)
    tok = token_of(body["link"])
    assert tok not in "\n".join(m.getMessage() for m in caplog.records)
    assert tok not in f.get("/crm/staff").text                                         # the list never carries it again
    with crm.db.connect() as conn:
        assert tok not in " ".join(str(dict(r)) for r in conn.execute("SELECT * FROM crm_invites").fetchall())


def test_with_a_real_mail_sender_the_link_is_emailed_and_not_returned(crm, monkeypatch):
    sent = []

    class Mailer:
        def __init__(self):
            self.sent = sent

        def send(self, to, subject, body):
            sent.append((to, subject, body))

    monkeypatch.setattr(crm.app.state, "email_sender", Mailer())
    f = crm.founder()
    r = f.post("/crm/staff/invite", {"email": "mail@example.test", "display_name": "Mail"})
    assert r.status_code == 200 and r.json() == {"user_id": r.json()["user_id"], "delivery": "email"}
    assert len(sent) == 1 and "token=" in sent[0][2]


def test_token_single_use_expiry_resend_and_revoke(crm):
    f = crm.founder()
    b1 = invite(f)
    uid = b1["user_id"]
    b2 = f.post(f"/crm/staff/{uid}/resend").json()
    c = TestClient(crm.app)
    assert c.post("/crm/auth/accept", json={"token": token_of(b1["link"]), "password": PW}).status_code == 400   # resend revoked it
    crm.clock.advance(24 * 3600 + 1)
    assert c.post("/crm/auth/accept", json={"token": token_of(b2["link"]), "password": PW}).status_code == 400   # expired
    f.login(); f.verify()                                                   # the Founder's own session also lapsed
    b3 = f.post(f"/crm/staff/{uid}/resend").json()
    assert f.post(f"/crm/staff/{uid}/revoke-invite").json() == {"revoked": 1}
    assert c.post("/crm/auth/accept", json={"token": token_of(b3["link"]), "password": PW}).status_code == 400   # revoked
    b4 = f.post(f"/crm/staff/{uid}/resend").json()
    assert c.post("/crm/auth/accept", json={"token": token_of(b4["link"]), "password": PW}).status_code == 200
    assert c.post("/crm/auth/accept", json={"token": token_of(b4["link"]), "password": PW}).status_code == 400   # used
    assert f.post(f"/crm/staff/{uid}/resend").status_code == 409                                                # already accepted


# ---- the full flow and the pre-activation boundary -----------------------------------------------------------------

def test_full_flow_invite_accept_enrol_wait_then_activate(crm):
    f = crm.founder()
    p = sign_up_through_the_real_flow(crm, f)
    me = p.get("/crm/me")
    assert me.status_code == 200 and me.json() == {"state": "awaiting_activation", "mfa": "ok"}     # own state only
    staff = f.get("/crm/staff").json()
    row = [x for x in staff if x["user_id"] == p.user_id][0]
    assert row["state"] == "awaiting_activation" and row["mfa_enrolled"]
    assert p.get("/crm/overview").status_code == 403
    r = f.post(f"/crm/staff/{p.user_id}/activate", {"credential_epoch": row["credential_epoch"], "mfa_epoch": row["mfa_epoch"]})
    assert r.status_code == 200
    assert p.get("/crm/overview").status_code == 200
    assert p.get("/crm/me").json()["state"] == "active"


def test_state_by_route_matrix_before_and_after_activation(crm):
    f = crm.founder()
    awaiting, cred, m = make_awaiting(crm, "wait@example.test")
    awaiting.login(); awaiting.secret = awaiting.secret; awaiting.verify()                 # password and a verified code
    cases = {"awaiting": awaiting}
    cred_only = Person(crm, "credonly@example.test", "specialist")
    with crm.db.connect() as conn:
        conn.execute("INSERT INTO crm_staff (user_id, display_name, active, created_at) VALUES (?, 'c', 0, '2026-01-01')", (cred_only.user_id,))
    cred_only.give_credential().login()
    cases["credentialed"] = cred_only
    anon = TestClient(crm.app)
    for (method, path), kind in classification().items():
        url = re.sub(r"\{[^}]+\}", "1", path)
        if kind in ("active", "founder"):
            for who in ("awaiting", "credentialed"):
                r = cases[who].client.request(method, url, json={})
                assert r.status_code == 403 and r.json()["detail"]["code"] == "awaiting_activation", (who, method, path, r.status_code)
                assert set(r.json()) == {"detail"} and set(r.json()["detail"]) <= {"code", "detail", "message"}   # no data in the body
            assert anon.request(method, url, json={}).status_code == 401
    # the denials are audited with the route name only
    with crm.db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM crm_audit_log WHERE action = 'denied_awaiting_activation'").fetchone()["n"]
    assert n > 40


def test_the_onboarding_allowlist_is_exactly_the_documented_set(crm):
    c = classification()
    onboarding = {k for k, v in c.items() if v == "onboarding"}
    assert onboarding == {("GET", "/crm/me"), ("POST", "/crm/auth/change-password"), ("POST", "/crm/mfa/enrol/start"),
                          ("POST", "/crm/mfa/enrol/confirm"), ("POST", "/crm/mfa/verify"), ("POST", "/crm/mfa/recover")}
    assert {k for k, v in c.items() if v == "public"} == PUBLIC_ROUTES
    assert not [k for k, v in c.items() if v == "unclassified"]                          # default deny: nothing unclassified


def test_search_export_reports_and_staff_management_are_denied_before_activation(crm):
    f = crm.founder()
    p, cred, m = make_awaiting(crm, "denied@example.test")
    p.login(); p.verify()
    for method, url in (("GET", "/crm/search?q=ab"), ("GET", "/crm/export.csv"), ("GET", "/crm/reports"), ("GET", "/crm/retention"),
                        ("GET", "/crm/audit"), ("GET", "/crm/staff"), ("POST", "/crm/staff/invite"),
                        (f"POST", f"/crm/staff/{p.user_id}/activate"), ("POST", f"/crm/staff/{p.user_id}/deactivate"),
                        ("POST", f"/crm/staff/{p.user_id}/reset-mfa"), ("POST", f"/crm/staff/{p.user_id}/unlock"),
                        ("POST", "/crm/leads"), ("POST", "/crm/duplicate-check")):
        r = p.client.request(method, url, json={})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "awaiting_activation", (method, url)


# ---- activation preconditions -----------------------------------------------------------------------------------

def test_activation_preconditions_each_fail_closed_and_change_nothing(crm):
    f = crm.founder()
    p, cred, m = make_awaiting(crm, "pre@example.test")
    url = f"/crm/staff/{p.user_id}/activate"

    def active():
        with crm.db.connect() as conn:
            return identity.staff_state(conn, p.user_id)[1]

    assert f.post(url, {"credential_epoch": cred + 1, "mfa_epoch": m}).json()["detail"]["code"] == "state_changed_retry"
    assert f.post(url, {"credential_epoch": cred, "mfa_epoch": m + 1}).json()["detail"]["code"] == "state_changed_retry"
    assert f.post(url, {"credential_epoch": cred}).status_code == 422
    assert f.post(url, {"credential_epoch": "1", "mfa_epoch": "x"}).status_code == 422
    assert active() == "awaiting_activation"
    # a locked authenticator
    with crm.db.connect() as conn:
        conn.execute("UPDATE crm_mfa SET hard_locked = 1 WHERE user_id = ?", (p.user_id,))
    assert f.post(url, {"credential_epoch": cred, "mfa_epoch": m}).json()["detail"]["code"] == "locked"
    with crm.db.connect() as conn:
        conn.execute("UPDATE crm_mfa SET hard_locked = 0 WHERE user_id = ?", (p.user_id,))
    # self, specialist, Founder, unknown, already active
    assert f.post(f"/crm/staff/{f.user_id}/activate", {"credential_epoch": 1, "mfa_epoch": 1}).status_code == 403
    assert p.client.post(url, json={"credential_epoch": cred, "mfa_epoch": m}).status_code == 401
    assert f.post("/crm/staff/99999/activate", {"credential_epoch": 1, "mfa_epoch": 1}).status_code == 404
    assert f.post(url, {"credential_epoch": cred, "mfa_epoch": m}).status_code == 200
    assert f.post(url, {"credential_epoch": cred, "mfa_epoch": m}).json()["detail"]["code"] == "already_active"


def test_activation_needs_a_password_and_an_enrolled_authenticator(crm):
    f = crm.founder()
    uid = invite(f, "np@example.test")["user_id"]
    r = f.post(f"/crm/staff/{uid}/activate", {"credential_epoch": 1, "mfa_epoch": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "password_not_set"
    with crm.db.connect() as conn:                                  # a credential but no accepted invitation
        credentials.set_password(conn, uid, PW)
    r = f.post(f"/crm/staff/{uid}/activate", {"credential_epoch": 1, "mfa_epoch": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "password_not_set"


def test_activation_refuses_a_founder_allowlisted_email_and_a_deactivated_account(crm):
    f = crm.founder()
    p, cred, m = make_awaiting(crm, "futureadmin@example.test")
    config.CRM_FOUNDER_EMAILS = config.CRM_FOUNDER_EMAILS + ("futureadmin@example.test",)
    try:
        assert f.post(f"/crm/staff/{p.user_id}/activate", {"credential_epoch": cred, "mfa_epoch": m}).json()["detail"]["code"] == "founder_email"
    finally:
        config.CRM_FOUNDER_EMAILS = tuple(e for e in config.CRM_FOUNDER_EMAILS if e != "futureadmin@example.test")
    f.post(f"/crm/staff/{p.user_id}/deactivate")
    assert f.post(f"/crm/staff/{p.user_id}/activate", {"credential_epoch": cred, "mfa_epoch": m}).json()["detail"]["code"] == "needs_new_invitation"


def test_reactivation_needs_a_fresh_invitation_credential_mfa_and_activation(crm):
    f = crm.founder()
    p = sign_up_through_the_real_flow(crm, f, "again@example.test")
    row = [x for x in f.get("/crm/staff").json() if x["user_id"] == p.user_id][0]
    f.post(f"/crm/staff/{p.user_id}/activate", {"credential_epoch": row["credential_epoch"], "mfa_epoch": row["mfa_epoch"]})
    f.post(f"/crm/staff/{p.user_id}/deactivate")
    assert p.get("/crm/overview").status_code == 401
    body = invite(f, "again@example.test")                           # a fresh invitation starts over
    q = Person(crm, "again@example.test", "specialist")
    assert q.client.post("/crm/auth/login", json={"email": q.email, "password": PW}).status_code == 401    # old password is gone
    assert q.client.post("/crm/auth/accept", json={"token": token_of(body["link"]), "password": "second-password-for-me-2"}).status_code == 200
    q.password = "second-password-for-me-2"
    q.login(); q.enrol()
    assert q.get("/crm/overview").status_code == 403                 # inactive until the Founder activates again
    row = [x for x in f.get("/crm/staff").json() if x["user_id"] == p.user_id][0]
    assert f.post(f"/crm/staff/{p.user_id}/activate", {"credential_epoch": row["credential_epoch"], "mfa_epoch": row["mfa_epoch"]}).status_code == 200
    assert q.get("/crm/overview").status_code == 200


# ---- lock order and activation races ------------------------------------------------------------------------------

def test_every_state_changing_path_takes_the_person_locks_first():
    import inspect
    from app.crm import authflow, mfa as mfa_mod, store
    for fn in (store.activate_staff, store.deactivate_staff, store.reset_staff_mfa, store.revoke_invite, store.invite_staff,
               mfa_mod.use_recovery_code, authflow.accept, authflow.change_password):
        src = inspect.getsource(fn)
        assert "lock_person(" in src, fn.__name__
        first_write = min([i for i in (src.find('"UPDATE'), src.find('"DELETE'), src.find("credentials.set_password"),
                                       src.find("mfa.reset_user")) if i >= 0] or [10**9])
        assert src.find("lock_person(") < first_write, fn.__name__           # invite_staff's insert-or-ignore precedes it by design
    import app.crm.locking as locking
    assert locking.ORDER == ("crm_staff", "crm_credentials", "crm_mfa")


def _race(crm, name, make_other, iterations=25):
    """Run activation against another operation, released together, many times. Returns per-iteration results."""
    f = crm.founder()
    results = []
    for i in range(iterations):
        p, cred, m = make_awaiting(crm, f"{name}{i}@example.test")
        other = make_other(f, p, i)
        barrier = threading.Barrier(2)
        url = f"/crm/staff/{p.user_id}/activate"

        def act():
            c = TestClient(crm.app)
            c.cookies.update(f.client.cookies)
            barrier.wait()
            return c.post(url, json={"credential_epoch": cred, "mfa_epoch": m}).status_code

        def oth():
            barrier.wait()
            return other()

        with ThreadPoolExecutor(2) as ex:
            fa, fo = ex.submit(act), ex.submit(oth)
            ra, ro = fa.result(), fo.result()
        with crm.db.connect() as conn:
            s, state = identity.staff_state(conn, p.user_id)
            cred_now = credentials.get(conn, p.user_id)["credential_epoch"]
            mrow = mfa.get_row(conn, p.user_id)
            activated = conn.execute("SELECT detail FROM crm_audit_log WHERE action = 'staff_activated' AND entity_id = ?", (p.user_id,)).fetchall()
        results.append((ra, ro, state, cred_now, mrow, [r["detail"] for r in activated], cred, m, p))
    return results


@pytest.fixture
def founder_session(crm):
    return crm.founder()


def test_race_activation_vs_founder_mfa_reset(crm):
    def make_other(f, p, i):
        def go():
            c = TestClient(crm.app); c.cookies.update(f.client.cookies)
            return c.post(f"/crm/staff/{p.user_id}/reset-mfa").status_code
        return go
    for ra, ro, state, cred_now, mrow, acts, cred, m, p in _race(crm, "racemfa", make_other):
        assert ro == 200 and ra in (200, 409)
        assert state != "active" and not mfa.is_enrolled(mrow)            # the reset always wins in the end: inactive, no authenticator
        if ra == 200:
            assert acts == [f"credential_epoch={cred};mfa_epoch={m}"]     # it activated exactly what was reviewed, then was undone
        else:
            assert acts == []


def test_race_activation_vs_the_persons_recovery_code(crm):
    def make_other(f, p, i):
        q = Person(crm, p.email, "specialist"); q.login()
        with crm.db.connect() as conn:                                    # a recovery code for the person (issued at enrolment)
            from app.crm import totp
            code = totp.new_recovery_codes()[0]
            conn.execute("INSERT INTO crm_mfa_recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, 'now')",
                         (p.user_id, totp.recovery_hash(code)))
        return lambda: q.client.post("/crm/mfa/recover", json={"code": code}).status_code
    for ra, ro, state, cred_now, mrow, acts, cred, m, p in _race(crm, "racerec", make_other):
        assert ro == 200 and ra in (200, 409)
        assert state != "active" and not mfa.is_enrolled(mrow)


def test_race_activation_vs_password_change(crm):
    def make_other(f, p, i):
        q = Person(crm, p.email, "specialist"); q.login()
        return lambda: q.client.post("/crm/auth/change-password", json={"current": PASSWORD, "new": "changed-password-%d-xyz" % i}).status_code
    for ra, ro, state, cred_now, mrow, acts, cred, m, p in _race(crm, "racepw", make_other):
        assert ro == 200 and ra in (200, 409)
        if ra == 409:
            assert state == "awaiting_activation" and acts == []          # the change won: nothing activated against stale epochs
        else:
            assert state == "active" and acts == [f"credential_epoch={cred};mfa_epoch={m}"]
            assert cred_now == cred + 1                                    # activated on the reviewed epoch, then the change followed


def test_race_activation_vs_a_fresh_invitation(crm):
    def make_other(f, p, i):
        def go():
            c = TestClient(crm.app); c.cookies.update(f.client.cookies)
            return c.post("/crm/staff/invite", json={"email": p.email, "display_name": "Again"}).status_code
        return go
    for ra, ro, state, cred_now, mrow, acts, cred, m, p in _race(crm, "raceinv", make_other):
        assert ro == 200 and ra in (200, 409)
        assert state == "invited"                                          # the new invitation always ends up in force
        assert not mfa.is_enrolled(mrow)


def test_race_activation_vs_deactivation(crm):
    def make_other(f, p, i):
        def go():
            c = TestClient(crm.app); c.cookies.update(f.client.cookies)
            return c.post(f"/crm/staff/{p.user_id}/deactivate").status_code
        return go
    for ra, ro, state, cred_now, mrow, acts, cred, m, p in _race(crm, "racedeact", make_other):
        assert ro == 200 and ra in (200, 409)
        assert state == "deactivated"


def test_race_two_simultaneous_activations_exactly_one_wins(crm):
    f = crm.founder()
    for i in range(25):
        p, cred, m = make_awaiting(crm, f"raceact{i}@example.test")
        barrier = threading.Barrier(2)

        def act():
            c = TestClient(crm.app); c.cookies.update(f.client.cookies)
            barrier.wait()
            return c.post(f"/crm/staff/{p.user_id}/activate", json={"credential_epoch": cred, "mfa_epoch": m})

        with ThreadPoolExecutor(2) as ex:
            r1, r2 = [x.result() for x in (ex.submit(act), ex.submit(act))]
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]
        assert [r.json()["detail"]["code"] for r in (r1, r2) if r.status_code == 409] == ["already_active"]
        with crm.db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM crm_audit_log WHERE action = 'staff_activated' AND entity_id = ?",
                                (p.user_id,)).fetchone()["n"] == 1


def test_activation_versus_the_person_starting_enrolment_changes_nothing(crm):
    f = crm.founder()
    p, cred, m = make_awaiting(crm, "enrolrace@example.test")
    q = Person(crm, p.email, "specialist"); q.login()
    assert q.post("/crm/mfa/enrol/start").status_code == 409              # already set up: re-enrolment needs a reset first
    assert f.post(f"/crm/staff/{p.user_id}/activate", {"credential_epoch": cred, "mfa_epoch": m}).status_code == 200
