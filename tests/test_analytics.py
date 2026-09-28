"""
Pre-Launch Registration / Live Admin Command Center phase — unit-level
coverage for app/analytics.py's small first-party primitives. Deliberately
narrow: this is not a generic events-warehouse, so the tests only need to
confirm the closed vocabulary, the session-cookie derivation, the coarse
device bucketing, and the fixed range-to-timestamp mapping the dashboard
relies on.
"""

from datetime import datetime, timezone

from app.analytics import (
    ALLOWED_EVENT_TYPES,
    device_category_from_user_agent,
    get_or_create_session_id,
    since_for_range,
    validate_event_type,
)


class _FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


def test_allowed_event_types_is_the_closed_vocabulary():
    assert ALLOWED_EVENT_TYPES == {
        "page_view", "registration_completed", "login", "ci_interest", "ad_interest",
    }


def test_validate_event_type_accepts_only_the_allowlist():
    assert validate_event_type("page_view") is True
    assert validate_event_type("ci_interest") is True
    assert validate_event_type("purchase_completed") is False
    assert validate_event_type("") is False
    assert validate_event_type(None) is False


def test_get_or_create_session_id_mints_a_fresh_token_when_absent():
    session_id, is_new = get_or_create_session_id(_FakeRequest())
    assert is_new is True
    assert len(session_id) >= 16


def test_get_or_create_session_id_reuses_a_valid_existing_cookie():
    existing = "abc123_-XYZ_a-plausible-token"
    session_id, is_new = get_or_create_session_id(_FakeRequest({"rv_sid": existing}))
    assert is_new is False
    assert session_id == existing


def test_get_or_create_session_id_rejects_a_malformed_cookie_value():
    # Never trusts an implausible/garbage cookie value as a real session --
    # mints a fresh one instead of passing through attacker-controlled input.
    session_id, is_new = get_or_create_session_id(_FakeRequest({"rv_sid": "../../etc/passwd"}))
    assert is_new is True
    assert session_id != "../../etc/passwd"


def test_device_category_desktop_default():
    assert device_category_from_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)") == "desktop"
    assert device_category_from_user_agent(None) == "desktop"
    assert device_category_from_user_agent("") == "desktop"


def test_device_category_mobile():
    assert device_category_from_user_agent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
    ) == "mobile"
    assert device_category_from_user_agent(
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) Mobile"
    ) == "mobile"


def test_device_category_tablet():
    assert device_category_from_user_agent(
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"
    ) == "tablet"
    assert device_category_from_user_agent(
        "Mozilla/5.0 (Linux; Android 13; SM-X200) AppleWebKit"
    ) == "tablet"


def test_since_for_range_today_is_midnight_utc():
    now = datetime(2026, 9, 29, 15, 30, tzinfo=timezone.utc)
    floor = since_for_range("today", now=now)
    assert floor.startswith("2026-09-29T00:00:00")


def test_since_for_range_7d_and_30d_are_relative_to_now():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    assert since_for_range("7d", now=now).startswith("2026-09-22")
    assert since_for_range("30d", now=now).startswith("2026-08-30")


def test_since_for_range_all_is_an_epoch_old_floor_not_none():
    # Callers uniformly do `created_at >= ?` -- "all" must still be a
    # real, comparably-early timestamp string, never None/null.
    floor = since_for_range("all")
    assert floor.startswith("2000-01-01")


def test_since_for_range_unknown_key_falls_back_to_the_all_time_floor():
    # Only "today"/"7d"/"30d" are special-cased; anything else (including
    # an unrecognized key) takes the same epoch-old floor as "all" --
    # the route layer (routes_dashboard._range_key) is what actually
    # constrains callers to the dashboard's fixed filter set.
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    assert since_for_range("bogus", now=now) == since_for_range("all", now=now)
