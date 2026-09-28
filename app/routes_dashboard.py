"""
Pre-Launch Registration / Live Admin Command Center phase — the Super
Admin dashboard's own first-party data, unlike app/routes_admin.py's
routes (which all proxy to app.rithavo.com for Explore/Mentorship data
that lives there). users, career_profiles, and the new
analytics_events table are all owned locally by this service (users/
career_profiles are the SHARED replica both services read/write
directly against the same physical tables — see app/db.py's own schema
docstring), so every route here reads request.app.state.db directly,
never a service-to-service call.

Every route requires require_super_admin — the identical fail-closed
check app/routes_admin.py already uses (rithavo.com's own session +
explicit super_admins membership). Member-level detail (email,
completion, registration date) is never exposed through any other,
non-admin route.
"""

from fastapi import APIRouter, Request

from .analytics import since_for_range
from .cashfree_gateway import get_cashfree_gateway_if_configured
from .launch_lock import is_purchase_locked
from .super_admin import require_super_admin
import config

router = APIRouter(prefix="/admin/dashboard")

_VALID_RANGES = {"today", "7d", "30d", "all"}


def _range_key(range_param: str) -> str:
    return range_param if range_param in _VALID_RANGES else "7d"


def _member_name(profile_json: str):
    """Best-effort, read-only: a display name only if the existing
    career_profiles.profile_json already carries one (never a new
    users.name column, per this phase's explicit instruction not to
    add one merely for this dashboard). Any parse failure or missing
    field silently falls back to None, never an error."""
    if not profile_json:
        return None
    try:
        import json
        data = json.loads(profile_json)
    except (ValueError, TypeError):
        return None
    identity = data.get("identity") if isinstance(data, dict) else None
    if isinstance(identity, dict):
        name = identity.get("name") or identity.get("full_name")
        if isinstance(name, dict):
            name = name.get("value")
        if name:
            return name
    return None


@router.get("/summary")
def dashboard_summary(request: Request, range: str = "7d"):
    require_super_admin(request)
    db = request.app.state.db
    range_key = _range_key(range)
    since = since_for_range(range_key)

    total_members = db.count_all_users()
    new_today = db.count_users_since(since_for_range("today"))
    new_week = db.count_users_since(since_for_range("7d"))
    new_month = db.count_users_since(since_for_range("30d"))

    total_visitors = db.count_distinct_sessions("page_view", since=since)
    page_views = db.count_analytics_events("page_view", since=since)
    top_pages = db.list_top_pages(since=since, limit=5)
    most_visited = top_pages[0]["path"] if top_pages else None
    new_vs_returning = db.count_new_vs_returning_sessions(since)

    ci_interest = db.count_analytics_events("ci_interest", since=since)
    ad_interest = db.count_analytics_events("ad_interest", since=since)

    registrations_in_range = db.count_users_since(since)

    profiles_started = db.count_profiles_started()
    profiles_completed = db.count_profiles_completed()
    avg_completeness = db.average_profile_completeness()

    launch_locked = is_purchase_locked()

    return {
        "range": range_key,
        "generated_at": _now_iso(),
        "summary_cards": {
            "total_visitors": total_visitors,
            "total_members": total_members,
            "new_members": new_month,
            "page_views": page_views,
            "most_visited_page": most_visited,
            "ci_interest": ci_interest,
            "ad_interest": ad_interest,
            "launch_status": "LOCKED" if launch_locked else "OPEN",
        },
        "members": {
            "total": total_members,
            "new_today": new_today,
            "new_this_week": new_week,
            "new_this_month": new_month,
        },
        "visitors": {
            "total_visitors": total_visitors,
            "page_views": page_views,
            "top_pages": [dict(r) for r in top_pages],
            "most_visited_page": most_visited,
            "new_sessions": new_vs_returning["new"],
            "returning_sessions": new_vs_returning["returning"],
        },
        "product_interest": {
            "ci_interest": ci_interest,
            "ad_interest": ad_interest,
        },
        "funnel": {
            "visitors": total_visitors,
            "registrations": registrations_in_range,
            "members": total_members,
            "profile_activity": profiles_started,
            "ci_interest": ci_interest,
            "ad_interest": ad_interest,
            "purchases": "LOCKED" if launch_locked else "OPEN",
        },
        "profile_activity": {
            "profiles_started": profiles_started,
            "profiles_completed": profiles_completed,
            "average_completeness_pct": round(avg_completeness, 1) if avg_completeness is not None else None,
        },
        "system": {
            "registration": "OPEN",
            "purchases": "LOCKED" if launch_locked else "OPEN",
            "launch_date": config.LAUNCH_DATE or "NOT YET CONFIGURED",
            "cashfree_activated": get_cashfree_gateway_if_configured() is not None,
        },
    }


@router.get("/members")
def dashboard_members(request: Request, limit: int = 200):
    require_super_admin(request)
    db = request.app.state.db
    limit = max(1, min(limit, 500))
    rows = db.list_members(limit=limit)
    members = []
    for r in rows:
        members.append({
            "id": r["id"],
            "name": _member_name(r["profile_json"]),
            "email": r["email"],
            "registered_at": r["created_at"],
            "profile_completeness_pct": r["completeness_pct"],
        })
    return {"members": members, "count": len(members)}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
