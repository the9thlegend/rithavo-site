"""
Cashfree Transaction Hardening phase — focused regression coverage for
exactly the hardening behavior introduced this phase: order-creation
failure handling, the PENDING-staleness classification helper, the new
Super-Admin-only refund route, the AD tailored-resume revision cap, and
the production gateway-safety fail-closed check. Everything already
covered elsewhere (launch lock, client-controlled-amount rejection,
duplicate webhook/confirm idempotency, phone handling) is deliberately
NOT re-tested here — see test_launch_lock.py, test_cashfree_purchase.py,
test_cashfree_webhook.py, test_cashfree_customer_phone.py.
"""

from datetime import datetime, timedelta, timezone

import config
from app.cashfree_gateway import CashfreeGateway
from app.pricing import APPLICATION_DIAGNOSIS_MAX_REVISIONS
from app.super_admin import bootstrap_super_admin

from .conftest import login_via_magic_link, seed_minimal_profile


def _install_cashfree_gateway(app, monkeypatch):
    gateway = CashfreeGateway("cf_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real", "SANDBOX")
    monkeypatch.setattr(app.state, "payment_gateway", gateway)
    return gateway


# =====================================================================
# 1. Cashfree order-creation failure
# =====================================================================

def test_order_creation_failure_marks_the_purchase_failed_not_orphaned_created(app_and_client, db, monkeypatch):
    import app.cashfree_gateway as cf_module

    def _fake_post(url, json, headers, timeout):
        raise cf_module.httpx.HTTPError("simulated network failure")

    app, client = app_and_client
    login_via_magic_link(client, app, "order-fail-ci@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    monkeypatch.setattr(cf_module.httpx, "post", _fake_post)

    resp = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    assert resp.status_code == 502
    # Never leaks the underlying exception text or any Cashfree detail.
    assert "simulated network failure" not in resp.text
    assert "cashfree" not in resp.text.lower()

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM purchases WHERE user_id = (SELECT id FROM users WHERE email = ?)",
                            ("order-fail-ci@example.com",)).fetchone()
    assert row["payment_status"] == "FAILED"
    assert row["entitlement_id"] is None


def test_order_creation_failure_creates_no_entitlement(app_and_client, db, monkeypatch):
    import app.cashfree_gateway as cf_module
    app, client = app_and_client
    login_via_magic_link(client, app, "order-fail-ad@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    monkeypatch.setattr(cf_module.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(cf_module.httpx.HTTPError("boom")))

    client.post("/diagnosis/purchase", json={"customer_phone": "9876543210"})
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM entitlements").fetchone()["c"]
    assert count == 0


def test_a_failed_order_creation_can_still_be_retried_with_a_fresh_attempt(app_and_client, monkeypatch):
    """The failure fix must not block a legitimate second attempt -- the
    frontend never reuses an idempotency_key across separate 'Buy'
    clicks (see app-shell.js), so each retry is simply a fresh request
    that succeeds once the gateway starts responding normally again."""
    import app.cashfree_gateway as cf_module
    app, client = app_and_client
    login_via_magic_link(client, app, "order-fail-retry@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    monkeypatch.setattr(cf_module.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(cf_module.httpx.HTTPError("boom")))
    first = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    assert first.status_code == 502

    def _fake_post_ok(url, json, headers, timeout):
        class _Resp:
            status_code = 200
            def json(self):
                return {"order_id": json["order_id"], "payment_session_id": "sess_ok"}
        return _Resp()

    monkeypatch.setattr(cf_module.httpx, "post", _fake_post_ok)
    second = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    assert second.status_code == 200, second.text
    assert second.json()["payment_status"] == "PENDING"


# =====================================================================
# 2. Abandoned PENDING purchases — staleness classification
# =====================================================================

def test_a_fresh_pending_purchase_is_not_stale(db):
    row = {"payment_status": "PENDING", "created_at": datetime.now(timezone.utc).isoformat()}
    assert db.is_purchase_pending_stale(row) is False


def test_a_pending_purchase_older_than_the_threshold_is_stale(db):
    old = datetime.now(timezone.utc) - timedelta(hours=db.PENDING_STALE_AFTER_HOURS + 1)
    row = {"payment_status": "PENDING", "created_at": old.isoformat()}
    assert db.is_purchase_pending_stale(row) is True


def test_an_old_succeeded_purchase_is_never_considered_stale(db):
    """Staleness is a property of an unresolved attempt only -- history
    is never 'stale'."""
    old = datetime.now(timezone.utc) - timedelta(days=365)
    row = {"payment_status": "SUCCEEDED", "created_at": old.isoformat()}
    assert db.is_purchase_pending_stale(row) is False


def test_staleness_classification_never_mutates_or_blocks_confirmation(app_and_client, db, monkeypatch):
    """The whole point: an abandoned PENDING purchase still cannot grant
    an entitlement merely by being old -- confirmation still
    independently re-verifies with the gateway, exactly as for a fresh
    PENDING purchase."""
    app, client = app_and_client
    login_via_magic_link(client, app, "stale-no-entitlement@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    import app.cashfree_gateway as cf_module

    def _fake_post(url, json, headers, timeout):
        class _Resp:
            status_code = 200
            def json(self):
                return {"order_id": json["order_id"], "payment_session_id": "sess"}
        return _Resp()
    monkeypatch.setattr(cf_module.httpx, "post", _fake_post)
    resp = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = resp.json()["purchase_id"]

    # Backdate created_at so it reads as stale, then attempt to confirm
    # -- must still require an actual PAID order status.
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE purchases SET created_at = ? WHERE id = ?", (old, purchase_id))

    monkeypatch.setattr(cf_module.httpx, "get", lambda *a, **k: type(
        "R", (), {"status_code": 200, "json": lambda self: {"order_status": "ACTIVE"}})())
    confirm = client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree", json={})
    assert confirm.status_code == 402  # not PAID -- staleness changed nothing about this outcome
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM entitlements").fetchone()["c"]
    assert count == 0


# =====================================================================
# 3. Refund lifecycle
# =====================================================================

def _make_super_admin(db, email="refund-admin@example.com", password="a-real-password"):
    bootstrap_super_admin(db, email, password)
    return db.get_or_create_user(email)


def test_refund_route_requires_sign_in(app_and_client):
    _, client = app_and_client
    resp = client.post("/admin/dashboard/purchases/1/refund", json={})
    assert resp.status_code == 401


def test_refund_route_rejects_an_ordinary_signed_in_member(app_and_client):
    app, client = app_and_client
    login_via_magic_link(client, app, "ordinary-refund-attempt@example.com")
    resp = client.post("/admin/dashboard/purchases/1/refund", json={})
    assert resp.status_code == 403


def test_super_admin_can_refund_a_succeeded_purchase(app_and_client, db, monkeypatch):
    app, client = app_and_client
    buyer_id = login_via_magic_link(client, app, "refund-buyer@example.com")
    result = db.begin_ad_purchase(buyer_id, gateway="test")
    db.mark_purchase_pending(result["purchase_id"], "gw_ref_refund_test")
    db.confirm_ad_purchase(result["purchase_id"], "gw_ref_refund_test")

    _make_super_admin(db)
    login_via_magic_link(client, app, "refund-admin@example.com")
    resp = client.post(f"/admin/dashboard/purchases/{result['purchase_id']}/refund", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entitlement_revoked"] is True
    assert body["cashfree_refund_initiated"] is False  # honest: no automated money movement

    purchase = db.get_purchase(result["purchase_id"])
    assert purchase["payment_status"] == "REFUNDED"


def test_a_refunded_purchase_cannot_regain_access_through_confirm(app_and_client, db, monkeypatch):
    """The core safety property: once refunded, neither the return-
    confirm route nor a later webhook can resurrect the entitlement."""
    app, client = app_and_client
    buyer_id = login_via_magic_link(client, app, "refund-no-regain@example.com")
    result = db.begin_ad_purchase(buyer_id, gateway="test")
    db.mark_purchase_pending(result["purchase_id"], "gw_ref_no_regain")
    db.confirm_ad_purchase(result["purchase_id"], "gw_ref_no_regain")
    db.refund_ad_purchase(result["purchase_id"])

    import pytest
    with pytest.raises(ValueError, match="REFUNDED"):
        db.confirm_ad_purchase(result["purchase_id"], "gw_ref_no_regain")

    purchase = db.get_purchase(result["purchase_id"])
    assert purchase["payment_status"] == "REFUNDED"


def test_duplicate_refund_requests_are_rejected_not_double_processed(app_and_client, db):
    app, client = app_and_client
    buyer_id = login_via_magic_link(client, app, "refund-duplicate@example.com")
    result = db.begin_ad_purchase(buyer_id, gateway="test")
    db.mark_purchase_pending(result["purchase_id"], "gw_ref_dup")
    db.confirm_ad_purchase(result["purchase_id"], "gw_ref_dup")

    _make_super_admin(db)
    login_via_magic_link(client, app, "refund-admin@example.com")
    first = client.post(f"/admin/dashboard/purchases/{result['purchase_id']}/refund", json={})
    assert first.status_code == 200
    second = client.post(f"/admin/dashboard/purchases/{result['purchase_id']}/refund", json={})
    assert second.status_code == 409


def test_cannot_refund_a_purchase_that_never_succeeded(app_and_client, db):
    app, client = app_and_client
    buyer_id = login_via_magic_link(client, app, "refund-never-succeeded@example.com")
    result = db.begin_ad_purchase(buyer_id, gateway="test")  # left in CREATED

    _make_super_admin(db)
    login_via_magic_link(client, app, "refund-admin@example.com")
    resp = client.post(f"/admin/dashboard/purchases/{result['purchase_id']}/refund", json={})
    assert resp.status_code == 409


# =====================================================================
# 4. AD tailored-resume revision cap
# =====================================================================

def test_ad_revision_cap_constant_is_ten():
    assert APPLICATION_DIAGNOSIS_MAX_REVISIONS == 10


def test_ad_resume_revisions_are_rejected_once_the_cap_is_reached(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "revision-cap@example.com")
    seed_minimal_profile(db, user_id, headline="Engineer", current_role="Engineer")
    result = db.begin_ad_purchase(user_id, gateway="test")
    db.mark_purchase_pending(result["purchase_id"], "gw_ref_revcap")
    db.confirm_ad_purchase(result["purchase_id"], "gw_ref_revcap")

    diag_resp = client.post("/diagnosis", json={"jd_text": "We need a backend engineer with 5 years experience " * 5})
    assert diag_resp.status_code == 200, diag_resp.text
    diagnostic_id = diag_resp.json()["diagnostic_id"]

    for i in range(APPLICATION_DIAGNOSIS_MAX_REVISIONS):
        resp = client.post(f"/diagnosis/{diagnostic_id}/resume")
        assert resp.status_code == 200, f"revision {i + 1} unexpectedly rejected: {resp.text}"

    over_cap = client.post(f"/diagnosis/{diagnostic_id}/resume")
    assert over_cap.status_code == 403
    listed = client.get(f"/diagnosis/{diagnostic_id}/resumes")
    assert len(listed.json()) == APPLICATION_DIAGNOSIS_MAX_REVISIONS


# =====================================================================
# 5. Production gateway safety — fail closed, never silently use TestPaymentGateway
# =====================================================================

def test_unconfigured_real_gateway_fails_closed_in_a_production_like_environment(app_and_client, monkeypatch):
    """The one new safety property: launch unlocked + a real
    (Postgres-backed, i.e. production-like) deployment + no Cashfree/
    Razorpay configured must never silently let a purchase complete
    through TestPaymentGateway. config.DATABASE_URL is this codebase's
    own existing production signal (already relied on by
    SESSION_COOKIE_HTTPS_ONLY)."""
    app, client = app_and_client
    login_via_magic_link(client, app, "prod-fail-closed@example.com")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://fake-prod-dsn-not-real/db")

    resp = client.post("/career-intelligence/purchase", json={})
    assert resp.status_code == 503

    resp2 = client.post("/diagnosis/purchase", json={})
    assert resp2.status_code == 503


def test_unconfigured_real_gateway_creates_no_purchase_row_when_failing_closed(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "prod-fail-closed-no-row@example.com")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://fake-prod-dsn-not-real/db")

    client.post("/career-intelligence/purchase", json={})
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM purchases").fetchone()["c"]
    assert count == 0


def test_local_dev_and_test_environment_is_unaffected_by_the_production_gateway_check(app_and_client):
    """The existing test suite (and local dev) has no DATABASE_URL set
    at all -- this fixture's own `db` uses a throwaway SQLite file, so
    the new check must be a complete no-op here, exactly as it already
    was before this phase for every other existing test."""
    app, client = app_and_client
    login_via_magic_link(client, app, "test-env-unaffected@example.com")
    resp = client.post("/career-intelligence/purchase", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["gateway"] == "test"
