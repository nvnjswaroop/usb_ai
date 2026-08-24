"""
In-process per-IP rate limiter — stdlib only (no slowapi).
ponytail: in-process limiter, single-instance only — multi-worker uvicorn would
need slowapi or shared state.

Strategy:
- Per-IP deque of recent request timestamps (float seconds).
- check(key) prunes stale entries (>60s), then allows / denies based on len.
- Locked mutation so concurrent requests don't race against the prune.
- Lightweight enough to call at the top of every matching handler.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict


class RateLimiter:
    """Sliding-window per-key request cap. check() returns True if allowed."""

    def __init__(self, max_per_minute: int = 30, window_seconds: int = 60):
        self.max_per_minute = max_per_minute
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        # ponytail: defaultdict gives us a fresh deque on first sight of any IP
        # without an explicit setdefault on the hot path.
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._last_sweep: float = 0.0
        # Idle-bucket eviction interval — see check().
        self.sweep_interval: float = 300.0

    def check(self, key: str) -> bool:
        """Record a request from `key` and return True if allowed, False if rate-limited."""
        now = time.monotonic()
        # ponytail: fold into the bucket key + prune in one locked pass.
        with self._lock:
            cutoff = now - self.window_seconds
            # ponytail: opportunistic sweep every 5 min — idle IPs' deques
            # otherwise accumulate forever (defaultdict entries are never
            # evicted; reset() is test-only). Amortised O(n) over buckets,
            # runs at most once per sweep_interval.
            if now - self._last_sweep > self.sweep_interval:
                stale = [k for k, b in self._buckets.items()
                         if not b or b[-1] < cutoff]
                for k in stale:
                    del self._buckets[k]
                self._last_sweep = now
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_per_minute:
                return False
            bucket.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        """Clear state for one key (or all). Not used in production — exposed for tests."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)
