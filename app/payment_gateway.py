"""
Payment gateway abstraction.

The real provider is Cashfree (app/cashfree_gateway.py), selected below only when its
required environment variables are configured (sandbox until a separate payment
activation approval). TestPaymentGateway remains the zero-config default for
local dev and every automated test: no network call, no real money, nothing that
could ever be mistaken for a live integration. Razorpay was removed after it was
shown that no environment configured it and nothing live depended on it.
"""

from abc import ABC, abstractmethod


class PaymentGateway(ABC):
    name: str

    @abstractmethod
    def create_payment_intent(self, purchase_id: int, amount_inr: int, user_email: str, customer_phone: str = None) -> dict:
        """Start a payment for an already-created PENDING purchase row.
        Returns whatever the frontend needs to complete payment (for a
        real gateway: a checkout session/order id; for the test gateway: a
        deterministic reference the test can immediately confirm with).

        customer_phone (Cashfree customer-phone phase): optional, and
        ignored entirely by gateways that don't need it (Test) — added here only so main.py's purchase routes can call
        every gateway through one shared signature. Never a merchant
        contact number, never a hardcoded placeholder; validated
        server-side in main.py before this is ever called."""
        raise NotImplementedError

    @abstractmethod
    def verify_webhook(self, payload: dict, headers: dict) -> dict:
        """Verify an inbound webhook's authenticity and extract the
        (purchase_id, gateway_reference, outcome) it reports. A real
        gateway adapter MUST cryptographically verify the payload here
        (e.g. an HMAC signature header) before trusting anything in it —
        this is the one place a fake success callback could otherwise
        forge a paid entitlement."""
        raise NotImplementedError


class TestPaymentGateway(PaymentGateway):
    """Deterministic, in-process simulation for Phase 1 and for automated
    tests. No network call, no real money, nothing that could ever be
    mistaken for a live integration. `create_payment_intent` immediately
    returns a reference the caller can pass straight to `simulate_outcome`
    (used directly by tests) or to `verify_webhook` (used by the actual
    /payments/webhook route, so that route's own logic is exercised the
    same way a real gateway's webhook would exercise it)."""

    name = "test"

    def create_payment_intent(self, purchase_id: int, amount_inr: int, user_email: str, customer_phone: str = None) -> dict:
        reference = f"test_pay_{purchase_id}_{amount_inr}"
        return {"gateway": self.name, "purchase_id": purchase_id, "gateway_reference": reference,
                "amount_inr": amount_inr}

    def verify_webhook(self, payload: dict, headers: dict) -> dict:
        # The test gateway's "signature" is a fixed shared-secret header —
        # intentionally simple, but still a real check: a payload missing
        # or mismatching it is rejected, exactly as a real HMAC check
        # would reject a forged/unsigned webhook call.
        if headers.get("X-Test-Gateway-Secret") != "test-gateway-shared-secret":
            raise ValueError("invalid webhook signature")
        required = {"purchase_id", "gateway_reference", "outcome"}
        if not required.issubset(payload):
            raise ValueError(f"webhook payload missing required fields: {required - set(payload)}")
        if payload["outcome"] not in ("SUCCEEDED", "FAILED"):
            raise ValueError(f"unknown outcome: {payload['outcome']}")
        return {
            "purchase_id": payload["purchase_id"],
            "gateway_reference": payload["gateway_reference"],
            "outcome": payload["outcome"],
        }


def get_gateway() -> PaymentGateway:
    """Cashfree (sandbox) when its env vars are configured, otherwise the zero-config test gateway."""
    from app.cashfree_gateway import get_cashfree_gateway_if_configured
    cashfree_gateway = get_cashfree_gateway_if_configured()
    if cashfree_gateway is not None:
        return cashfree_gateway
    return TestPaymentGateway()
