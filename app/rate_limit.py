"""
Minimal in-memory sliding-window rate limiter for magic-link requests.

Scoped to this one process, which is the right tradeoff for Phase 1 (a
single new service instance, low volume) — the goal here is closing the
"abuse/rate-limit protection" gap the founder explicitly called out, not
building distributed rate-limiting infrastructure a Phase-1-scale service
doesn't need yet. Moving this to a shared store (Redis, or a DB-backed
counter) is a drop-in swap of this one module if/when the service ever
runs as more than one process — nothing else would need to change.
"""

import time
from collections import defaultdict

WINDOW_SECONDS = 15 * 60
MAX_REQUESTS_PER_WINDOW = 5


class RateLimiter:
    def __init__(self, window_seconds: int = WINDOW_SECONDS, max_requests: int = MAX_REQUESTS_PER_WINDOW):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._hits = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        hits = [t for t in self._hits[key] if t > cutoff]
        if len(hits) >= self.max_requests:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True

    def reset(self) -> None:
        """Test-only convenience — never called from application code."""
        self._hits.clear()
