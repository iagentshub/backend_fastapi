"""Regresiones para las degradaciones deliberadas de Centinel."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.centinel import _shared, _state

# Centinel es un paquete (`app/api/routes/centinel/`): el estado vive en
# `_state` y los helpers en `_shared`. Los parches van al módulo donde se
# resuelve el nombre, no al paquete, o no llegan a tener efecto.


def test_broadcasts_keep_latest_event_for_slow_subscribers():
    cases = (
        (
            _shared._broadcast_sync,
            _state._subscribers,
            _state._run["events"],
            {"type": "done"},
        ),
        (
            _shared._stress_broadcast_sync,
            _state._stress_subscribers,
            _state._stress["events"],
            {"type": "stress_done"},
        ),
    )

    with (
        patch.object(_shared, "_persist_run_events"),
        patch.object(_shared.flog, "warning") as warning,
    ):
        for broadcast, subscribers, events, latest in cases:
            queue = asyncio.Queue(maxsize=1)
            queue.put_nowait({"type": "stale"})
            previous_event_count = len(events)
            subscribers.append(queue)
            try:
                broadcast(latest)
                assert queue.get_nowait() == latest
            finally:
                subscribers.remove(queue)
                while len(events) > previous_event_count:
                    events.pop()

    assert warning.call_count == 2


def test_terminate_process_failure_is_non_fatal_and_visible():
    class BrokenProcess:
        returncode = None

        def terminate(self):
            raise OSError("proceso desaparecido")

    with patch.object(_shared.flog, "warning") as warning:
        _shared._terminate_process(BrokenProcess(), "test")

    assert "no se pudo terminar proceso" in warning.call_args.args[0]


def test_read_centinel_state_invalid_json_returns_empty_and_warns(tmp_path):
    state_file = tmp_path / "centinel-state.json"
    state_file.write_text("no-es-json", encoding="utf-8")

    with (
        patch("app.config.data.CENTINEL_STATE_FILE", state_file),
        patch.object(_state.flog, "warning") as warning,
    ):
        state = _state._read_centinel_state()

    assert state == {}
    assert "estado compartido ilegible" in warning.call_args.args[0]


def test_centinel_request_models_reject_invalid_ranges():
    from pydantic import ValidationError

    from app.api.routes.centinel.probe import ProbeRequest
    from app.api.routes.centinel.stress import StressRequest

    with pytest.raises(ValidationError):
        StressRequest(users=-1, duration=0, timeout=-3)
    with pytest.raises(ValidationError):
        ProbeRequest(start_users=0, step=0, duration=0)


def test_run_event_persistence_is_batched_and_terminal_is_forced(monkeypatch):
    writes = []
    monkeypatch.setattr(_state, "_last_event_flush", 0.0)
    monkeypatch.setattr(
        _state, "_update_centinel_state", lambda update: writes.append(update)
    )
    _state._persist_run_events()
    _state._persist_run_events()
    assert len(writes) == 1
    _state._persist_run_events(force=True)
    assert len(writes) == 2


def test_run_internal_error_does_not_leak_exception_text():
    from app.api.routes.centinel import run

    broadcast = AsyncMock()
    with (
        patch.object(
            run.asyncio,
            "create_subprocess_exec",
            new=AsyncMock(side_effect=RuntimeError("secret=/private/token")),
        ),
        patch.object(run, "_broadcast", new=broadcast),
        patch.object(run, "_persist_run_state"),
        patch.object(run.flog, "error"),
    ):
        asyncio.run(run._execute_run("run-id", "tests/unit"))

    event = broadcast.await_args.args[0]
    assert event == {
        "type": "error",
        "code": "internal_error",
        "message": "Error interno al ejecutar las pruebas.",
    }
    assert "secret" not in repr(event)


def test_stress_internal_error_does_not_leak_exception_text():
    from app.api.routes.centinel import stress

    events = []

    class BrokenExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("secret=/private/token")

        def __exit__(self, *args):
            return False

    with (
        patch("concurrent.futures.ThreadPoolExecutor", BrokenExecutor),
        patch.object(stress, "_stress_broadcast_sync", side_effect=events.append),
        patch.object(stress, "_persist_stress_state"),
        patch.object(stress.flog, "error"),
    ):
        asyncio.run(
            stress._execute_stress(
                "stress-id", stress.StressRequest(users=1, duration=1)
            )
        )

    event = events[-1]
    assert event == {
        "type": "stress_error",
        "code": "internal_error",
        "message": "Error interno durante la prueba de carga.",
    }
    assert "secret" not in repr(event)
