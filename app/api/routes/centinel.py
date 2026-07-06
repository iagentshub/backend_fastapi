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
CENTINEL_ENABLED: bool = os.getenv("CENTINEL_ENABLED", "true").lower() in ("1", "true", "yes")
_BACKEND_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# ── Estado global ────────────────────────────────────────────────────────────
_run: Dict[str, Any] = {
    "run_id": None,
    "proc": None,
    "status": "idle",   # idle | running | done | aborted | error
    "target": None,
    "started_at": None,
    "finished_at": None,
    "events": [],       # lista de todos los eventos emitidos (para replay)
    "summary": {},
    "failed_ids": [],
}
_subscribers: List[asyncio.Queue] = []
_history: List[Dict[str, Any]] = []

# ── Helpers ──────────────────────────────────────────────────────────────────
def _guard() -> None:
    if not CENTINEL_ENABLED:
        raise HTTPException(status_code=403, detail="Centinel no está habilitado (CENTINEL_ENABLED=false)")


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
            sys.executable, "-m", "pytest", "tests/",
            "--collect-only", "-q", "--no-header",
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
        raise HTTPException(status_code=409, detail="Ya hay un run en curso. Espera o abórtalo.")

    target = body.target.strip()
    # Seguridad: target solo dentro de tests/
    if not target.startswith("tests") or ".." in target or target.startswith("/"):
        raise HTTPException(status_code=422, detail="Target no válido")

    # Re-run solo los fallidos
    if body.rerun_failed and _run["failed_ids"]:
        target = " ".join(_run["failed_ids"])

    run_id = str(uuid.uuid4())
    _run.update({
        "run_id": run_id,
        "proc": None,
        "status": "running",
        "target": target,
        "started_at": time.time(),
        "finished_at": None,
        "events": [],
        "summary": {},
        "failed_ids": [],
    })
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
    r'^(tests/[^\s:]+)::(\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAILED|XPASSED|WARNING)\s+\[?\s*(\d+)%'
)
_RE_COLLECTING = re.compile(r'collected (\d+) items?')
_RE_DURATION = re.compile(r'in ([\d.]+)s')


async def _execute_run(run_id: str, target: str) -> None:
    """Lanza pytest como subproceso y emite eventos SSE."""
    target_args = target.split() if " " in target else [target]
    cmd = [
        sys.executable, "-m", "pytest", *target_args,
        "-v", "--tb=short", "--no-header",
        "--color=no", "-p", "no:cacheprovider",
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
                    m.group(1), m.group(2), m.group(3), int(m.group(4))
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
            if line.startswith("=") and ("passed" in line or "failed" in line or "error" in line):
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
        await _broadcast({
            "type": "done",
            "exit_code": proc.returncode,
            "status": "done",
            "failed_ids": failed_ids,
        })
        flog.info(f"[centinel] run finalizado id={run_id[:8]} rc={proc.returncode}")

    except Exception as exc:
        flog.error(f"[centinel] error en run {run_id[:8]}: {exc}")
        _run["status"] = "error"
        _run["finished_at"] = time.time()
        await _broadcast({"type": "error", "message": str(exc)})
