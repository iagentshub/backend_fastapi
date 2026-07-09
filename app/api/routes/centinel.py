"""Centinel — Test Runner para administradores.

Permite lanzar la suite de pytest desde el panel de administración y
visualizar los resultados en tiempo real vía Server-Sent Events (SSE).

Endpoints:
  GET  /api/admin/centinel/status          Estado actual del runner
  GET  /api/admin/centinel/tree            Árbol de tests descubiertos
  POST /api/admin/centinel/run             Lanza un run (background task)
  DEL  /api/admin/centinel/run             Aborta el run en curso
  GET  /api/admin/centinel/history         Últimas 5 ejecuciones
  GET  /api/admin/centinel/stream/{run_id} SSE stream de un run
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.routes.auth import require_admin
from app.utils import flog

router = APIRouter(prefix="/api/admin/centinel", tags=["centinel"])

# ── Configuración ────────────────────────────────────────────────────────────
CENTINEL_ENABLED: bool = os.getenv("CENTINEL_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
_BACKEND_DIR: str = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

# ── Estado global ────────────────────────────────────────────────────────────
_run: Dict[str, Any] = {
    "run_id": None,
    "proc": None,
    "status": "idle",  # idle | running | done | aborted | error
    "target": None,
    "started_at": None,
    "finished_at": None,
    "events": [],  # lista de todos los eventos emitidos (para replay)
    "summary": {},
    "failed_ids": [],
}
_subscribers: List[asyncio.Queue] = []
_history: List[Dict[str, Any]] = []


# ── Helpers ──────────────────────────────────────────────────────────────────
def _guard() -> None:
    if not CENTINEL_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Centinel no está habilitado (CENTINEL_ENABLED=false)",
        )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _broadcast_sync(event: dict) -> None:
    _run["events"].append(event)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def _broadcast(event: dict) -> None:
    _broadcast_sync(event)


def _push_history() -> None:
    entry = {
        "run_id": _run["run_id"],
        "target": _run["target"],
        "started_at": _run["started_at"],
        "finished_at": _run["finished_at"],
        "status": _run["status"],
        "summary": dict(_run["summary"]),
    }
    _history.insert(0, entry)
    del _history[5:]


def _build_tree(lines: List[str]) -> dict:
    """Convierte la salida de pytest --collect-only -q en un árbol jerárquico."""
    tree: Dict[str, Dict[str, List[str]]] = {}
    for line in lines:
        line = line.strip()
        if "::" not in line or not line.startswith("tests/"):
            continue
        parts = line.split("::")
        filepath = parts[0]
        test_name = parts[1] if len(parts) > 1 else ""
        path_parts = filepath.split("/")
        dir_key = "/".join(path_parts[:-1]) + "/" if len(path_parts) > 2 else "tests/"
        tree.setdefault(dir_key, {}).setdefault(filepath, [])
        if test_name:
            tree[dir_key][filepath].append(test_name)
    return {
        "dirs": [
            {
                "dir": d,
                "files": [
                    {"file": f, "tests": ts, "count": len(ts)}
                    for f, ts in sorted(files.items())
                ],
                "count": sum(len(ts) for ts in files.values()),
            }
            for d, files in sorted(tree.items())
        ]
    }


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status(_: str = Depends(require_admin)) -> dict:
    _guard()
    return {
        "status": _run["status"],
        "run_id": _run["run_id"],
        "target": _run["target"],
        "started_at": _run["started_at"],
        "finished_at": _run["finished_at"],
        "summary": _run["summary"],
        "failed_ids": _run["failed_ids"],
    }


@router.get("/tree")
async def get_tree(_: str = Depends(require_admin)) -> dict:
    _guard()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "--collect-only",
            "-q",
            "--no-header",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=_BACKEND_DIR,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return _build_tree(stdout.decode().splitlines())
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout descubriendo tests")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class RunRequest(BaseModel):
    target: str = "tests/"
    rerun_failed: bool = False


@router.post("/run")
async def start_run(
    body: RunRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_admin),
) -> dict:
    _guard()
    if _run["status"] == "running":
        raise HTTPException(
            status_code=409, detail="Ya hay un run en curso. Espera o abórtalo."
        )

    target = body.target.strip()
    # Seguridad: target solo dentro de tests/
    if not target.startswith("tests") or ".." in target or target.startswith("/"):
        raise HTTPException(status_code=422, detail="Target no válido")

    # Re-run solo los fallidos
    if body.rerun_failed and _run["failed_ids"]:
        target = " ".join(_run["failed_ids"])

    run_id = str(uuid.uuid4())
    _run.update(
        {
            "run_id": run_id,
            "proc": None,
            "status": "running",
            "target": target,
            "started_at": time.time(),
            "finished_at": None,
            "events": [],
            "summary": {},
            "failed_ids": [],
        }
    )
    background_tasks.add_task(_execute_run, run_id, target)
    flog.info(f"[centinel] run iniciado id={run_id[:8]} target={target!r}")
    return {"run_id": run_id, "status": "running"}


@router.delete("/run")
async def abort_run(_: str = Depends(require_admin)) -> dict:
    _guard()
    if _run["status"] != "running":
        raise HTTPException(status_code=409, detail="No hay run en curso")
    proc = _run.get("proc")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass
    _run["status"] = "aborted"
    _run["finished_at"] = time.time()
    _broadcast_sync({"type": "aborted"})
    _push_history()
    flog.warning("[centinel] run abortado")
    return {"ok": True}


@router.get("/history")
async def get_history(_: str = Depends(require_admin)) -> list:
    _guard()
    return _history


@router.get("/stream/{run_id}")
async def stream_run(run_id: str, _: str = Depends(require_admin)) -> StreamingResponse:
    _guard()
    if _run["run_id"] != run_id:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    return StreamingResponse(
        _sse_generator(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── SSE generator ─────────────────────────────────────────────────────────────


async def _sse_generator(run_id: str):
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    _subscribers.append(q)
    try:
        # Replay eventos pasados (reconexión o run ya terminado)
        for event in list(_run["events"]):
            yield _sse(event)
        # Si el run ya terminó, salimos tras el replay
        if _run["status"] not in ("running",):
            return
        # Stream en vivo
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield _sse(event)
                if event.get("type") in ("done", "aborted", "error"):
                    break
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # keepalive
    finally:
        if q in _subscribers:
            _subscribers.remove(q)


# ── Background runner ─────────────────────────────────────────────────────────

_RE_TEST = re.compile(
    r"^(tests/[^\s:]+)::(\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAILED|XPASSED|WARNING)\s+\[?\s*(\d+)%"
)
_RE_COLLECTING = re.compile(r"collected (\d+) items?")
_RE_DURATION = re.compile(r"in ([\d.]+)s")


async def _execute_run(run_id: str, target: str) -> None:
    """Lanza pytest como subproceso y emite eventos SSE."""
    target_args = target.split() if " " in target else [target]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *target_args,
        "-v",
        "--tb=short",
        "--no-header",
        "--color=no",
        "-p",
        "no:cacheprovider",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=_BACKEND_DIR,
        )
        _run["proc"] = proc
        await _broadcast({"type": "started", "run_id": run_id, "target": target})

        pending_test: Optional[dict] = None
        traceback_buf: List[str] = []

        async for raw in proc.stdout:
            if _run["status"] == "aborted":
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue

            # ── collecting count
            m = _RE_COLLECTING.search(line)
            if m:
                await _broadcast({"type": "collecting", "count": int(m.group(1))})
                continue

            # ── test result line
            m = _RE_TEST.match(line)
            if m:
                if pending_test:
                    if traceback_buf:
                        pending_test["traceback"] = "\n".join(traceback_buf)
                    await _broadcast(pending_test)
                    traceback_buf = []

                filepath, name, status_raw, progress = (
                    m.group(1),
                    m.group(2),
                    m.group(3),
                    int(m.group(4)),
                )
                status = status_raw.lower()
                if status in ("xfailed", "skipped"):
                    status = "skipped"
                elif status == "xpassed":
                    status = "passed"
                pending_test = {
                    "type": "test",
                    "file": filepath,
                    "name": name,
                    "status": status,
                    "progress": progress,
                    "traceback": None,
                }
                continue

            # ── summary line
            if line.startswith("=") and (
                "passed" in line or "failed" in line or "error" in line
            ):
                if pending_test:
                    if traceback_buf:
                        pending_test["traceback"] = "\n".join(traceback_buf)
                    await _broadcast(pending_test)
                    pending_test = None
                    traceback_buf = []
                summary: dict = {}
                for word in ("passed", "failed", "error", "skipped", "warning"):
                    sm = re.search(r"(\d+) " + word, line)
                    if sm:
                        summary[word] = int(sm.group(1))
                dm = _RE_DURATION.search(line)
                if dm:
                    summary["duration_s"] = float(dm.group(1))
                _run["summary"] = summary
                await _broadcast({"type": "summary", **summary})
                continue

            # ── traceback lines
            if pending_test:
                traceback_buf.append(line)

        await proc.wait()
        if _run["status"] == "aborted":
            return

        failed_ids = [
            f"{e['file']}::{e['name']}"
            for e in _run["events"]
            if e.get("type") == "test" and e.get("status") in ("failed", "error")
        ]
        _run["failed_ids"] = failed_ids
        _run["status"] = "done"
        _run["finished_at"] = time.time()
        _push_history()
        await _broadcast(
            {
                "type": "done",
                "exit_code": proc.returncode,
                "status": "done",
                "failed_ids": failed_ids,
            }
        )
        flog.info(f"[centinel] run finalizado id={run_id[:8]} rc={proc.returncode}")

    except Exception as exc:
        flog.error(f"[centinel] error en run {run_id[:8]}: {exc}")
        _run["status"] = "error"
        _run["finished_at"] = time.time()
        await _broadcast({"type": "error", "message": str(exc)})


# ════════════════════════════════════════════════════════════════════════════
#  STRESS / PERFORMANCE TEST
# ════════════════════════════════════════════════════════════════════════════

import math
import statistics

_stress: Dict[str, Any] = {
    "run_id": None,
    "status": "idle",  # idle | running | done | aborted | error
    "started_at": None,
    "finished_at": None,
    "events": [],  # para replay al reconectar
    "result": {},
    "ticks": [],  # historial de ticks (max 600) para el frontend
    "errors": [],  # últimos 200 errores con detalle
}
_stress_subscribers: List[asyncio.Queue] = []


def _stress_sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stress_broadcast_sync(event: dict) -> None:
    _stress["events"].append(event)
    for q in list(_stress_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


class StressRequest(BaseModel):
    method: str = "GET"  # GET | POST | DELETE | RANDOM
    path: str = "/api/auth/me"  # ruta relativa o "RANDOM"
    body: Optional[str] = None  # JSON body para POST
    users: int = 10  # usuarios concurrentes
    duration: int = 30  # segundos totales
    ramp_up: int = 0  # segundos de rampa inicial
    timeout: float = 10.0  # timeout por petición en segundos
    fluctuate_users: bool = False  # variar carga aleatoriamente durante el test
    token: Optional[str] = None  # cookie ga_token si es necesario


# Endpoints predefinidos para el modo RANDOM
_RANDOM_ENDPOINTS: List[tuple] = [
    ("/api/auth/me", "GET"),
    ("/api/agents", "GET"),
    ("/api/connections", "GET"),
    ("/api/skills/private", "GET"),
    ("/api/knowledge", "GET"),
]


@router.get("/stress/status")
async def stress_status(_: str = Depends(require_admin)) -> dict:
    _guard()
    return {
        "status": _stress["status"],
        "run_id": _stress["run_id"],
        "started_at": _stress["started_at"],
        "finished_at": _stress["finished_at"],
        "result": _stress["result"],
        "ticks": _stress["ticks"],
        "errors": _stress["errors"],  # detalle de los últimos errores
    }


@router.post("/stress/run")
async def stress_run(
    body: StressRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_admin),
) -> dict:
    _guard()
    if _stress["status"] == "running":
        raise HTTPException(
            status_code=409, detail="Ya hay una prueba de estrés en curso."
        )
    run_id = str(uuid.uuid4())
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
        }
    )
    background_tasks.add_task(_execute_stress, run_id, body)
    flog.info(
        f"[centinel-stress] run iniciado id={run_id[:8]} path={body.path} users={body.users} dur={body.duration}s"
    )
    return {"run_id": run_id, "status": "running"}


@router.delete("/stress/run")
async def stress_abort(_: str = Depends(require_admin)) -> dict:
    _guard()
    if _stress["status"] != "running":
        raise HTTPException(status_code=409, detail="No hay prueba de estrés en curso.")
    _stress["status"] = "aborted"
    _stress["finished_at"] = time.time()
    _stress_broadcast_sync({"type": "stress_abort"})
    flog.warning("[centinel-stress] prueba abortada")
    return {"ok": True}


@router.get("/stress/stream/{run_id}")
async def stress_stream(
    run_id: str, _: str = Depends(require_admin)
) -> StreamingResponse:
    _guard()
    if _stress["run_id"] != run_id:
        raise HTTPException(status_code=404, detail="Run no encontrado")
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
    """Runner principal del stress test. Lanza N usuarios concurrentes."""
    import httpx

    # Determinar la URL base (mismo servidor)
    base_url = f"http://127.0.0.1:{os.getenv('GAIA_PORT', '8765')}"
    method = cfg.method.upper()
    headers = {"Content-Type": "application/json"}
    if cfg.token:
        headers["Cookie"] = f"ga_token={cfg.token}"

    # Acumuladores thread-safe via asyncio
    lock = asyncio.Lock()
    all_samples: List[float] = []  # tiempos de respuesta en ms
    error_count = 0
    request_count = 0

    # Muestreo por segundo para la gráfica
    tick_samples: List[float] = []
    tick_errors = 0
    last_tick = time.monotonic()
    tick_index = 0

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

    async def _worker(worker_id: int) -> None:
        import random as _random

        nonlocal error_count, request_count, tick_errors

        # Ramp-up: cada worker arranca en un momento escalonado
        if cfg.ramp_up > 0:
            delay = (worker_id / max(cfg.users, 1)) * cfg.ramp_up
            await asyncio.sleep(delay)

        # Cliente propio por worker: evita contención en pool compartido
        async with httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=cfg.timeout,
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        ) as client:
            while time.monotonic() < deadline and _stress["status"] == "running":
                # Modo Random: endpoint y método al azar
                if is_random_endpoint:
                    req_path, req_method = _random.choice(_RANDOM_ENDPOINTS)
                else:
                    req_path = cfg.path
                    req_method = method

                if is_random_method and not is_random_endpoint:
                    req_method = _random.choice(["GET", "POST", "DELETE"])

                t0 = time.monotonic()
                error_detail: Optional[str] = None
                status_code: Optional[int] = None
                try:
                    if req_method == "GET":
                        r = await client.get(req_path)
                    elif req_method == "DELETE":
                        r = await client.delete(req_path)
                    else:
                        r = await client.request(
                            req_method, req_path, content=cfg.body or ""
                        )
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    status_code = r.status_code
                    is_error = r.status_code >= 500
                    if is_error:
                        error_detail = f"HTTP {r.status_code}"
                except Exception as exc:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    is_error = True
                    error_detail = type(exc).__name__ + (f": {exc}" if str(exc) else "")

                async with lock:
                    all_samples.append(elapsed_ms)
                    tick_samples.append(elapsed_ms)
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
                                    "ms": round(elapsed_ms, 1),
                                }
                            )

                if cfg.fluctuate_users:
                    await asyncio.sleep(_random.uniform(0, 0.2))

    # Ticker: emite un evento SSE por segundo con stats agregadas
    async def _ticker() -> None:
        nonlocal tick_samples, tick_errors, tick_index, last_tick

        while _stress["status"] == "running":
            await asyncio.sleep(1.0)
            if _stress["status"] != "running":
                break
            tick_index += 1
            now = time.monotonic()
            elapsed = now - last_tick
            last_tick = now

            async with lock:
                samples = tick_samples[:]
                errs = tick_errors
                tick_samples = []
                tick_errors = 0

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
                "avg_ms": round(avg, 1),
                "p95_ms": round(p95, 1),
                "min_ms": round(mn, 1),
                "max_ms": round(mx, 1),
                "rps": round(rps, 1),
            }
            # Guardar tick en el historial (max 600)
            _stress["ticks"].append(tick_data)
            if len(_stress["ticks"]) > 600:
                _stress["ticks"] = _stress["ticks"][-600:]
            _stress_broadcast_sync(tick_data)

    try:
        tasks = [asyncio.create_task(_worker(i)) for i in range(cfg.users)]
        ticker_task = asyncio.create_task(_ticker())
        await asyncio.gather(*tasks)
        ticker_task.cancel()
        try:
            await ticker_task
        except asyncio.CancelledError:
            pass

        if _stress["status"] == "aborted":
            return

        # Calcular resultado final
        duration_s = time.monotonic() - (
            _stress["started_at"] - time.time() + time.monotonic()
        )
        actual_duration = time.time() - _stress["started_at"]

        def _pct(samples: List[float], p: float) -> float:
            if not samples:
                return 0.0
            s = sorted(samples)
            idx = max(0, int(math.ceil(len(s) * p / 100)) - 1)
            return round(s[idx], 1)

        result = {
            "total": request_count,
            "errors": error_count,
            "duration_s": round(actual_duration, 2),
            "rps": round(request_count / max(actual_duration, 0.001), 1),
            "avg_ms": round(statistics.mean(all_samples), 1) if all_samples else 0,
            "min_ms": round(min(all_samples), 1) if all_samples else 0,
            "max_ms": round(max(all_samples), 1) if all_samples else 0,
            "p50_ms": _pct(all_samples, 50),
            "p90_ms": _pct(all_samples, 90),
            "p95_ms": _pct(all_samples, 95),
            "p99_ms": _pct(all_samples, 99),
        }
        _stress["result"] = result
        _stress["status"] = "done"
        _stress["finished_at"] = time.time()
        _stress_broadcast_sync({"type": "stress_done", **result})
        flog.info(
            f"[centinel-stress] finalizado id={run_id[:8]} total={request_count} err={error_count}"
        )

    except Exception as exc:
        flog.error(f"[centinel-stress] error id={run_id[:8]}: {exc}")
        _stress["status"] = "error"
        _stress["finished_at"] = time.time()
        _stress_broadcast_sync({"type": "stress_error", "message": str(exc)})
