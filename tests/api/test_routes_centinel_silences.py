"""Regresiones para las degradaciones deliberadas de Centinel."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.api.routes import centinel


def test_broadcasts_keep_latest_event_for_slow_subscribers():
    cases = (
        (
            centinel._broadcast_sync,
            centinel._subscribers,
            centinel._run["events"],
            {"type": "done"},
        ),
        (
            centinel._stress_broadcast_sync,
            centinel._stress_subscribers,
            centinel._stress["events"],
            {"type": "stress_done"},
        ),
    )

    with (
        patch.object(centinel, "_persist_run_events"),
        patch.object(centinel.flog, "warning") as warning,
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

    with patch.object(centinel.flog, "warning") as warning:
        centinel._terminate_process(BrokenProcess(), "test")

    assert "no se pudo terminar proceso" in warning.call_args.args[0]


def test_read_centinel_state_invalid_json_returns_empty_and_warns(tmp_path):
    state_file = tmp_path / "centinel-state.json"
    state_file.write_text("no-es-json", encoding="utf-8")

    with (
        patch("app.config.data.CENTINEL_STATE_FILE", state_file),
        patch.object(centinel.flog, "warning") as warning,
    ):
        state = centinel._read_centinel_state()

    assert state == {}
    assert "estado compartido ilegible" in warning.call_args.args[0]
