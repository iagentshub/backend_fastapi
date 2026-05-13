"""Tests del RateLimiter."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.ratelimit import RateLimiter


def _make_app(calls: int, window: int) -> FastAPI:
    app = FastAPI()
    limiter = RateLimiter(calls=calls, window=window)

    @app.get("/test")
    async def endpoint(request: Request, _: None = None):
        await limiter(request)
        return {"ok": True}

    return app


def test_allows_under_limit():
    client = TestClient(_make_app(calls=3, window=60))
    for _ in range(3):
        r = client.get("/test")
        assert r.status_code == 200


def test_blocks_over_limit():
    client = TestClient(_make_app(calls=2, window=60))
    client.get("/test")
    client.get("/test")
    r = client.get("/test")
    assert r.status_code == 429


def test_different_ips_independent(monkeypatch):
    app = FastAPI()
    limiter = RateLimiter(calls=1, window=60)
    call_count = {"n": 0}

    @app.get("/test")
    async def endpoint(request: Request):
        await limiter(request)
        call_count["n"] += 1
        return {"ok": True}

    client = TestClient(app)
    # Primera IP
    r1 = client.get("/test", headers={"x-forwarded-for": "1.2.3.4"})
    assert r1.status_code == 200
    # Segunda IP diferente — no debe estar limitada
    r2 = client.get("/test", headers={"x-forwarded-for": "5.6.7.8"})
    assert r2.status_code == 200
    assert call_count["n"] == 2
