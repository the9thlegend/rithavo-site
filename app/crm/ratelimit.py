"""
Database-backed CRM rate limiting (scope 2.5, 2.5a).

Counters live in `crm_rate_limits` so every serverless instance shares them. Keys are
HMAC-SHA-256(subkey(scope), "<dimension>|<value>"): no email, address or token is stored. The key comes from
RITHAVO_CRM_RL_HMAC_KEY (base64url, >= 32 random bytes); a missing, malformed, short or session-secret-equal key
makes CRM authentication unavailable (`RateLimitKeyError`), never "no limiting". During a rotation the previous
key is also read (counts are summed) so limits stay continuous; writes always use the current key.
Only failed attempts are counted. Fixed windows; atomic upsert.
"""

import base64
import hashlib
import hmac
import logging
import time

import config
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException

from . import audit


class RateLimitKeyError(RuntimeError):
    """Category-only message; never carries key material."""


def _decode(value):
    if not value or not isinstance(value, str):
        raise RateLimitKeyError("rate_limit_key_invalid")
    try:
        raw = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except Exception:
        raise RateLimitKeyError("rate_limit_key_invalid")
    if len(raw) < 32:
        raise RateLimitKeyError("rate_limit_key_invalid")
    return raw


def keys():
    """[current] or [current, previous]. Raises RateLimitKeyError (fail closed)."""
    cur_raw = config.CRM_RL_HMAC_KEY
    cur = _decode(cur_raw)
    if config.SESSION_SECRET and cur_raw.strip() == str(config.SESSION_SECRET).strip():
        raise RateLimitKeyError("rate_limit_key_invalid")
    out = [cur]
    prev = config.CRM_RL_HMAC_KEY_PREVIOUS
    if prev and prev.strip():
        out.append(_decode(prev))
    return out


def validate():
    keys()


def ready():
    """For request handlers: an invalid key means CRM authentication is unavailable (503), never unlimited."""
    try:
        keys()
    except RateLimitKeyError:
        logging.getLogger("rithavo_web.crm").error("crm_rate_limit_key_invalid")
        raise HTTPException(status_code=503, detail="Sign-in is not available right now.")


def _subkey(key, scope):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"rithavo-crm-ratelimit-v1|" + scope.encode()).derive(key)


def key_hash(key, name, value):
    scope = name.split("_", 1)[0]
    return hmac.new(_subkey(key, scope), f"{name}|{value}".encode(), hashlib.sha256).hexdigest()


def _window(name, now):
    secs = config.CRM_LIMITS[name][1]
    return int(now // secs * secs), secs


def _hashes(name, value):
    return [key_hash(k, name, value) for k in keys()]


def count(db, name, value, now=None):
    now = time.time() if now is None else now
    start, _ = _window(name, now)
    hs = _hashes(name, value)
    marks = ",".join("?" for _ in hs)
    with db.connect() as conn:
        row = conn.execute(f"SELECT COALESCE(SUM(count), 0) AS n FROM crm_rate_limits "
                           f"WHERE window_start = ? AND key_hash IN ({marks})", (start, *hs)).fetchone()
    return int(row["n"] or 0)


def blocked(db, dims, now=None):
    """dims: [(name, value)]. Returns [(name, retry_after_seconds)] for every dimension at or over its limit."""
    now = time.time() if now is None else now
    out = []
    for name, value in dims:
        limit, secs = config.CRM_LIMITS[name]
        if count(db, name, value, now) >= limit:
            start, _ = _window(name, now)
            out.append((name, max(1, int(start + secs - now))))
    return out


def enforce(db, dims, now=None):
    """Raise a generic 429 (never naming the dimension) if any dimension is at its limit; audit the event."""
    hit = blocked(db, dims, now)
    if hit:
        scope = hit[0][0].split("_", 1)[0]
        audit.write_own(db, None, None, "rate_limited", scope, None, "denied", "dimension=" + ",".join(n for n, _ in hit))
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait and try again.",
                            headers={"Retry-After": str(max(s for _, s in hit))})


def fail(db, dims, now=None):
    """Count one failed attempt in every dimension (current key only) and purge expired windows."""
    now = time.time() if now is None else now
    cur = keys()[0]
    with db.connect() as conn:
        for name, value in dims:
            start, _ = _window(name, now)
            conn.execute(
                "INSERT INTO crm_rate_limits (key_hash, window_start, count) VALUES (?, ?, 1) "
                "ON CONFLICT (key_hash, window_start) DO UPDATE SET count = crm_rate_limits.count + 1",
                (key_hash(cur, name, value), start))
        longest = max(s for _, s in config.CRM_LIMITS.values())
        conn.execute("DELETE FROM crm_rate_limits WHERE window_start < ?", (int(now) - 2 * longest,))


def clear(db, name, value, now=None):
    now = time.time() if now is None else now
    start, _ = _window(name, now)
    hs = _hashes(name, value)
    marks = ",".join("?" for _ in hs)
    with db.connect() as conn:
        conn.execute(f"DELETE FROM crm_rate_limits WHERE window_start = ? AND key_hash IN ({marks})", (start, *hs))
