"""
TOTP (RFC 6238 over RFC 4226) and secret protection for CRM MFA.

Standard library for the OTP maths; `cryptography` (Fernet) only for
encrypting the shared secret at rest. Nothing here logs or returns key
material.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

import config

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accept the previous, current and next step


def new_secret() -> bytes:
    return secrets.token_bytes(20)


def b32(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


def hotp(secret: bytes, counter: int, digits: int = DIGITS, algo=hashlib.sha1) -> str:
    mac = hmac.new(secret, struct.pack(">Q", counter), algo).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def step_for(ts: float = None) -> int:
    return int((time.time() if ts is None else ts) // STEP_SECONDS)


def code_at(secret: bytes, ts: float = None) -> str:
    return hotp(secret, step_for(ts))


def matching_step(secret: bytes, code: str, ts: float = None):
    """The time step the code belongs to (within the window), else None.
    Compares every candidate in constant time so timing does not reveal
    which step matched."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    base = step_for(ts)
    found = None
    for step in range(base - WINDOW, base + WINDOW + 1):
        if hmac.compare_digest(hotp(secret, step), code):
            found = step
    return found


def otpauth_uri(secret: bytes, account: str, issuer: str = "Rithavo CRM") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={b32(secret)}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


# ---- key handling -----------------------------------------------------------

class MfaKeyUnavailable(RuntimeError):
    pass


def _hkdf(key_material: bytes, info: bytes, length: int = 32) -> bytes:
    prk = hmac.new(b"rithavo-crm-mfa", key_material, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _fernet_keys() -> list:
    raw = config.CRM_MFA_ENCRYPTION_KEY
    if raw:
        return [k.strip().encode() for k in raw.split(",") if k.strip()]
    if config.ISOLATED_DEPLOYMENT or getattr(config, "CRM_ALLOW_DERIVED_KEY", False):
        # Preview/test only: derived from the session secret, never used in Production.
        return [base64.urlsafe_b64encode(_hkdf(config.SESSION_SECRET.encode(), b"preview-fernet"))]
    raise MfaKeyUnavailable("MFA encryption key is not configured.")


def _multi():
    from cryptography.fernet import Fernet, MultiFernet
    return MultiFernet([Fernet(k) for k in _fernet_keys()])


def encrypt_secret(secret: bytes) -> str:
    return _multi().encrypt(secret).decode()


def decrypt_secret(token: str) -> bytes:
    return _multi().decrypt(token.encode())


def recovery_hash_candidates(code: str) -> list:
    """One hash per configured key, newest first, so recovery codes issued
    before a key rotation still verify. New codes are stored with [0]."""
    normalized = code.strip().upper().replace("-", "").replace(" ", "")
    return [hmac.new(_hkdf(k, b"recovery-hmac"), normalized.encode(), hashlib.sha256).hexdigest()
            for k in _fernet_keys()]


def recovery_hash(code: str) -> str:
    return recovery_hash_candidates(code)[0]


def new_recovery_codes(n: int = 10) -> list:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        out.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:]}")
    return out
