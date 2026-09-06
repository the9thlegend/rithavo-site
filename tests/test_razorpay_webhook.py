"""
Phase 2B-2 Task 6 — the dedicated Razorpay webhook endpoint
(POST /payments/razorpay/webhook). Signature verification is checked
against a REAL HMAC-SHA256 computed the same way Razorpay's own SDK
does it (not mocked here, unlike the gateway unit tests) — this file
proves the actual raw-body-verification wiring works end to end, using
a throwaway fake secret, never a real credential.
"""

import hashlib
import hmac
import json

from unittest.mock import MagicMock

from app.razorpay_gateway import RazorpayGateway
from .conftest import login_via_magic_link

WEBHOOK_SECRET = "fake-webhook-secret-not-real"


def _install_razorpay_gateway(app, monkeypatch):
    """Only order.create (a real network call) is mocked — utility.verify_webhook_signature
    is left as the REAL SDK method, so these tests exercise genuine
    HMAC-SHA256 verification, not a mock that would rubber-stamp any
    signature. See the module docstring."""
    gateway = RazorpayGateway("rzp_test_fake", "fake-secret-not-real", WEBHOOK_SECRET)
    gateway._client.order.create = MagicMock(
        side_effect=lambda data: {"id": f"order_fake_{data['notes']['purchase_id']}"}
    )
    monkeypatch.setattr(app.state, "payment_gateway", gateway)
    return gateway


def _signed_webhook_request(client, body: dict, secret: str = WEBHOOK_SECRET):
    raw = json.dumps(body).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/payments/razorpay/webhook",
        content=raw,
        headers={"content-type": "application/json", "x-razorpay-signature": signature},
    )


def _payment_captured_payload(order_id, payment_id="pay_webhook_1"):
    return {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, "status": "captured"}}},
    }


def _payment_failed_payload(order_id, payment_id="pay_webhook_fail"):
    return {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, "status": "failed"}}},
    }


def test_webhook_404s_when_razorpay_is_not_the_active_gateway(app_and_client):
    """Default test fixture uses TestPaymentGateway — the Razorpay-
    specific webhook route must not pretend to handle anything in that
    configuration."""
    app, client = app_and_client
    resp = _signed_webhook_request(client, _payment_captured_payload("order_x"))
    assert resp.status_code == 404


def test_valid_signature_confirms_the_purchase_and_creates_one_entitlement(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "webhook-success@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()

    resp = _signed_webhook_request(client, _payment_captured_payload(started["razorpay_order_id"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"

    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSTIC'",
            (user_id,),
        ).fetchone()["c"]
    assert count == 1


def test_invalid_signature_is_rejected_and_never_confirms_anything(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-bad-sig@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()

    resp = _signed_webhook_request(client, _payment_captured_payload(started["razorpay_order_id"]), secret="wrong-secret")
    assert resp.status_code == 400

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "PENDING"


def test_duplicate_webhook_delivery_is_idempotent(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-duplicate@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/career-intelligence/purchase", json={}).json()

    first = _signed_webhook_request(client, _payment_captured_payload(started["razorpay_order_id"]))
    second = _signed_webhook_request(client, _payment_captured_payload(started["razorpay_order_id"]))
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["entitlement_id"] == second.json()["entitlement_id"]

    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 1


def test_payment_failed_event_marks_the_purchase_failed(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-failed@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()

    resp = _signed_webhook_request(client, _payment_failed_payload(started["razorpay_order_id"]))
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "FAILED"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 0


def test_unknown_order_id_is_acknowledged_but_ignored(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-unknown@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    resp = _signed_webhook_request(client, _payment_captured_payload("order_never_created_by_us"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_unrecognized_event_type_is_acknowledged_and_ignored(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-unrecognized@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()

    resp = _signed_webhook_request(client, {
        "event": "order.paid",
        "payload": {"payment": {"entity": {"id": "pay_x", "order_id": started["razorpay_order_id"]}}},
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "PENDING"  # untouched


def test_webhook_never_trusts_a_user_id_in_the_payload(app_and_client, db, monkeypatch):
    """A malicious/malformed payload claiming to be for a different user
    must still only ever resolve/affect the purchase matching the real
    gateway_reference — there is no user-identity field this route reads
    from the payload at all (verified structurally: the route only ever
    looks up by order_id)."""
    app, client = app_and_client
    attacker_id = login_via_magic_link(client, app, "webhook-attacker@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    victim_id = db.get_or_create_user("webhook-victim@example.com")
    victim_purchase = db.begin_ad_purchase(victim_id)
    db.mark_purchase_pending(victim_purchase["purchase_id"], "order_victim_webhook")

    payload = _payment_captured_payload("order_victim_webhook")
    payload["payload"]["payment"]["entity"]["notes"] = {"attacker_user_id": attacker_id}  # ignored, not a real field
    resp = _signed_webhook_request(client, payload)
    assert resp.status_code == 200

    victim_row = db.get_purchase(victim_purchase["purchase_id"])
    assert victim_row["payment_status"] == "SUCCEEDED"
    entitlement = db.get_entitlement(victim_row["entitlement_id"])
    assert entitlement["user_id"] == victim_id  # never the attacker


def test_webhook_secret_never_appears_in_a_failure_response(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-secret-leak@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()

    resp = _signed_webhook_request(client, _payment_captured_payload(started["razorpay_order_id"]), secret="wrong")
    assert WEBHOOK_SECRET not in resp.text


# ---- Phase 2B-2.1: FAILED -> SUCCEEDED retry, at the webhook layer ----

def test_payment_failed_then_payment_captured_for_the_same_order_confirms_the_purchase(app_and_client, db, monkeypatch):
    """Test B: the same real-world sequence Phase 2B-2 verification hit —
    Razorpay delivers payment.failed for a first attempt, then
    payment.captured for a retry on the SAME order. Before this phase
    the second delivery was silently ignored ('state_conflict'); it must
    now confirm the purchase and create exactly one entitlement."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "webhook-failed-then-captured@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    order_id = started["razorpay_order_id"]

    failed_resp = _signed_webhook_request(client, _payment_failed_payload(order_id, payment_id="pay_attempt_1"))
    assert failed_resp.status_code == 200
    assert db.get_purchase(started["purchase_id"])["payment_status"] == "FAILED"

    captured_resp = _signed_webhook_request(client, _payment_captured_payload(order_id, payment_id="pay_attempt_2"))
    assert captured_resp.status_code == 200, captured_resp.text
    assert captured_resp.json()["status"] == "confirmed"

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "SUCCEEDED"
    with db.connect() as conn:
        entitlements = conn.execute(
            "SELECT * FROM entitlements WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSTIC'", (user_id,)
        ).fetchall()
    assert len(entitlements) == 1


def test_repeated_payment_failed_events_remain_idempotent_and_harmless(app_and_client, db, monkeypatch):
    """Test E: Razorpay may redeliver the same payment.failed event more
    than once — this must never error and must never itself create
    state beyond FAILED (fail_ad_purchase's own WHERE clause already
    guards this; proven here at the HTTP layer)."""
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-repeated-failed@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/career-intelligence/purchase", json={}).json()
    order_id = started["razorpay_order_id"]

    first = _signed_webhook_request(client, _payment_failed_payload(order_id, payment_id="pay_fail_dup"))
    second = _signed_webhook_request(client, _payment_failed_payload(order_id, payment_id="pay_fail_dup"))
    assert first.status_code == 200
    assert second.status_code == 200

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "FAILED"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 0


def test_repeated_payment_captured_after_a_recovered_failure_does_not_duplicate_entitlement(app_and_client, db, monkeypatch):
    """Test F, specifically on the recovery path: once a FAILED purchase
    has been recovered to SUCCEEDED by a captured webhook, a duplicate
    delivery of that SAME captured event must remain idempotent — same
    entitlement, no second row."""
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-recovered-duplicate@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/career-intelligence/purchase", json={}).json()
    order_id = started["razorpay_order_id"]

    _signed_webhook_request(client, _payment_failed_payload(order_id, payment_id="pay_recover_attempt_1"))
    first_captured = _signed_webhook_request(
        client, _payment_captured_payload(order_id, payment_id="pay_recover_attempt_2"))
    second_captured = _signed_webhook_request(
        client, _payment_captured_payload(order_id, payment_id="pay_recover_attempt_2"))
    assert first_captured.status_code == 200
    assert second_captured.status_code == 200
    assert first_captured.json()["entitlement_id"] == second_captured.json()["entitlement_id"]

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "SUCCEEDED"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 1
