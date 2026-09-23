"""
Cashfree Payment Gateway (Sandbox) — the dedicated webhook endpoint
(POST /payments/cashfree/webhook). Signature verification is checked
against a REAL HMAC-SHA256 computed the same way Cashfree's own
documented mechanism works (not mocked here, unlike the gateway's
network calls) — this file proves the actual raw-body-verification
wiring works end to end, using a throwaway fake secret, never a real
credential.
"""

import base64
import hashlib
import hmac
import json

from app.cashfree_gateway import CashfreeGateway
from .conftest import login_via_magic_link

WEBHOOK_SECRET = "fake-webhook-secret-not-real"


def _install_cashfree_gateway(app, monkeypatch):
    gateway = CashfreeGateway("cf_test_fake", "fake-secret-not-real", WEBHOOK_SECRET, "SANDBOX")
    monkeypatch.setattr(app.state, "payment_gateway", gateway)
    return gateway


class _FakeResponse:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body


def _create_pending_purchase(app, client, monkeypatch, product_path="/career-intelligence/purchase"):
    import app.cashfree_gateway as cf_module

    def _fake_post(url, json, headers, timeout):
        return _FakeResponse(200, {
            "order_id": json["order_id"], "payment_session_id": "session_x", "order_status": "ACTIVE",
        })

    monkeypatch.setattr(cf_module.httpx, "post", _fake_post)
    resp = client.post(product_path, json={"customer_phone": "9876543210"})
    data = resp.json()
    return data["purchase_id"], data["gateway_reference"]


def _signed_webhook_request(client, body: dict, timestamp: str = "1700000000", secret: str = WEBHOOK_SECRET):
    raw = json.dumps(body).encode("utf-8")
    message = timestamp.encode("utf-8") + raw
    signature = base64.b64encode(hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()).decode("utf-8")
    return client.post(
        "/payments/cashfree/webhook",
        content=raw,
        headers={"content-type": "application/json", "x-webhook-signature": signature, "x-webhook-timestamp": timestamp},
    )


def _success_payload(order_id, payment_id="cf_pay_1"):
    return {
        "type": "PAYMENT_SUCCESS_WEBHOOK",
        "data": {"order": {"order_id": order_id}, "payment": {"cf_payment_id": payment_id, "payment_status": "SUCCESS"}},
    }


def _failed_payload(order_id, payment_id="cf_pay_fail"):
    return {
        "type": "PAYMENT_FAILED_WEBHOOK",
        "data": {"order": {"order_id": order_id}, "payment": {"cf_payment_id": payment_id, "payment_status": "FAILED"}},
    }


def test_webhook_404s_when_cashfree_is_not_the_active_gateway(app_and_client):
    """Default test fixture uses TestPaymentGateway -- the Cashfree-
    specific webhook route must not process a raw Cashfree-shaped
    payload from any other gateway configuration."""
    app, client = app_and_client
    resp = _signed_webhook_request(client, _success_payload("order_x"))
    assert resp.status_code == 404


def test_valid_signature_confirms_the_purchase(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "cf-webhook-success@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)

    resp = _signed_webhook_request(client, _success_payload(order_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"

    purchase = db.get_purchase(purchase_id)
    assert purchase["payment_status"] == "SUCCEEDED"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE user_id = ? AND product = 'career_intelligence'", (user_id,)
        ).fetchone()["c"]
    assert count == 1


def test_invalid_signature_is_rejected(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-webhook-bad-sig@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)

    resp = _signed_webhook_request(client, _success_payload(order_id), secret="wrong-secret")
    assert resp.status_code == 400

    purchase = app.state.db.get_purchase(purchase_id)
    assert purchase["payment_status"] != "SUCCEEDED"


def test_missing_signature_headers_are_rejected(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-webhook-missing-sig@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)

    raw = json.dumps(_success_payload(order_id)).encode("utf-8")
    resp = client.post("/payments/cashfree/webhook", content=raw, headers={"content-type": "application/json"})
    assert resp.status_code == 400


def test_duplicate_webhook_delivery_does_not_create_a_second_entitlement(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-webhook-duplicate@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)

    first = _signed_webhook_request(client, _success_payload(order_id))
    second = _signed_webhook_request(client, _success_payload(order_id))
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json().get("already_confirmed") is True

    with app.state.db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE product = 'career_intelligence'"
        ).fetchone()["c"]
    assert count == 1


def test_failure_event_marks_the_purchase_failed_without_an_entitlement(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-webhook-failed@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)

    resp = _signed_webhook_request(client, _failed_payload(order_id))
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    purchase = app.state.db.get_purchase(purchase_id)
    assert purchase["payment_status"] == "FAILED"
    with app.state.db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE product = 'career_intelligence'"
        ).fetchone()["c"]
    assert count == 0


def test_webhook_for_an_unknown_order_is_acknowledged_not_erred(app_and_client, monkeypatch):
    app, client = app_and_client
    _install_cashfree_gateway(app, monkeypatch)
    resp = _signed_webhook_request(client, _success_payload("order_never_created"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_unrecognized_event_type_is_ignored_not_erred(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-webhook-unrecognized@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)

    payload = {"type": "SOME_OTHER_EVENT", "data": {"order": {"order_id": order_id}}}
    resp = _signed_webhook_request(client, payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"

    purchase = app.state.db.get_purchase(purchase_id)
    assert purchase["payment_status"] == "PENDING"


def test_ad_product_webhook_confirms_the_correct_entitlement_type(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-webhook-ad@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch, product_path="/diagnosis/purchase")

    resp = _signed_webhook_request(client, _success_payload(order_id))
    assert resp.status_code == 200

    with app.state.db.connect() as conn:
        entitlement = conn.execute(
            "SELECT * FROM entitlements WHERE product = 'APPLICATION_DIAGNOSTIC' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert entitlement is not None
