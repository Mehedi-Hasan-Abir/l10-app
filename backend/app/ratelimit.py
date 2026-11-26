"""In-process sliding-window rate limiter (used by `POST /api/auth/login`).

A deliberately small building block: one process, one event loop, no external store. It is a *sliding-window
log*: for each key it remembers the timestamps of the hits that were let through during the last
`window_s` seconds. A hit is blocked when `limit` hits are already inside the window, and a blocked hit is
**not** recorded, so a client that keeps hammering is let back in as soon as its oldest accepted hit ages out
instead of being locked out forever.

Limits of the design (acceptable for this app, stated so nobody mistakes it for a distributed limiter):
state lives in one worker process and is lost on restart; with several uvicorn workers or replicas each one
enforces its own `limit`. Moving to Redis (or a Caddy rate-limit module) is the upgrade path.
"""

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """Allow at most `limit` hits per `key` in any `window_s`-second window. `limit == 0` disables it.

    Asyncio safety: `hit()` is plain synchronous code with no `await`, so on a single event loop the
    check-and-record step cannot be interleaved with another coroutine; no lock is needed.
    `clock` must be monotonic (default `time.monotonic`); tests inject a fake one.
    """

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        if limit < 0:
            raise ValueError("limit must be >= 0 (0 disables the limiter)")
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = clock()

    def __len__(self) -> int:
        """Number of keys currently tracked (bounded: expired keys are swept at most once per window)."""
        return len(self._hits)

    def hit(self, key: str) -> float | None:
        """Record an attempt for `key`.

        Returns `None` when the attempt is allowed, or the number of seconds (> 0) until it would be allowed
        when it is blocked.
        """
        if self.limit == 0:
            return None
        now = self._clock()
        if now - self._last_sweep >= self.window_s:
            self._sweep(now)

        bucket = self._hits.get(key)
        if bucket is None:
            bucket = self._hits[key] = deque()
        self._expire(bucket, now)
        if len(bucket) >= self.limit:
            return bucket[0] + self.window_s - now  # > 0: expired entries were just removed
        bucket.append(now)
        return None

    def _expire(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self.window_s
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def _sweep(self, now: float) -> None:
        """Drop every key whose hits have all left the window (keeps memory proportional to active keys)."""
        self._last_sweep = now
        cutoff = now - self.window_s
        for key in [k for k, bucket in self._hits.items() if not bucket or bucket[-1] <= cutoff]:
            del self._hits[key]
