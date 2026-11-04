"""
Razorpay is gone and must not come back by accident.

Evidence it was safe to remove (3 November 2026): no RAZORPAY_* variable exists in any Vercel environment (Production or
Preview) of this project, so payment_gateway.get_gateway() could never select it; every Production purchase went through Cashfree
(sandbox) or the test gateway; the only customer-facing trace was a script tag that loaded checkout.razorpay.com on two pages
and a branch in app-shell.js that only ran when the server reported gateway == "razorpay".

Cashfree is the intended payment provider. Re-introducing another provider needs a deliberate change to this test.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"tests", "node_modules", ".git", "tools"}
DOC_SUFFIXES = {".md"}


def _files(*suffixes):
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in suffixes and not (set(p.relative_to(ROOT).parts) & SKIP):
            yield p


def test_no_customer_facing_file_mentions_razorpay():
    offenders = [str(p.relative_to(ROOT)) for p in _files(".html", ".js", ".css", ".webmanifest", ".json")
                 if re.search(r"razorpay", p.read_text(encoding="utf-8", errors="ignore"), re.I)]
    assert not offenders, offenders


def test_no_python_module_imports_routes_or_configures_razorpay():
    code_hits = []
    for p in _files(".py"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"^\s*(from|import)\s+\S*razorpay|razorpay_gateway|RAZORPAY_|/payments/razorpay|razorpay_(order|payment)_id|razorpay_signature", line, re.I):
                code_hits.append((str(p.relative_to(ROOT)), stripped[:80]))
    assert not code_hits, code_hits


def test_the_razorpay_module_dependency_and_local_runner_are_gone():
    assert not (ROOT / "app" / "razorpay_gateway.py").exists()
    assert not (ROOT / "run_local_with_razorpay.py").exists()
    assert not re.search(r"(?im)^\s*razorpay\b", (ROOT / "requirements.txt").read_text(encoding="utf-8"))
    assert "razorpay" not in (ROOT / ".vercelignore").read_text(encoding="utf-8").lower()


def test_only_cashfree_or_the_test_gateway_can_be_selected(monkeypatch):
    from app import payment_gateway
    for var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"):
        monkeypatch.setenv(var, "set-but-ignored")
    monkeypatch.delenv("CASHFREE_APP_ID", raising=False)
    monkeypatch.delenv("CASHFREE_SECRET_KEY", raising=False)
    gateway = payment_gateway.get_gateway()
    assert gateway.name == "test"


def test_there_is_no_razorpay_route():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if "razorpay" in p.lower()]
    # the old Razorpay-shaped confirm routes (the Cashfree ones are /confirm-cashfree) are gone too
    assert "/career-intelligence/purchase/{purchase_id}/confirm" not in paths
    assert "/diagnosis/purchase/{purchase_id}/confirm" not in paths
