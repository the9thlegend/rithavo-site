"""
Phase 2B-2 — real payment via Razorpay Standard Checkout / Orders API.

Satisfies the exact same PaymentGateway interface TestPaymentGateway
does (payment_gateway.py's own docstring: "Swapping in a real provider
later... means writing one new class satisfying this same interface...
nothing in main.py, db.py, or pricing.py needs to change") — selected by
get_gateway() only when all three env vars below are present; falls
back to TestPaymentGateway otherwise, exactly mirroring
email_sender.get_email_sender()'s Console/Zoho selection pattern.

Credentials — RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
RAZORPAY_WEBHOOK_SECRET — are read from the environment only, never
hardcoded, never logged. The Key Secret and Webhook Secret never leave
this module; the Key ID (a public, non-secret value by Razorpay's own
design — required client-side to open Checkout) is the only one ever
returned to a caller (create_payment_intent's razorpay_key_id field).

Amounts are always computed server-side by Database.begin_ad_purchase/
begin_ci_purchase (both already race-safe against this same user's
concurrent attempts, see db.py) and passed to Razorpay in paise — this
module never accepts, trusts, or forwards a client-supplied amount.
"""

import os

from app.payment_gateway import PaymentGateway


class RazorpayVerificationError(Exception):
    """Raised whenever a payment or webhook signature fails
    verification, or a payment's provider-side status isn't actually
    captured/successful. Deliberately a single, generic exception type
    — the caller (main.py) always turns this into the same safe, no-
    detail 402/400 response; the specific reason is only ever logged
    server-side, never echoed to the client."""


class RazorpayGateway(PaymentGateway):
    name = "razorpay"

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str):
        import razorpay  # deferred: only imported when this gateway is actually selected
        self._key_id = key_id
        self._webhook_secret = webhook_secret
        self._client = razorpay.Client(auth=(key_id, key_secret))

    def create_payment_intent(self, purchase_id: int, amount_inr: int, user_email: str, customer_phone: str = None) -> dict:
        """Creates a Razorpay Order for the server-calculated amount.
        The order's own `notes.purchase_id` and `receipt` both carry the
        internal purchase id, purely for reconciliation/support lookups
        on Razorpay's own dashboard — nothing in this service's own
        verification logic trusts them; verify_payment/verify_webhook
        always re-derive the purchase from OUR OWN gateway_reference
        column, never from a client- or provider-echoed value alone.

        customer_phone: accepted only to satisfy the shared
        PaymentGateway signature — Razorpay's own existing customer
        flow is completely unmodified and never uses it; Razorpay
        Checkout collects/prefills contact details entirely on its own
        widget, unrelated to this parameter."""
        order = self._client.order.create({
            "amount": amount_inr * 100,  # Razorpay amounts are in paise
            "currency": "INR",
            "receipt": f"rithavo_purchase_{purchase_id}",
            "notes": {"purchase_id": str(purchase_id)},
        })
        return {
            "gateway": self.name,
            "purchase_id": purchase_id,
            "gateway_reference": order["id"],
            "amount_inr": amount_inr,
            "razorpay_order_id": order["id"],
            "razorpay_key_id": self._key_id,
        }

    def verify_webhook(self, payload: dict, headers: dict) -> dict:
        """Razorpay's real webhook verification needs the RAW request
        body bytes (an HMAC over the exact bytes Razorpay sent) — a
        pre-parsed dict is not sufficient and re-serializing it is not
        guaranteed byte-identical. The dedicated route
        (POST /payments/razorpay/webhook, main.py) captures raw bytes
        itself and calls verify_webhook_signature() below directly,
        rather than going through this abstract-interface method at
        all. Implemented here (ABC requires it) only to fail loudly if
        ever mistakenly reached, rather than silently verifying nothing."""
        raise NotImplementedError(
            "RazorpayGateway.verify_webhook is not used — call verify_webhook_signature() "
            "with the raw request body from the dedicated /payments/razorpay/webhook route."
        )

    def verify_payment_signature(self, order_id: str, payment_id: str, signature: str) -> None:
        """Task 5's server-side verification, exactly per Razorpay's
        documented mechanism (their own SDK's utility.verify_payment_signature —
        an HMAC-SHA256 of "{order_id}|{payment_id}" using the Key
        Secret, constant-time compared to the client-supplied signature).
        Raises RazorpayVerificationError on any mismatch; the caller
        must not proceed to confirm the purchase if this raises."""
        from razorpay.errors import SignatureVerificationError
        try:
            self._client.utility.verify_payment_signature({
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            })
        except SignatureVerificationError as exc:
            raise RazorpayVerificationError("payment signature verification failed") from exc

    def fetch_payment_status(self, payment_id: str) -> str:
        """Belt-and-braces beyond signature verification alone (Task 5:
        "verify payment/order status server-side before fulfilling") —
        a valid signature proves the payment_id/order_id pair is
        authentic, but not necessarily that the payment actually
        captured (e.g. an account using manual capture could have an
        `authorized`-but-not-yet-`captured` payment). Fetches the
        payment directly from Razorpay's API and returns its `status`
        field ("captured" is the only value this service ever treats
        as fulfillable)."""
        payment = self._client.payment.fetch(payment_id)
        return payment["status"]

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> None:
        """Task 6. Verifies Razorpay's X-Razorpay-Signature header
        against the RAW request body (not a re-parsed/re-serialized
        dict — see verify_webhook's docstring above) using the Webhook
        Secret (distinct from the Key Secret). Raises
        RazorpayVerificationError on mismatch."""
        from razorpay.errors import SignatureVerificationError
        try:
            self._client.utility.verify_webhook_signature(
                raw_body.decode("utf-8"), signature, self._webhook_secret,
            )
        except SignatureVerificationError as exc:
            raise RazorpayVerificationError("webhook signature verification failed") from exc


def get_razorpay_gateway_if_configured():
    """None if any of the three required env vars is missing — the
    caller (payment_gateway.get_gateway) falls back to TestPaymentGateway
    in that case, exactly mirroring email_sender.get_email_sender()'s
    ConsoleEmailSender fallback when Zoho isn't configured."""
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if key_id and key_secret and webhook_secret:
        return RazorpayGateway(key_id, key_secret, webhook_secret)
    return None
