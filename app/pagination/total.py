"""Cálculo protegido del total exacto de una página cursor."""

from __future__ import annotations

import asyncio
from typing import Any
from weakref import WeakKeyDictionary

from app.config.pagination import (
    EXACT_TOTAL_MAX_CONCURRENCY,
    EXACT_TOTAL_TIMEOUT_SECONDS,
)
from app.pagination.metrics import increment
from app.storage.db import AsyncConn


class ExactTotalTimeout(RuntimeError):
    """El COUNT exacto superó su presupuesto operativo."""


_gates: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    WeakKeyDictionary()
)


def _gate_for_current_loop() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(EXACT_TOTAL_MAX_CONCURRENCY)
        _gates[loop] = gate
    return gate


async def exact_total(
    conn: AsyncConn,
    *,
    sql: str,
    params: tuple[Any, ...],
    resource: str,
    cursor_total: int | None,
) -> tuple[int, bool]:
    """Devuelve el total y si salió del estado firmado del cursor."""

    if cursor_total is not None:
        return cursor_total, True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + EXACT_TOTAL_TIMEOUT_SECONDS
    gate = _gate_for_current_loop()
    acquired = False
    try:
        await asyncio.wait_for(gate.acquire(), timeout=EXACT_TOTAL_TIMEOUT_SECONDS)
        acquired = True
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        value = await conn.fetchval_with_timeout(
            sql,
            params,
            timeout=remaining,
        )
    except TimeoutError as exc:
        increment(resource, "total_timeouts")
        raise ExactTotalTimeout from exc
    finally:
        if acquired:
            gate.release()
    increment(resource, "total_queries")
    return int(value or 0), False
