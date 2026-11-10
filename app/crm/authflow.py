"""
CRM sign-in, invitation acceptance (including the one-time Founder bootstrap) and password change.
Independent of the customer site: own credential, own session stages, own rate limits. Responses never reveal
whether an account exists, has a password, or why a token failed.
"""

import config
from fastapi import HTTPException

from . import access, audit, credentials, identity, invites, locking, mfa, ratelimit, session
from .source import source_bucket

GENERIC_LOGIN = "Invalid email or password."
GENERIC_LINK = "This link is not valid."


def _norm_email(email):
    return (email or "").strip().lower() if isinstance(email, str) else ""


def login(request, db, email, password):
    """Password step. Returns {"status": "ok", "mfa": "enrol"|"verify"|...}; creates only the `crm_pre` stage."""
    access.crm_enabled_or_404()
    ratelimit.ready()
    email = _norm_email(email)
    src = source_bucket(request)
    pair = ("login_pair", f"{email}|{src}")
    dims = [pair, ("login_account", email), ("login_source", src)]
    ratelimit.enforce(db, dims)
    ident = cred = None
    with db.connect() as conn:
        uid = identity.user_id_by_email(conn, email) if email else None
        m = None
        if uid is not None:
            ident = identity.resolve(conn, uid)
            cred = credentials.get(conn, uid)
            m = mfa.get_row(conn, uid)
    ok = credentials.verify(cred, password)
    eligible = (ident is not None and ident.role is not None and ident.state not in ("deactivated", "invited")
                and credentials.has_password(cred))
    if not (ok and eligible):
        ratelimit.fail(db, dims)
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN)
    ratelimit.clear(db, *pair)
    epoch = m["session_epoch"] if m is not None else 1
    session.start_pre(request, ident.user_id, cred["credential_epoch"], epoch)
    audit.write_own(db, ident.user_id, ident.role, "crm_login", "staff", ident.user_id)
    state = "enrol"
    if m is not None and mfa.is_enrolled(m):
        state = "verify"
    return {"status": "ok", "mfa": state}


def accept(request, db, token, password):
    """Invitation acceptance and Founder bootstrap. The token is consumed and the password set in one transaction.
    Limits (scope 2.5): unknown or failed tokens count against the invitation-source limit (10 / 15 min) and the
    bootstrap-source limit (5 / 30 min) and the bootstrap global brake; the stricter bootstrap limits are ENFORCED
    only once the token is known to be a bootstrap token."""
    access.crm_enabled_or_404()
    ratelimit.ready()
    src = source_bucket(request)
    inv_src = ("invite_source", src)
    boot_src = ("bootstrap_source", src)
    boot_all = ("bootstrap_global", "all")
    ratelimit.enforce(db, [inv_src])
    with db.connect() as conn:
        inv = invites.peek(conn, token, (invites.STAFF_INVITE, invites.FOUNDER_BOOTSTRAP))
    if inv is None:
        _failed(db, [inv_src, boot_src, boot_all])
        raise HTTPException(status_code=400, detail=GENERIC_LINK)
    purpose, user_id, email = inv["purpose"], inv["user_id"], inv["email"]
    if purpose == invites.FOUNDER_BOOTSTRAP:
        ratelimit.enforce(db, [boot_src, boot_all])
    tok_dim = ("invite_token", str(inv["id"]))
    ratelimit.enforce(db, [tok_dim])
    try:
        credentials.check_policy(password, email)
    except HTTPException:
        ratelimit.fail(db, [tok_dim])
        raise
    with db.connect() as conn:
        locking.lock_person(conn, user_id)
        row = None
        if purpose == invites.FOUNDER_BOOTSTRAP:
            if identity.is_founder(conn, user_id, email):
                row = invites.consume(conn, token, (purpose,))
        else:
            s, state = identity.staff_state(conn, user_id)
            if s is not None and state == "invited" and not identity.is_allowlisted(email):
                row = invites.consume(conn, token, (purpose,))
        if row is not None:
            credentials.set_password(conn, user_id, password, set_by=user_id)
            if purpose == invites.FOUNDER_BOOTSTRAP:
                mfa.reset_user(conn, user_id)              # a new Founder credential always means a new authenticator
                audit.write(conn, None, "owner_db", "founder_bootstrap_accepted", "staff", user_id)
                audit.write(conn, user_id, "founder", "founder_credential_set", "staff", user_id)
            else:
                audit.write(conn, user_id, "specialist", "staff_invite_accepted", "staff", user_id)
    if row is None:
        _failed(db, [inv_src, boot_src, boot_all] if purpose == invites.FOUNDER_BOOTSTRAP else [inv_src])
        raise HTTPException(status_code=400, detail=GENERIC_LINK)
    return {"status": "ok"}


def _failed(db, dims):
    ratelimit.fail(db, dims)
    if ("bootstrap_global", "all") in dims:
        _bootstrap_brake(db)


def _bootstrap_brake(db):
    """Global brake for the highest-value endpoint: at the global failure limit every open bootstrap token is revoked
    (the owner issues a new one) and the event is audited once."""
    if ratelimit.count(db, "bootstrap_global", "all") >= config.CRM_LIMITS["bootstrap_global"][0]:
        with db.connect() as conn:
            n = invites.revoke_all_open(conn, invites.FOUNDER_BOOTSTRAP)
            if n:
                audit.write(conn, None, "system", "founder_bootstrap_revoked_rate_limit", "invite", None, "denied",
                            f"revoked={n}")
        audit.write_own(db, None, "system", "founder_bootstrap_failed", "invite", None, "denied", None)


def change_password(request, db, actor, current, new):
    ratelimit.ready()
    dim = ("password_account", str(actor.user_id))
    ratelimit.enforce(db, [dim])
    with db.connect() as conn:
        cred = credentials.get(conn, actor.user_id)
    if not credentials.verify(cred, current):
        ratelimit.fail(db, [dim])
        raise HTTPException(status_code=401, detail="Your current password is not correct.")
    credentials.check_policy(new, actor.email)
    with db.connect() as conn:
        locking.lock_person(conn, actor.user_id)
        credentials.set_password(conn, actor.user_id, new, set_by=actor.user_id)   # epoch bump ends every session
        audit.write(conn, actor.user_id, actor.role, "crm_password_changed", "staff", actor.user_id)
    session.clear(request)
    return {"status": "ok", "next": "sign_in"}
