"""
Runtime proof for Preview isolation: import the REAL app (app.main) the way a
Vercel Preview function does, with a production-looking database URL in the
environment, and check that (a) it works on a throwaway SQLite file, (b) no
network connection or DNS lookup to the production host is ever attempted, on
any code path, and (c) if SQLite itself cannot start the app fails closed
instead of falling back to the production database.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROD_URL = "postgresql://prod-user:prod-pass@prod-host.invalid:6543/postgres"

PROBE = textwrap.dedent("""
    import json, socket, sys
    attempts = []
    _c, _g = socket.socket.connect, socket.getaddrinfo
    def connect(self, address, *a, **k):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if host not in ("127.0.0.1", "::1", "localhost", "testserver"):
            attempts.append(("connect", str(host)))
        return _c(self, address, *a, **k)
    def getaddrinfo(host, *a, **k):
        if host not in ("127.0.0.1", "::1", "localhost", "testserver", None, ""):
            attempts.append(("dns", str(host)))
        return _g(host, *a, **k)
    socket.socket.connect, socket.getaddrinfo = connect, getaddrinfo
    import config
    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    codes = {
        "me": client.get("/me").status_code,                       # needs a session: 401
        "price": client.get("/diagnosis/price").status_code,       # 401 without a session
        "explore": client.get("/explore/stories").status_code,     # data-backed read path
        "auth_start": client.post("/auth/start", data={"email": "preview-check@example.test"}).status_code,
        "analytics": client.post("/analytics/event", json={"event_type": "page_view", "path": "/"}).status_code,
        "webhook": client.post("/payments/cashfree/webhook", content=b"{}").status_code,
    }
    print(json.dumps({"postgres": app.state.db._is_postgres, "db_path": str(app.state.db.db_path),
                      "config_url": config.DATABASE_URL, "codes": codes, "attempts": attempts}))
""")


def run(tmp_path, **env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("RITHAVO_", "VERCEL", "DATABASE", "SUPABASE", "CASHFREE", "RAZORPAY"))}
    base.update({"TMP": str(tmp_path), "TEMP": str(tmp_path), "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1",
                 "RITHAVO_WEB_SESSION_SECRET": "preview-session-secret-not-real"})
    base.update(env)
    return subprocess.run([sys.executable, "-B", "-c", PROBE], cwd=ROOT, env=base, capture_output=True, text=True, timeout=120)


def test_preview_app_runs_on_sqlite_and_never_contacts_the_production_host(tmp_path):
    out = run(tmp_path, VERCEL_ENV="preview", RITHAVO_WEB_DATABASE_URL=PROD_URL)
    assert out.returncode == 0, out.stderr[-600:]
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["postgres"] is False and result["config_url"] is None
    assert Path(result["db_path"]).parent == tmp_path and Path(result["db_path"]).exists()
    assert result["attempts"] == []                                    # no connect, no DNS lookup, on any path
    assert result["codes"]["me"] == 401 and result["codes"]["price"] == 401
    assert result["codes"]["auth_start"] == 200 and result["codes"]["analytics"] == 200
    assert result["codes"]["webhook"] == 404                           # no Cashfree gateway on a Preview


def test_development_deployment_behaves_the_same(tmp_path):
    out = run(tmp_path, VERCEL_ENV="development", RITHAVO_WEB_DATABASE_URL=PROD_URL)
    assert out.returncode == 0, out.stderr[-600:]
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["postgres"] is False and result["attempts"] == []


def test_if_sqlite_cannot_start_the_app_fails_closed_without_touching_production(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    out = run(tmp_path, VERCEL_ENV="preview", RITHAVO_WEB_DATABASE_URL=PROD_URL,
              RITHAVO_WEB_DB_PATH=str(blocker / "inside" / "preview.db"))
    assert out.returncode != 0                                         # startup fails, it does not fall back
    assert "prod-host.invalid" not in out.stdout                       # (the probe never printed a result either)
    assert "prod-host.invalid" not in out.stderr                       # no connection error naming the production host


def test_identical_preview_and_production_urls_are_refused_at_startup(tmp_path):
    out = run(tmp_path, VERCEL_ENV="preview", RITHAVO_WEB_DATABASE_URL=PROD_URL, RITHAVO_PREVIEW_DATABASE_URL=PROD_URL)
    assert out.returncode != 0
    assert "must not point at the production database" in out.stderr


def test_same_server_database_and_user_with_a_different_password_is_also_refused(tmp_path):
    same_target = "postgresql://prod-user:another-password@prod-host.invalid:6543/postgres"
    out = run(tmp_path, VERCEL_ENV="preview", RITHAVO_WEB_DATABASE_URL=PROD_URL, RITHAVO_PREVIEW_DATABASE_URL=same_target)
    assert out.returncode != 0 and "must not point at the production database" in out.stderr


def test_a_genuinely_separate_preview_database_is_accepted(tmp_path):
    other = "postgresql://preview-user:preview-pass@preview-host.invalid:6543/postgres"
    probe = "import config; print(config.DATABASE_URL)"
    base = {k: v for k, v in os.environ.items() if not k.startswith(("RITHAVO_", "VERCEL"))}
    base.update({"VERCEL_ENV": "preview", "RITHAVO_WEB_DATABASE_URL": PROD_URL, "RITHAVO_PREVIEW_DATABASE_URL": other,
                 "RITHAVO_WEB_DB_PATH": str(tmp_path / "unused.db")})
    out = subprocess.run([sys.executable, "-B", "-c", probe], cwd=ROOT, env=base, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == other
