"""
Authentication for the Rithavo Web Platform — magic-link (original) plus
email+password (P0 conventional sign-in rework).

Same mechanism/shape as the sibling app.rithavo.com codebase (signed,
single-use, time-limited token) — deliberately re-implemented here rather
than imported, since the two services are independently deployed and must
never depend on each other at runtime. The two implementations can drift
in code, but they resolve to the exact same identity (Section 2 of the
architecture doc): `get_or_create_user(email)` against the one shared
`users` table is the only thing tying them together. Password hashes are
stored per-user on that same shared `users` row (password_hash column),
so a member who sets a password on one service can use it on the other
too, the same way an existing magic-link account already worked on both.
"""

import hashlib
import hmac
import os
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

TOKEN_MAX_AGE_SECONDS = 15 * 60
PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS = 30 * 60

# scrypt (stdlib hashlib, no new dependency) — OWASP-recommended memory-hard
# KDF, same tier as bcrypt/argon2. Parameters are the OWASP "interactive"
# baseline (N=2**14, r=8, p=1). Never invented: this is a standard,
# well-vetted scheme, applied with a random per-password salt and a
# constant-time comparison on verify.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """False on any malformed/unrecognized stored_hash rather than raising
    — a corrupt or foreign-shaped value must never be treated as "no
    password set" (which would be a bypass) or crash the login route."""
    if not stored_hash:
        return False
    try:
        scheme, salt_hex, hash_hex = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return hmac.compare_digest(derived, expected)


class MagicLinkError(Exception):
    pass


class PasswordResetError(Exception):
    pass


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    # Distinct salt from the sibling app's serializer — a token minted by
    # one service is cryptographically meaningless to the other, even if
    # (hypothetically) the same secret value were ever reused.
    return URLSafeTimedSerializer(secret_key, salt="rithavo-web-magic-link")


def _password_reset_serializer(secret_key: str) -> URLSafeTimedSerializer:
    # Distinct salt from both the magic-link serializer above and the
    # sibling service's own tokens — a password-reset token is
    # cryptographically meaningless anywhere else, and vice versa.
    return URLSafeTimedSerializer(secret_key, salt="rithavo-web-password-reset")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_magic_link_token(db, secret_key: str, email: str) -> str:
    email = email.strip().lower()
    token = _serializer(secret_key).dumps({"email": email, "nonce": secrets.token_hex(16)})
    db.record_magic_link_token(_token_hash(token), email)
    return token


def verify_and_consume_magic_link_token(db, secret_key: str, token: str) -> str:
    try:
        data = _serializer(secret_key).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired:
        raise MagicLinkError("This sign-in link has expired — request a new one.")
    except BadSignature:
        raise MagicLinkError("This sign-in link is invalid.")
    if not db.consume_magic_link_token(_token_hash(token)):
        raise MagicLinkError("This sign-in link has already been used — request a new one.")
    return data["email"]


def issue_password_reset_token(db, secret_key: str, email: str) -> str:
    email = email.strip().lower()
    token = _password_reset_serializer(secret_key).dumps({"email": email, "nonce": secrets.token_hex(16)})
    db.record_password_reset_token(_token_hash(token), email)
    return token


def verify_and_consume_password_reset_token(db, secret_key: str, token: str) -> str:
    """Same fail-closed shape as verify_and_consume_magic_link_token —
    expired/tampered/already-used all raise, never distinguished in a way
    that would leak account existence."""
    try:
        data = _password_reset_serializer(secret_key).loads(token, max_age=PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired:
        raise PasswordResetError("This password reset link has expired — request a new one.")
    except BadSignature:
        raise PasswordResetError("This password reset link is invalid.")
    if not db.consume_password_reset_token(_token_hash(token)):
        raise PasswordResetError("This password reset link has already been used — request a new one.")
    return data["email"]
