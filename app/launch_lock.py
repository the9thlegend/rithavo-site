"""
Pre-Launch Product Lock phase — CI/AD are purchasable only on or after
config.LAUNCH_DATE. This is a hard business rule, enforced here once
and imported everywhere a purchase could otherwise be initiated (the
purchase-creation routes in app/main.py) — never left to the frontend
alone (a hidden/disabled button is a UX nicety on top of this, not a
substitute for it).

Fails closed: no configured date, an unparseable one, or a naive
datetime without a timezone are all treated as LOCKED, never as
"launch already happened." The only way purchases become enabled is an
explicit, valid, past-or-present RITHAVO_LAUNCH_DATE.
"""

from datetime import datetime, timezone

import config


def _parsed_launch_date():
    raw = config.LAUNCH_DATE
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A naive datetime is ambiguous about which timezone it means —
        # refuse to guess. Locked until it's supplied with an explicit
        # offset (or "Z").
        return None
    return parsed


def is_purchase_locked() -> bool:
    launch_date = _parsed_launch_date()
    if launch_date is None:
        return True
    return datetime.now(timezone.utc) < launch_date
