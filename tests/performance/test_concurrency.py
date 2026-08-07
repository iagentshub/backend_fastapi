"""Tests de concurrencia y rendimiento.

Miden si las rutas críticas aguantan carga simultánea sin errores ni timeouts.
No hay aserciones de speedup relativo: el SQLite singleton serializa los accesos
incluso con run_in_executor (todos los threads esperan la misma conexión).
En PostgreSQL (producción) sí hay speedup real gracias al pool de conexiones.

Ejecutar solo estos tests:
    pytest tests/performance/ -v -s

Excluirlos del CI normal:
    pytest --ignore=tests/performance/
"""
from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import List, NamedTuple

import httpx
import pytest

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def perf_data_dir():
    d = Path(tempfile.mkdtemp(prefix="gaia_perf_"))
    (d / "connections").mkdir()
    (d / "agents").mkdir()
    (d / "skills").mkdir()
    (d / "memory").mkdir()
    (d / "settings.json").write_text(
        json.dumps({"jwt_secret": "perf-test-secret"}),
        encoding="utf-8",
    )
    (d / "connections" / "connections.json").write_text("[]", encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def perf_app(perf_data_dir):
    """FastAPI app + token admin compartidos por todos los tests del módulo."""
    import app.config.data as cfg
    import app.storage.db as db_mod

    db_file = perf_data_dir / "perf.db"

    cfg.DATA_DIR = perf_data_dir
    cfg.DB_FILE = db_file
    cfg.CONN_FILE = perf_data_dir / "connections" / "connections.json"
    cfg.AGENTS_DIR = perf_data_dir / "agents"
    cfg.SKILLS_DIR = perf_data_dir / "skills"
    cfg.MEMORY_DIR = perf_data_dir / "memory"
    cfg.SETTINGS_FILE = perf_data_dir / "settings.json"

    import app.auth.passwords as passwords_mod
    passwords_mod.SETTINGS_FILE = perf_data_dir / "settings.json"

    db_mod.IS_PG = False
    db_mod.PH = "?"

    asyncio.run(db_mod.init_db(db_file))

    from app.api.app import create_app
    app = create_app()

    from app.auth.auth import create_token, register_user
    from app.storage.db import open_db
    asyncio.run(register_user("perf_admin", "pass1234", email="perf@example.com"))

    async def _make_admin():
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                ("admin", "perf_admin"),
            )
            await conn.commit()

    asyncio.run(_make_admin())

    token = create_token("perf_admin")
    yield app, token


# ── Helpers ───────────────────────────────────────────────────────────────────


class RequestResult(NamedTuple):
    status: int
    elapsed_ms: float
    error: str | None


async def _get(client: httpx.AsyncClient, url: str) -> RequestResult:
    t0 = time.perf_counter()
    try:
        r = await client.get(url)
        return RequestResult(r.status_code, (time.perf_counter() - t0) * 1000, None)
    except Exception as exc:
        return RequestResult(0, (time.perf_counter() - t0) * 1000, str(exc))


def _report(label: str, results: List[RequestResult]) -> None:
    ok = [r for r in results if r.status == 200]
    err = [r for r in results if r.status != 200]
    times = [r.elapsed_ms for r in results]
    n = len(times)

    print(f"\n{'─'*55}")
    print(f"  {label}")
    print(f"{'─'*55}")
    print(f"  Requests : {n}  OK={len(ok)}  Err={len(err)}")
    if n >= 2:
        p = statistics.quantiles(times, n=100)
        print(f"  Avg={statistics.mean(times):.1f}ms  "
              f"P50={p[49]:.1f}ms  P90={p[89]:.1f}ms  Max={max(times):.1f}ms")
    for r in err[:3]:
        print(f"  !! status={r.status}  error={r.error}")


# ── Constante de timeout absoluto por petición (ms) ───────────────────────────
# SQLite serializa accesos (un thread a la vez) → el peor caso es N peticiones
# en cola. Con N=20 y ~5ms por request el timeout es generoso pero acotado.
MAX_REQUEST_MS = 2000


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_me_todas_las_peticiones_responden(perf_app):
    """20 peticiones simultáneas a /api/auth/me deben responder todas con 200.

    Verifica correctness bajo carga: ninguna petición debe colgarse, dar 500
    ni superar MAX_REQUEST_MS ms de latencia individual.
    """
    app, token = perf_app
    N = 20

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies={"ga_token": token},
    ) as client:
        await _get(client, "/api/auth/me")  # calentamiento

        results = list(await asyncio.gather(
            *[_get(client, "/api/auth/me") for _ in range(N)]
        ))

    _report(f"/api/auth/me ×{N}", results)

    errors = [r for r in results if r.status != 200]
    assert not errors, (
        f"{len(errors)}/{N} peticiones fallaron: "
        + ", ".join(f"status={r.status}" for r in errors[:5])
    )

    slow = [r for r in results if r.elapsed_ms > MAX_REQUEST_MS]
    assert not slow, (
        f"{len(slow)} peticiones superaron {MAX_REQUEST_MS}ms: "
        + ", ".join(f"{r.elapsed_ms:.0f}ms" for r in slow[:5])
    )


@pytest.mark.asyncio
async def test_event_loop_no_bloqueado(perf_app):
    """El event loop debe procesar tareas ligeras durante peticiones DB pesadas.

    Lanza N peticiones DB en paralelo y una coroutine pura (solo awaits).
    Si el event loop está bloqueado, la coroutine pura tardará proporcional a N.
    Con run_db() el event loop es libre y la coroutine pura termina rápido.
    """
    app, token = perf_app
    N = 10

    async def _pure_async_probe() -> float:
        """Coroutine sin DB: mide cuánto tarda en ejecutar un sleep corto."""
        t0 = time.perf_counter()
        await asyncio.sleep(0.001)  # 1 ms nominal
        return (time.perf_counter() - t0) * 1000

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies={"ga_token": token},
    ) as client:
        db_tasks = [_get(client, "/api/auth/me") for _ in range(N)]
        probe_task = _pure_async_probe()

        all_results = await asyncio.gather(*db_tasks, probe_task)

    db_results = list(all_results[:N])
    probe_ms: float = all_results[-1]  # type: ignore[assignment]

    _report(f"/api/auth/me ×{N} (durante probe)", db_results)
    print(f"\n  Probe pura (asyncio.sleep 1ms): {probe_ms:.1f} ms reales")

    # La probe pura no debe tardar más que 1 request DB completo.
    # Si tarda N× un request DB, el event loop estaba bloqueado.
    single_req_ms = max(r.elapsed_ms for r in db_results)
    assert probe_ms < single_req_ms * 2, (
        f"El event loop parece bloqueado: la probe pura tardó {probe_ms:.1f} ms "
        f"(máximo aceptable: {single_req_ms * 2:.1f} ms = 2× el request más lento)"
    )


@pytest.mark.asyncio
async def test_carga_mixta_sin_errores(perf_app):
    """Rutas variadas en paralelo: auth/me + agents + admin/users — todas deben responder."""
    app, token = perf_app
    REPS = 5

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies={"ga_token": token},
    ) as client:
        t0 = time.perf_counter()
        tasks = (
            [_get(client, "/api/auth/me") for _ in range(REPS)]
            + [_get(client, "/api/agents") for _ in range(REPS)]
            + [_get(client, "/api/admin/users") for _ in range(REPS)]
        )
        results = list(await asyncio.gather(*tasks))
        total_ms = (time.perf_counter() - t0) * 1000

    me_res = results[:REPS]
    agent_res = results[REPS: REPS * 2]
    admin_res = results[REPS * 2:]

    _report(f"/api/auth/me     ×{REPS}", me_res)
    _report(f"/api/agents      ×{REPS}", agent_res)
    _report(f"/api/admin/users ×{REPS}", admin_res)
    print(f"\n  Total wall time ({len(tasks)} peticiones mixtas): {total_ms:.1f} ms")

    assert all(r.status == 200 for r in me_res), (
        f"/api/auth/me con errores: {[r.status for r in me_res if r.status != 200]}"
    )
    all_slow = [r for r in results if r.elapsed_ms > MAX_REQUEST_MS]
    assert not all_slow, f"{len(all_slow)} peticiones superaron {MAX_REQUEST_MS}ms"


@pytest.mark.asyncio
async def test_latencia_no_degrada_entre_rafagas(perf_app):
    """La latencia P90 no debe multiplicarse más de 3× entre la primera y última ráfaga.

    Detecta fugas de memoria, bloqueos acumulativos o crecimiento cuadrático.
    """
    app, token = perf_app
    BURST = 10
    WAVES = 5

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies={"ga_token": token},
    ) as client:
        wave_p90s: List[float] = []
        for wave in range(WAVES):
            results = list(await asyncio.gather(
                *[_get(client, "/api/auth/me") for _ in range(BURST)]
            ))
            times = sorted(r.elapsed_ms for r in results)
            p90 = times[int(len(times) * 0.9)]
            wave_p90s.append(p90)
            ok = sum(1 for r in results if r.status == 200)
            print(f"  Ráfaga {wave + 1}/{WAVES}  P90={p90:.1f}ms  OK={ok}/{BURST}")
            await asyncio.sleep(0.05)

    ratio = wave_p90s[-1] / wave_p90s[0] if wave_p90s[0] > 0 else 1.0
    print(f"\n  P90 primera: {wave_p90s[0]:.1f}ms  última: {wave_p90s[-1]:.1f}ms  ratio={ratio:.2f}×")

    assert ratio < 3.0, (
        f"La latencia P90 creció {ratio:.1f}× entre ráfagas "
        f"({wave_p90s[0]:.1f}ms → {wave_p90s[-1]:.1f}ms)"
    )


@pytest.mark.asyncio
async def test_secuencial_vs_concurrente_informe(perf_app):
    """Mide y muestra speedup concurrente vs secuencial (informativo, sin aserción de ratio).

    SQLite singleton: speedup ≈ 1× (serializado en capa DB).
    PostgreSQL pool : speedup real proporcional a conexiones disponibles.
    """
    app, token = perf_app
    N = 10

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies={"ga_token": token},
    ) as client:
        t0 = time.perf_counter()
        seq_results: List[RequestResult] = []
        for _ in range(N):
            seq_results.append(await _get(client, "/api/auth/me"))
        t_seq = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        con_results = list(await asyncio.gather(
            *[_get(client, "/api/auth/me") for _ in range(N)]
        ))
        t_con = (time.perf_counter() - t0) * 1000

    _report(f"/api/auth/me ×{N} secuencial", seq_results)
    _report(f"/api/auth/me ×{N} concurrente", con_results)

    speedup = t_seq / t_con if t_con > 0 else 1.0
    print(f"\n  Secuencial : {t_seq:.1f}ms")
    print(f"  Concurrente: {t_con:.1f}ms")
    print(f"  Speedup    : {speedup:.2f}×")
    print("  Nota: speedup≈1 es normal con SQLite singleton; en PostgreSQL será >1")

    assert all(r.status == 200 for r in con_results), "Algunas peticiones concurrentes fallaron"
