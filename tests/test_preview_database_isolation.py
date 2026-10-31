"""
Preview / Development deployments must never open the production database.

config.py is evaluated at import, so each case runs a fresh interpreter with a
controlled environment (no network, no database is opened: only the resulting
config values are printed).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROD_URL = "postgresql://prod-user:prod-pass@prod-host:6543/postgres"
PREVIEW_URL = "postgresql://preview-user:preview-pass@preview-host:6543/postgres"

PROBE = (
    "import json, config;"
    "print(json.dumps({'url': config.DATABASE_URL, 'db_path': str(config.DB_PATH),"
    " 'https_only': config.SESSION_COOKIE_HTTPS_ONLY, 'isolated': config.ISOLATED_DEPLOYMENT}))"
)


def run_config(tmp_path, **env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("RITHAVO_", "VERCEL", "DATABASE", "SUPABASE", "CASHFREE", "RAZORPAY"))}
    base.update({"TMP": str(tmp_path), "TEMP": str(tmp_path), "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"})
    base.update(env)
    out = subprocess.run([sys.executable, "-B", "-c", PROBE], cwd=ROOT, env=base,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-400:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_preview_ignores_the_production_database_url(tmp_path):
    cfg = run_config(tmp_path, VERCEL_ENV="preview", RITHAVO_WEB_DATABASE_URL=PROD_URL)
    assert cfg["isolated"] is True
    assert cfg["url"] is None                      # production URL never reaches the app
    assert Path(cfg["db_path"]).parent == tmp_path  # throwaway SQLite in the temp directory
    assert cfg["https_only"] is True


def test_development_deployment_is_isolated_too(tmp_path):
    cfg = run_config(tmp_path, VERCEL_ENV="development", RITHAVO_WEB_DATABASE_URL=PROD_URL)
    assert cfg["url"] is None and cfg["isolated"] is True


def test_preview_uses_only_an_explicit_preview_database(tmp_path):
    cfg = run_config(tmp_path, VERCEL_ENV="preview", RITHAVO_WEB_DATABASE_URL=PROD_URL,
                     RITHAVO_PREVIEW_DATABASE_URL=PREVIEW_URL)
    assert cfg["url"] == PREVIEW_URL
    assert cfg["https_only"] is True


def test_production_is_unchanged(tmp_path):
    cfg = run_config(tmp_path, VERCEL_ENV="production", RITHAVO_WEB_DATABASE_URL=PROD_URL,
                     RITHAVO_WEB_DB_PATH=str(tmp_path / "unused.db"))
    assert cfg["url"] == PROD_URL
    assert cfg["isolated"] is False and cfg["https_only"] is True


def test_off_vercel_behaviour_is_unchanged(tmp_path):
    local = run_config(tmp_path, RITHAVO_WEB_DB_PATH=str(tmp_path / "local.db"))
    assert local["url"] is None and local["isolated"] is False and local["https_only"] is False
    with_url = run_config(tmp_path, RITHAVO_WEB_DATABASE_URL=PROD_URL)
    assert with_url["url"] == PROD_URL  # a developer deliberately pointing at a database keeps working


def test_unknown_or_missing_vercel_env_never_isolates(tmp_path):
    # only the exact values isolate, so a production deployment can never be switched to SQLite by accident
    for value in ("", "Production", "prod", "staging"):
        cfg = run_config(tmp_path, VERCEL_ENV=value, RITHAVO_WEB_DATABASE_URL=PROD_URL)
        assert cfg["url"] == PROD_URL and cfg["isolated"] is False, value
