"""Tests del RateLimiter."""
from __future__ import annotations

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


def test_allogroup_under_limit():
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
    assert r.json()["detail"]["code"] == "rate_limit_exceeded"
    assert r.json()["detail"]["retry_after"] == 60
    assert r.headers["retry-after"] == "60"


def test_different_ips_independent(monkeypatch):
    # TestClient envía peticiones con peer="testclient". client_ip() solo lee
    # X-Forwarded-For si el peer pertenece a TRUSTED_PROXIES. Parcheamos el
    # atributo local de net.py para que "testclient" sea considerado proxy
    # confiable y así poder distinguir las dos IPs del test.
    import app.utils.net as net_mod
    monkeypatch.setattr(net_mod, "TRUSTED_PROXIES", frozenset({"testclient"}))

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


def test_rejects_invalid_configuration():
    for calls, window in ((0, 60), (1, 0), (-1, 60)):
        try:
            RateLimiter(calls=calls, window=window)
        except ValueError:
            continue
        raise AssertionError("La configuración inválida debía rechazarse")


# ── Reparto entre workers (BE-06) ─────────────────────────────────────────────
# uvicorn arranca GAIA_WORKERS procesos y cada uno lleva su propio contador en
# memoria, así que el límite real era el declarado × workers. Se reparte.


def test_la_cuota_se_reparte_entre_los_workers(monkeypatch):
    import app.middleware.ratelimit as rl

    monkeypatch.setattr(rl, "_WORKERS", 4)
    assert rl.RateLimiter(calls=20, window=60)._calls == 5


def test_con_un_worker_el_limite_no_cambia(monkeypatch):
    import app.middleware.ratelimit as rl

    monkeypatch.setattr(rl, "_WORKERS", 1)
    assert rl.RateLimiter(calls=5, window=60)._calls == 5


def test_nunca_baja_de_dos_llamadas(monkeypatch):
    """Con un solo intento por worker, equivocarse una vez ya devuelve 429."""
    import app.middleware.ratelimit as rl

    monkeypatch.setattr(rl, "_WORKERS", 8)
    assert rl.RateLimiter(calls=5, window=60)._calls == rl._MIN_CALLS == 2


def test_el_reparto_redondea_hacia_arriba(monkeypatch):
    """5 // 4 = 1 dejaba un único intento de login por proceso: ceil deja 2.

    Pasarse del límite declarado (8 en el cluster en vez de 5) es preferible a
    bloquear a quien solo se equivocó una vez de contraseña.
    """
    import app.middleware.ratelimit as rl

    monkeypatch.setattr(rl, "_WORKERS", 4)
    assert rl.RateLimiter(calls=5, window=60)._calls == 2


def test_los_tests_corren_con_un_solo_worker():
    """Guardia: si esto falla, los límites de toda la suite cambian bajo los pies."""
    from app.config.server import WORKERS

    assert WORKERS == 1


def test_limiter_compartido_conserva_la_cuota_global(monkeypatch):
    import app.middleware.ratelimit as rl

    monkeypatch.setattr(rl, "_WORKERS", 8)
    limiter = rl.RateLimiter(
        calls=5, window=60, shared=True, name="test-auth-global"
    )
    assert limiter._calls == 5
    assert limiter._shared is True
