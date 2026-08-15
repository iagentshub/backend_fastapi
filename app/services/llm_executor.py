"""Executor aislado y acotado para el transporte LLM síncrono.

Las llamadas de chat siguen usando el transporte ``safe_http`` bloqueante para
conservar el DNS pinning. Este módulo evita que esas conexiones largas ocupen
el executor por defecto de asyncio, que también usa bcrypt, knowledge y otros
servicios del backend.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Generic, TypeVar

_T = TypeVar("_T")


def _configured_max_workers() -> int:
    default = 16
    try:
        return max(1, int(os.getenv("GAIA_LLM_MAX_THREADS", str(default))))
    except ValueError:
        return default


class LLMCapacityError(RuntimeError):
    """No queda capacidad para iniciar otra llamada LLM en este worker."""


class LLMLease:
    """Reserva transferible que solo puede consumir una ejecución."""

    def __init__(self, owner: "LLMExecutor") -> None:
        self._owner = owner
        self._lock = threading.Lock()
        self._available = True

    def _consume(self) -> bool:
        with self._lock:
            if not self._available:
                return False
            self._available = False
            return True

    def release_if_unused(self) -> None:
        """Devuelve la reserva si nunca llegó a enviarse trabajo al executor."""
        if self._consume():
            self._owner._release_slot()


class LLMExecutor(Generic[_T]):
    """Thread pool dedicado, sin cola silenciosa por encima de su capacidad."""

    def __init__(self, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("max_workers debe ser al menos 1")
        self.max_workers = max_workers
        self._slots = threading.BoundedSemaphore(max_workers)
        self._pool: ThreadPoolExecutor | None = None
        self._pool_lock = threading.Lock()

    def try_acquire(self) -> LLMLease | None:
        if not self._slots.acquire(blocking=False):
            return None
        return LLMLease(self)

    def _release_slot(self) -> None:
        self._slots.release()

    def _get_pool(self) -> ThreadPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self.max_workers,
                    thread_name_prefix="iagents-llm",
                )
            return self._pool

    async def run(
        self,
        fn: Callable[..., _T],
        *args: Any,
        lease: LLMLease | None = None,
    ) -> _T:
        active_lease = (
            lease
            if lease is not None and lease._owner is self and lease._consume()
            else None
        )
        if active_lease is None:
            active_lease = self.try_acquire()
            if active_lease is None:
                raise LLMCapacityError("Capacidad de llamadas LLM agotada")
            active_lease._consume()

        context = contextvars.copy_context()

        def invoke() -> _T:
            try:
                return context.run(fn, *args)
            finally:
                self._release_slot()

        try:
            future = asyncio.get_running_loop().run_in_executor(
                self._get_pool(), invoke
            )
        except Exception:
            self._release_slot()
            raise
        return await future

    def shutdown(self) -> None:
        """Impide nuevas colas en el pool viejo sin esperar streams remotos."""
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            # Los trabajos aceptados conservan su slot hasta que invoke() termina.
            # Cancelar futuros aún en cola impediría ejecutar ese finally y dejaría
            # capacidad secuestrada si el proceso se reutiliza (p. ej. TestClient).
            pool.shutdown(wait=False)


_LLM_EXECUTOR = LLMExecutor[Any](_configured_max_workers())


def try_acquire_llm_lease() -> LLMLease | None:
    return _LLM_EXECUTOR.try_acquire()


async def run_llm_blocking(
    fn: Callable[..., _T],
    *args: Any,
    lease: LLMLease | None = None,
) -> _T:
    return await _LLM_EXECUTOR.run(fn, *args, lease=lease)


def shutdown_llm_executor() -> None:
    _LLM_EXECUTOR.shutdown()
