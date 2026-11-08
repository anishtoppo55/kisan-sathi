"""Tiny in-memory per-IP rate limiter for /api/* (protects Gnani credits and Gemini quota).

Sliding window: at most `limit` requests per `window` seconds per client IP.
Single-process only, which matches the one-uvicorn-worker Lightsail setup.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimiter:
    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > 10_000:  # drop idle keys
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: RateLimiter, prefix: str = "/api/"):
        super().__init__(app)
        self.limiter, self.prefix = limiter, prefix

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(self.prefix):
            ip = request.client.host if request.client else "unknown"
            if not self.limiter.allow(ip):
                return JSONResponse({"detail": "Bahut zyada sawal ek saath. Kripya ek minute baad koshish karein."}, status_code=429)
        return await call_next(request)
