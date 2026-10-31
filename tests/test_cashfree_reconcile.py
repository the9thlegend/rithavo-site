"""
Cashfree reconciliation (POST /admin/payments/cashfree/reconcile) and the
webhook's malformed-payload handling. Cashfree's network calls are mocked; the
database, ownership, signature and confirm logic are real. No real credential.
"""

import base64
import hashlib
import hmac

import pytest

from app.cashfree_gateway import CashfreeGateway
from app.super_admin import bootstrap_super_admin

from .conftest import login_via_magic_link
from .test_cashfree_webhook import (
    WEBHOOK_SECRET, _FakeResponse, _create_pending_purchase, _install_cashfree_gateway, _success_payload,
)


def _order_status(monkeypatch, status_by_order=None, default="ACTIVE", fail=False):
    import app.cashfree_gateway as cf_module

    def fake_get(url, headers, timeout):
        if fail:
            raise cf_module.httpx.ConnectError("boom")
        order_id = url.rsplit("/", 1)[-1]
        return _FakeResponse(200, {"order_status": (status_by_order or {}).get(order_id, default)})

    monkeypatch.setattr(cf_module.httpx, "get", fake_get)


@pytest.fixture
def pending(app_and_client, db, monkeypatch):
    """One signed-in customer with one PENDING Cashfree purchase, then a signed-in Super Admin."""
    app, client = app_and_client
    customer_id = login_via_magic_link(client, app, "cf-reconcile-customer@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)
    client.post("/auth/logout")
    bootstrap_super_admin(db, "cf-reconcile-admin@example.com", "a-real-password")
    login_via_magic_link(client, app, "cf-reconcile-admin@example.com")
    return app, client, db, customer_id, purchase_id, order_id


def _entitlements(db, user_id):
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM entitlements WHERE user_id = ?", (user_id,)).fetchone()["c"]


def test_requires_a_super_admin(app_and_client, monkeypatch):
    app, client = app_and_client
    _install_cashfree_gateway(app, monkeypatch)
    assert client.post("/admin/payments/cashfree/reconcile").status_code == 401
    login_via_magic_link(client, app, "cf-reconcile-ordinary@example.com")
    assert client.post("/admin/payments/cashfree/reconcile").status_code == 403


def test_404_when_cashfree_is_not_the_active_gateway(app_and_client, db):
    app, client = app_and_client
    bootstrap_super_admin(db, "cf-reconcile-admin2@example.com", "a-real-password")
    login_via_magic_link(client, app, "cf-reconcile-admin2@example.com")
    assert client.post("/admin/payments/cashfree/reconcile").status_code == 404


def test_dry_run_reports_and_changes_nothing(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    report = client.post("/admin/payments/cashfree/reconcile").json()
    assert report["applied"] is False and report["counts"] == {"would_confirm": 1}
    assert db.get_purchase(purchase_id)["payment_status"] == "PENDING"
    assert _entitlements(db, customer_id) == 0


def test_apply_confirms_a_paid_order_exactly_once(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    first = client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()
    assert first["counts"] == {"confirmed": 1}
    assert db.get_purchase(purchase_id)["payment_status"] == "SUCCEEDED"
    assert _entitlements(db, customer_id) == 1
    second = client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()
    assert second["checked"] == 0                      # no longer unresolved
    assert _entitlements(db, customer_id) == 1


def test_a_late_webhook_after_reconciliation_does_not_double_grant(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply")
    from .test_cashfree_webhook import _signed_webhook_request
    resp = _signed_webhook_request(client, _success_payload(order_id))
    assert resp.status_code == 200 and resp.json().get("already_confirmed") is True
    assert _entitlements(db, customer_id) == 1


def test_unpaid_and_unknown_statuses_are_left_alone(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    for status in ("ACTIVE", "SOMETHING_NEW"):
        _order_status(monkeypatch, {order_id: status})
        report = client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()
        assert report["counts"] == {"left_unchanged": 1}
        assert db.get_purchase(purchase_id)["payment_status"] == "PENDING"


def test_expired_order_is_marked_failed_only_when_applied(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "EXPIRED"})
    assert client.post("/admin/payments/cashfree/reconcile").json()["counts"] == {"would_mark_failed": 1}
    assert db.get_purchase(purchase_id)["payment_status"] == "PENDING"
    assert client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()["counts"] == {"marked_failed": 1}
    assert db.get_purchase(purchase_id)["payment_status"] == "FAILED"
    assert _entitlements(db, customer_id) == 0


def test_a_failed_purchase_that_later_turns_out_paid_is_still_honoured(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    db.fail_ad_purchase(purchase_id)                 # e.g. an early "payment failed" webhook on the same order
    assert db.get_purchase(purchase_id)["payment_status"] == "FAILED"
    _order_status(monkeypatch, {order_id: "PAID"})
    assert client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()["counts"] == {"confirmed": 1}
    assert _entitlements(db, customer_id) == 1


def test_gateway_errors_change_nothing(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, fail=True)
    report = client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()
    assert report["counts"] == {"check_failed": 1}
    assert db.get_purchase(purchase_id)["payment_status"] == "PENDING"


def test_report_contains_no_personal_or_payment_data(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    text = client.post("/admin/payments/cashfree/reconcile").text
    for forbidden in ("cf-reconcile-customer", "9876543210", order_id, "@"):
        assert forbidden not in text


def test_other_gateways_orders_are_never_checked(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    with db.connect() as conn:
        conn.execute("UPDATE purchases SET gateway_reference = ? WHERE id = ?", ("order_razorpay_style", purchase_id))
    _order_status(monkeypatch, default="PAID")
    assert client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()["checked"] == 0


# ---- webhook: malformed but correctly signed bodies ----

def _signed(client, raw: bytes, timestamp="1700000000"):
    signature = base64.b64encode(hmac.new(WEBHOOK_SECRET.encode(), timestamp.encode() + raw, hashlib.sha256).digest()).decode()
    return client.post("/payments/cashfree/webhook", content=raw,
                       headers={"content-type": "application/json", "x-webhook-signature": signature,
                                "x-webhook-timestamp": timestamp})


@pytest.mark.parametrize("raw", [b"not json at all", b"[1, 2, 3]", b'"a string"', b'{"data": "x"}', b'{"data": {"order": 5}}'])
def test_signed_but_malformed_webhook_is_a_clean_400(app_and_client, monkeypatch, raw):
    app, client = app_and_client
    _install_cashfree_gateway(app, monkeypatch)
    resp = _signed(client, raw)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid payload."
