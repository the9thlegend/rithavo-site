"""
Payment gateway abstraction.

No real payment provider is integrated in this phase — there is no live
gateway account or API key anywhere in this project today (the existing
Career Intelligence product's purchases are entirely manual/Tally-form,
payment_source="tally_manual"). Rather than fabricate a live integration
or silently skip building the commercial flow, this module defines the
narrow interface a real gateway adapter would implement, backed by a
TestPaymentGateway that simulates success/failure/webhook-style
confirmation deterministically — no real money, no external network call,
fully exercised by the automated test suite.

Swapping in a real provider later (Razorpay is the natural default for
INR) means writing one new class satisfying this same interface and
selecting it in config — nothing in main.py, db.py, or pricing.py needs to
change.
"""

from abc import ABC, abstractmethod


class PaymentGateway(ABC):
    name: str

    @abstractmethod
    def create_payment_intent(self, purchase_id: int, amount_inr: int, user_email: str) -> dict:
        """Start a payment for an already-created PENDING purchase row.
        Returns whatever the frontend needs to complete payment (for a
        real gateway: a checkout session/order id; for the test gateway: a
        deterministic reference the test can immediately confirm with)."""
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

    def create_payment_intent(self, purchase_id: int, amount_inr: int, user_email: str) -> dict:
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
    return TestPaymentGateway()
