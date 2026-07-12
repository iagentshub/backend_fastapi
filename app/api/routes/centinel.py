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
    "requested_users": None,  # usuarios pedidos por el admin
    "effective_users": None,  # usuarios realmente lanzados (cap de seguridad de hilos)
    "abort_requested": False,  # señal cross-proceso (ver _persist_stress_state)
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


# ── Estado compartido entre workers ──────────────────────────────────────────
# uvicorn corre GAIA_WORKERS procesos independientes (4 en producción, ver
# docker-compose.yml) que comparten el socket de escucha pero NO memoria: si el
# POST que arranca el test cae en el worker A, el polling de estado desde el
# frontend puede caer en B/C/D, que no tienen ni idea de que hay un run activo.
# Se persiste un snapshot en disco (mismo patrón que settings.py/SETTINGS_FILE)
# para que /stress/status, /stress/probe y el abort funcionen sea cual sea el
# worker que atienda la petición.
_STRESS_PERSIST_KEYS = (
    "status", "run_id", "started_at", "finished_at", "result",
    "ticks", "errors", "requested_users", "effective_users", "abort_requested",
    "updated_at",
)
_PROBE_PERSIST_KEYS = (
    "status", "run_id", "steps", "ticks", "current_users", "verdict",
    "abort_requested", "error", "updated_at",
)

# Si "status" lleva más de esto sin heartbeat (ver _persist_*_state), el
# proceso que lo ejecutaba ya no existe — un reinicio/crash/deploy a mitad de
# test deja el archivo compartido en "running" para siempre, porque nadie
# vuelve a escribirlo. El ticker persiste cada ~1s mientras corre de verdad,
# así que 10s de silencio es una señal inequívoca de proceso muerto, no de
# lag normal.
_STALE_SECONDS = 10


def _read_centinel_state() -> dict:
    from app.config.data import CENTINEL_STATE_FILE

    try:
        if CENTINEL_STATE_FILE.exists():
            return json.loads(CENTINEL_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_centinel_state(data: dict) -> None:
    from app.config.data import CENTINEL_STATE_FILE

    try:
        CENTINEL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CENTINEL_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(CENTINEL_STATE_FILE)
    except Exception as exc:
        flog.warning(f"[centinel] no se pudo persistir estado compartido: {exc}")


def _persist_stress_state() -> None:
    _stress["updated_at"] = time.time()
    data = _read_centinel_state()
    data["stress"] = {k: _stress.get(k) for k in _STRESS_PERSIST_KEYS}
    _write_centinel_state(data)


def _persist_probe_state() -> None:
    _probe["updated_at"] = time.time()
    data = _read_centinel_state()
    data["probe"] = {k: _probe.get(k) for k in _PROBE_PERSIST_KEYS}
    _write_centinel_state(data)


def _heal_if_stale(data: dict, section: str) -> dict:
    """Si data[section] dice 'running' sin heartbeat reciente, el proceso que
    lo ejecutaba ya no existe (reinicio/crash) y nadie va a terminarlo nunca
    — se corrige a 'error' y se persiste, para no bloquear /stress/run o
    /stress/probe con un 409 fantasma para siempre."""
    part = data.get(section) or {}
    if part.get("status") != "running":
        return data
    updated_at = part.get("updated_at") or 0
    if (time.time() - updated_at) <= _STALE_SECONDS:
        return data
    part["status"] = "error"
    part["error"] = "Prueba interrumpida: el proceso que la ejecutaba se reinició o cayó a mitad de la ejecución."
    part["finished_at"] = time.time()
    data[section] = part
    _write_centinel_state(data)
    flog.warning(
        f"[centinel] {section} huérfano detectado (sin heartbeat > {_STALE_SECONDS}s) — marcado como error"
    )
    return data


class StressRequest(BaseModel):
    method: str = "GET"  # GET | POST | DELETE | RANDOM
    path: str = "/api/auth/me"  # ruta relativa o "RANDOM"
    body: Optional[str] = None  # JSON body para POST
    users: int = 10  # usuarios concurrentes
    duration: int = 30  # segundos totales
    ramp_up: int = 0  # segundos de rampa inicial
    timeout: float = 10.0  # timeout por petición en segundos
    fluctuate_users: bool = False  # variar carga aleatoriamente durante el test
    max_concurrency: int = 0  # 0 = sin límite
    token: Optional[str] = None  # cookie ga_token si es necesario


# Endpoints predefinidos para el modo RANDOM
_RANDOM_ENDPOINTS: List[tuple] = [
    ("/api/auth/me", "GET"),
    ("/api/agents", "GET"),
    ("/api/connections", "GET"),
    ("/api/skills/private", "GET"),
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
    except Exception as exc:
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
        raise HTTPException(status_code=409, detail="No hay prueba de estrés en curso.")

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
        data = _read_centinel_state()
        data["stress"] = persisted
        _write_centinel_state(data)

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
    """Runner principal del stress test. Lanza N workers en threads independientes."""
    import httpx
    import threading
    from concurrent.futures import ThreadPoolExecutor

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
            if (
                other_worker_state.get("run_id") == run_id
                and other_worker_state.get("abort_requested")
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
        try:
            await ticker_task
        except asyncio.CancelledError:
            pass

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
        avg_per_user_s = round(statistics.mean(per_worker_means), 3) if per_worker_means else 0.0

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

    except Exception as exc:
        flog.error(f"[centinel-stress] error id={run_id[:8]}: {exc}")
        _stress["status"] = "error"
        _stress["finished_at"] = time.time()
        _persist_stress_state()
        _stress_broadcast_sync({"type": "stress_error", "message": str(exc)})


# ── Probe: búsqueda automática del punto de quiebre ───────────────────────────

_probe: Dict[str, Any] = {
    "status": "idle",  # idle | running | done | aborted | error
    "run_id": None,
    "steps": [],  # [{users, total, errors, error_rate, rps, avg_s, elapsed_s, status}]
    "ticks": [],  # [{users, rps, errors, avg_s}] — 1 tick/segundo, máx 600
    "current_users": None,
    "verdict": None,  # {stable_users, break_users, error_rate}
    "abort_requested": False,  # señal cross-proceso (ver _persist_probe_state)
    "error": None,
}


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
        raise HTTPException(status_code=409, detail="Probe ya en ejecución")
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
    import httpx
    import threading
    from concurrent.futures import ThreadPoolExecutor

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
    try:
        await ticker_task
    except asyncio.CancelledError:
        pass

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
