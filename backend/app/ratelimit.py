"""Shared in-process HTTP rate limiting (launch L1; build plan: every new
endpoint ships rate-limited).

The backend is one serialized uvicorn process (engine concurrency doc), so
an in-memory sliding window is exact, not a distributed approximation.
Bounded per the OOM guard: the key map is LRU-evicted past ``max_keys`` so
an address-rotating client can't grow it without limit.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    """At most ``limit`` hits per ``window_s`` seconds per key."""

    def __init__(self, limit: int, window_s: float, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """Record an attempt. Returns (allowed, retry_after_seconds)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            while hits and t - hits[0] >= self.window_s:
                hits.popleft()
            self._hits.move_to_end(key)
            if len(hits) >= self.limit:
                return False, self.window_s - (t - hits[0])
            hits.append(t)
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
            return True, 0.0


def client_ip(request: Request) -> str:
    """First hop of x-forwarded-for, else the socket peer. TRUST BOUNDARY:
    the value is only as honest as whoever set it — through the Next proxy
    it is Vercel's spoof-resistant client IP, but a direct-to-backend
    caller controls it freely. Fine while every limited surface is keyed
    per-account; L4's anonymous armor must NOT rely on this alone (it
    pairs the IP window with the signed anon token + Turnstile)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_key(request: Request) -> str:
    """Per-account when the request carries a resolved identity, per-IP
    otherwise — a signed-in user behind a shared NAT must not exhaust
    strangers' budget, and vice versa."""
    user = getattr(request.state, "auth_user", None)
    if user is not None:
        return f"user:{user.id}"
    return f"ip:{client_ip(request)}"


def rate_limited(scope: str, limit: int, window_s: float) -> Callable[[Request], None]:
    """Dependency factory: 429 + Retry-After past the window budget."""
    limiter = SlidingWindowLimiter(limit, window_s)

    def dependency(request: Request) -> None:
        allowed, retry_after = limiter.check(f"{scope}:{_rate_key(request)}")
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded — try again in {math.ceil(retry_after)}s",
                headers={"Retry-After": str(math.ceil(retry_after))},
            )

    return dependency
