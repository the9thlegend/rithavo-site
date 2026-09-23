"""
Cashfree Payment Gateway (Sandbox) — end-to-end purchase flow through
the real HTTP routes, with CashfreeGateway's underlying httpx calls
mocked (never a real network call). Covers Career Intelligence (flat
₹799) and Application Diagnostic (existing ladder), server-side price
authority, the return-confirm flow (order status re-fetched from
Cashfree, never trusted from the client), idempotency, and security
(cross-user isolation).

Razorpay's own tests (test_razorpay_purchase.py) are untouched and
still exercise Razorpay's own gateway unchanged -- these two gateways
never interact with each other.
"""

import config
from app.cashfree_gateway import CashfreeGateway
from .conftest import login_via_magic_link


def _install_cashfree_gateway(app, monkeypatch):
    """Swaps app.state.payment_gateway for a real CashfreeGateway whose
    underlying httpx calls are mocked at the module boundary -- the
    same convention test_photo_access.py/test_internal_client.py
    already established for this service's other server-to-server
    calls."""
    gateway = CashfreeGateway("cf_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real", "SANDBOX")
    monkeypatch.setattr(app.state, "payment_gateway", gateway)
    return gateway


class _FakeResponse:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body


def _mock_order_creation(monkeypatch, order_status="ACTIVE"):
    import app.cashfree_gateway as cf_module
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(200, {
            "cf_order_id": "cf_internal_12345",
            "order_id": json["order_id"],
            "payment_session_id": f"session_{json['order_id']}",
            "order_status": order_status,
        })

    monkeypatch.setattr(cf_module.httpx, "post", _fake_post)
    return captured


def _mock_order_status(monkeypatch, status: str):
    import app.cashfree_gateway as cf_module
    captured = {}

    def _fake_get(url, headers, timeout):
        captured["url"] = url
        return _FakeResponse(200, {"order_status": status})

    monkeypatch.setattr(cf_module.httpx, "get", _fake_get)
    return captured


# ---- Career Intelligence: order creation, server-side price authority ----

def test_ci_purchase_creates_a_cashfree_order_with_server_computed_amount(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ci-purchase@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    captured = _mock_order_creation(monkeypatch)

    resp = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["amount_inr"] == 799
    assert data["payment_status"] == "PENDING"
    assert data["cashfree_payment_session_id"]
    assert captured["json"]["order_amount"] == 799.0
    assert captured["json"]["order_currency"] == "INR"


def test_customer_cannot_submit_a_different_amount(app_and_client, monkeypatch):
    """The purchase-creation route accepts no amount field at all --
    proving there is nothing to override. Cashfree's own order_amount
    is always the server-computed CAREER_INTELLIGENCE_PRICE_INR."""
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-no-override@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    captured = _mock_order_creation(monkeypatch)

    client.post("/career-intelligence/purchase", json={"amount_inr": 1, "customer_phone": "9876543210"})
    assert captured["json"]["order_amount"] == 799.0


def test_ad_purchase_creates_a_cashfree_order(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ad-purchase@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    captured = _mock_order_creation(monkeypatch)

    resp = client.post("/diagnosis/purchase", json={"customer_phone": "9876543210"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["amount_inr"] == 399
    assert captured["json"]["order_amount"] == 399.0


# ---- Confirm: server-side status re-fetch, never a client claim ----

def test_ci_purchase_confirms_to_exactly_one_entitlement_when_paid(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "cf-ci-paid@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]

    _mock_order_status(monkeypatch, "PAID")
    confirmed = client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    with db.connect() as conn:
        entitlements = conn.execute(
            "SELECT * FROM entitlements WHERE user_id = ? AND product = 'career_intelligence'", (user_id,)
        ).fetchall()
    assert len(entitlements) == 1
    assert entitlements[0]["status"] == "ACTIVE"

    purchase = db.get_purchase(purchase_id)
    assert purchase["payment_status"] == "SUCCEEDED"
    assert purchase["gateway"] == "cashfree"


def test_confirm_is_rejected_when_order_still_active(app_and_client, db, monkeypatch):
    """Returning from Cashfree checkout must NOT itself mean success --
    an ACTIVE (not yet paid) order must never confirm."""
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ci-pending@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]

    _mock_order_status(monkeypatch, "ACTIVE")
    confirmed = client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
    assert confirmed.status_code == 402

    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM entitlements WHERE product = 'career_intelligence'").fetchone()["c"]
    assert count == 0


def test_confirm_is_rejected_for_an_expired_order(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ci-expired@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]

    _mock_order_status(monkeypatch, "EXPIRED")
    confirmed = client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
    assert confirmed.status_code == 402


def test_confirm_handles_cashfree_api_failure_without_confirming(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ci-api-failure@example.com")
    gateway = _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]

    import app.cashfree_gateway as cf_module
    monkeypatch.setattr(cf_module.httpx, "get", lambda url, headers, timeout: _FakeResponse(500))
    confirmed = client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
    assert confirmed.status_code == 402


def test_confirming_twice_does_not_create_a_second_entitlement(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ci-double-confirm@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]

    _mock_order_status(monkeypatch, "PAID")
    client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
    second = client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
    assert second.status_code == 200
    assert second.json()["already_confirmed"] is True

    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM entitlements WHERE product = 'career_intelligence'").fetchone()["c"]
    assert count == 1


def test_ci_entitlement_receives_existing_validity_rules(app_and_client, db, monkeypatch):
    """Do not rewrite CI validity -- 30-day expiry, 3 evaluations, and
    the existing one-active-entitlement enforcement all come from the
    SAME confirm_ci_purchase/entitlements machinery Razorpay uses."""
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ci-validity@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]
    _mock_order_status(monkeypatch, "PAID")
    client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")

    with db.connect() as conn:
        entitlement = conn.execute(
            "SELECT * FROM entitlements WHERE product = 'career_intelligence' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert entitlement["expires_at"] is not None
    activated = entitlement["activated_at"]
    assert entitlement["expires_at"] > activated  # a real future expiry was set, not left NULL


def test_ad_entitlement_receives_existing_approved_rules(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-ad-validity@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/diagnosis/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]
    _mock_order_status(monkeypatch, "PAID")
    confirmed = client.post(f"/diagnosis/purchase/{purchase_id}/confirm-cashfree")
    assert confirmed.status_code == 200

    with db.connect() as conn:
        entitlement = conn.execute(
            "SELECT * FROM entitlements WHERE product = 'APPLICATION_DIAGNOSTIC' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert entitlement is not None
    assert entitlement["status"] == "ACTIVE"


# ---- Security ----

def test_cannot_confirm_another_users_purchase(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-victim@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)
    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    purchase_id = started.json()["purchase_id"]

    login_via_magic_link(client, app, "cf-attacker@example.com")
    _mock_order_status(monkeypatch, "PAID")
    resp = client.post(f"/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
    assert resp.status_code == 404


def test_confirm_route_404s_when_cashfree_is_not_the_active_gateway(app_and_client, monkeypatch):
    """Default test fixture uses TestPaymentGateway -- confirming a
    (nonexistent) purchase via the Cashfree-specific route must not
    even reach ownership checks when Cashfree isn't the active
    gateway."""
    app, client = app_and_client
    login_via_magic_link(client, app, "cf-inactive-gateway@example.com")
    resp = client.post("/career-intelligence/purchase/1/confirm-cashfree")
    assert resp.status_code == 400
