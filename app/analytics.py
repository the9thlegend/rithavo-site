"""
Pre-Launch Registration / Live Admin Command Center phase — the one
first-party, minimal analytics layer this phase adds. Deliberately NOT
a generic events warehouse: a closed, small vocabulary of event types,
one flat table (see app/db.py's analytics_events schema), no free-form
properties blob, no third-party service, no paid analytics provider.

Privacy: session_id is a random UUID stored in an ordinary (non-
httpOnly, first-party, no cross-site use) cookie -- readable by this
site's own JS so it can be sent with each event beacon, never shared
with or set by any other origin. It identifies a browser, not a
person; it is associated with a real user_id only once that visitor is
authenticated, exactly the way any ordinary session cookie already
works in this codebase. No IP address is stored. device_category is a
coarse three-way bucket (mobile/tablet/desktop) parsed from the
request's own User-Agent header server-side -- never a full UA string,
never a fingerprint, never third-party.
"""

import re
import secrets
from datetime import datetime, timedelta, timezone

SESSION_COOKIE_NAME = "rv_sid"

# The entire vocabulary this system will ever record. Anything else is
# rejected outright by record_event -- this is what keeps this a small,
# purpose-built table rather than an arbitrary event sink.
ALLOWED_EVENT_TYPES = frozenset({
    "page_view",
    "registration_completed",
    "login",
    "ci_interest",
    "ad_interest",
})


def get_or_create_session_id(request) -> tuple:
    """Returns (session_id, is_new). Reads the existing first-party
    cookie if present and looks like a real token; otherwise mints a
    fresh one. Never derived from IP, headers, or any other
    fingerprinting signal -- purely a random value the browser already
    holds or is about to be given."""
    existing = request.cookies.get(SESSION_COOKIE_NAME)
    if existing and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", existing):
        return existing, False
    return secrets.token_urlsafe(24), True


def device_category_from_user_agent(user_agent: str) -> str:
    """A coarse, deterministic three-way bucket -- never the raw UA
    string is stored. 'tablet' checked before 'mobile' since iPad/most
    Android tablet UAs also contain mobile-ish tokens on some devices."""
    ua = (user_agent or "").lower()
    if "ipad" in ua or "tablet" in ua or ("android" in ua and "mobile" not in ua):
        return "tablet"
    if "mobi" in ua or "iphone" in ua or "android" in ua:
        return "mobile"
    return "desktop"


def validate_event_type(event_type: str) -> bool:
    return event_type in ALLOWED_EVENT_TYPES


def since_for_range(range_key: str, now: datetime = None) -> str:
    """Maps the dashboard's fixed filter set to an ISO timestamp floor.
    'all' returns an epoch-old floor (effectively "since tracking
    began") rather than None, so every caller can use the same
    `created_at >= ?` shape uniformly."""
    now = now or datetime.now(timezone.utc)
    if range_key == "today":
        floor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_key == "7d":
        floor = now - timedelta(days=7)
    elif range_key == "30d":
        floor = now - timedelta(days=30)
    else:  # "all"
        floor = datetime(2000, 1, 1, tzinfo=timezone.utc)
    return floor.isoformat()
