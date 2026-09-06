"""
Phase 2B-2.2A — synchronization: real-Postgres concurrency proof for the
Phase 2B-2.1 correction (_confirm_purchase, app/db.py), ported from
rithavo-web-platform@26ab8315b52481ad6f2e8a3ed175d92271b531d3
(tests/test_commerce_concurrency_postgres.py), extracted to this one
test only — the rest of that file covers general commerce concurrency
(idempotency-key races, entitlement-consumption races, pricing races)
unrelated to this specific correction.

Skipped unless RITHAVO_WEB_TEST_POSTGRES_DSN is set (see conftest.py) —
SQLite's simpler locking model cannot stand in for this guarantee.
"""

import os
import threading

import pytest

_POSTGRES_ACTIVE = bool(os.environ.get("RITHAVO_WEB_TEST_POSTGRES_DSN"))
pytestmark = pytest.mark.skipif(
    not _POSTGRES_ACTIVE,
    reason="Only meaningfully provable against real Postgres (transaction isolation, "
           "advisory locks have no SQLite equivalent).",
)


def _run_concurrently(*fns, timeout=10):
    """Runs each callable in its own thread, all released from a shared
    barrier at the same instant. Returns results in the SAME order as
    fns (not completion order), collected via a results list indexed by
    position."""
    n = len(fns)
    barrier = threading.Barrier(n)
    results = [None] * n

    def wrapper(i, fn):
        barrier.wait(timeout=timeout)
        results[i] = fn()

    threads = [threading.Thread(target=wrapper, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)
    return results


def test_concurrent_recovery_confirmation_after_a_failed_purchase_creates_exactly_one_entitlement(db):
    """Phase 2B-2.1 Test G: the browser confirm callback and the
    Razorpay webhook can genuinely race against each other for the SAME
    successful retry payment, on a purchase that was previously FAILED.
    The advisory lock _confirm_purchase already takes (unchanged by this
    phase's fix — only the status guard after it moved) must still
    serialize these exactly like any other pair of concurrent confirms:
    exactly one entitlement, both callers agreeing on its id."""
    user_id = db.get_or_create_user("concurrent-recovery@example.com")
    purchase = db.begin_ad_purchase(user_id)
    db.mark_purchase_pending(purchase["purchase_id"], "order_concurrent_recovery")
    db.fail_ad_purchase(purchase["purchase_id"])
    assert db.get_purchase(purchase["purchase_id"])["payment_status"] == "FAILED"

    def confirm():
        try:
            return db.confirm_ad_purchase(
                purchase["purchase_id"], "order_concurrent_recovery", "pay_concurrent_recovery")
        except ValueError as exc:
            return {"error": str(exc)}

    results = _run_concurrently(confirm, confirm)
    assert all(r is not None and "error" not in r for r in results), results
    entitlement_ids = {r["entitlement_id"] for r in results}
    assert len(entitlement_ids) == 1, "both concurrent confirms must agree on the same entitlement id"

    with db.connect() as conn:
        entitlement_count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (purchase["purchase_id"],)
        ).fetchone()["c"]
    assert entitlement_count == 1
