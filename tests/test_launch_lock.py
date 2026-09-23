"""
Pre-Launch Product Lock phase — CI/AD must not be purchasable before
config.LAUNCH_DATE. A hard business rule, enforced server-side in the
purchase-creation routes regardless of gateway or how the request
arrives (never only a frontend button). The rest of the test suite
overrides this to "already launched" via conftest.py's autouse
_launch_has_happened_by_default fixture; these tests explicitly
restore the locked/edge-case states to verify the gate itself.
"""

import config
from app.cashfree_gateway import CashfreeGateway
from app.razorpay_gateway import RazorpayGateway
from app.launch_lock import is_purchase_locked
from .conftest import login_via_magic_link


def test_no_launch_date_configured_is_locked(monkeypatch):
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    assert is_purchase_locked() is True


def test_future_launch_date_is_locked(monkeypatch):
    monkeypatch.setattr(config, "LAUNCH_DATE", "2099-01-01T00:00:00+00:00")
    assert is_purchase_locked() is True


def test_past_launch_date_is_unlocked(monkeypatch):
    monkeypatch.setattr(config, "LAUNCH_DATE", "2020-01-01T00:00:00+00:00")
    assert is_purchase_locked() is False


def test_unparseable_launch_date_fails_closed_to_locked(monkeypatch):
    monkeypatch.setattr(config, "LAUNCH_DATE", "not-a-real-date")
    assert is_purchase_locked() is True


def test_naive_datetime_without_timezone_fails_closed_to_locked(monkeypatch):
    """A launch date with no explicit UTC offset is ambiguous -- refuse
    to guess which timezone it means, and lock rather than risk
    unlocking early or late by accident."""
    monkeypatch.setattr(config, "LAUNCH_DATE", "2020-01-01T00:00:00")
    assert is_purchase_locked() is True


# ---- Enforcement at the actual purchase-creation routes ----

def test_ci_purchase_is_blocked_before_launch(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-ci@example.com")

    resp = client.post("/career-intelligence/purchase", json={})
    assert resp.status_code == 403


def test_ad_purchase_is_blocked_before_launch(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-ad@example.com")

    resp = client.post("/diagnosis/purchase", json={})
    assert resp.status_code == 403


def test_direct_api_call_is_blocked_the_same_as_any_other_request(app_and_client, monkeypatch):
    """'Direct/manual API calls must also be rejected server-side' --
    there is no separate 'browser vs API' code path at all; every
    caller of this route hits the exact same check."""
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-direct-api@example.com")

    resp = client.post("/career-intelligence/purchase", json={"idempotency_key": "manual-attempt-1"})
    assert resp.status_code == 403


def test_no_purchase_row_is_created_while_locked(app_and_client, db, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-no-row@example.com")

    client.post("/career-intelligence/purchase", json={})
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM purchases").fetchone()["c"]
    assert count == 0


def test_no_cashfree_order_is_created_while_locked(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-no-cashfree-order@example.com")
    gateway = CashfreeGateway("cf_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real", "SANDBOX")
    monkeypatch.setattr(app.state, "payment_gateway", gateway)

    called = {"post": False}
    import app.cashfree_gateway as cf_module
    monkeypatch.setattr(cf_module.httpx, "post", lambda *a, **k: called.update(post=True))

    resp = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    assert resp.status_code == 403
    assert called["post"] is False  # Cashfree's own API was never even contacted


def test_no_razorpay_order_is_created_while_locked(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-no-razorpay-order@example.com")
    from unittest.mock import MagicMock
    gateway = RazorpayGateway("rzp_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real")
    gateway._client = MagicMock()
    monkeypatch.setattr(app.state, "payment_gateway", gateway)

    resp = client.post("/career-intelligence/purchase", json={})
    assert resp.status_code == 403
    gateway._client.order.create.assert_not_called()


def test_no_entitlement_can_be_created_through_the_purchase_flow_while_locked(app_and_client, db, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-no-entitlement@example.com")

    client.post("/career-intelligence/purchase", json={})
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM entitlements").fetchone()["c"]
    assert count == 0


def test_price_preview_reports_launch_locked_true_before_launch(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "lock-preview-ci@example.com")
    assert client.get("/career-intelligence/price").json()["launch_locked"] is True
    assert client.get("/diagnosis/price").json()["launch_locked"] is True


def test_purchase_becomes_available_after_the_configured_launch_date(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", "2020-01-01T00:00:00+00:00")
    login_via_magic_link(client, app, "lock-after-launch@example.com")

    assert client.get("/career-intelligence/price").json()["launch_locked"] is False
    resp = client.post("/career-intelligence/purchase", json={})
    assert resp.status_code == 200, resp.text
