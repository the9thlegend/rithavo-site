"""
Cashfree Payment Gateway — Sandbox implementation, confirm + webhook
routes (Final Explore Corrections + Cashfree Sandbox phase).

Order CREATION needs no new route at all: POST /career-intelligence/
purchase and POST /diagnosis/purchase (main.py, unchanged) already call
request.app.state.payment_gateway.create_payment_intent(...) generically
— when Cashfree is the active gateway (see payment_gateway.get_gateway),
those existing routes create Cashfree orders automatically.

What Cashfree's flow needs that Razorpay's didn't is here: confirming a
purchase after the customer returns from Cashfree's hosted checkout,
and receiving Cashfree's own server-to-server webhook. Unlike Razorpay
(which hands the browser an order_id/payment_id/signature triple to
submit back for verification), Cashfree's own recommended pattern needs
NOTHING from the browser at all on return — this service already knows
which Cashfree order it created for this purchase (gateway_reference,
stored at PENDING time), and independently asks Cashfree's own API
whether that exact order is PAID. The browser is never the authority
for amount, status, or entitlement creation, even more strictly than
the existing Razorpay path.

Both the return-confirm route and the webhook route funnel into the
SAME Database.confirm_ci_purchase/confirm_ad_purchase used by Razorpay
— already idempotent (a second confirm of an already-SUCCEEDED purchase
returns already_confirmed=True rather than creating a second
entitlement, see db.py's _confirm_purchase), so duplicate delivery
(webhook arrives after/before the browser's own return-confirm call)
can never grant two entitlements for one payment.
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from .cashfree_gateway import CashfreeVerificationError
from .security import owned_purchase, require_user

router = APIRouter()

logger = logging.getLogger("rithavo_web.cashfree")


def _verify_and_confirm_cashfree_payment(request: Request, purchase_id: int, expected_product: str, confirm_fn) -> dict:
    """Cashfree equivalent of main.py's _verify_and_confirm_razorpay_payment.
    Ownership: owned_purchase() raises 404 for a purchase belonging to
    another user before any of this runs. expected_product guards a
    caller confirming their OWN purchase_id but for the wrong product.
    Trusts nothing from the request body — re-derives the Cashfree
    order id purely from this purchase's own already-stored
    gateway_reference (set by create_payment_intent/mark_purchase_pending
    at PENDING time), then asks Cashfree directly whether THAT exact
    order is PAID."""
    db = request.app.state.db
    session_user_id = require_user(request)
    gateway = request.app.state.payment_gateway
    if getattr(gateway, "name", None) != "cashfree":
        raise HTTPException(status_code=400, detail="Cashfree payment verification is not configured.")

    purchase = owned_purchase(db, purchase_id, session_user_id)
    if purchase["product"] != expected_product:
        raise HTTPException(status_code=404, detail="Not found.")
    order_id = purchase["gateway_reference"]
    if not order_id:
        raise HTTPException(status_code=400, detail="This purchase has no payment in progress.")

    try:
        status = gateway.fetch_order_status(order_id)
    except CashfreeVerificationError:
        logger.warning("cashfree_confirm verification_failed purchase_id=%s", purchase_id)
        raise HTTPException(
            status_code=402,
            detail="We couldn't verify this payment. If money was deducted, it will be confirmed automatically "
                   "shortly — otherwise, please try again.",
        )
    if status != "PAID":
        logger.info("cashfree_confirm not_paid purchase_id=%s status=%s", purchase_id, status)
        raise HTTPException(status_code=402, detail="This payment has not completed yet.")

    return confirm_fn(purchase_id, order_id)


@router.post("/career-intelligence/purchase/{purchase_id}/confirm-cashfree")
def confirm_ci_purchase_cashfree(request: Request, purchase_id: int):
    db = request.app.state.db
    result = _verify_and_confirm_cashfree_payment(
        request, purchase_id, "CAREER_INTELLIGENCE",
        confirm_fn=lambda pid, order_id: db.confirm_ci_purchase(pid, order_id),
    )
    return {"status": "confirmed", **result}


@router.post("/diagnosis/purchase/{purchase_id}/confirm-cashfree")
def confirm_ad_purchase_cashfree(request: Request, purchase_id: int):
    db = request.app.state.db
    result = _verify_and_confirm_cashfree_payment(
        request, purchase_id, "APPLICATION_DIAGNOSIS",
        confirm_fn=lambda pid, order_id: db.confirm_ad_purchase(pid, order_id),
    )
    return {"status": "confirmed", **result}


# Recognized Cashfree webhook event types this endpoint acts on (per
# Cashfree's current documented payload shape). Every other event is
# acknowledged with 200 but otherwise ignored — an unrecognized event
# must never become a 4xx/5xx that makes Cashfree retry it forever, but
# also must never be silently treated as a success/failure it isn't.
_CASHFREE_SUCCESS_EVENTS = {"PAYMENT_SUCCESS_WEBHOOK"}
_CASHFREE_FAILURE_EVENTS = {"PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK"}


@router.post("/payments/cashfree/webhook")
async def cashfree_webhook(request: Request):
    """Verifies the signature against the RAW request body (captured
    via request.body() before any JSON parsing) using the timestamp
    and signature headers Cashfree sends — see cashfree_gateway.py's
    verify_webhook_signature docstring for why a pre-parsed dict isn't
    sufficient. Resolves the purchase purely via gateway_reference (the
    order id THIS server itself generated and sent to Cashfree) — never
    via anything identity-shaped in the payload. Idempotent by
    construction: routes into the same confirm_ci_purchase/
    confirm_ad_purchase the return-confirm routes above use, which
    already returns already_confirmed=True on a second delivery rather
    than creating a second entitlement."""
    db = request.app.state.db
    gateway = request.app.state.payment_gateway
    if getattr(gateway, "name", None) != "cashfree":
        raise HTTPException(status_code=404, detail="Not found.")

    raw_body = await request.body()
    signature = request.headers.get("x-webhook-signature", "")
    timestamp = request.headers.get("x-webhook-timestamp", "")
    try:
        gateway.verify_webhook_signature(raw_body, timestamp, signature)
    except CashfreeVerificationError:
        logger.warning("cashfree_webhook invalid_signature")
        raise HTTPException(status_code=400, detail="Invalid signature.")

    payload = json.loads(raw_body)
    event_type = payload.get("type", "")
    data = payload.get("data") or {}
    order_id = (data.get("order") or {}).get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Missing order reference.")

    purchase = db.get_purchase_by_gateway_reference(order_id)
    if purchase is None:
        # Not necessarily an error — could be a webhook for an order
        # this service never created. Acknowledge, don't retry.
        logger.info("cashfree_webhook unknown_order event=%s", event_type)
        return {"status": "ignored", "reason": "unknown_order"}

    if event_type in _CASHFREE_SUCCESS_EVENTS:
        confirm_fn = db.confirm_ci_purchase if purchase["product"] == "CAREER_INTELLIGENCE" else db.confirm_ad_purchase
        try:
            result = confirm_fn(purchase["id"], order_id)
        except ValueError as exc:
            # e.g. "cannot confirm a purchase in status REFUNDED" — an
            # out-of-order/late webhook arriving after the purchase
            # moved on for an unrelated reason. Acknowledge; log for
            # investigation.
            logger.warning("cashfree_webhook confirm_rejected purchase_id=%s reason=%s", purchase["id"], exc)
            return {"status": "ignored", "reason": "state_conflict"}
        return {"status": "confirmed", **result}

    if event_type in _CASHFREE_FAILURE_EVENTS:
        db.fail_ad_purchase(purchase["id"])  # product-agnostic despite the name — see its own docstring
        return {"status": "failed", "purchase_id": purchase["id"]}

    return {"status": "ignored", "event": event_type}
