"""
Cashfree Payment Gateway — Sandbox implementation (Final Explore
Corrections + Cashfree Sandbox phase).

Satisfies the exact same PaymentGateway interface RazorpayGateway does
(app/payment_gateway.py's own docstring) — selected by get_gateway()
only when all required env vars below are present, exactly mirroring
RazorpayGateway's own get_razorpay_gateway_if_configured() precedent.
Razorpay is checked FIRST and is completely unmodified by this file;
Cashfree only ever becomes active in an environment where Razorpay's
own three env vars are absent.

API contract (verified against Cashfree's current live documentation,
not an assumed/outdated pattern, September 2026):
  - Create Order:  POST {base}/orders          -> payment_session_id
  - Get Order:     GET  {base}/orders/{order_id} -> order_status
      ("PAID" is the only status this module treats as fulfillable;
      ACTIVE/EXPIRED/TERMINATED/TERMINATION_REQUESTED are all "not yet
      paid" or "will never be paid" — never treated as success)
  - Headers: x-client-id, x-client-secret, x-api-version
  - Webhook signature: base64(HMAC-SHA256(timestamp + raw_body,
    secret)), verified against the x-webhook-signature header using
    the x-webhook-timestamp header's own value — the RAW request body,
    never a re-parsed/re-serialized dict (same requirement as
    Razorpay's own verify_webhook_signature).

Credentials — CASHFREE_APP_ID / CASHFREE_SECRET_KEY / CASHFREE_ENVIRONMENT
/ CASHFREE_WEBHOOK_SECRET — are read from the environment only, never
hardcoded, never logged. Nuance found while verifying against current
documentation: Cashfree does not issue a webhook-specific secret
distinct from the API secret by default (it signs webhooks with the
same client secret) — CASHFREE_WEBHOOK_SECRET is still accepted as its
own named variable (matching how this codebase already names Razorpay's
three credentials separately) and, if unset, this module falls back to
CASHFREE_SECRET_KEY so the sandbox works correctly out of the box; set
it explicitly only if a specific Cashfree account is ever configured
with a distinct signing key.

Amounts are always computed server-side by Database.begin_ad_purchase/
begin_ci_purchase and passed to Cashfree in rupees (not paise — unlike
Razorpay, Cashfree's order_amount is already a decimal rupee value) —
this module never accepts, trusts, or forwards a client-supplied
amount.

customer_phone is a REQUIRED field on Cashfree's Create Order API and
Rithavo does not collect a phone number anywhere in this product today
— a fixed placeholder is used (see _PLACEHOLDER_CUSTOMER_PHONE) for
this Sandbox phase. This is a genuine, documented limitation: a real
phone number (or confirmation from Cashfree that it is waivable for
this merchant category) is required before production activation —
see this phase's own report.
"""

import os
import secrets
import hashlib
import hmac
import base64

import httpx

from app.payment_gateway import PaymentGateway

_SANDBOX_BASE_URL = "https://sandbox.cashfree.com/pg"
_PRODUCTION_BASE_URL = "https://api.cashfree.com/pg"
# Pinned, not "latest" — verified against Cashfree's live documentation
# at implementation time (September 2026). Bump deliberately, not
# implicitly, if Cashfree deprecates this version.
_API_VERSION = "2025-01-01"
_REQUEST_TIMEOUT_SECONDS = 15.0
# See module docstring — no phone number is collected anywhere in this
# product today; this is a Sandbox-phase placeholder, not a production
# answer.
_PLACEHOLDER_CUSTOMER_PHONE = "9999999999"

_PAID_STATUS = "PAID"


class CashfreeVerificationError(Exception):
    """Raised whenever an order/payment status check or a webhook
    signature fails verification. Deliberately a single, generic
    exception type — the caller always turns this into the same safe,
    no-detail 402/400 response; the specific reason is only ever
    logged server-side, never echoed to the client."""


class CashfreeGateway(PaymentGateway):
    name = "cashfree"

    def __init__(self, app_id: str, secret_key: str, webhook_secret: str, environment: str):
        self._app_id = app_id
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret or secret_key
        self._base_url = _PRODUCTION_BASE_URL if environment.upper() == "PRODUCTION" else _SANDBOX_BASE_URL

    def _headers(self) -> dict:
        return {
            "x-client-id": self._app_id,
            "x-client-secret": self._secret_key,
            "x-api-version": _API_VERSION,
            "Content-Type": "application/json",
        }

    def create_payment_intent(self, purchase_id: int, amount_inr: int, user_email: str) -> dict:
        """Creates a Cashfree Order for the server-calculated amount.
        Unlike Razorpay (which mints its own order id), Cashfree
        requires THIS service to supply a unique order_id -- generated
        fresh per call so a retried/expired attempt on the same
        purchase row never collides with an earlier Cashfree order.
        gateway_reference (returned here, stored by the caller via
        Database.mark_purchase_pending exactly as it already does for
        Razorpay) is always OUR OWN order_id, never Cashfree's separate
        cf_order_id -- the webhook payload echoes back our own
        order_id, which is what get_purchase_by_gateway_reference needs
        to resolve the purchase."""
        order_id = f"rithavo_{purchase_id}_{secrets.token_hex(4)}"
        body = {
            "order_id": order_id,
            "order_amount": float(amount_inr),
            "order_currency": "INR",
            "customer_details": {
                "customer_id": f"rithavo_user_{purchase_id}",
                "customer_email": user_email,
                "customer_phone": _PLACEHOLDER_CUSTOMER_PHONE,
            },
        }
        try:
            resp = httpx.post(
                f"{self._base_url}/orders", json=body, headers=self._headers(), timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise CashfreeVerificationError(f"order creation request failed: {exc}")
        if resp.status_code not in (200, 201):
            raise CashfreeVerificationError(f"order creation failed: HTTP {resp.status_code}")
        data = resp.json()
        return {
            "gateway": self.name,
            "purchase_id": purchase_id,
            "gateway_reference": order_id,
            "amount_inr": amount_inr,
            "cashfree_order_id": order_id,
            "cashfree_payment_session_id": data.get("payment_session_id"),
        }

    def verify_webhook(self, payload: dict, headers: dict) -> dict:
        """Same reasoning as RazorpayGateway.verify_webhook: real
        verification needs the RAW request body (an HMAC over exact
        bytes), which a pre-parsed dict can't guarantee. The dedicated
        route (POST /payments/cashfree/webhook) captures raw bytes
        itself and calls verify_webhook_signature() directly."""
        raise NotImplementedError(
            "CashfreeGateway.verify_webhook is not used — call verify_webhook_signature() "
            "with the raw request body from the dedicated /payments/cashfree/webhook route."
        )

    def fetch_order_status(self, order_id: str) -> str:
        """The one authority this service trusts for 'did this order
        actually get paid' — never a client-side claim. 'PAID' is the
        only status ever treated as fulfillable."""
        try:
            resp = httpx.get(
                f"{self._base_url}/orders/{order_id}", headers=self._headers(), timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise CashfreeVerificationError(f"order status request failed: {exc}")
        if resp.status_code != 200:
            raise CashfreeVerificationError(f"order status fetch failed: HTTP {resp.status_code}")
        return resp.json().get("order_status", "")

    def verify_webhook_signature(self, raw_body: bytes, timestamp: str, signature: str) -> None:
        """Cashfree's documented mechanism: base64(HMAC-SHA256(timestamp
        + raw_body, secret)), constant-time compared to the
        x-webhook-signature header's value. timestamp is the exact
        x-webhook-timestamp header value — both must be the literal
        strings Cashfree sent, never reconstructed."""
        if not timestamp or not signature:
            raise CashfreeVerificationError("missing webhook signature or timestamp")
        message = timestamp.encode("utf-8") + raw_body
        expected = base64.b64encode(
            hmac.new(self._webhook_secret.encode("utf-8"), message, hashlib.sha256).digest()
        ).decode("utf-8")
        if not hmac.compare_digest(expected, signature):
            raise CashfreeVerificationError("webhook signature verification failed")


def get_cashfree_gateway_if_configured():
    """None if any required env var is missing — the caller
    (payment_gateway.get_gateway) falls back to TestPaymentGateway in
    that case. CASHFREE_WEBHOOK_SECRET is intentionally NOT required
    here (see module docstring: it falls back to CASHFREE_SECRET_KEY
    inside CashfreeGateway itself if unset)."""
    app_id = os.environ.get("CASHFREE_APP_ID")
    secret_key = os.environ.get("CASHFREE_SECRET_KEY")
    environment = os.environ.get("CASHFREE_ENVIRONMENT", "SANDBOX")
    webhook_secret = os.environ.get("CASHFREE_WEBHOOK_SECRET")
    if app_id and secret_key:
        return CashfreeGateway(app_id, secret_key, webhook_secret, environment)
    return None
