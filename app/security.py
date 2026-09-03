"""
Authorization layer — the ONE place ownership is checked, so it can only
be gotten right or wrong once, not re-implemented (and potentially
mis-implemented) per route.

Hard rule this module exists to satisfy: the backend must never rely on
"the frontend won't ask for another user's ID." Every function here
re-derives the caller's identity from the signed session on every call,
and every resource lookup filters/verifies ownership against that
identity before returning anything. A request for someone else's
education/experience row (by substituting an ID) is treated exactly like
a request for a row that doesn't exist — a plain 404, not a 403 — so an
attacker can't even distinguish "wrong ID" from "someone else's ID" by
the response.
"""

from fastapi import HTTPException, Request


def require_user(request: Request) -> int:
    """Every protected route depends on this. No user_id in the session ->
    401. This is the only place a request's caller identity is established
    — nothing downstream ever trusts a user_id passed in a URL, form field,
    query string, or JSON body."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user_id


def require_owned_row(row, session_user_id: int, owner_field: str = "user_id"):
    """Fetch-then-check, always in that order, always both steps. Returns
    the row only if it exists AND belongs to the caller. Otherwise raises
    a plain 404 — deliberately indistinguishable from "no such resource"
    so that probing IDs that belong to other users never confirms their
    existence."""
    if row is None or row[owner_field] != session_user_id:
        raise HTTPException(status_code=404, detail="Not found.")
    return row


def owned_education(db, education_id: int, session_user_id: int):
    return require_owned_row(db.get_education(education_id), session_user_id)


def owned_experience_entry(db, entry_id: int, session_user_id: int):
    return require_owned_row(db.get_experience_entry(entry_id), session_user_id)


def owned_career_profile(db, target_user_id: int, session_user_id: int):
    """The one case where the 'resource id' IS a user id (profile lookup
    by user_id, e.g. from a URL like /profile/{user_id}) — same rule
    applies: the target must equal the caller, or it's a 404."""
    if target_user_id != session_user_id:
        raise HTTPException(status_code=404, detail="Not found.")
    return db.get_career_profile(target_user_id)


# ---- Phase 1: purchases, entitlements, diagnoses, resumes ----
# Same fetch-then-check, same 404-not-403 rule as every check above.
# diagnostics/resumes are keyed on `profile_id` (== the owning user's id,
# per the shared schema) rather than `user_id` — owner_field makes that
# explicit rather than silently relying on require_owned_row's default.

def owned_purchase(db, purchase_id: int, session_user_id: int):
    return require_owned_row(db.get_purchase(purchase_id), session_user_id)


def owned_entitlement(db, entitlement_id: int, session_user_id: int):
    return require_owned_row(db.get_entitlement(entitlement_id), session_user_id)


def owned_diagnosis(db, diagnostic_id: int, session_user_id: int):
    return require_owned_row(db.get_diagnosis(diagnostic_id), session_user_id, owner_field="profile_id")


def owned_resume(db, resume_id: int, session_user_id: int):
    return require_owned_row(db.get_resume(resume_id), session_user_id, owner_field="profile_id")
