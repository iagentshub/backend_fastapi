"""Sonda: sube la carga por escalones hasta encontrar el punto de quiebre."""

from __future__ import annotations

import asyncio
import contextlib
import os
import statistics
import time
from typing import List, Optional

from fastapi import Depends
from pydantic import BaseModel

from app.api.routes.auth import require_admin
from app.api.routes.centinel._router import router
from app.api.routes.centinel._state import (
    _PROBE_PERSIST_KEYS,
    _heal_if_stale,
    _persist_probe_state,
    _probe,
    _read_centinel_state,
    _write_centinel_state,
)
from app.api.routes.centinel.stress import (
    _WORKER_THREAD_STACK_BYTES,
    CENTINEL_THREAD_CEILING,
    _do_stress_request,
)
from app.errors import APIError
from app.utils import flog


class ProbeRequest(BaseModel):
    path: str = "/api/auth/me"
    start_users: int = 10
    step: int = 50
    duration: int = 30  # segundos por paso

    error_threshold: float = 0.0  # % errores tolerados (0.0 = cero errores)
    max_concurrency: int = 0  # 0 = sin límite
    timeout: float = 10.0
    token: Optional[str] = None


@router.get("/stress/probe")
async def probe_status(_: str = Depends(require_admin)) -> dict:
    data = _heal_if_stale(_read_centinel_state(), "probe")
    persisted = data.get("probe")
    if persisted:
        return persisted
    return {k: _probe.get(k) for k in _PROBE_PERSIST_KEYS}


@router.post("/stress/probe")
async def probe_start(body: ProbeRequest, _: str = Depends(require_admin)) -> dict:
    persisted = _heal_if_stale(_read_centinel_state(), "probe").get("probe", {})
    if _probe["status"] == "running" or persisted.get("status") == "running":
        raise APIError(
            409, "already_exists", "Probe ya en ejecución", extra={"resource": "probe"}
        )
    run_id = str(int(time.time() * 1000))
    _probe.update(
        {
            "status": "running",
            "run_id": run_id,
            "steps": [],
            "ticks": [],
            "current_users": body.start_users,
            "verdict": None,
            "abort_requested": False,
            "error": None,
        }
    )
    _persist_probe_state()
    asyncio.create_task(_execute_probe(body))
    return {"run_id": run_id}


@router.delete("/stress/probe")
async def probe_abort(_: str = Depends(require_admin)) -> dict:
    persisted = _heal_if_stale(_read_centinel_state(), "probe").get("probe", {})
    is_local = _probe["status"] == "running" and _probe["run_id"] == persisted.get(
        "run_id"
    )

    if is_local:
        _probe["status"] = "aborted"
        stop_ev = _probe.get("_stop_event")
        if stop_ev:
            stop_ev.set()
        _persist_probe_state()
        return {"status": _probe["status"]}

    if persisted.get("status") == "running":
        # El probe corre en otro worker: señalizar vía archivo compartido,
        # su ticker de paso lo detecta en el próximo tick (~1s).
        persisted["abort_requested"] = True
        data = _read_centinel_state()
        data["probe"] = persisted
        _write_centinel_state(data)
        return {"status": "running"}

    return {"status": persisted.get("status", "idle")}


async def _run_probe_step(
    users: int,
    cfg: ProbeRequest,
    base_url: str,
    headers: dict,
) -> dict:
    """Ejecuta un paso del probe con workers en threads independientes."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    deadline = time.monotonic() + cfg.duration
    lock = threading.Lock()
    sem_limit = cfg.max_concurrency if cfg.max_concurrency > 0 else users
    effective_users = min(users, CENTINEL_THREAD_CEILING)
    sem = threading.Semaphore(min(sem_limit, effective_users))
    stop_event = threading.Event()
    _probe["_stop_event"] = stop_event
    all_samples: List[float] = []
    tick_samples: List[float] = []
    error_count = 0
    request_count = 0
    tick_errors = 0
    t_start = time.monotonic()
    run_id = _probe["run_id"]

    def _w() -> None:
        nonlocal error_count, request_count, tick_errors
        with httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=cfg.timeout,
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        ) as client:
            while (
                not stop_event.is_set()
                and time.monotonic() < deadline
                and _probe["status"] == "running"
            ):
                with sem:  # timer inicia tras adquirir el semáforo
                    elapsed, _status_code, error_detail = _do_stress_request(
                        client, "GET", cfg.path
                    )
                is_err = error_detail is not None
                with lock:
                    all_samples.append(elapsed)
                    tick_samples.append(elapsed)
                    request_count += 1
                    if is_err:
                        error_count += 1
                        tick_errors += 1

    async def _probe_ticker() -> None:
        nonlocal tick_samples, tick_errors
        last = time.monotonic()
        while time.monotonic() < deadline and _probe["status"] == "running":
            await asyncio.sleep(1.0)
            # Abort disparado desde OTRO worker: se revisa vía archivo
            # compartido en cada tick (igual que en el stress test normal).
            other_worker_state = _read_centinel_state().get("probe", {})
            if (
                other_worker_state.get("run_id") == run_id
                and other_worker_state.get("abort_requested")
            ):
                _probe["status"] = "aborted"
                stop_event.set()
            if _probe["status"] != "running":
                break
            now = time.monotonic()
            elapsed_tick = now - last
            last = now
            with lock:
                s = tick_samples[:]
                errs = tick_errors
                tick_samples = []
                tick_errors = 0
            rps = len(s) / max(elapsed_tick, 0.001)
            avg = statistics.mean(s) if s else 0.0
            _probe["ticks"].append(
                {
                    "users": users,
                    "rps": round(rps, 1),
                    "errors": errs,
                    "avg_s": round(avg, 3),
                }
            )
            if len(_probe["ticks"]) > 600:
                _probe["ticks"] = _probe["ticks"][-600:]
            _persist_probe_state()

    loop = asyncio.get_running_loop()
    ticker_task = asyncio.create_task(_probe_ticker())
    prev_stack_size = threading.stack_size()
    threading.stack_size(_WORKER_THREAD_STACK_BYTES)
    try:
        with ThreadPoolExecutor(
            max_workers=effective_users, thread_name_prefix="probe"
        ) as executor:
            worker_futures = [
                loop.run_in_executor(executor, _w) for _ in range(effective_users)
            ]
            await asyncio.gather(*worker_futures)
    finally:
        threading.stack_size(prev_stack_size)
    ticker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ticker_task

    actual = time.monotonic() - t_start
    error_rate = error_count / max(request_count, 1)
    return {
        "users": users,
        "effective_users": effective_users,  # ver _MAX_THREADS: puede ser < users
        "total": request_count,
        "errors": error_count,
        "error_rate": round(error_rate, 4),
        "rps": round(request_count / max(actual, 0.001), 1),
        "avg_s": round(statistics.mean(all_samples), 3) if all_samples else 0.0,
        "elapsed_s": round(actual, 1),
        "status": "ok" if error_rate <= cfg.error_threshold else "fail",
    }


async def _execute_probe(cfg: ProbeRequest) -> None:
    base_url = f"http://127.0.0.1:{os.getenv('GAIA_PORT', '8765')}"
    headers = {"Content-Type": "application/json"}
    if cfg.token:
        headers["Cookie"] = f"ga_token={cfg.token}"

    try:
        users = cfg.start_users
        last_ok: Optional[int] = None
        _SAFETY_CAP = 50_000  # evitar bucle infinito accidental

        while users <= _SAFETY_CAP and _probe["status"] == "running":
            _probe["current_users"] = users
            step_idx = len(_probe["steps"])
            _probe["steps"].append({"users": users, "status": "running"})
            _persist_probe_state()

            result = await _run_probe_step(users, cfg, base_url, headers)
            _probe["steps"][step_idx] = result
            _persist_probe_state()

            if result["status"] == "ok":
                last_ok = users
                users += cfg.step
            else:
                _probe["verdict"] = {
                    "stable_users": last_ok,
                    "break_users": users,
                    "error_rate": result["error_rate"],
                    "break_total": result["total"],
                    "break_rps": result["rps"],
                }
                break

        if _probe["status"] == "running":
            # Solo llega aquí si se abortó o llegó al cap de seguridad sin errores
            if not _probe["verdict"]:
                _probe["verdict"] = {
                    "stable_users": last_ok,
                    "break_users": None,
                    "note": "No se encontraron errores",
                }
            _probe["status"] = "done"
            _probe["current_users"] = None

        _persist_probe_state()

    except Exception as exc:
        _probe["status"] = "error"
        _probe["error"] = str(exc)
        _persist_probe_state()
        flog.error(f"[centinel-probe] error: {exc}")
