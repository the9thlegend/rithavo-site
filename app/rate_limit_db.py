"""
Database-backed rate limiter: a drop-in alternative for app/rate_limit.RateLimiter (same `allow` / `reset`
interface) whose counters are shared by every serverless instance. PROTOTYPE: not wired into the existing
sign-in routes (that is a change to existing authentication and needs separate approval).

Fixed window, one atomic upsert per attempt, so concurrent instances cannot both pass on the same slot. Keys are
hashed, so no email address is stored. The caller supplies `window_seconds` and `max_requests` exactly like the
in-memory class.
"""

import hashlib
import time

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS web_rate_limit_buckets (
    rl_key TEXT NOT NULL,
    bucket BIGINT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (rl_key, bucket)
)
"""


class DbRateLimiter:
    def __init__(self, db, scope: str, window_seconds: int = 15 * 60, max_requests: int = 5, clock=time.time):
        self.db, self.scope = db, scope
        self.window_seconds, self.max_requests, self.clock = window_seconds, max_requests, clock

    def ensure_table(self):
        with self.db.connect() as conn:
            conn.execute(TABLE_SQL)

    def _key(self, key: str) -> str:
        return hashlib.sha256(f"{self.scope}:{key}".encode()).hexdigest()

    def allow(self, key: str) -> bool:
        now = int(self.clock())
        bucket = now // self.window_seconds
        k = self._key(key)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO web_rate_limit_buckets (rl_key, bucket, n) VALUES (?, ?, 1) "
                "ON CONFLICT (rl_key, bucket) DO UPDATE SET n = web_rate_limit_buckets.n + 1", (k, bucket))
            n = conn.execute("SELECT n FROM web_rate_limit_buckets WHERE rl_key = ? AND bucket = ?",
                             (k, bucket)).fetchone()["n"]
            if now % 97 == 0:  # opportunistic cleanup of expired windows
                conn.execute("DELETE FROM web_rate_limit_buckets WHERE bucket < ?", (bucket - 1,))
        return n <= self.max_requests

    def reset(self) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM web_rate_limit_buckets")
