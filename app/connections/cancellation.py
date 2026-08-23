"""Cancelación thread-safe compartida por todos los transportes LLM."""

from __future__ import annotations

import threading
from typing import Any


class ProviderCancellation:
    """Cierra el socket bloqueante cuando el consumidor ASGI se desconecta."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._response: Any = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def attach(self, response: Any) -> None:
        close_now = False
        with self._lock:
            if self._event.is_set():
                close_now = True
            else:
                self._response = response
        if close_now:
            try:
                response.close()
            except (OSError, ValueError):
                return

    def detach(self, response: Any) -> None:
        with self._lock:
            if self._response is response:
                self._response = None

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            response = self._response
            self._response = None
        if response is not None:
            try:
                response.close()
            except (OSError, ValueError):
                return

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)
