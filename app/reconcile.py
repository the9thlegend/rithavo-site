"""
Cashfree reconciliation: close the gap where a customer paid, Cashfree's
webhook never arrived (or was rejected), and the customer never came back to
the return page, which leaves the purchase PENDING and the entitlement missing.

Nothing here trusts a client or a webhook body. For every unresolved purchase
it asks Cashfree directly for the status of the order id THIS server created
(CashfreeGateway.fetch_order_status, the same authority the return page uses)
and only a status of PAID can grant an entitlement, through the same
idempotent Database.confirm_*_purchase the webhook uses (advisory-locked and
status-checked), so running this any number of times, concurrently with
itself or with a late webhook, can never create a second entitlement.

Purchase-state transitions this module can cause, and nothing else:
    PENDING | FAILED -> SUCCEEDED   (order is PAID at Cashfree; via confirm_*)
    PENDING          -> FAILED      (order is EXPIRED or TERMINATED at Cashfree)
SUCCEEDED, REFUNDED and ADMIN_GRANT purchases are never selected.

Bounded work: at most `limit` orders per call, newest first, with a cursor
(`before_id`) for the next page, and a wall-clock budget so one call always
finishes inside the function time limit; whatever is not reached is reported
as truncated and picked up by the next page. One order failing (network, bad
response, anything) is recorded for that order only; the rest continue.

dry-run by default: with apply=False it reports what it WOULD do and changes
nothing. The report holds purchase ids, products and statuses only.
"""

import logging
import time

from .cashfree_gateway import CashfreeVerificationError

logger = logging.getLogger("rithavo_web.reconcile")

_PAID = "PAID"
# Cashfree order states that can no longer be paid. Anything else (ACTIVE, or a
# status we do not recognise) is left exactly as it is.
_DEAD = frozenset({"EXPIRED", "TERMINATED"})

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
# Vercel's function limit for this project is 60 s and one Cashfree status
# call can take up to 15 s, so stop starting new orders after 30 s.
DEFAULT_TIME_BUDGET_SECONDS = 30.0


def reconcile_cashfree_purchases(db, gateway, *, apply: bool = False, limit: int = DEFAULT_LIMIT,
                                 before_id: int = None, actor=None,
                                 time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
                                 clock=time.monotonic) -> dict:
    limit = max(1, min(int(limit), MAX_LIMIT))
    purchases = db.list_unresolved_cashfree_purchases(limit, before_id)
    started = clock()
    results = []
    truncated = False
    for purchase in purchases:
        if results and clock() - started > time_budget_seconds:
            truncated = True
            break
        results.append(_reconcile_one(db, gateway, purchase, apply, actor))

    counts = {}
    for entry in results:
        counts[entry["result"]] = counts.get(entry["result"], 0) + 1
    more = truncated or len(purchases) == limit
    return {
        "applied": bool(apply),
        "checked": len(results),
        "counts": counts,
        "truncated": truncated,
        # pass back as before_id to continue; None when this was the last page
        "next_before_id": results[-1]["purchase_id"] if (more and results) else None,
        "purchases": results,
    }


def _reconcile_one(db, gateway, purchase, apply: bool, actor) -> dict:
    entry = {"purchase_id": purchase["id"], "product": purchase["product"], "was": purchase["payment_status"]}
    order_id = purchase["gateway_reference"]
    try:
        try:
            status = gateway.fetch_order_status(order_id)
        except CashfreeVerificationError:
            logger.warning("reconcile check_failed purchase_id=%s", purchase["id"])
            entry["result"] = "check_failed"
            return entry
        entry["gateway_status"] = status

        if status == _PAID:
            if not apply:
                entry["result"] = "would_confirm"
                return entry
            confirm = db.confirm_ci_purchase if purchase["product"] == "CAREER_INTELLIGENCE" else db.confirm_ad_purchase
            try:
                outcome = confirm(purchase["id"], order_id)
            except ValueError as exc:
                logger.warning("reconcile state_conflict actor=%s purchase_id=%s reason=%s", actor, purchase["id"], exc)
                entry["result"] = "state_conflict"
                return entry
            entry["result"] = "already_confirmed" if outcome.get("already_confirmed") else "confirmed"
            _audit(actor, purchase, entry["result"], status)
        elif status in _DEAD and purchase["payment_status"] == "PENDING":
            if not apply:
                entry["result"] = "would_mark_failed"
                return entry
            db.fail_ad_purchase(purchase["id"])  # only moves CREATED/PENDING; never touches a paid purchase
            entry["result"] = "marked_failed"
            _audit(actor, purchase, entry["result"], status)
        else:
            entry["result"] = "left_unchanged"
    except Exception as exc:  # one bad order must never abort the batch or hide earlier results
        logger.error("reconcile error actor=%s purchase_id=%s error_type=%s", actor, purchase["id"], type(exc).__name__)
        entry["result"] = "error"
    return entry


def _audit(actor, purchase, result: str, gateway_status: str) -> None:
    """One structured line per state-changing action (who, which purchase, what, why).
    These go to the platform logs; there is deliberately no new table here, so
    the trail is only as durable as the log retention (see the runbook)."""
    logger.warning("reconcile_action actor=%s purchase_id=%s product=%s from=%s result=%s gateway_status=%s",
                   actor, purchase["id"], purchase["product"], purchase["payment_status"], result, gateway_status)
