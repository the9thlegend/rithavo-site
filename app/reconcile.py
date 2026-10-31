"""
Cashfree reconciliation: close the gap where a customer paid, Cashfree's
webhook never arrived (or was rejected), and the customer never came back to
the return page, which leaves the purchase PENDING and the entitlement missing.

Nothing here trusts a client or a webhook body. For every unresolved purchase
it asks Cashfree directly for the status of the order id THIS server created
(CashfreeGateway.fetch_order_status, the same authority the return page uses)
and only a status of PAID can grant an entitlement, through the same
idempotent Database.confirm_*_purchase the webhook uses, so running this any
number of times, or concurrently with a late webhook, can never create a second
entitlement.

dry-run by default: with apply=False it reports what it WOULD do and changes
nothing. The report holds purchase ids, products and statuses only.
"""

import logging

from .cashfree_gateway import CashfreeVerificationError

logger = logging.getLogger("rithavo_web.reconcile")

_PAID = "PAID"
# Cashfree order states that can no longer be paid. Anything else (ACTIVE, or a
# status we do not recognise) is left exactly as it is.
_DEAD = frozenset({"EXPIRED", "TERMINATED"})


def reconcile_cashfree_purchases(db, gateway, *, apply: bool = False, limit: int = 50) -> dict:
    results = []
    for purchase in db.list_unresolved_cashfree_purchases(limit):
        entry = {"purchase_id": purchase["id"], "product": purchase["product"], "was": purchase["payment_status"]}
        order_id = purchase["gateway_reference"]
        try:
            status = gateway.fetch_order_status(order_id)
        except CashfreeVerificationError:
            logger.warning("reconcile check_failed purchase_id=%s", purchase["id"])
            entry["result"] = "check_failed"
            results.append(entry)
            continue
        entry["gateway_status"] = status

        if status == _PAID:
            if not apply:
                entry["result"] = "would_confirm"
            else:
                confirm = db.confirm_ci_purchase if purchase["product"] == "CAREER_INTELLIGENCE" else db.confirm_ad_purchase
                try:
                    outcome = confirm(purchase["id"], order_id)
                    entry["result"] = "already_confirmed" if outcome.get("already_confirmed") else "confirmed"
                    logger.info("reconcile %s purchase_id=%s", entry["result"], purchase["id"])
                except ValueError as exc:
                    logger.warning("reconcile state_conflict purchase_id=%s reason=%s", purchase["id"], exc)
                    entry["result"] = "state_conflict"
        elif status in _DEAD and purchase["payment_status"] == "PENDING":
            if not apply:
                entry["result"] = "would_mark_failed"
            else:
                db.fail_ad_purchase(purchase["id"])  # only moves CREATED/PENDING; never touches a paid purchase
                entry["result"] = "marked_failed"
                logger.info("reconcile marked_failed purchase_id=%s", purchase["id"])
        else:
            entry["result"] = "left_unchanged"
        results.append(entry)

    counts = {}
    for entry in results:
        counts[entry["result"]] = counts.get(entry["result"], 0) + 1
    return {"applied": bool(apply), "checked": len(results), "counts": counts, "purchases": results}
