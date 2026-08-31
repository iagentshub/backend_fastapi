"""Test de carga: lanza N usuarios concurrentes contra una ruta."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import statistics
import time
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import require_admin
from app.api.routes.centinel._router import router
from app.api.routes.centinel._shared import _guard, _stress_broadcast_sync, _stress_sse
from app.api.routes.centinel._state import (
    _STRESS_PERSIST_KEYS,
    _heal_if_stale,
    _persist_stress_state,
    _read_centinel_state,
    _stress,
    _stress_subscribers,
    _update_centinel_state,
)
from app.errors import APIError
from app.utils import flog
from app.utils.generators import generate_id


class StressRequest(BaseModel):
    method: str = "GET"  # GET | POST | DELETE | RANDOM
    path: str = "/api/auth/me"  # ruta relativa o "RANDOM"
    body: Optional[str] = None  # JSON body para POST
    users: int = Field(10, ge=1, le=10_000)  # usuarios concurrentes
    duration: int = Field(30, ge=1, le=3_600)  # segundos totales
    ramp_up: int = Field(0, ge=0, le=3_600)  # segundos de rampa inicial
    timeout: float = Field(10.0, gt=0, le=120)  # timeout por petición
    fluctuate_users: bool = False  # variar carga aleatoriamente durante el test
    max_concurrency: int = Field(0, ge=0, le=10_000)  # 0 = sin límite
    token: Optional[str] = None  # cookie ga_token si es necesario


# Endpoints predefinidos para el modo RANDOM
_RANDOM_ENDPOINTS: List[tuple] = [
    ("/api/auth/me", "GET"),
    ("/api/v2/agents", "GET"),
    ("/api/connections", "GET"),
    ("/api/v2/skills?scope=private", "GET"),
    ("/api/knowledge", "GET"),
]

# Stack por hilo para los workers de carga: cada hilo solo hace peticiones
# httpx en un bucle simple, así que 512 KB sobra con margen. El stack por
# defecto del SO (hasta 8 MB en Linux/macOS) multiplicado por cientos de
# hilos simultáneos reserva varios GB de espacio de direcciones sin motivo.
# threading.stack_size() es un ajuste GLOBAL del proceso (afecta a cualquier
# hilo nuevo mientras esté activo) — se restaura al valor previo justo
# después de crear el pool para no afectar a hilos de otras peticiones.
_WORKER_THREAD_STACK_BYTES = 512 * 1024

# Backstop técnico de hilos nativos — NO es una política de producto ni está
# ligado a admin/config: "usuarios concurrentes" y "concurrencia máx = sin
# límite" se respetan tal cual se pidan. Este valor solo evita que un typo
# (un cero de más) tumbe el proceso; por eso se fija muy por encima del máximo
# que la propia UI permite pedir (10 000, ver stress-users-custom en el
# frontend) — en uso normal nunca debería activarse. Configurable por env si
# el servidor tiene menos recursos y hace falta bajarlo.
CENTINEL_THREAD_CEILING: int = int(os.getenv("CENTINEL_MAX_THREADS", "10000"))


def _do_stress_request(
    client: Any, method: str, path: str, body: Optional[str] = None
) -> tuple:
    """Ejecuta una petición HTTP con un reintento ante ConnectError transitorio.

    Compartido entre el worker de /stress/run y el de /stress/probe para no
    duplicar la lógica de reintento y medición de tiempo en dos sitios.
    Devuelve (elapsed_s, status_code, error_detail). error_detail es None si
    la petición fue exitosa (status < 500).
    """
    import httpx

    t0 = time.monotonic()
    try:
        for attempt in range(2):
            try:
                if method == "GET":
                    r = client.get(path)
                elif method == "DELETE":
                    r = client.delete(path)
                else:
                    r = client.request(method, path, content=body or "")
                break
            except httpx.ConnectError:
                if attempt == 0:
                    time.sleep(0.05)
                else:
                    raise
        elapsed_s = time.monotonic() - t0
        error_detail = f"HTTP {r.status_code}" if r.status_code >= 500 else None
        return elapsed_s, r.status_code, error_detail
    except Exception as exc:  # noqa: BLE001
        # Esta función mide una petición: el tipo del fallo ES el resultado
        # que se reporta (error_detail), por eso no se registra aparte.
        elapsed_s = time.monotonic() - t0
        error_detail = type(exc).__name__ + (f": {exc}" if str(exc) else "")
        return elapsed_s, None, error_detail


@router.get("/stress/status")
async def stress_status(_: str = Depends(require_admin)) -> dict:
    _guard()
    data = _heal_if_stale(_read_centinel_state(), "stress")
    persisted = data.get("stress")
    if persisted:
        return persisted
    return {k: _stress.get(k) for k in _STRESS_PERSIST_KEYS}


@router.post("/stress/run")
async def stress_run(
    body: StressRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_admin),
) -> dict:
    _guard()
    persisted = _heal_if_stale(_read_centinel_state(), "stress").get("stress", {})
    if _stress["status"] == "running" or persisted.get("status") == "running":
        raise APIError(
            409,
            "already_exists",
            "Ya hay una prueba de estrés en curso.",
            extra={"resource": "stress_test"},
        )
    run_id = generate_id(32)
    _stress.update(
        {
            "run_id": run_id,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "events": [],
            "result": {},
            "ticks": [],
            "errors": [],
            "requested_users": body.users,
            "effective_users": None,
            "abort_requested": False,
        }
    )
    _persist_stress_state()
    background_tasks.add_task(_execute_stress, run_id, body)
    flog.info(
        f"[centinel-stress] run iniciado id={run_id[:8]} path={body.path} users={body.users} dur={body.duration}s"
    )
    return {"run_id": run_id, "status": "running"}


@router.delete("/stress/run")
async def stress_abort(_: str = Depends(require_admin)) -> dict:
    _guard()
    persisted = _heal_if_stale(_read_centinel_state(), "stress").get("stress", {})
    is_local = _stress["status"] == "running" and _stress["run_id"] == persisted.get(
        "run_id"
    )
    if not is_local and persisted.get("status") != "running":
        raise APIError(
            409, "no_stress_test_in_progress", "No hay prueba de estrés en curso."
        )

    if is_local:
        # Camino rápido: este mismo proceso está ejecutando el test.
        _stress["status"] = "aborted"
        _stress["finished_at"] = time.time()
        stop_ev = _stress.get("_stop_event")
        if stop_ev:
            stop_ev.set()
        _persist_stress_state()
        _stress_broadcast_sync({"type": "stress_abort"})
    else:
        # El test corre en otro worker: señalizar vía archivo compartido.
        # El ticker de ese proceso revisa "abort_requested" en cada tick (~1s)
        # y detiene sus threads al detectarlo.
        persisted["abort_requested"] = True
        _update_centinel_state(lambda data: data.__setitem__("stress", persisted))

    flog.warning("[centinel-stress] prueba abortada")
    return {"ok": True}


@router.get("/stress/stream/{run_id}")
async def stress_stream(
    run_id: str, _: str = Depends(require_admin)
) -> StreamingResponse:
    _guard()
    if _stress["run_id"] != run_id:
        raise APIError(404, "not_found", "Run no encontrado", extra={"resource": "run"})
    return StreamingResponse(
        _stress_sse_generator(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stress_sse_generator(run_id: str):
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    _stress_subscribers.append(q)
    try:
        for event in list(_stress["events"]):
            yield _stress_sse(event)
        if _stress["status"] not in ("running",):
            return
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield _stress_sse(event)
                if event.get("type") in ("stress_done", "stress_abort", "stress_error"):
                    break
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        if q in _stress_subscribers:
            _stress_subscribers.remove(q)


async def _execute_stress(run_id: str, cfg: StressRequest) -> None:
    """Runner principal del stress test. Lanza N workers en threads independientes."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    base_url = f"http://127.0.0.1:{os.getenv('GAIA_PORT', '8765')}"
    method = cfg.method.upper()
    headers = {"Content-Type": "application/json"}
    if cfg.token:
        headers["Cookie"] = f"ga_token={cfg.token}"

    # Acumuladores — threading.Lock porque los workers son threads, no coroutines
    lock = threading.Lock()
    all_samples: List[float] = []
    tick_samples: List[float] = []
    # Muestras por worker: permite calcular la media de "cada usuario" por
    # separado y compararla con la media global — revela si algunos hilos
    # están siendo penalizados más que otros (scheduling injusto entre threads).
    per_worker_samples: Dict[int, List[float]] = {}
    error_count = 0
    request_count = 0
    tick_errors = 0
    last_tick = time.monotonic()
    tick_index = 0
    # Nº de workers que ya pasaron su ramp-up y están pidiendo activamente.
    # Con ramp_up=0 todos arrancan a la vez (línea plana = effective_users).
    active_users = 0

    # Stop event para abort instantáneo y cap de seguridad en threads
    stop_event = threading.Event()
    _stress["_stop_event"] = stop_event
    effective_users = min(cfg.users, CENTINEL_THREAD_CEILING)
    _stress["effective_users"] = effective_users
    _persist_stress_state()

    _stress_broadcast_sync(
        {
            "type": "stress_started",
            "run_id": run_id,
            "config": {
                "method": method,
                "path": cfg.path,
                "users": cfg.users,
                "duration": cfg.duration,
                "ramp_up": cfg.ramp_up,
                "fluctuate_users": cfg.fluctuate_users,
            },
        }
    )

    deadline = time.monotonic() + cfg.duration
    is_random_endpoint = cfg.path == "RANDOM"
    is_random_method = method == "RANDOM"

    _sem_limit = cfg.max_concurrency if cfg.max_concurrency > 0 else effective_users
    sem = threading.Semaphore(min(_sem_limit, effective_users))

    def _worker(worker_id: int) -> None:
        import random as _random

        nonlocal error_count, request_count, tick_errors, active_users

        if cfg.ramp_up > 0:
            delay = (worker_id / max(effective_users, 1)) * cfg.ramp_up
            # sleep en trozos pequeños para detectar abort rápido
            end = time.monotonic() + delay
            while time.monotonic() < end and not stop_event.is_set():
                time.sleep(min(0.1, end - time.monotonic()))

        if stop_event.is_set():
            return
        with lock:
            active_users += 1
            per_worker_samples[worker_id] = []

        with httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=cfg.timeout,
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        ) as client:
            while (
                not stop_event.is_set()
                and time.monotonic() < deadline
                and _stress["status"] == "running"
            ):
                if is_random_endpoint:
                    req_path, req_method = _random.choice(_RANDOM_ENDPOINTS)
                else:
                    req_path = cfg.path
                    req_method = method

                if is_random_method and not is_random_endpoint:
                    req_method = _random.choice(["GET", "POST", "DELETE"])

                with sem:  # timer inicia tras adquirir el semáforo
                    elapsed_s, status_code, error_detail = _do_stress_request(
                        client, req_method, req_path, cfg.body
                    )
                is_error = error_detail is not None

                with lock:
                    all_samples.append(elapsed_s)
                    tick_samples.append(elapsed_s)
                    per_worker_samples[worker_id].append(elapsed_s)
                    request_count += 1
                    if is_error:
                        error_count += 1
                        tick_errors += 1
                        if len(_stress["errors"]) < 200:
                            _stress["errors"].append(
                                {
                                    "t": round(
                                        time.monotonic() - (deadline - cfg.duration), 1
                                    ),
                                    "method": req_method,
                                    "path": req_path,
                                    "status": status_code,
                                    "msg": error_detail or "Error desconocido",
                                    "s": round(elapsed_s, 3),
                                }
                            )

                if cfg.fluctuate_users:
                    time.sleep(_random.uniform(0, 0.2))

    # Ticker: corre en el event loop (asyncio), lee contadores del lock de threads
    async def _ticker() -> None:
        nonlocal tick_samples, tick_errors, tick_index, last_tick

        while _stress["status"] == "running":
            await asyncio.sleep(1.0)
            # Un abort disparado desde OTRO worker (ver /stress/run DELETE)
            # llega aquí vía archivo compartido, no memoria — se revisa cada tick.
            other_worker_state = _read_centinel_state().get("stress", {})
            if other_worker_state.get("run_id") == run_id and other_worker_state.get(
                "abort_requested"
            ):
                _stress["status"] = "aborted"
                _stress["finished_at"] = time.time()
                stop_event.set()
            if _stress["status"] != "running":
                break
            tick_index += 1
            now = time.monotonic()
            elapsed = now - last_tick
            last_tick = now

            with lock:
                samples = tick_samples[:]
                errs = tick_errors
                tick_samples = []
                tick_errors = 0
                active_now = active_users

            if samples:
                avg = statistics.mean(samples)
                p95 = sorted(samples)[int(len(samples) * 0.95)]
                mx = max(samples)
                mn = min(samples)
                rps = len(samples) / max(elapsed, 0.001)
            else:
                avg = p95 = mx = mn = rps = 0

            tick_data = {
                "type": "stress_tick",
                "tick": tick_index,
                "count": len(samples),
                "errors": errs,
                "avg_s": round(avg, 3),
                "p95_s": round(p95, 3),
                "min_s": round(mn, 3),
                "max_s": round(mx, 3),
                "rps": round(rps, 1),
                "active_users": active_now,
            }
            _stress["ticks"].append(tick_data)
            if len(_stress["ticks"]) > 600:
                _stress["ticks"] = _stress["ticks"][-600:]
            _persist_stress_state()
            _stress_broadcast_sync(tick_data)

    try:
        loop = asyncio.get_running_loop()
        ticker_task = asyncio.create_task(_ticker())
        prev_stack_size = threading.stack_size()
        threading.stack_size(_WORKER_THREAD_STACK_BYTES)
        try:
            with ThreadPoolExecutor(
                max_workers=effective_users, thread_name_prefix="stress"
            ) as executor:
                worker_futures = [
                    loop.run_in_executor(executor, _worker, i)
                    for i in range(effective_users)
                ]
                await asyncio.gather(*worker_futures)
        finally:
            threading.stack_size(prev_stack_size)
        ticker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker_task

        if _stress["status"] == "aborted":
            _persist_stress_state()
            return

        actual_duration = time.time() - _stress["started_at"]

        def _pct(samples: List[float], p: float) -> float:
            if not samples:
                return 0.0
            s = sorted(samples)
            idx = max(0, int(math.ceil(len(s) * p / 100)) - 1)
            return round(s[idx], 3)

        # Media de las medias por worker: si difiere mucho de avg_s (la media
        # global de todas las peticiones), algunos usuarios/hilos concretos
        # están recibiendo peor servicio que la media, no solo "hay carga alta".
        per_worker_means = [
            statistics.mean(s) for s in per_worker_samples.values() if s
        ]
        avg_per_user_s = (
            round(statistics.mean(per_worker_means), 3) if per_worker_means else 0.0
        )

        result = {
            "total": request_count,
            "errors": error_count,
            "duration_s": round(actual_duration, 2),
            "rps": round(request_count / max(actual_duration, 0.001), 1),
            "avg_s": round(statistics.mean(all_samples), 3) if all_samples else 0,
            "avg_per_user_s": avg_per_user_s,
            "min_s": round(min(all_samples), 3) if all_samples else 0,
            "max_s": round(max(all_samples), 3) if all_samples else 0,
            "p50_s": _pct(all_samples, 50),
            "p90_s": _pct(all_samples, 90),
            "p95_s": _pct(all_samples, 95),
            "p99_s": _pct(all_samples, 99),
        }
        _stress["result"] = result
        _stress["status"] = "done"
        _stress["finished_at"] = time.time()
        _persist_stress_state()
        _stress_broadcast_sync({"type": "stress_done", **result})
        flog.info(
            f"[centinel-stress] finalizado id={run_id[:8]} total={request_count} err={error_count}"
        )

    except Exception as exc:  # noqa: BLE001
        # Ver probe.py: el fallo se registra y pasa a ser estado del run.
        flog.error(f"[centinel-stress] error id={run_id[:8]}: {exc}")
        _stress["status"] = "error"
        _stress["finished_at"] = time.time()
        _persist_stress_state()
        _stress_broadcast_sync(
            {
                "type": "stress_error",
                "code": "internal_error",
                "message": "Error interno durante la prueba de carga.",
            }
        )
