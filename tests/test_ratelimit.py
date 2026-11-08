from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ratelimit import RateLimiter, RateLimitMiddleware


def test_limiter_window():
    now = [0.0]
    rl = RateLimiter(limit=2, window=60, clock=lambda: now[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a")
    assert rl.allow("b")  # per key
    now[0] = 61
    assert rl.allow("a")


def test_middleware_only_limits_api():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(1, 60))

    @app.get("/api/x")
    def x():
        return {}

    @app.get("/health")
    def h():
        return {}

    c = TestClient(app)
    assert c.get("/api/x").status_code == 200
    assert c.get("/api/x").status_code == 429
    assert c.get("/health").status_code == 200 and c.get("/health").status_code == 200
