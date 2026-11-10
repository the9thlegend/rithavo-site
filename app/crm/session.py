"""
CRM session stages, held in the signed session cookie under keys that only the CRM uses (scope 2.2).
`crm_pre`: a correct password. Absolute life CRM_PRE_SECONDS from `at`; only onboarding and MFA operations.
`crm`: password and MFA both done. Absolute and idle limits.
The public customer key (`user_id`) is never read or written here. Dicts are reassigned, never mutated in place
(Starlette re-issues the cookie only when the session is marked modified).
"""

import time

PRE = "crm_pre"
FULL = "crm"


def _valid(stamp, keys):
    return isinstance(stamp, dict) and all(isinstance(stamp.get(k), int) for k in keys)


def pre(request):
    s = request.session.get(PRE)
    return s if _valid(s, ("uid", "cred", "mfa", "at")) else None


def full(request):
    s = request.session.get(FULL)
    return s if _valid(s, ("uid", "cred", "mfa", "at", "last")) else None


def uid(request):
    s = full(request) or pre(request)
    return s["uid"] if s else None


def start_pre(request, user_id, cred_epoch, mfa_epoch):
    now = int(time.time())
    request.session.pop(FULL, None)
    request.session[PRE] = {"uid": user_id, "cred": cred_epoch, "mfa": mfa_epoch, "at": now}


def update_pre_mfa_epoch(request, mfa_epoch):
    """After recovery: new MFA epoch for the recovering session only; the ORIGINAL `at` is kept, so the stage
    does not live a second longer than the password step allowed."""
    s = pre(request)
    if s is None:
        return False
    request.session[PRE] = {**s, "mfa": mfa_epoch}
    return True


def stamp_full(request, user_id, cred_epoch, mfa_epoch):
    now = int(time.time())
    request.session.pop(PRE, None)
    request.session[FULL] = {"uid": user_id, "cred": cred_epoch, "mfa": mfa_epoch, "at": now, "last": now}


def touch(request):
    s = full(request)
    if s is not None:
        request.session[FULL] = {**s, "last": int(time.time())}


def clear(request):
    request.session.pop(PRE, None)
    request.session.pop(FULL, None)
