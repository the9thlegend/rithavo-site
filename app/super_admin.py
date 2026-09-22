"""
Home/Explore/Admin Integration Phase — ONE dedicated Rithavo Super
Admin account, logging in and operating the entire product from
rithavo.com (never app.rithavo.com — see app/internal_client.py's own
module docstring for why that service is now internal-only).

The account itself is an ordinary row in the shared `users` table,
created/authenticated through the exact same machinery every other
Rithavo account already uses (get_or_create_user, hash_password,
POST /auth/login) — there is no second login system. What makes it
"Super Admin" is membership in the `super_admins` table (owned by this
service alone, see app/db.py), an explicit, checkable fact, never an
email-matching heuristic and never a flag that could be casually
toggled by an unrelated code path.

bootstrap_super_admin is idempotent and safe to call on every process
start: with RITHAVO_SUPER_ADMIN_EMAIL/RITHAVO_SUPER_ADMIN_PASSWORD both
set, it ensures the account exists, its password hash matches the
currently-configured password, and it is a super_admins member — never
creating a duplicate account and never storing the plaintext password
anywhere. With either env var unset, it does nothing at all (no
passwordless admin account is ever silently created).
"""

from fastapi import HTTPException, Request

from .auth import hash_password
from .security import require_user


def bootstrap_super_admin(db, email, password) -> None:
    if not email or not password:
        return
    user_id = db.get_or_create_user(email)
    db.set_user_password(user_id, hash_password(password))
    db.add_super_admin_if_missing(user_id)


def require_super_admin(request: Request) -> int:
    """Every Admin route depends on this. Layers on top of require_user
    (still 401 for "not signed in at all") with a 403 for "signed in,
    but not the Super Admin" — never a 404, since unlike the sibling's
    own operator tool this route's existence is not a secret worth
    hiding, only its use is restricted."""
    user_id = require_user(request)
    db = request.app.state.db
    if not db.is_super_admin(user_id):
        raise HTTPException(status_code=403, detail="Not authorized.")
    return user_id
