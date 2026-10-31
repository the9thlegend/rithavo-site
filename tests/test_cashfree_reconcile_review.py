"""
Review additions for Cashfree reconciliation: explicit apply, paging, time
budget, partial failure, audit lines, replayed webhooks, state invariants and
(on a real local Postgres only) concurrency. Cashfree's network is mocked.
"""

import logging
import threading

import pytest

from app.reconcile import reconcile_cashfree_purchases
from app.super_admin import bootstrap_super_admin

from .conftest import POSTGRES_DSN, login_via_magic_link
from .test_cashfree_reconcile import _entitlements, _order_status, pending  # noqa: F401  (pytest fixture)
from .test_cashfree_webhook import (
    _FakeResponse, _create_pending_purchase, _failed_payload, _install_cashfree_gateway, _signed_webhook_request,
    _success_payload,
)


def test_apply_without_the_confirmation_is_refused_and_changes_nothing(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    assert client.post("/admin/payments/cashfree/reconcile?apply=true").status_code == 400
    assert client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=yes").status_code == 400
    assert db.get_purchase(purchase_id)["payment_status"] == "PENDING"
    assert _entitlements(db, customer_id) == 0


def _make_pending(app, client, monkeypatch, email):
    client.post("/auth/logout")
    user_id = login_via_magic_link(client, app, email)
    purchase_id, order_id = _create_pending_purchase(app, client, monkeypatch)
    return user_id, purchase_id, order_id


@pytest.fixture
def three_pending(app_and_client, db, monkeypatch):
    app, client = app_and_client
    _install_cashfree_gateway(app, monkeypatch)
    made = [_make_pending(app, client, monkeypatch, f"cf-page-{i}@example.com") for i in range(3)]
    client.post("/auth/logout")
    bootstrap_super_admin(db, "cf-page-admin@example.com", "a-real-password")
    login_via_magic_link(client, app, "cf-page-admin@example.com")
    return app, client, db, made


def test_paging_walks_every_unresolved_order_exactly_once(three_pending, monkeypatch):
    app, client, db, made = three_pending
    _order_status(monkeypatch, default="ACTIVE")
    seen, cursor = [], None
    for _ in range(5):
        url = "/admin/payments/cashfree/reconcile?limit=2" + (f"&before_id={cursor}" if cursor else "")
        page = client.post(url).json()
        seen += [p["purchase_id"] for p in page["purchases"]]
        cursor = page["next_before_id"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(m[1] for m in made) and len(seen) == 3


def test_batch_size_is_capped_and_floored(three_pending, monkeypatch):
    app, client, db, made = three_pending
    _order_status(monkeypatch, default="ACTIVE")
    assert client.post("/admin/payments/cashfree/reconcile?limit=100000").json()["checked"] == 3
    assert client.post("/admin/payments/cashfree/reconcile?limit=0").json()["checked"] == 1


def test_time_budget_stops_the_batch_and_the_next_page_resumes(three_pending, monkeypatch):
    app, client, db, made = three_pending
    _order_status(monkeypatch, default="ACTIVE")
    ticks = iter([0.0, 99.0, 99.0, 99.0])  # start, then already over budget when the 2nd order is about to start
    report = reconcile_cashfree_purchases(db, app.state.payment_gateway, limit=3, time_budget_seconds=30,
                                          clock=lambda: next(ticks))
    assert report["truncated"] is True and report["checked"] == 1 and report["next_before_id"] is not None
    rest = reconcile_cashfree_purchases(db, app.state.payment_gateway, limit=3, before_id=report["next_before_id"])
    assert rest["checked"] == 2 and rest["truncated"] is False and rest["next_before_id"] is None


def test_one_bad_order_does_not_abort_the_batch(three_pending, monkeypatch):
    app, client, db, made = three_pending
    import app.cashfree_gateway as cf_module
    ids = {m[2]: m[1] for m in made}
    poisoned = made[1][2]

    class _Broken:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    def fake_get(url, headers, timeout):
        order = url.rsplit("/", 1)[-1]
        return _Broken() if order == poisoned else _FakeResponse(200, {"order_status": "PAID"})

    monkeypatch.setattr(cf_module.httpx, "get", fake_get)
    report = client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()
    assert report["counts"] == {"confirmed": 2, "error": 1}
    assert db.get_purchase(ids[poisoned])["payment_status"] == "PENDING"
    assert sum(1 for m in made if db.get_purchase(m[1])["payment_status"] == "SUCCEEDED") == 2


def test_state_changes_are_audited_with_the_acting_admin(pending, monkeypatch, caplog):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    with caplog.at_level(logging.INFO, logger="rithavo_web.reconcile"):
        client.post("/admin/payments/cashfree/reconcile")  # dry run: no audit line
        assert not [r for r in caplog.records if "reconcile_action" in r.getMessage()]
        client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply")
    lines = [r.getMessage() for r in caplog.records if "reconcile_action" in r.getMessage()]
    assert len(lines) == 1
    assert f"purchase_id={purchase_id}" in lines[0] and "actor=" in lines[0] and "result=confirmed" in lines[0]
    assert "@" not in lines[0] and order_id not in lines[0]


def test_replayed_success_webhooks_create_one_entitlement(pending):
    app, client, db, customer_id, purchase_id, order_id = pending
    for _ in range(3):
        assert _signed_webhook_request(client, _success_payload(order_id)).status_code == 200
    assert _entitlements(db, customer_id) == 1


def test_a_failure_webhook_after_payment_changes_nothing_and_says_so(pending):
    app, client, db, customer_id, purchase_id, order_id = pending
    _signed_webhook_request(client, _success_payload(order_id))
    resp = _signed_webhook_request(client, _failed_payload(order_id))
    assert resp.json() == {"status": "ignored", "reason": "state_conflict"}
    assert db.get_purchase(purchase_id)["payment_status"] == "SUCCEEDED"
    assert _entitlements(db, customer_id) == 1


def test_a_failure_webhook_then_a_success_webhook_still_honours_the_payment(pending):
    app, client, db, customer_id, purchase_id, order_id = pending
    assert _signed_webhook_request(client, _failed_payload(order_id)).json()["status"] == "failed"
    assert db.get_purchase(purchase_id)["payment_status"] == "FAILED"
    assert _signed_webhook_request(client, _success_payload(order_id)).json()["status"] == "confirmed"
    assert _entitlements(db, customer_id) == 1


def test_reconciliation_never_touches_paid_refunded_or_granted_purchases(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, default="PAID")
    for state in ("SUCCEEDED", "REFUNDED", "ADMIN_GRANT"):
        with db.connect() as conn:
            conn.execute("UPDATE purchases SET payment_status = ? WHERE id = ?", (state, purchase_id))
        assert client.post("/admin/payments/cashfree/reconcile?apply=true&confirm=apply").json()["checked"] == 0
        assert db.get_purchase(purchase_id)["payment_status"] == state


@pytest.mark.skipif(not POSTGRES_DSN, reason="needs real locking: run with RITHAVO_WEB_TEST_POSTGRES_DSN (local only)")
def test_concurrent_reconciliations_and_a_confirm_create_exactly_one_entitlement(pending, monkeypatch):
    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    gateway = app.state.payment_gateway
    barrier = threading.Barrier(4)
    errors = []

    def run_reconcile():
        try:
            barrier.wait()
            reconcile_cashfree_purchases(db, gateway, apply=True)
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    def run_confirm():
        try:
            barrier.wait()
            db.confirm_ci_purchase(purchase_id, order_id)
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=run_reconcile) for _ in range(3)] + [threading.Thread(target=run_confirm)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    assert _entitlements(db, customer_id) == 1
    assert db.get_purchase(purchase_id)["payment_status"] == "SUCCEEDED"


@pytest.mark.skipif(not POSTGRES_DSN, reason="needs real locking: run with RITHAVO_WEB_TEST_POSTGRES_DSN (local only)")
def test_confirmation_waits_for_the_per_purchase_lock_then_creates_one_entitlement(pending, monkeypatch):
    """Deterministic proof of serialization: while another transaction holds the purchase's advisory lock,
    reconciliation must block (not skip ahead and double-grant); when released it completes exactly once."""
    import time

    import psycopg2

    app, client, db, customer_id, purchase_id, order_id = pending
    _order_status(monkeypatch, {order_id: "PAID"})
    holder = psycopg2.connect(POSTGRES_DSN)
    try:
        cur = holder.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"confirm_purchase:{purchase_id}",))
        done = []
        worker = threading.Thread(target=lambda: done.append(
            reconcile_cashfree_purchases(db, app.state.payment_gateway, apply=True)))
        worker.start()
        time.sleep(1.5)
        assert worker.is_alive(), "reconciliation did not wait for the purchase lock"
        assert _entitlements(db, customer_id) == 0
        holder.commit()  # releases the advisory lock
        worker.join(timeout=30)
        assert done and done[0]["counts"] == {"confirmed": 1}
        assert _entitlements(db, customer_id) == 1
    finally:
        holder.close()
