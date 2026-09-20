"""
Phase 2B-2 — end-to-end purchase flow through the real HTTP routes, with
RazorpayGateway's underlying razorpay.Client mocked (never a real
network call). Covers Career Intelligence (flat ₹799) and Application
Diagnosis (ladder), signature/order verification, idempotency, and
security (cross-user isolation, no client-controlled price/identity).
"""

from unittest.mock import MagicMock

from app.razorpay_gateway import RazorpayGateway
from .conftest import login_via_magic_link


def _install_razorpay_gateway(app, monkeypatch):
    """Swaps app.state.payment_gateway for a RazorpayGateway whose
    underlying SDK client is a MagicMock — every test in this file gets
    a fresh one via this helper (never shared state across tests)."""
    gateway = RazorpayGateway("rzp_test_fake", "fake-secret-not-real", "fake-webhook-secret-not-real")
    gateway._client = MagicMock()
    gateway._client.order.create.side_effect = (
        lambda data: {"id": f"order_fake_{data['notes']['purchase_id']}"}
    )
    monkeypatch.setattr(app.state, "payment_gateway", gateway)
    return gateway


def _mock_successful_payment(gateway):
    gateway._client.utility.verify_payment_signature.return_value = True
    gateway._client.payment.fetch.return_value = {"status": "captured"}


def _mock_failed_signature(gateway):
    from razorpay.errors import SignatureVerificationError
    gateway._client.utility.verify_payment_signature.side_effect = SignatureVerificationError("bad")


def _confirm_payload(order_id, payment_id="pay_fake_1", signature="sig_fake_1"):
    return {"razorpay_order_id": order_id, "razorpay_payment_id": payment_id, "razorpay_signature": signature}


# ---- Career Intelligence: ₹799, order creation, verified success ----

def test_ci_price_is_always_799(app_and_client):
    app, client = app_and_client
    login_via_magic_link(client, app, "ci-price@example.com")
    resp = client.get("/career-intelligence/price")
    assert resp.status_code == 200
    assert resp.json() == {"amount_inr": 799}


def test_ci_purchase_creates_order_and_confirms_to_exactly_one_entitlement(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "ci-purchase@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)

    started = client.post("/career-intelligence/purchase", json={})
    assert started.status_code == 200, started.text
    data = started.json()
    assert data["amount_inr"] == 799
    assert data["payment_status"] == "PENDING"
    assert data["razorpay_key_id"] == "rzp_test_fake"
    order_id = data["razorpay_order_id"]

    _mock_successful_payment(gateway)
    confirmed = client.post(f"/career-intelligence/purchase/{data['purchase_id']}/confirm",
                             json=_confirm_payload(order_id))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    with db.connect() as conn:
        entitlements = conn.execute(
            "SELECT * FROM entitlements WHERE user_id = ? AND product = 'career_intelligence'", (user_id,)
        ).fetchall()
    assert len(entitlements) == 1
    assert entitlements[0]["status"] == "ACTIVE"

    purchase = db.get_purchase(data["purchase_id"])
    assert purchase["payment_status"] == "SUCCEEDED"
    assert purchase["amount_inr"] == 799


def test_ci_purchase_is_blocked_while_an_active_entitlement_still_has_runway(app_and_client, db, monkeypatch):
    """CI validity foundation: 'one active CI entitlement per email' —
    a second purchase attempt while the first is still within its 30-day
    validity AND has evaluations remaining must be rejected (409), not
    silently create a second, simultaneously-usable entitlement. This
    replaces the prior 'CI is not a subscription, a second purchase is
    independent' test, which encoded the OLD, since-superseded behavior —
    see the approved CI validity rules."""
    app, client = app_and_client
    login_via_magic_link(client, app, "ci-repeat@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)

    started = client.post("/career-intelligence/purchase", json={}).json()
    client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                json=_confirm_payload(started["razorpay_order_id"], payment_id="pay_fake_0"))

    second_attempt = client.post("/career-intelligence/purchase", json={})
    assert second_attempt.status_code == 409
    assert "already have an active" in second_attempt.json()["detail"]

    purchases = client.get("/purchases").json()
    ci_purchases = [p for p in purchases if p["product"] == "CAREER_INTELLIGENCE"]
    assert len(ci_purchases) == 1


def test_ci_purchase_is_allowed_again_once_evaluations_are_exhausted(app_and_client, db, monkeypatch):
    """The flip side of the block above: once the existing entitlement's
    evaluations are used up, 'a new evaluation requires a new valid
    purchase' — the second purchase must succeed."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "ci-repurchase-after-exhaustion@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)

    started = client.post("/career-intelligence/purchase", json={}).json()
    client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                json=_confirm_payload(started["razorpay_order_id"], payment_id="pay_fake_first"))
    first_entitlement_id = db.get_purchase(started["purchase_id"])["entitlement_id"]

    second_attempt = client.post("/career-intelligence/purchase", json={})
    assert second_attempt.status_code == 409  # not yet exhausted

    # Exhaust the first entitlement's evaluations directly (the Run
    # action itself lives entirely on the sibling app, out of scope for
    # this repo's own tests — see reserve_ci_evaluation's tests there).
    with db.connect() as conn:
        conn.execute(
            "UPDATE entitlements SET evaluations_used = 3 WHERE id = ?", (first_entitlement_id,)
        )

    third_attempt = client.post("/career-intelligence/purchase", json={})
    assert third_attempt.status_code == 200, third_attempt.text


# ---- Application Diagnosis ----

def test_ad_purchase_starts_at_399_and_confirms_to_exactly_one_entitlement(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "ad-purchase@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)

    started = client.post("/diagnosis/purchase", json={}).json()
    assert started["amount_inr"] == 399
    order_id = started["razorpay_order_id"]

    _mock_successful_payment(gateway)
    confirmed = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                             json=_confirm_payload(order_id))
    assert confirmed.status_code == 200, confirmed.text

    with db.connect() as conn:
        entitlements = conn.execute(
            "SELECT * FROM entitlements WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSTIC'", (user_id,)
        ).fetchall()
    assert len(entitlements) == 1


# ---- Verification failure handling ----

def test_invalid_signature_never_creates_an_entitlement(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "bad-sig@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()

    _mock_failed_signature(gateway)
    resp = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                        json=_confirm_payload(started["razorpay_order_id"]))
    assert resp.status_code == 402

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "PENDING"  # unchanged — not SUCCEEDED, not FAILED
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 0


def test_valid_signature_but_payment_not_yet_captured_is_rejected(app_and_client, db, monkeypatch):
    """Task 5: signature validity alone is not sufficient — the payment's
    own status must also be captured before fulfillment."""
    app, client = app_and_client
    login_via_magic_link(client, app, "not-captured@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()

    gateway._client.utility.verify_payment_signature.return_value = True
    gateway._client.payment.fetch.return_value = {"status": "authorized"}  # not "captured"
    resp = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                        json=_confirm_payload(started["razorpay_order_id"]))
    assert resp.status_code == 402

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "PENDING"


def test_order_id_mismatch_is_rejected_before_any_signature_check(app_and_client, db, monkeypatch):
    """A client cannot confirm purchase A using a valid signature for a
    DIFFERENT order it doesn't own/that isn't associated with purchase A."""
    app, client = app_and_client
    login_via_magic_link(client, app, "order-mismatch@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    _mock_successful_payment(gateway)  # would succeed if ever reached

    resp = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                        json=_confirm_payload("order_someone_elses_order"))
    assert resp.status_code == 400
    gateway._client.utility.verify_payment_signature.assert_not_called()

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "PENDING"


def test_repeated_success_callback_for_the_same_payment_is_idempotent(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "double-callback@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/career-intelligence/purchase", json={}).json()
    _mock_successful_payment(gateway)

    first = client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                         json=_confirm_payload(started["razorpay_order_id"]))
    second = client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                          json=_confirm_payload(started["razorpay_order_id"]))
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["entitlement_id"] == second.json()["entitlement_id"]

    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 1


def test_failed_payment_never_creates_a_usable_entitlement(app_and_client, db, monkeypatch):
    """Simulates an abandoned/failed checkout: no confirm call is ever
    made (the customer never completed payment) — the purchase simply
    stays PENDING, and no entitlement exists to consume."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "abandoned@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    client.post("/diagnosis/purchase", json={})

    resp = client.get("/diagnosis/entitlement/active")
    assert resp.json() == {"has_active_entitlement": False, "entitlement_id": None}


# ---- Full AD ladder, through the real HTTP + Razorpay-verified path ----

def test_full_ad_ladder_across_all_nine_tiers_via_razorpay_confirmed_purchases(app_and_client, db, monkeypatch):
    """Task 5's explicit requirement: every one of the nine price tiers,
    walked via the actual HTTP purchase->confirm flow (not a direct db.*
    call) with real signature verification (only order.create is
    mocked)."""
    app, client = app_and_client
    login_via_magic_link(client, app, "razorpay-ladder@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)

    expected = [399, 339, 319, 299, 279, 259, 239, 219, 199, 199]  # 10th purchase floors at 199
    for i, expected_price in enumerate(expected):
        preview = client.get("/diagnosis/price").json()
        assert preview["amount_inr"] == expected_price, f"preview before purchase #{i}"

        started = client.post("/diagnosis/purchase", json={}).json()
        assert started["amount_inr"] == expected_price, f"purchase #{i}"

        confirmed = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                                 json=_confirm_payload(started["razorpay_order_id"], payment_id=f"pay_ladder_{i}"))
        assert confirmed.status_code == 200, f"confirm #{i}: {confirmed.text}"

    purchases = [p for p in client.get("/purchases").json() if p["product"] == "APPLICATION_DIAGNOSIS"]
    assert len(purchases) == 10
    assert all(p["payment_status"] == "SUCCEEDED" for p in purchases)
    with db.connect() as conn:
        entitlement_count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE user_id = (SELECT id FROM users WHERE email = ?)",
            ("razorpay-ladder@example.com",),
        ).fetchone()["c"]
    assert entitlement_count == 10  # exactly one per successful purchase


def test_pending_purchase_never_advances_the_ladder(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "ladder-pending@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    client.post("/diagnosis/purchase", json={})  # left PENDING, never confirmed

    preview = client.get("/diagnosis/price").json()
    assert preview == {"qualifying_count": 0, "amount_inr": 399}


def test_failed_purchase_never_advances_the_ladder(app_and_client, db, monkeypatch):
    """The webhook path's own signature-verification/routing is tested
    in test_razorpay_webhook.py; here the ladder-exclusion property
    itself is what's under test, so this drives fail_ad_purchase
    directly (state-machine-level, gateway-agnostic — the ladder query
    only ever looks at payment_status, never how a purchase got there)."""
    app, client = app_and_client
    login_via_magic_link(client, app, "ladder-failed@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    db.fail_ad_purchase(started["purchase_id"])

    preview = client.get("/diagnosis/price").json()
    assert preview == {"qualifying_count": 0, "amount_inr": 399}


def test_refunded_purchase_never_advances_the_ladder(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "ladder-refunded@example.com")
    user_id = db.get_or_create_user("ladder-refunded@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)
    started = client.post("/diagnosis/purchase", json={}).json()
    client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                json=_confirm_payload(started["razorpay_order_id"]))

    db.refund_ad_purchase(started["purchase_id"], user_id)

    preview = client.get("/diagnosis/price").json()
    assert preview == {"qualifying_count": 0, "amount_inr": 399}


def test_admin_grant_never_advances_the_ladder(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "ladder-admin-grant@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    db.admin_grant_ad_entitlement(user_id)

    preview = client.get("/diagnosis/price").json()
    assert preview == {"qualifying_count": 0, "amount_inr": 399}


# ---- Refund regression (Task 8/existing refund rules, now proven
#      against a Razorpay-confirmed purchase rather than only a
#      TestPaymentGateway-confirmed one; refund_ad_purchase itself is
#      product-agnostic — see its own db.py docstring) ----

def test_refund_before_diagnosis_revokes_entitlement_and_excludes_from_ladder(app_and_client, db, monkeypatch):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "refund-before-diagnosis@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)
    started = client.post("/diagnosis/purchase", json={}).json()
    client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                json=_confirm_payload(started["razorpay_order_id"]))

    refund = db.refund_ad_purchase(started["purchase_id"], user_id)
    assert refund["entitlement_revoked"] is True

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "REFUNDED"
    entitlement = db.get_entitlement(purchase["entitlement_id"])
    assert entitlement["status"] == "REVOKED"
    assert client.get("/diagnosis/price").json()["qualifying_count"] == 0


def test_ci_refund_is_allowed_before_the_first_evaluation(app_and_client, db, monkeypatch):
    """refund_ad_purchase's name is historical — it operates purely on
    purchase_id/payment_status, plus (CI validity foundation) a
    product-specific 'not yet evaluated' check: for CI this now keys off
    evaluations_used == 0, not the AD-only consumed_at column, per the
    approved rule 'before first evaluation -> refund eligible'."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "refund-ci-before@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)
    started = client.post("/career-intelligence/purchase", json={}).json()
    client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                json=_confirm_payload(started["razorpay_order_id"]))

    refund = db.refund_ad_purchase(started["purchase_id"], user_id)
    assert refund["entitlement_revoked"] is True
    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "REFUNDED"
    entitlement = db.get_entitlement(purchase["entitlement_id"])
    assert entitlement["status"] == "REVOKED"


def test_ci_refund_is_blocked_after_the_first_evaluation(app_and_client, db, monkeypatch):
    """Pre-deployment review fix: 'after first evaluation -> not refund
    eligible' now means the WHOLE refund operation is rejected -- money
    side AND entitlement side -- never a partial state where the
    purchase gets marked REFUNDED while the entitlement is merely left
    active. The Run action itself lives entirely on the sibling app
    (reserve_ci_evaluation), so this simulates its effect directly on
    evaluations_used -- the point under test here is refund_ad_purchase's
    own CI-aware branch, not the Run action."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "refund-ci-after@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)
    started = client.post("/career-intelligence/purchase", json={}).json()
    client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                json=_confirm_payload(started["razorpay_order_id"]))
    entitlement_id = db.get_purchase(started["purchase_id"])["entitlement_id"]
    with db.connect() as conn:
        conn.execute("UPDATE entitlements SET evaluations_used = 1 WHERE id = ?", (entitlement_id,))

    try:
        db.refund_ad_purchase(started["purchase_id"], user_id)
        raised = False
    except ValueError:
        raised = True
    assert raised is True

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "SUCCEEDED"  # NOT marked refunded
    entitlement = db.get_entitlement(entitlement_id)
    assert entitlement["status"] == "ACTIVE"  # untouched


def test_ad_refund_behavior_is_completely_unchanged_by_the_ci_aware_branch(app_and_client, db, monkeypatch):
    """Regression guard: refund_ad_purchase's AD branch must still key
    off consumed_at exactly as before -- the new CI-specific branch is
    additive, never a replacement of AD's own semantics."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "refund-ad-unchanged@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    _mock_successful_payment(gateway)
    started = client.post("/diagnosis/purchase", json={}).json()
    client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                json=_confirm_payload(started["razorpay_order_id"]))

    refund = db.refund_ad_purchase(started["purchase_id"], user_id)
    assert refund["entitlement_revoked"] is True
    entitlement = db.get_entitlement(db.get_purchase(started["purchase_id"])["entitlement_id"])
    assert entitlement["status"] == "REVOKED"


# ---- Security ----

def test_user_a_cannot_confirm_user_b_purchase(app_and_client, db, monkeypatch):
    app, client = app_and_client
    login_via_magic_link(client, app, "attacker@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)

    victim_id = db.get_or_create_user("victim@example.com")
    victim_purchase = db.begin_ad_purchase(victim_id)
    db.mark_purchase_pending(victim_purchase["purchase_id"], "order_victim")

    _mock_successful_payment(gateway)
    resp = client.post(f"/diagnosis/purchase/{victim_purchase['purchase_id']}/confirm",
                        json=_confirm_payload("order_victim"))
    assert resp.status_code == 404

    purchase = db.get_purchase(victim_purchase["purchase_id"])
    assert purchase["payment_status"] == "PENDING"


def test_client_cannot_confirm_the_wrong_product_for_an_owned_purchase(app_and_client, db, monkeypatch):
    """Owns the purchase, but it's a CI purchase — confirming it through
    the AD-specific route must be rejected, not silently create an
    APPLICATION_DIAGNOSTIC entitlement for a Career Intelligence payment."""
    app, client = app_and_client
    login_via_magic_link(client, app, "wrong-product@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/career-intelligence/purchase", json={}).json()
    _mock_successful_payment(gateway)

    resp = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                        json=_confirm_payload(started["razorpay_order_id"]))
    assert resp.status_code == 404


def test_purchase_endpoints_accept_no_client_supplied_price_field():
    """Structural check: neither purchase-initiation route has any
    request field a client could use to supply a price at all."""
    import inspect
    from app import main
    start_ad = inspect.signature(main.create_ad_purchase)
    start_ci = inspect.signature(main.create_ci_purchase)
    assert "amount_inr" not in start_ad.parameters
    assert "amount_inr" not in start_ci.parameters
    assert list(start_ad.parameters) == ["request", "idempotency_key"]
    assert list(start_ci.parameters) == ["request", "idempotency_key"]


def test_confirm_route_requires_authentication(app_and_client, monkeypatch):
    app, client = app_and_client
    _install_razorpay_gateway(app, monkeypatch)
    resp = client.post("/diagnosis/purchase/1/confirm", json=_confirm_payload("order_x"))
    assert resp.status_code == 401


# ---- Phase 2B-2.1: FAILED -> SUCCEEDED retry on the same Razorpay Order ----
#
# Reproduces the exact production-readiness blocker found during Phase
# 2B-2's real Razorpay Test Mode verification: a Razorpay Order's first
# payment attempt fails (purchase -> FAILED), the customer retries on the
# SAME still-valid Order, and the retry succeeds. Before this phase, the
# browser confirm route raised an uncaught ValueError (-> HTTP 500) for
# any FAILED purchase; see _confirm_purchase's own docstring in db.py for
# why allowing FAILED here is safe (order/ownership already established
# by the caller before this route is ever reached).

def test_failed_purchase_confirmed_by_browser_after_valid_retry_on_same_order(app_and_client, db, monkeypatch):
    """Test A / C: the previously-500ing path. A first failed attempt
    marks the purchase FAILED (same mechanism the webhook route uses —
    see fail_ad_purchase); a second, genuinely successful payment on the
    SAME order must now confirm cleanly instead of raising."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "retry-after-failed@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    order_id = started["razorpay_order_id"]

    db.fail_ad_purchase(started["purchase_id"])
    assert db.get_purchase(started["purchase_id"])["payment_status"] == "FAILED"

    _mock_successful_payment(gateway)
    confirmed = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                             json=_confirm_payload(order_id, payment_id="pay_retry_success"))
    assert confirmed.status_code == 200, confirmed.text  # was an unhandled 500 before this fix
    assert confirmed.json()["already_confirmed"] is False

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "SUCCEEDED"
    assert purchase["amount_inr"] == 399

    with db.connect() as conn:
        entitlements = conn.execute(
            "SELECT * FROM entitlements WHERE user_id = ? AND product = 'APPLICATION_DIAGNOSTIC'", (user_id,)
        ).fetchall()
    assert len(entitlements) == 1
    assert entitlements[0]["status"] == "ACTIVE"


def test_browser_confirmation_converges_with_an_already_webhook_confirmed_retry(app_and_client, db, monkeypatch):
    """Test D: the webhook (simulated here via a direct db call — the
    webhook route's own signature/routing is covered in
    test_razorpay_webhook.py) recovers the FAILED purchase first; the
    browser's own confirm callback for that same payment, arriving
    afterward, must return the already-persisted success rather than
    failing or duplicating anything."""
    app, client = app_and_client
    login_via_magic_link(client, app, "webhook-then-browser@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/career-intelligence/purchase", json={}).json()
    order_id = started["razorpay_order_id"]

    db.fail_ad_purchase(started["purchase_id"])
    webhook_result = db.confirm_ci_purchase(started["purchase_id"], order_id, "pay_converge_1")
    assert webhook_result["already_confirmed"] is False

    gateway = app.state.payment_gateway
    _mock_successful_payment(gateway)
    browser_resp = client.post(f"/career-intelligence/purchase/{started['purchase_id']}/confirm",
                                json=_confirm_payload(order_id, payment_id="pay_converge_1"))
    assert browser_resp.status_code == 200, browser_resp.text
    assert browser_resp.json()["already_confirmed"] is True
    assert browser_resp.json()["entitlement_id"] == webhook_result["entitlement_id"]

    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 1


def test_unrelated_successful_payment_cannot_resurrect_a_failed_purchase(app_and_client, db, monkeypatch):
    """Test H: a payment/order that does NOT belong to this purchase must
    still be rejected even though the purchase is now (post-fix)
    confirmable from FAILED — the pre-existing order-match check runs
    before confirm_fn is ever called."""
    app, client = app_and_client
    login_via_magic_link(client, app, "unrelated-payment@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    db.fail_ad_purchase(started["purchase_id"])

    _mock_successful_payment(gateway)  # would succeed if ever reached
    resp = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                        json=_confirm_payload("order_someone_elses_unrelated_order"))
    assert resp.status_code == 400
    gateway._client.utility.verify_payment_signature.assert_not_called()

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "FAILED"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 0


def test_failed_purchase_with_no_retry_never_gets_an_entitlement(app_and_client, db, monkeypatch):
    """Test I: a FAILED purchase that nobody ever successfully retries
    must simply stay FAILED forever — this fix only makes a genuine
    successful retry confirmable, it does not proactively act on FAILED
    purchases in any way."""
    app, client = app_and_client
    login_via_magic_link(client, app, "failed-no-retry@example.com")
    _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    db.fail_ad_purchase(started["purchase_id"])

    purchase = db.get_purchase(started["purchase_id"])
    assert purchase["payment_status"] == "FAILED"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (started["purchase_id"],)
        ).fetchone()["c"]
    assert count == 0


def test_recovered_failed_purchase_counts_exactly_once_toward_the_ad_ladder(app_and_client, db, monkeypatch):
    """Test J: a FAILED->SUCCEEDED recovery must advance the discount
    ladder exactly like any other successful purchase — no more, no
    less. count_qualifying_ad_purchases only ever looks at payment_status
    = 'SUCCEEDED', so this is a natural consequence of the fix, proven
    here explicitly rather than assumed."""
    app, client = app_and_client
    login_via_magic_link(client, app, "ladder-recovered@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)
    started = client.post("/diagnosis/purchase", json={}).json()
    assert started["amount_inr"] == 399  # 0 prior qualifying purchases
    db.fail_ad_purchase(started["purchase_id"])

    _mock_successful_payment(gateway)
    confirmed = client.post(f"/diagnosis/purchase/{started['purchase_id']}/confirm",
                             json=_confirm_payload(started["razorpay_order_id"], payment_id="pay_ladder_recover"))
    assert confirmed.status_code == 200, confirmed.text

    preview = client.get("/diagnosis/price").json()
    assert preview == {"qualifying_count": 1, "amount_inr": 339}  # counted exactly once


def test_attacker_cannot_confirm_a_different_users_failed_purchase_via_retry(app_and_client, db, monkeypatch):
    """Cross-user substitution, specifically on the new FAILED-retry
    path: owned_purchase's ownership check must still reject an
    attacker's session naming a victim's (now-retryable) FAILED
    purchase_id, exactly as it already does for a PENDING one."""
    app, client = app_and_client
    login_via_magic_link(client, app, "retry-attacker@example.com")
    gateway = _install_razorpay_gateway(app, monkeypatch)

    victim_id = db.get_or_create_user("retry-victim@example.com")
    victim_purchase = db.begin_ad_purchase(victim_id)
    db.mark_purchase_pending(victim_purchase["purchase_id"], "order_victim_retry")
    db.fail_ad_purchase(victim_purchase["purchase_id"])

    _mock_successful_payment(gateway)
    resp = client.post(f"/diagnosis/purchase/{victim_purchase['purchase_id']}/confirm",
                        json=_confirm_payload("order_victim_retry"))
    assert resp.status_code == 404

    purchase = db.get_purchase(victim_purchase["purchase_id"])
    assert purchase["payment_status"] == "FAILED"
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM entitlements WHERE purchase_id = ?", (victim_purchase["purchase_id"],)
        ).fetchone()["c"]
    assert count == 0
