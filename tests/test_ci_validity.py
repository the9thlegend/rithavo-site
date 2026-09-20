"""
CI validity foundation — the entitlement-creation side that lives on this
repo (the canonical, customer-facing Razorpay purchase flow). The
evaluation-consumption side (reserve_ci_evaluation, the Run/Re-Evaluate
action) lives entirely on the sibling app (rithavo-career-profile) and is
tested there — this file only covers what THIS repo actually creates and
reads: expires_at set at confirm time, legacy-row handling, and that
Application Diagnosis is completely unaffected.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.razorpay_gateway import RazorpayGateway

from .conftest import login_via_magic_link


def _install_razorpay_gateway(app, monkeypatch):
    gateway = RazorpayGateway("rzp_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real")
    gateway._client = MagicMock()
    gateway._client.order.create.side_effect = (
        lambda data: {"id": f"order_fake_{data['notes']['purchase_id']}"}
    )
    gateway._client.utility.verify_payment_signature.return_value = True
    gateway._client.payment.fetch.return_value = {"status": "captured"}
    monkeypatch.setattr(app.state, "payment_gateway", gateway)
    return gateway


def test_ci_purchase_confirmation_sets_a_30_day_expiry(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "ci-expiry-site@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/career-intelligence/purchase", json={}).json()
    client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                json={"razorpay_order_id": started["razorpay_order_id"],
                      "razorpay_payment_id": "pay_expiry_test", "razorpay_signature": "sig"})

    entitlement_id = db.get_purchase(started["purchase_id"])["entitlement_id"]
    entitlement = db.get_entitlement(entitlement_id)
    assert entitlement["evaluations_used"] == 0
    expires_at = datetime.fromisoformat(entitlement["expires_at"])
    activated_at = datetime.fromisoformat(entitlement["activated_at"])
    assert abs((expires_at - activated_at).total_seconds() - 30 * 86400) < 5


def test_ad_purchase_confirmation_gets_no_expiry(app_and_client, db, monkeypatch):
    """Application Diagnostic has no expiry concept at all — confirming
    one must not invent an expires_at value for it."""
    app, client = app_and_client
    login_via_magic_link(client, app, "ad-no-expiry-site@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                json={"razorpay_order_id": started["razorpay_order_id"],
                      "razorpay_payment_id": "pay_ad_no_expiry", "razorpay_signature": "sig"})

    entitlement_id = db.get_purchase(started["purchase_id"])["entitlement_id"]
    entitlement = db.get_entitlement(entitlement_id)
    assert entitlement["expires_at"] is None
    assert entitlement["evaluations_used"] == 0  # harmless default, unread by AD logic


def test_legacy_ci_entitlement_row_with_null_expiry_is_readable(db):
    """Simulates a row created before this phase — expires_at NULL,
    evaluations_used defaulted to 0 by the additive column, never
    backfilled with an invented value."""
    user_id = db.get_or_create_user("legacy-site@example.com")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO entitlements (user_id, product, status, amount, currency, payment_source, "
            "external_payment_reference, purchased_at, activated_at) "
            "VALUES (?, 'career_intelligence', 'ACTIVE', 799, 'INR', 'rithavo_web_gateway', 'legacy-ref', ?, ?)",
            (user_id, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
    entitlement = db.find_active_ci_entitlement(user_id)
    assert entitlement is not None
    assert entitlement["expires_at"] is None
    assert entitlement["evaluations_used"] == 0

    # A legacy (never-expiring) entitlement does not block a fresh
    # purchase attempt once its evaluations are exhausted -- but WHILE
    # evaluations_used is still below the cap, it does block one, exactly
    # like a real 30-day entitlement would.
    try:
        db.begin_ci_purchase(user_id)
        blocked = False
    except ValueError:
        blocked = True
    assert blocked is True


def test_application_diagnostic_entitlement_is_unaffected_by_ci_validity_columns(db):
    user_id = db.get_or_create_user("ad-unaffected-site@example.com")
    purchase = db.begin_ad_purchase(user_id)
    db.mark_purchase_pending(purchase["purchase_id"], "order_ad_unaffected")
    result = db.confirm_ad_purchase(purchase["purchase_id"], "order_ad_unaffected", "pay_ad_unaffected")
    entitlement = db.get_entitlement(result["entitlement_id"])
    assert entitlement["status"] == "ACTIVE"
    assert entitlement["expires_at"] is None
    assert entitlement["evaluations_used"] == 0
