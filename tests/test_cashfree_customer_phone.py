"""
Cashfree customer-phone phase — the CUSTOMER's own mobile number
(never the merchant/KYC contact number, never a hardcoded placeholder),
required and validated server-side only when Cashfree is the active
gateway. Razorpay is untouched (test_razorpay_purchase.py's own
structural test already proves it accepts and ignores this field).
"""

from app.cashfree_gateway import CashfreeGateway
from app.razorpay_gateway import RazorpayGateway
from .conftest import login_via_magic_link


def _install_cashfree_gateway(app, monkeypatch):
    gateway = CashfreeGateway("cf_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real", "SANDBOX")
    monkeypatch.setattr(app.state, "payment_gateway", gateway)
    return gateway


class _FakeResponse:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body


def _mock_order_creation(monkeypatch):
    import app.cashfree_gateway as cf_module
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["json"] = json
        return _FakeResponse(200, {"order_id": json["order_id"], "payment_session_id": "session_x", "order_status": "ACTIVE"})

    monkeypatch.setattr(cf_module.httpx, "post", _fake_post)
    return captured


def test_valid_phone_is_accepted_and_forwarded_to_cashfree(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "phone-valid@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    captured = _mock_order_creation(monkeypatch)

    resp = client.post("/career-intelligence/purchase", json={"customer_phone": "9876543210"})
    assert resp.status_code == 200, resp.text
    assert captured["json"]["customer_details"]["customer_phone"] == "9876543210"


def test_missing_phone_is_rejected_when_cashfree_is_active(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "phone-missing@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    resp = client.post("/career-intelligence/purchase", json={})
    assert resp.status_code == 400
    assert "valid" in resp.json()["detail"].lower()


def test_invalid_phone_formats_are_rejected(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "phone-invalid@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    for bad_phone in ["12345", "12345678901", "5876543210", "abcdefghij", "98765 43210", ""]:
        resp = client.post("/career-intelligence/purchase", json={"customer_phone": bad_phone})
        assert resp.status_code == 400, f"expected 400 for {bad_phone!r}, got {resp.status_code}"


def test_valid_phone_is_persisted_on_the_purchase_row(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "phone-persisted@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    started = client.post("/career-intelligence/purchase", json={"customer_phone": "9123456780"})
    purchase = db.get_purchase(started.json()["purchase_id"])
    assert purchase["customer_phone"] == "9123456780"


def test_phone_is_never_stored_on_users_or_career_profiles(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "phone-not-on-user@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    _mock_order_creation(monkeypatch)

    client.post("/career-intelligence/purchase", json={"customer_phone": "9123456780"})

    user_row = dict(db.get_user_by_id(user_id))
    assert "phone" not in {k.lower() for k in user_row.keys()}
    career_profile = db.get_career_profile(user_id)
    if career_profile is not None:
        assert "phone" not in career_profile["profile_json"].lower()


def test_merchant_kyc_number_is_never_used_as_a_customer_default(app_and_client, monkeypatch):
    """The merchant's own Cashfree account contact number is separate,
    account-level information this service never touches or defaults
    to -- a purchase with no customer-supplied phone must be rejected,
    never silently filled in with anything."""
    app, client = app_and_client
    login_via_magic_link(client, app, "phone-no-merchant-default@example.com")
    _install_cashfree_gateway(app, monkeypatch)
    captured = _mock_order_creation(monkeypatch)

    resp = client.post("/career-intelligence/purchase", json={})
    assert resp.status_code == 400
    assert "json" not in captured  # no Cashfree order was ever created


def test_razorpay_purchase_flow_is_unaffected_by_the_phone_requirement(app_and_client, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "phone-razorpay-unaffected@example.com")
    gateway = RazorpayGateway("rzp_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real")
    from unittest.mock import MagicMock
    gateway._client = MagicMock()
    gateway._client.order.create.side_effect = lambda data: {"id": f"order_fake_{data['notes']['purchase_id']}"}
    monkeypatch.setattr(app.state, "payment_gateway", gateway)

    resp = client.post("/career-intelligence/purchase", json={})  # no phone at all
    assert resp.status_code == 200, resp.text
