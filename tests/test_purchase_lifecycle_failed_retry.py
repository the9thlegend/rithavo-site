"""
Phase 2B-2.2A — synchronization: regression coverage for the Phase
2B-2.1 correction (_confirm_purchase, app/db.py), ported from
rithavo-web-platform@26ab8315b52481ad6f2e8a3ed175d92271b531d3
(tests/test_commerce_phase2a.py), extracted to these two functions only
— the rest of that file covers general commerce-lifecycle scope
(idempotency keys, pricing preview, admin grants, etc.) unrelated to
this specific correction.
"""

import pytest


def test_a_failed_purchase_can_be_confirmed_by_a_later_successful_retry(db):
    """Phase 2B-2.1: FAILED is a retryable state, not terminal — see
    _confirm_purchase's own docstring in db.py. Was
    test_cannot_confirm_an_already_failed_purchase before this phase;
    that name/assertion encoded the exact production-readiness bug this
    phase corrects (a real successful Razorpay retry on the same Order
    could never be recorded). REFUNDED/ADMIN_GRANT remain non-confirmable
    — see test_cannot_confirm_a_refunded_purchase, immediately below."""
    user_id = db.get_or_create_user("lifecycle-fail-then-confirm@example.com")
    result = db.begin_ad_purchase(user_id)
    db.fail_ad_purchase(result["purchase_id"])
    confirm = db.confirm_ad_purchase(result["purchase_id"], "gw-ref-5")
    assert confirm["already_confirmed"] is False
    assert db.get_purchase(result["purchase_id"])["payment_status"] == "SUCCEEDED"


def test_cannot_confirm_a_refunded_purchase(db):
    """Phase 2B-2.1 scope guard: unlike FAILED, REFUNDED is NOT in
    _confirm_purchase's allowed-predecessor-states tuple — this proves
    the fix did not accidentally widen confirmability beyond FAILED to
    every other terminal status."""
    user_id = db.get_or_create_user("lifecycle-refund-then-confirm@example.com")
    result = db.begin_ad_purchase(user_id)
    db.confirm_ad_purchase(result["purchase_id"], "gw-ref-refund-reconfirm")
    db.refund_ad_purchase(result["purchase_id"])
    with pytest.raises(ValueError):
        db.confirm_ad_purchase(result["purchase_id"], "gw-ref-refund-reconfirm")
    assert db.get_purchase(result["purchase_id"])["payment_status"] == "REFUNDED"
