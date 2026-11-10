"""CRM rate limiting, its HMAC secret and trusted source extraction (scope 2.5, 2.5a, 2.5b; tests 6.4)."""

import base64
import logging
import os
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from app.crm import ratelimit, source
from app.db import Database
from tests.crm_helpers import PASSWORD, Person, clock, crm  # noqa: F401


def h(ip):
    return {"x-vercel-forwarded-for": ip}


@pytest.fixture
def vercel(monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", True)


def bad_login(c, email, ip="198.51.100.1", pw="definitely-wrong-pw"):
    return c.post("/crm/auth/login", json={"email": email, "password": pw}, headers=h(ip))


def good_login(c, email, ip="198.51.100.1"):
    return c.post("/crm/auth/login", json={"email": email, "password": PASSWORD}, headers=h(ip))


# ---- login: the three dimensions ---------------------------------------------------------------------------------

def test_login_pair_limit_is_5_failures_and_a_correct_login_before_it_clears_the_pair(vercel, crm):
    s = crm.specialist("rl1@example.test", enrol=False)
    c = TestClient(crm.app)
    for _ in range(4):
        assert bad_login(c, s.email).status_code == 401
    assert good_login(c, s.email).status_code == 200                       # N-1 failures: still allowed, and clears the pair
    assert ratelimit.count(crm.db, "login_pair", f"{s.email}|198.51.100.1") == 0
    assert ratelimit.count(crm.db, "login_account", s.email) == 4          # only the pair counter was cleared
    for _ in range(5):
        assert bad_login(c, s.email).status_code == 401
    r = good_login(c, s.email)                                             # 5 failures: even the right password is refused
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert r.json() == {"detail": "Too many attempts. Please wait and try again."}      # never says which dimension


def test_the_pair_limit_does_not_lock_the_person_out_from_another_place(vercel, crm):
    s = crm.specialist("rl2@example.test", enrol=False)
    c = TestClient(crm.app)
    for _ in range(5):
        bad_login(c, s.email, ip="203.0.113.1")                            # a stranger hammers the account from one place
    assert good_login(c, s.email, ip="203.0.113.1").status_code == 429
    assert good_login(c, s.email, ip="203.0.113.99").status_code == 200    # the real person, elsewhere, still signs in


def test_login_account_limit_is_20_across_sources(vercel, crm):
    s = crm.specialist("rl3@example.test", enrol=False)
    c = TestClient(crm.app)
    for i in range(20):
        assert bad_login(c, s.email, ip="192.0.2.%d" % (i + 1)).status_code == 401
    assert good_login(c, s.email, ip="192.0.2.200").status_code == 429     # a 21st source: the account itself is at its limit
    other = crm.specialist("rl3b@example.test", enrol=False)
    assert good_login(c, other.email, ip="192.0.2.201").status_code == 200  # other accounts are unaffected


def test_login_source_limit_is_20_across_accounts(vercel, crm):
    people = [crm.specialist("spray%d@example.test" % i, enrol=False) for i in range(21)]
    c = TestClient(crm.app)
    for i in range(20):
        assert bad_login(c, people[i].email, ip="203.0.113.7").status_code == 401
    assert good_login(c, people[20].email, ip="203.0.113.7").status_code == 429     # one source spraying accounts is stopped
    assert good_login(c, people[20].email, ip="203.0.113.8").status_code == 200


def test_windows_expire_after_15_minutes(vercel, crm):
    s = crm.specialist("rl4@example.test", enrol=False)
    c = TestClient(crm.app)
    for _ in range(5):
        bad_login(c, s.email)
    assert good_login(c, s.email).status_code == 429
    crm.clock.advance(15 * 60)
    assert good_login(c, s.email).status_code == 200


def test_a_throttled_login_does_not_reveal_whether_the_account_exists(vercel, crm):
    c = TestClient(crm.app)
    for _ in range(5):
        bad_login(c, "ghost@example.test")
    r = bad_login(c, "ghost@example.test")
    s = crm.specialist("real@example.test", enrol=False)
    for _ in range(5):
        bad_login(c, s.email, ip="203.0.113.60")
    r2 = bad_login(c, s.email, ip="203.0.113.60")
    assert (r.status_code, r.json()) == (r2.status_code, r2.json()) == (429, {"detail": "Too many attempts. Please wait and try again."})


# ---- invitation, password change, MFA ----------------------------------------------------------------------------

def test_invitation_source_limit_is_10_invalid_tokens(vercel, crm):
    c = TestClient(crm.app)
    for i in range(10):
        assert c.post("/crm/auth/accept", json={"token": "nope-%d" % i, "password": "whatever-password-1"}, headers=h("203.0.113.20")).status_code == 400
    assert c.post("/crm/auth/accept", json={"token": "nope-x", "password": "whatever-password-1"}, headers=h("203.0.113.20")).status_code == 429
    assert c.post("/crm/auth/accept", json={"token": "nope-y", "password": "whatever-password-1"}, headers=h("203.0.113.21")).status_code == 400


def test_invitation_per_token_limit_is_20_rejected_passwords(vercel, crm):
    f = crm.founder()
    body = f.post("/crm/staff/invite", {"email": "slow@example.test", "display_name": "Slow"}).json()
    tok = body["link"].split("token=")[1]
    c = TestClient(crm.app)
    for i in range(20):
        assert c.post("/crm/auth/accept", json={"token": tok, "password": "short"}, headers=h("203.0.113.%d" % (30 + i))).status_code == 422
    r = c.post("/crm/auth/accept", json={"token": tok, "password": "a-perfectly-good-password-1"}, headers=h("203.0.113.99"))
    assert r.status_code == 429


def test_change_password_limit_is_5_failures_per_account(vercel, crm):
    s = crm.specialist("rl5@example.test")
    for _ in range(5):
        assert s.post("/crm/auth/change-password", {"current": "not-my-password-1", "new": "new-password-long-1"}).status_code == 401
    assert s.post("/crm/auth/change-password", {"current": PASSWORD, "new": "new-password-long-1"}).status_code == 429


def test_mfa_verify_source_limit_is_30_failures(vercel, crm):
    s = crm.specialist("rl6@example.test")
    for _ in range(30):
        ratelimit.fail(crm.db, [("mfa_source", "203.0.113.90")])
    q = Person(crm, s.email, "specialist").login()
    r = q.client.post("/crm/mfa/verify", json={"code": "000000"}, headers=h("203.0.113.90"))
    assert r.status_code == 429
    assert q.client.post("/crm/mfa/verify", json={"code": "000000"}, headers=h("203.0.113.91")).status_code == 401


def test_the_public_customer_limiter_is_untouched(crm):
    import app.rate_limit as public
    assert not hasattr(public, "CRM_LIMITS")
    src = open(public.__file__, encoding="utf-8").read()
    assert "crm" not in src.lower()


# ---- the HMAC secret (2.5a) ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "   ", "not base64 !!", base64.urlsafe_b64encode(os.urandom(16)).decode(),
                                   base64.urlsafe_b64encode(os.urandom(31)).decode()])
def test_a_missing_malformed_or_short_key_fails_closed(crm, monkeypatch, value):
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", value)
    with pytest.raises(ratelimit.RateLimitKeyError):
        ratelimit.validate()
    c = TestClient(crm.app)
    assert c.post("/crm/auth/login", json={"email": "a@example.test", "password": "x" * 14}).status_code == 503
    assert c.post("/crm/auth/accept", json={"token": "t", "password": "x" * 14}).status_code == 503


def test_a_key_equal_to_the_session_secret_is_refused(crm, monkeypatch):
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", key)
    monkeypatch.setattr(config, "SESSION_SECRET", key)
    with pytest.raises(ratelimit.RateLimitKeyError):
        ratelimit.validate()


def test_stored_keys_are_hashes_and_never_contain_the_email_address_or_token(vercel, crm):
    s = crm.specialist("hashme@example.test", enrol=False)
    c = TestClient(crm.app)
    bad_login(c, s.email, ip="203.0.113.44")
    c.post("/crm/auth/accept", json={"token": "SECRET-TOKEN-VALUE", "password": "x" * 14}, headers=h("203.0.113.44"))
    with crm.db.connect() as conn:
        rows = conn.execute("SELECT key_hash FROM crm_rate_limits").fetchall()
    assert rows and all(re.fullmatch(r"[0-9a-f]{64}", r["key_hash"]) for r in rows)
    blob = " ".join(r["key_hash"] for r in rows)
    for needle in ("hashme", "example.test", "203.0.113.44", "SECRET-TOKEN-VALUE"):
        assert needle not in blob


def test_subkeys_differ_per_scope_and_two_keys_give_different_hashes(crm):
    k1, k2 = os.urandom(32), os.urandom(32)
    assert ratelimit.key_hash(k1, "login_pair", "x") != ratelimit.key_hash(k1, "bootstrap_source", "x")
    assert ratelimit.key_hash(k1, "login_pair", "x") != ratelimit.key_hash(k2, "login_pair", "x")
    assert ratelimit.key_hash(k1, "login_pair", "x") == ratelimit.key_hash(k1, "login_pair", "x")     # stable across instances


def test_two_instances_with_the_same_key_share_counters_and_different_keys_do_not(vercel, crm, monkeypatch):
    s = crm.specialist("rl7@example.test", enrol=False)
    other = Database(crm.db.db_path)
    c = TestClient(crm.app)
    for _ in range(3):
        bad_login(c, s.email)
    crm.app.state.db = other
    for _ in range(2):
        bad_login(c, s.email)
    assert good_login(c, s.email).status_code == 429                          # failures split across two instances add up
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())   # a mis-provisioned instance
    assert ratelimit.count(other, "login_pair", f"{s.email}|198.51.100.1") == 0                       # would not see them (detectable)


def test_the_key_never_appears_in_logs_responses_or_audit_rows(vercel, crm, caplog):
    s = crm.specialist("rl8@example.test", enrol=False)
    c = TestClient(crm.app)
    with caplog.at_level(logging.DEBUG):
        for _ in range(6):
            r = bad_login(c, s.email)
    key = config.CRM_RL_HMAC_KEY
    assert key not in r.text and key not in "\n".join(m.getMessage() for m in caplog.records)
    with crm.db.connect() as conn:
        assert key not in " ".join(str(dict(x)) for x in conn.execute("SELECT * FROM crm_audit_log").fetchall())


def test_rotation_with_a_previous_key_keeps_the_limits_continuous(vercel, crm, monkeypatch):
    s = crm.specialist("rl9@example.test", enrol=False)
    c = TestClient(crm.app)
    old = config.CRM_RL_HMAC_KEY
    for _ in range(3):
        bad_login(c, s.email)
    new = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", new)
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY_PREVIOUS", old)
    assert ratelimit.count(crm.db, "login_pair", f"{s.email}|198.51.100.1") == 3          # old counters still count
    for _ in range(2):
        bad_login(c, s.email)                                                              # new failures are written under the NEW key
    assert good_login(c, s.email).status_code == 429                                       # 3 (old) + 2 (new) = 5: continuous
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY_PREVIOUS", None)                           # previous removed after the window
    assert ratelimit.count(crm.db, "login_pair", f"{s.email}|198.51.100.1") == 2
    with crm.db.connect() as conn:                                                         # and writes used only the new key
        hashes = {r["key_hash"] for r in conn.execute("SELECT key_hash FROM crm_rate_limits").fetchall()}
    new_raw = base64.urlsafe_b64decode(new + "=" * (-len(new) % 4))
    assert ratelimit.key_hash(new_raw, "login_pair", f"{s.email}|198.51.100.1") in hashes


def test_an_emergency_rotation_without_a_previous_key_starts_empty_without_error(vercel, crm, monkeypatch):
    s = crm.specialist("rl10@example.test", enrol=False)
    c = TestClient(crm.app)
    for _ in range(5):
        bad_login(c, s.email)
    assert good_login(c, s.email).status_code == 429
    monkeypatch.setattr(config, "CRM_RL_HMAC_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    assert good_login(c, s.email).status_code == 200


# ---- trusted source extraction (2.5b) ---------------------------------------------------------------------------

def req(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers={k.lower(): v for k, v in (headers or {}).items()}, client=SimpleNamespace(host=host) if host else None)


SPOOFS = {"X-Forwarded-For": "6.6.6.6", "Forwarded": "for=6.6.6.6", "X-Real-IP": "6.6.6.6", "CF-Connecting-IP": "6.6.6.6",
          "True-Client-IP": "6.6.6.6", "X-Client-IP": "6.6.6.6"}


def test_on_vercel_only_the_platform_header_is_used(monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", True)
    assert source.source_bucket(req({"x-vercel-forwarded-for": "203.0.113.5"}, host="10.0.0.9")) == "203.0.113.5"
    assert source.source_bucket(req({"x-vercel-forwarded-for": "203.0.113.5", **SPOOFS}, host="10.0.0.9")) == "203.0.113.5"
    same = {source.source_bucket(req({"x-vercel-forwarded-for": "203.0.113.5", "x-forwarded-for": v}, host=h)) for v in ("1.1.1.1", "2.2.2.2") for h in ("10.0.0.1", "10.0.0.2")}
    assert same == {"203.0.113.5"}                                            # spoofed values and the socket peer change nothing
    assert source.source_bucket(req(SPOOFS, host="10.0.0.9")) == source.UNATTRIBUTED   # no platform header: never fall back to spoofable ones


def test_off_vercel_only_the_socket_peer_is_used_and_every_header_is_ignored(monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", False)
    assert source.source_bucket(req({"x-vercel-forwarded-for": "9.9.9.9", **SPOOFS}, host="203.0.113.5")) == "203.0.113.5"
    assert source.source_bucket(req({}, host="testclient")) == source.UNATTRIBUTED


@pytest.mark.parametrize("value", ["", " ", "1.2.3.4, 5.6.7.8", "1.2.3.4:443", "[::1]", "example.com", "1.2.3", "999.1.1.1",
                                   "1.2.3.4 ", "a" * 80, "1.2.3.4;rm", "fe80::1%eth0", None])
def test_invalid_values_go_to_the_shared_unattributed_bucket(monkeypatch, value):
    monkeypatch.setattr(config, "ON_VERCEL", True)
    got = source.source_bucket(req({"x-vercel-forwarded-for": value} if value is not None else {}))
    # a value that is exactly one valid address (after trimming whitespace only) is the single permitted exception
    assert got == source.UNATTRIBUTED or value.strip() == got


def test_ipv4_mapped_ipv6_maps_to_ipv4_and_ipv6_collapses_to_its_slash_64():
    assert source.normalise("::ffff:203.0.113.5") == "203.0.113.5"
    a = source.normalise("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    b = source.normalise("2001:db8:1:2:1111:2222:3333:4444")
    c = source.normalise("2001:db8:1:3::1")
    assert a == b == "2001:db8:1:2::/64" and c != a
    assert source.normalise("203.0.113.5") == "203.0.113.5"


def test_the_environment_not_a_header_decides_vercel_mode(crm, monkeypatch):
    monkeypatch.setattr(config, "ON_VERCEL", False)
    r = req({"x-vercel-forwarded-for": "9.9.9.9", "x-forwarded-for": "9.9.9.9", "vercel": "1", "x-vercel-env": "1"}, host="198.51.100.4")
    assert source.source_bucket(r) == "198.51.100.4"


def test_unattributed_requests_share_one_bucket_with_the_same_limits(vercel, crm):
    s = crm.specialist("rl11@example.test", enrol=False)
    c = TestClient(crm.app)
    for i in range(5):
        c.post("/crm/auth/login", json={"email": s.email, "password": "wrong-password-%d" % i})     # no platform header at all
    r = c.post("/crm/auth/login", json={"email": s.email, "password": PASSWORD})
    assert r.status_code == 429
