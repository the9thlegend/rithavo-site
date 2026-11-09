"""Prototype DB-backed limiter: counters are shared across instances. Runs on SQLite, and on PostgreSQL when the
suite's opt-in DSN is set (conftest's `db` fixture)."""

import threading

from app.db import Database
from app.rate_limit import RateLimiter
from app.rate_limit_db import DbRateLimiter


def test_in_memory_limiter_is_per_instance_the_existing_weakness():
    a, b = RateLimiter(), RateLimiter()  # two serverless instances
    assert [a.allow("x@example.test") for _ in range(5)] == [True] * 5 and not a.allow("x@example.test")
    assert b.allow("x@example.test")  # the other instance knows nothing: the attempt budget doubles per instance


def test_db_limiter_shares_the_budget_across_instances(db):
    other = Database(db.db_path)  # a second instance on the same database
    t = [1_700_000_000]
    one = DbRateLimiter(db, "login", clock=lambda: t[0]); one.ensure_table()
    two = DbRateLimiter(other, "login", clock=lambda: t[0])
    results = [one.allow("v@example.test"), two.allow("v@example.test"), one.allow("v@example.test"),
               two.allow("v@example.test"), one.allow("v@example.test")]
    assert results == [True] * 5
    assert one.allow("v@example.test") is False and two.allow("v@example.test") is False
    assert one.allow("someone.else@example.test") is True       # other keys unaffected
    t[0] += 15 * 60                                              # the window rolls
    assert two.allow("v@example.test") is True
    one.reset(); assert two.allow("v@example.test") is True


def test_db_limiter_is_atomic_under_concurrency_and_stores_no_email(db):
    lim = DbRateLimiter(db, "login"); lim.ensure_table()
    instances = [DbRateLimiter(Database(db.db_path), "login") for _ in range(4)]
    out = []

    def hammer(l):
        for _ in range(5):
            out.append(l.allow("race@example.test"))
    threads = [threading.Thread(target=hammer, args=(l,)) for l in instances]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sum(out) == 5 and len(out) == 20   # exactly the budget passes, however the instances interleave
    with db.connect() as conn:
        keys = " ".join(r["rl_key"] for r in conn.execute("SELECT rl_key FROM web_rate_limit_buckets").fetchall())
    assert "race" not in keys and "@" not in keys
