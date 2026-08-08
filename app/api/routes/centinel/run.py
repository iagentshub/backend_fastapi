"""Runner funcional: lanza pytest, lo sigue por SSE y guarda el historial."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from typing import List, Optional

from fastapi import BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.routes.auth import require_admin
from app.api.routes.centinel._router import router
from app.api.routes.centinel._shared import (
    _broadcast,
    _broadcast_sync,
    _build_tree,
    _guard,
    _push_history,
    _sse,
    _terminate_process,
)
from app.api.routes.centinel._state import (
    _BACKEND_DIR,
    _RUN_PERSIST_KEYS,
    _heal_if_stale,
    _history,
    _persist_run_state,
    _read_centinel_state,
    _reset_run_events,
    _run,
    _subscribers,
    _write_centinel_state,
)
from app.errors import APIError
from app.utils import flog
from app.utils.generators import generate_id

# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status(_: str = Depends(require_admin)) -> dict:
    _guard()
    data = _heal_if_stale(_read_centinel_state(), "run")
    persisted = data.get("run")
    if persisted:
        return persisted
    return {k: _run.get(k) for k in _RUN_PERSIST_KEYS}


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
        raise APIError(504, "upstream_error", "Timeout descubriendo tests")
    except Exception as exc:
        raise APIError(500, "internal_error", str(exc))


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
    persisted = _heal_if_stale(_read_centinel_state(), "run").get("run", {})
    if _run["status"] == "running" or persisted.get("status") == "running":
        raise APIError(
            409,
            "already_exists",
            "Ya hay un run en curso. Espera o abórtalo.",
            extra={"resource": "run"},
        )

    target = body.target.strip()
    # Seguridad: target solo dentro de tests/
    if not target.startswith("tests") or ".." in target or target.startswith("/"):
        raise APIError(422, "invalid_field", "Target no válido", extra={"field": "target"})

    # Re-run solo los fallidos — prefiere la memoria local (mismo worker que
    # acaba de terminar el run anterior) y cae a lo persistido si este worker
    # no es el que lo ejecutó.
    failed_ids = _run["failed_ids"] or persisted.get("failed_ids") or []
    if body.rerun_failed and failed_ids:
        target = " ".join(failed_ids)

    run_id = generate_id(32)
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
            "abort_requested": False,
            "raw_lines": [],
        }
    )
    _persist_run_state()
    _reset_run_events()
    background_tasks.add_task(_execute_run, run_id, target)
    flog.info(f"[centinel] run iniciado id={run_id[:8]} target={target!r}")
    return {"run_id": run_id, "status": "running"}


@router.delete("/run")
async def abort_run(_: str = Depends(require_admin)) -> dict:
    _guard()
    persisted = _heal_if_stale(_read_centinel_state(), "run").get("run", {})
    is_local = _run["status"] == "running" and _run["run_id"] == persisted.get(
        "run_id"
    )
    if not is_local and persisted.get("status") != "running":
        raise APIError(409, "no_run_in_progress", "No hay run en curso")

    if is_local:
        # Camino rápido: este mismo proceso está ejecutando el run.
        _terminate_process(_run.get("proc"), "abort local")
        _run["status"] = "aborted"
        _run["finished_at"] = time.time()
        _broadcast_sync({"type": "aborted"})
        _persist_run_state()
        _push_history()
    else:
        # El run corre en otro worker: señalizar vía archivo compartido — su
        # _execute_run tiene un ticker que revisa "abort_requested" cada ~1s.
        persisted["abort_requested"] = True
        data = _read_centinel_state()
        data["run"] = persisted
        _write_centinel_state(data)

    flog.warning("[centinel] run abortado")
    return {"ok": True}


@router.get("/history")
async def get_history(_: str = Depends(require_admin)) -> list:
    _guard()
    data = _read_centinel_state()
    persisted_history = data.get("run_history")
    if persisted_history is not None:
        return persisted_history
    return _history


@router.delete("/history/{run_id}")
async def delete_history_entry(run_id: str, _: str = Depends(require_admin)) -> dict:
    _guard()
    _history[:] = [h for h in _history if h.get("run_id") != run_id]
    data = _read_centinel_state()
    history = data.get("run_history") or []
    data["run_history"] = [h for h in history if h.get("run_id") != run_id]
    _write_centinel_state(data)
    return {"ok": True}


@router.get("/stream/{run_id}")
async def stream_run(run_id: str, _: str = Depends(require_admin)) -> StreamingResponse:
    _guard()
    if _run["run_id"] != run_id:
        persisted = _read_centinel_state().get("run", {})
        if persisted.get("run_id") != run_id:
            raise APIError(
                404, "not_found", "Run no encontrado", extra={"resource": "run"}
            )
        # El run existe pero lo ejecuta otro worker: sin memoria compartida no
        # hay eventos en vivo que reenviar desde aquí, pero al menos no se
        # corta con un 404 — el generador replica el estado final conocido.
    return StreamingResponse(
        _sse_generator(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── SSE generator ─────────────────────────────────────────────────────────────


async def _sse_generator(run_id: str):
    if _run["run_id"] == run_id:
        # Este worker ejecuta el run: cola en memoria, entrega en vivo exacta.
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
        return

    # El run lo ejecuta OTRO worker: no hay cola en memoria que compartir, así
    # que se sondea el log de eventos persistido (ver _persist_run_events)
    # cada ~1s y se reenvía lo nuevo, hasta que el estado persistido deje de
    # decir "running".
    sent = 0
    idle_ticks = 0
    while True:
        data = _read_centinel_state()
        if (data.get("run") or {}).get("run_id") != run_id:
            return
        events = data.get("run_events") or []
        new_events = events[sent:]
        for event in new_events:
            yield _sse(event)
        sent = len(events)
        if new_events:
            idle_ticks = 0
        else:
            idle_ticks += 1
            if idle_ticks >= 15:
                yield ": ping\n\n"  # keepalive
                idle_ticks = 0
        status = (data.get("run") or {}).get("status")
        if status not in ("running",):
            return
        await asyncio.sleep(1.0)


# ── Background runner ─────────────────────────────────────────────────────────

_RE_TEST = re.compile(
    r"^(tests/[^\s:]+)::(\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAILED|XPASSED|WARNING)\s+\[?\s*(\d+)%"
)
_RE_COLLECTING = re.compile(r"collected (\d+) items?")
_RE_DURATION = re.compile(r"in ([\d.]+)s")


async def _run_ticker(run_id: str, proc: "asyncio.subprocess.Process") -> None:
    """Doble función, revisada cada ~1s mientras el run está activo:
    1) Refresca el heartbeat ("updated_at") para que _heal_if_stale no marque
       el run como huérfano en otro worker — una suite real tarda minutos,
       muy por encima de _STALE_SECONDS, así que sin este refresco periódico
       cualquier /status que caiga en otro worker lo daría por muerto.
    2) Detecta un abort disparado desde OTRO worker (ver /run DELETE), que
       llega aquí vía archivo compartido, no memoria."""
    while _run["status"] == "running":
        await asyncio.sleep(1.0)
        if _run["status"] != "running":
            return
        other_worker_state = _read_centinel_state().get("run", {})
        if other_worker_state.get("run_id") == run_id and other_worker_state.get(
            "abort_requested"
        ):
            _run["status"] = "aborted"
            _run["finished_at"] = time.time()
            _terminate_process(proc, "abort remoto")
            _broadcast_sync({"type": "aborted"})
            _persist_run_state()
            _push_history()
            return
        _persist_run_state()


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
    # En producción (GAIA_WORKERS>1) el proceso maestro migra el esquema una
    # sola vez y marca GAIA_SCHEMA_MIGRATED=1 en su entorno antes de lanzar
    # los workers de uvicorn (ver main.py) — este subproceso, hijo de un
    # worker, heredaría esa marca aunque va a crear sus propias bases de
    # datos SQLite efímeras y vacías (ver conftest.py), haciendo que
    # init_db() se salte migrate_schema() y truene con "no such table".
    env = os.environ.copy()
    env.pop("GAIA_SCHEMA_MIGRATED", None)

    ticker_task: Optional[asyncio.Task] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=_BACKEND_DIR,
            env=env,
        )
        _run["proc"] = proc
        ticker_task = asyncio.create_task(_run_ticker(run_id, proc))
        await _broadcast({"type": "started", "run_id": run_id, "target": target})

        pending_test: Optional[dict] = None
        traceback_buf: List[str] = []

        async for raw in proc.stdout:
            if _run["status"] == "aborted":
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue

            # Sin filtrar — incluye lo que el parser de abajo no reconoce,
            # como las secciones ERRORS/FAILURES (con el traceback real),
            # que pytest emite al final, no junto a cada test.
            _run["raw_lines"].append(line)
            if len(_run["raw_lines"]) > 4500:
                del _run["raw_lines"][:-4000]

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
            # El ticker (abort de otro worker) ya persistió y emitió el evento;
            # un abort local ya lo hizo el propio endpoint DELETE /run.
            return

        failed_ids = [
            f"{e['file']}::{e['name']}"
            for e in _run["events"]
            if e.get("type") == "test" and e.get("status") in ("failed", "error")
        ]
        _run["failed_ids"] = failed_ids
        _run["status"] = "done"
        _run["finished_at"] = time.time()
        # Orden importa: el evento terminal debe quedar en run_events ANTES
        # de que el status persistido diga "done" — si no, un poller remoto
        # (ver _sse_generator) puede leer status=done sin haber visto aún el
        # evento "done" y cortar el stream perdiéndoselo.
        await _broadcast(
            {
                "type": "done",
                "exit_code": proc.returncode,
                "status": "done",
                "failed_ids": failed_ids,
            }
        )
        _persist_run_state()
        _push_history()
        flog.info(f"[centinel] run finalizado id={run_id[:8]} rc={proc.returncode}")

    except Exception as exc:
        flog.error(f"[centinel] error en run {run_id[:8]}: {exc}")
        _run["status"] = "error"
        _run["finished_at"] = time.time()
        await _broadcast({"type": "error", "message": str(exc)})
        _persist_run_state()

    finally:
        if ticker_task is not None:
            ticker_task.cancel()
