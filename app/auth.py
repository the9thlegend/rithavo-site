"""
Magic-link authentication for the Rithavo Web Platform.

Same mechanism/shape as the sibling app.rithavo.com codebase (signed,
single-use, time-limited token) — deliberately re-implemented here rather
than imported, since the two services are independently deployed and must
never depend on each other at runtime. The two implementations can drift
in code, but they resolve to the exact same identity (Section 2 of the
architecture doc): `get_or_create_user(email)` against the one shared
`users` table is the only thing tying them together.
"""

import hashlib
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

TOKEN_MAX_AGE_SECONDS = 15 * 60


class MagicLinkError(Exception):
    pass


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    # Distinct salt from the sibling app's serializer — a token minted by
    # one service is cryptographically meaningless to the other, even if
    # (hypothetically) the same secret value were ever reused.
    return URLSafeTimedSerializer(secret_key, salt="rithavo-web-magic-link")


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
