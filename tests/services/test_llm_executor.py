"""Aislamiento y backpressure del executor dedicado a proveedores LLM."""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.services.llm_executor import LLMCapacityError, LLMExecutor


async def _wait_until_set(event: threading.Event) -> None:
    for _ in range(100):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("El trabajo bloqueante no llegó a arrancar")


async def test_llm_executor_no_ocupa_el_pool_por_defecto_de_asyncio() -> None:
    executor = LLMExecutor[str](max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def slow_llm() -> str:
        started.set()
        release.wait()
        return threading.current_thread().name

    task = asyncio.create_task(executor.run(slow_llm))
    try:
        await _wait_until_set(started)
        default_thread = await asyncio.wait_for(
            asyncio.to_thread(lambda: threading.current_thread().name),
            timeout=0.5,
        )
        assert not default_thread.startswith("iagents-llm")
    finally:
        release.set()
        llm_thread = await task
        executor.shutdown()

    assert llm_thread.startswith("iagents-llm")


async def test_llm_executor_rechaza_exceso_sin_encolarlo() -> None:
    executor = LLMExecutor[str](max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def slow_llm() -> str:
        started.set()
        release.wait()
        return "ok"

    task = asyncio.create_task(executor.run(slow_llm))
    try:
        await _wait_until_set(started)
        with pytest.raises(LLMCapacityError):
            await executor.run(lambda: "no debe ejecutarse")
    finally:
        release.set()
        assert await task == "ok"
        executor.shutdown()


async def test_cancelar_await_no_libera_el_slot_mientras_sigue_el_hilo() -> None:
    executor = LLMExecutor[str](max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def slow_llm() -> str:
        started.set()
        release.wait()
        return "ok"

    task = asyncio.create_task(executor.run(slow_llm))
    await _wait_until_set(started)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with pytest.raises(LLMCapacityError):
        await executor.run(lambda: "todavía no")

    release.set()
    for _ in range(100):
        lease = executor.try_acquire()
        if lease is not None:
            lease.release_if_unused()
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("El hilo terminado no liberó su slot")
    executor.shutdown()


async def test_reserva_previa_se_transfiere_a_la_ejecucion() -> None:
    executor = LLMExecutor[str](max_workers=1)
    lease = executor.try_acquire()
    assert lease is not None

    assert await executor.run(lambda: "ok", lease=lease) == "ok"
    next_lease = executor.try_acquire()
    assert next_lease is not None
    next_lease.release_if_unused()
    executor.shutdown()
