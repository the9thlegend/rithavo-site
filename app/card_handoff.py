"""
Phase P0.2 — Profile -> Rithavo Card handoff (rithavo.com side).

This module does exactly one thing: mint a short-lived, single-purpose,
signed token proving "this already-authenticated rithavo.com session
belongs to users.id = N" so the sibling app (app.rithavo.com) can
establish its own session for the same person without asking them to
sign in a second time.

Deliberately minimal and one-directional:
  - Never reads, writes, or reasons about Card data (card_settings,
    capabilities, trust, etc.) — this service has no business logic
    about Cards at all. The sibling's existing, unmodified
    get_or_create_card_settings_for_person remains the only thing that
    ever creates or mutates a Card.
  - Uses its own dedicated secret (config.CARD_HANDOFF_SECRET) — never
    SESSION_SECRET/RITHAVO_WEB_SESSION_SECRET — so this token can never
    be forged from (or used to forge) an ordinary Rithavo session, and
    a leaked session secret could never be used to mint one of these.
  - Stateless on this side: single-use enforcement happens entirely on
    the sibling, which is the only side with a database that outlives
    a single request from either service. Nothing here is persisted.
"""

import secrets

from itsdangerous import URLSafeTimedSerializer

# Intentionally short — this token only needs to survive the single
# browser round trip from clicking the Card CTA to the sibling
# consuming it, not a real session lifetime.
HANDOFF_TOKEN_MAX_AGE_SECONDS = 90


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    # A dedicated salt, separate from any other itsdangerous use in
    # either codebase (e.g. the sibling's own magic-link salt) — even if
    # a secret were ever reused by mistake, tokens minted for one
    # purpose could never be replayed against another.
    return URLSafeTimedSerializer(secret_key, salt="rithavo-card-handoff")


def issue_card_handoff_token(secret_key: str, user_id: int) -> str:
    """The token's entire payload: which shared users.id this is for,
    a fixed purpose tag (defense in depth against cross-purpose token
    reuse even under a shared secret), and a random nonce so two tokens
    minted in the same second are never byte-identical.

    Raises ValueError if secret_key is falsy — a defense-in-depth check
    behind POST /card/continue's own fail-closed check (Phase P0.2A),
    so this can never sign a token with no real secret even if some
    future caller forgets that check."""
    if not secret_key:
        raise ValueError("CARD_HANDOFF_SECRET is not configured — refusing to mint a handoff token.")
    return _serializer(secret_key).dumps({
        "user_id": user_id,
        "purpose": "card_handoff",
        "nonce": secrets.token_hex(16),
    })
