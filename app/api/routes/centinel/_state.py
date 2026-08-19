"""Estado de Centinel: el de proceso y el compartido entre workers.

Los tres diccionarios (`_run`, `_stress`, `_probe`) se mutan **in situ** desde
los submódulos; nunca se reasignan, porque cada submódulo importa la
referencia y una reasignación aquí dejaría a los demás mirando el objeto
viejo.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from typing import Any, Callable, Dict, List

from app.utils import flog

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
    "abort_requested": False,  # señal cross-proceso (ver _persist_run_state)
    # Salida cruda línea a línea, SIN filtrar — a diferencia de "events", que
    # solo guarda lo que el parser reconoce (test/collecting/summary). Las
    # secciones "ERRORS"/"FAILURES" de pytest (con el traceback real) van
    # DESPUÉS de todas las líneas de test, cuando pending_test ya es None —
    # el parser las descartaba en silencio. Acotado para no crecer sin límite
    # en un run con miles de tests.
    "raw_lines": [],
}
_subscribers: List[asyncio.Queue] = []
_history: List[Dict[str, Any]] = []


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


# ── Estado compartido entre workers ──────────────────────────────────────────
# uvicorn corre GAIA_WORKERS procesos independientes (4 en producción, ver
# docker-compose.yml) que comparten el socket de escucha pero NO memoria: si el
# POST que arranca el test cae en el worker A, el polling de estado desde el
# frontend puede caer en B/C/D, que no tienen ni idea de que hay un run activo.
# Se persiste un snapshot en disco (mismo patrón que settings.py/SETTINGS_FILE)
# para que /stress/status, /stress/probe y el abort funcionen sea cual sea el
# worker que atienda la petición.
_STRESS_PERSIST_KEYS = (
    "status",
    "run_id",
    "started_at",
    "finished_at",
    "result",
    "ticks",
    "errors",
    "requested_users",
    "effective_users",
    "abort_requested",
    "updated_at",
)
_PROBE_PERSIST_KEYS = (
    "status",
    "run_id",
    "steps",
    "ticks",
    "current_users",
    "verdict",
    "abort_requested",
    "error",
    "updated_at",
)
# El test runner funcional (pytest, _run más abajo) tenía el mismo problema de
# fondo que stress/probe pero nunca recibió este mismo arreglo — ver _run,
# _persist_run_state y los endpoints /status, /run, /history, /stream.
_RUN_PERSIST_KEYS = (
    "status",
    "run_id",
    "target",
    "started_at",
    "finished_at",
    "summary",
    "failed_ids",
    "abort_requested",
    "updated_at",
)

# Si "status" lleva más de esto sin heartbeat (ver _persist_*_state), el
# proceso que lo ejecutaba ya no existe — un reinicio/crash/deploy a mitad de
# test deja el archivo compartido en "running" para siempre, porque nadie
# vuelve a escribirlo. El ticker persiste cada ~1s mientras corre de verdad,
# así que 10s de silencio es una señal inequívoca de proceso muerto, no de
# lag normal.
_STALE_SECONDS = 10


def _read_centinel_state_unlocked() -> dict:
    from app.config.data import CENTINEL_STATE_FILE

    try:
        if CENTINEL_STATE_FILE.exists():
            state = json.loads(CENTINEL_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise TypeError("el estado compartido no contiene un objeto JSON")
            return state
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        flog.warning(f"[centinel] estado compartido ilegible: {exc}")
    return {}


def _write_centinel_state_unlocked(data: dict) -> None:
    from app.config.data import CENTINEL_STATE_FILE

    CENTINEL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{CENTINEL_STATE_FILE.name}.",
        suffix=".tmp",
        dir=CENTINEL_STATE_FILE.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, CENTINEL_STATE_FILE)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _with_state_lock(action: Callable[[], Any]) -> Any:
    """Ejecuta lectura-modificación-escritura bajo un lock entre procesos."""
    import fcntl

    from app.config.data import CENTINEL_STATE_FILE

    CENTINEL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = CENTINEL_STATE_FILE.with_suffix(".lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            return action()
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_centinel_state() -> dict:
    try:
        return _with_state_lock(_read_centinel_state_unlocked)
    except Exception as exc:  # noqa: BLE001
        # Estado compartido entre workers vía fichero con flock: si no se
        # puede leer se sigue con estado vacío, ya registrado.
        flog.warning(f"[centinel] no se pudo leer estado compartido: {exc}")
        return {}


def _write_centinel_state(data: dict) -> None:
    try:
        _with_state_lock(lambda: _write_centinel_state_unlocked(data))
    except Exception as exc:  # noqa: BLE001
        # Ver _read_centinel_state: perder una escritura de estado degrada
        # el panel, no el servicio.
        flog.warning(f"[centinel] no se pudo persistir estado compartido: {exc}")


def _update_centinel_state(mutator: Callable[[dict], None]) -> dict:
    """Actualiza una sección sin perder escrituras concurrentes de otro worker."""
    result: dict = {}

    def update() -> None:
        nonlocal result
        result = _read_centinel_state_unlocked()
        mutator(result)
        _write_centinel_state_unlocked(result)

    try:
        _with_state_lock(update)
    except Exception as exc:  # noqa: BLE001
        # Ver _read_centinel_state.
        flog.warning(f"[centinel] no se pudo actualizar estado compartido: {exc}")
    return result


def _persist_stress_state() -> None:
    _stress["updated_at"] = time.time()
    snapshot = {k: _stress.get(k) for k in _STRESS_PERSIST_KEYS}
    _update_centinel_state(lambda data: data.__setitem__("stress", snapshot))


def _persist_probe_state() -> None:
    _probe["updated_at"] = time.time()
    snapshot = {k: _probe.get(k) for k in _PROBE_PERSIST_KEYS}
    _update_centinel_state(lambda data: data.__setitem__("probe", snapshot))


def _persist_run_state() -> None:
    _run["updated_at"] = time.time()
    snapshot = {k: _run.get(k) for k in _RUN_PERSIST_KEYS}
    _update_centinel_state(lambda data: data.__setitem__("run", snapshot))


_last_event_flush = 0.0


def _persist_run_events(*, force: bool = False) -> None:
    """Espeja _run["events"] y _run["raw_lines"] en disco para que
    /stream/{run_id} pueda reenviarlos en vivo aunque la conexión SSE caiga
    en un worker distinto al que ejecuta el run (ver _sse_generator, rama
    "remota"), y para poder inspeccionar la salida cruda de un run desde
    fuera de la app (p.ej. `docker exec` + leer centinel_state.json) cuando
    el resumen estructurado no basta para diagnosticar un fallo."""
    global _last_event_flush
    now = time.monotonic()
    if not force and now - _last_event_flush < 1.0:
        return
    events = list(_run["events"])
    raw_lines = list(_run["raw_lines"])

    def update(data: dict) -> None:
        data["run_events"] = events
        data["run_raw_lines"] = raw_lines

    _update_centinel_state(update)
    _last_event_flush = now


def _reset_run_events() -> None:
    def update(data: dict) -> None:
        data["run_events"] = []
        data["run_raw_lines"] = []

    _update_centinel_state(update)


def _prune_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mismo horizonte que la retención de logs (Admin → Config → Logs) — un
    solo dial para "cuánto histórico guardamos" en vez de dos ajustes que
    puedan desincronizarse."""
    from app.services.platform_settings import _read_platform_cfg

    retention_days = int(_read_platform_cfg().get("log_retention_days", 30))
    cutoff = time.time() - retention_days * 86400
    return [h for h in history if (h.get("finished_at") or 0) >= cutoff]


def _persist_run_history(entry: Dict[str, Any]) -> None:
    def update(data: dict) -> None:
        history = data.get("run_history") or []
        history.insert(0, entry)
        data["run_history"] = _prune_history(history)

    _update_centinel_state(update)


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
    part["error"] = (
        "Prueba interrumpida: el proceso que la ejecutaba se reinició o cayó a mitad de la ejecución."
    )
    part["finished_at"] = time.time()
    _update_centinel_state(lambda current: current.__setitem__(section, part))
    flog.warning(
        f"[centinel] {section} huérfano detectado (sin heartbeat > {_STALE_SECONDS}s) — marcado como error"
    )
    return data
