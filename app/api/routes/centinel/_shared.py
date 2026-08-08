"""Helpers comunes a run, stress y probe: guard, SSE, broadcast e historial."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from app.api.routes.centinel._state import (
    CENTINEL_ENABLED,
    _history,
    _persist_run_events,
    _persist_run_history,
    _prune_history,
    _run,
    _stress,
    _stress_subscribers,
    _subscribers,
)
from app.errors import APIError
from app.utils import flog


# ── Helpers ──────────────────────────────────────────────────────────────────
def _guard() -> None:
    if not CENTINEL_ENABLED:
        raise APIError(
            403,
            "centinel_disabled",
            "Centinel no está habilitado (CENTINEL_ENABLED=false)",
        )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _put_latest(q: asyncio.Queue, event: dict, channel: str) -> None:
    """Deliver the latest event, evicting one stale item for slow subscribers."""
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        dropped = q.get_nowait()
        q.put_nowait(event)
        flog.warning(
            f"[centinel] suscriptor lento en {channel}; "
            f"evento {dropped.get('type', '?')} descartado"
        )


def _terminate_process(proc: Any, source: str) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.terminate()
    except (OSError, RuntimeError) as exc:
        flog.warning(f"[centinel] no se pudo terminar proceso ({source}): {exc}")


def _broadcast_sync(event: dict) -> None:
    _run["events"].append(event)
    _persist_run_events()
    for q in list(_subscribers):
        _put_latest(q, event, "run")


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
    _history[:] = _prune_history(_history)
    # _history es de proceso — con varios workers, el que atienda /history
    # puede no ser el que ejecutó el run. Se persiste igual que el resto del
    # estado de Centinel para que el historial sea el mismo lo sirva quien lo
    # sirva.
    _persist_run_history(entry)


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


def _stress_sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stress_broadcast_sync(event: dict) -> None:
    _stress["events"].append(event)
    for q in list(_stress_subscribers):
        _put_latest(q, event, "stress")
