"""Tests del RateLimiter."""

from __future__ import annotations

import importlib
import pkgutil

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
    limiter = rl.RateLimiter(calls=5, window=60, shared=True, name="test-auth-global")
    assert limiter._calls == 5
    assert limiter._shared is True


# ── Clave por principal ───────────────────────────────────────────────────────
# La IP falla en las dos direcciones para un endpoint autenticado: tras un NAT
# toda la oficina comparte cupo, y quien rota IPs no encuentra techo.


def _app_con_limiter(limiter):
    app = FastAPI()

    @app.get("/test")
    async def endpoint(request: Request):
        await limiter(request)
        return {"ok": True}

    return app


def test_la_clave_sale_del_usuario_del_jwt():
    from app.auth.auth import create_token
    from app.middleware.ratelimit import principal_key

    app = FastAPI()
    visto: list[str] = []

    @app.get("/test")
    async def endpoint(request: Request):
        visto.append(principal_key(request))
        return {"ok": True}

    client = TestClient(app)
    client.cookies.set("ga_token", create_token("ana"))
    client.get("/test")
    assert visto == ["user:ana"]


def test_dos_usuarios_desde_la_misma_ip_no_comparten_cuota(tmp_path):
    """El caso del NAT: TestClient siempre sale con la misma IP."""
    from app.auth.auth import create_token
    from app.middleware.ratelimit import RateLimiter, principal_key

    limiter = RateLimiter(
        calls=1, window=60, key_func=principal_key, shared=True, name="test-nat"
    )
    client = TestClient(_app_con_limiter(limiter))

    client.cookies.set("ga_token", create_token("ana"))
    assert client.get("/test").status_code == 200
    assert client.get("/test").status_code == 429

    client.cookies.set("ga_token", create_token("luis"))
    assert client.get("/test").status_code == 200


def test_un_token_ilegible_cae_a_la_ip():
    """principal_key no autoriza: un token falso no es una identidad nueva."""
    from app.middleware.ratelimit import principal_key

    app = FastAPI()
    visto: list[str] = []

    @app.get("/test")
    async def endpoint(request: Request):
        visto.append(principal_key(request))
        return {"ok": True}

    client = TestClient(app)
    client.cookies.set("ga_token", "no-es-un-jwt")
    client.get("/test")
    assert visto == ["ip:testclient"]


def test_el_pat_se_identifica_por_su_hash():
    """Resolver el PAT a usuario costaría una consulta en la ruta caliente."""
    from app.middleware.ratelimit import principal_key

    app = FastAPI()
    visto: list[str] = []

    @app.get("/test")
    async def endpoint(request: Request):
        visto.append(principal_key(request))
        return {"ok": True}

    client = TestClient(app)
    client.get("/test", headers={"authorization": "Bearer iah_uno"})
    client.get("/test", headers={"authorization": "Bearer iah_uno"})
    client.get("/test", headers={"authorization": "Bearer iah_dos"})
    assert visto[0].startswith("pat:")
    assert visto[0] == visto[1] != visto[2]
    assert "iah_uno" not in visto[0]  # la clave no lleva el secreto dentro


# ── Ventana secundaria por IP ─────────────────────────────────────────────────


def test_la_ventana_por_ip_corta_las_cuentas_desechables():
    from app.auth.auth import create_token
    from app.middleware.ratelimit import RateLimiter, principal_key

    limiter = RateLimiter(
        calls=1,
        window=60,
        key_func=principal_key,
        shared=True,
        name="test-ipwide",
        ip_calls=2,
    )
    client = TestClient(_app_con_limiter(limiter))

    # Tres cuentas distintas: cada una tiene su cupo de 1, pero la IP tiene 2.
    for user in ("ana", "luis"):
        client.cookies.set("ga_token", create_token(user))
        assert client.get("/test").status_code == 200

    client.cookies.set("ga_token", create_token("marta"))
    assert client.get("/test").status_code == 429


def test_sin_credencial_una_peticion_gasta_una_sola_vez():
    """La clave primaria cae a la IP; la ventana secundaria no puede duplicar."""
    from app.middleware.ratelimit import RateLimiter, principal_key

    limiter = RateLimiter(
        calls=3,
        window=60,
        key_func=principal_key,
        shared=True,
        name="test-sin-credencial",
        ip_calls=3,
    )
    client = TestClient(_app_con_limiter(limiter))
    for _ in range(3):
        assert client.get("/test").status_code == 200
    assert client.get("/test").status_code == 429


def test_ip_calls_invalido_se_rechaza():
    from app.middleware.ratelimit import RateLimiter

    try:
        RateLimiter(calls=5, window=60, shared=True, name="test-mal", ip_calls=0)
    except ValueError:
        return
    raise AssertionError("ip_calls=0 debía rechazarse")


# ── Los limiters de las rutas no cuentan en memoria ───────────────────────────


def _limiters_de_rutas():
    """Los RateLimiter declarados en app/api/routes, con su módulo."""
    import app.api.routes as routes_pkg
    from app.middleware.ratelimit import RateLimiter

    encontrados = []
    for mod_info in pkgutil.walk_packages(
        routes_pkg.__path__, prefix="app.api.routes."
    ):
        mod = importlib.import_module(mod_info.name)
        for attr, obj in vars(mod).items():
            if isinstance(obj, RateLimiter):
                encontrados.append((mod_info.name, attr, obj))
    return encontrados


def test_todo_limiter_de_ruta_comparte_su_cuota():
    """El contador en memoria se divide entre workers y se pierde al reiniciar.

    Doce de los diecinueve limiters seguían así mucho después de que existiera
    `shared=True`, porque nada lo comprobaba: el que se olvida no falla, solo
    limita menos de lo que dice su código.
    """
    encontrados = _limiters_de_rutas()
    assert encontrados, "El recorrido no encontró ningún limiter: revisa el test"
    en_memoria = [f"{mod}.{attr}" for mod, attr, lim in encontrados if not lim._shared]
    assert not en_memoria, f"Limiters con contador de proceso: {en_memoria}"


def test_ningun_limiter_de_ruta_esta_sin_usar():
    """Un limiter declarado y nunca puesto en un Depends no limita nada.

    Cuatro se quedaron así al extraer agent_chat.py y connection_*.py de sus
    módulos originales: el nombre seguía ahí y el endpoint ya no lo pedía.

    No basta con buscar `Depends(...)`: unos endpoints llaman al limiter a mano
    después de validar el cuerpo, y `_login_limiter` se declara en
    dependencies.py y se aplica desde otros tres módulos a propósito. La señal
    es que alguien lo *aplique* en algún sitio del paquete.
    """
    import re
    from pathlib import Path

    import app.api.routes as routes_pkg

    raiz = Path(routes_pkg.__path__[0])
    codigo = "\n".join(
        f.read_text(encoding="utf-8") for f in sorted(raiz.rglob("*.py"))
    )
    huerfanos = [
        f"{mod}.{attr}"
        for mod, attr, _ in _limiters_de_rutas()
        if not re.search(rf"(Depends\(|await )\s*{re.escape(attr)}\b", codigo)
    ]
    assert not huerfanos, f"Limiters declarados que nadie aplica: {huerfanos}"


def test_los_nombres_de_limiter_no_colisionan():
    """Dos limiters distintos con el mismo nombre comparten fila en la BD."""
    por_nombre: dict[str, set[int]] = {}
    for _mod, _attr, lim in _limiters_de_rutas():
        por_nombre.setdefault(lim._name, set()).add(id(lim))
    repetidos = [n for n, ids in por_nombre.items() if len(ids) > 1]
    assert not repetidos, f"Nombre compartido por limiters distintos: {repetidos}"


@pytest.mark.asyncio
async def test_cuota_ponderada_cobra_el_coste_real():
    limiter = RateLimiter(calls=5, window=60)
    await limiter.consume_key("user:ana", cost=4)
    with pytest.raises(Exception) as exc_info:
        await limiter.consume_key("user:ana", cost=2)
    assert getattr(exc_info.value, "status_code", None) == 429


def test_todas_las_rutas_llm_declaran_cuota():
    """Inventario de puertas activas al LLM, incluidas las indirectas de admin."""
    from pathlib import Path

    from app.api.app import create_app
    from app.api.routes.llm_limits import (
        interactive_llm_limiter,
        workflow_start_limiter,
    )

    expected = {
        "/api/agents/{agent_id}/chat": interactive_llm_limiter,
        "/api/agent-builder/chat": interactive_llm_limiter,
        "/api/skill-builder/chat": interactive_llm_limiter,
        "/api/agents/{scope}/{agent_id}/try": interactive_llm_limiter,
        "/api/workflows/{workflow_id}/run": workflow_start_limiter,
        "/api/workflows/{workflow_id}/runs": workflow_start_limiter,
    }
    app = create_app()

    def effective(routes):
        for route in routes:
            if hasattr(route, "original_router"):
                yield from effective(route.original_router.routes)
            else:
                yield route

    by_path = {getattr(route, "path", ""): route for route in effective(app.routes)}
    missing = [
        path
        for path, limiter in expected.items()
        if path not in by_path
        or limiter
        not in {dependency.call for dependency in by_path[path].dependant.dependencies}
    ]
    assert not missing, f"Rutas LLM sin cuota: {missing}"

    official = (
        Path(__file__).parents[1]
        / "app"
        / "api"
        / "routes"
        / "admin"
        / "official_sources.py"
    ).read_text(encoding="utf-8")
    assert official.count("await official_llm_limiter(request)") == 3


# ── Purga de ventanas vencidas ────────────────────────────────────────────────


def test_la_purga_borra_lo_vencido_y_respeta_lo_vivo():
    import asyncio
    import time

    from app.middleware.ratelimit import purge_expired_windows
    from app.storage.db import open_db

    async def _run():
        async with open_db() as conn:
            for key, edad in (("viejo", 10_000), ("reciente", 5)):
                await conn.execute(
                    "INSERT INTO rate_limit_windows"
                    "(limiter_key, window_start, request_count) VALUES (?, ?, ?)",
                    (key, time.time() - edad, 1),
                )
            await conn.commit()
        borradas = await purge_expired_windows()
        async with open_db() as conn:
            filas = await conn.fetchall("SELECT limiter_key FROM rate_limit_windows")
        return borradas, {f[0] for f in filas}

    borradas, quedan = asyncio.run(_run())
    assert borradas == 1
    assert quedan == {"reciente"}


def test_el_horizonte_de_purga_es_la_ventana_mas_larga(monkeypatch):
    """Purgar con el corte de 60 s borraría la cuota de auth-forgot (1 h)."""
    import app.middleware.ratelimit as rl

    largo = rl.RateLimiter(calls=5, window=3600, shared=True, name="test-largo")
    corto = rl.RateLimiter(calls=5, window=60, shared=True, name="test-corto")
    monkeypatch.setattr(rl, "INSTANCES", [corto, largo])
    horizonte = max(li._window for li in rl.INSTANCES if li._shared)
    assert horizonte == 3600
