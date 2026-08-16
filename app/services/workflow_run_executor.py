"""In-process workflow tasks backed by a cross-worker database event log."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.services.workflow_errors import workflow_error_event
from app.services.workflow_runner import run_workflow
from app.storage.workflow_runs import WorkflowRunStorage
from app.utils import flog

AgentResolver = Callable[[str], Any]
_storage = WorkflowRunStorage()
_tasks: set[asyncio.Task[None]] = set()


def start_workflow_run(
    run_id: str,
    definition: dict[str, Any],
    input_text: str,
    resolve: AgentResolver,
) -> None:
    task = asyncio.create_task(
        _drive(run_id, definition, input_text, resolve),
        name=f"workflow-run-{run_id}",
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _consume(
    run_id: str,
    definition: dict[str, Any],
    input_text: str,
    resolve: AgentResolver,
) -> str | None:
    final_output: str | None = None
    async for event in run_workflow(definition, input_text, resolve):
        if event.get("type") == "heartbeat":
            await _storage.touch(run_id)
            continue
        await _storage.append_event(run_id, event)
        if event.get("type") == "workflow_done":
            final_output = event.get("output")
    return final_output


async def _drive(
    run_id: str,
    definition: dict[str, Any],
    input_text: str,
    resolve: AgentResolver,
) -> None:
    execution: asyncio.Task[str | None] | None = None
    try:
        if not await _storage.mark_running(run_id):
            run = await _storage.get(run_id)
            if run and run["status"] == "cancelling":
                await _storage.append_event(run_id, {"type": "cancelled"})
                await _storage.set_status(run_id, "cancelled")
            return

        execution = asyncio.create_task(
            _consume(run_id, definition, input_text, resolve),
            name=f"workflow-run-body-{run_id}",
        )
        while not execution.done():
            try:
                await asyncio.wait_for(asyncio.shield(execution), timeout=1.0)
            except TimeoutError:
                run = await _storage.get(run_id)
                if not run or run["status"] == "cancelling":
                    execution.cancel()
                    break
        try:
            final_output = await execution
        except asyncio.CancelledError:
            await _storage.append_event(run_id, {"type": "cancelled"})
            await _storage.set_status(run_id, "cancelled")
            return
        await _storage.set_status(run_id, "completed", final_output=final_output)
        await _storage.purge()
    except asyncio.CancelledError:
        if execution and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        await _storage.set_status(
            run_id,
            "failed",
            error="Ejecución interrumpida porque el worker se detuvo",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        # Un nodo de workflow ejecuta lo que el usuario haya configurado, así
        # que aquí cae cualquier cosa. Se convierte en evento de fallo y se
        # persiste con el run marcado como 'failed': no se pierde nada.
        event = workflow_error_event(exc, context="workflow-run")
        message = str(event["message"])
        try:
            await _storage.append_event(run_id, event)
            await _storage.set_status(run_id, "failed", error=message)
        except Exception as persist_exc:  # noqa: BLE001
            # Último recurso: si ni siquiera se puede escribir el fallo en BD,
            # queda al menos en el log del proceso.
            flog.error(
                f"[workflow-run] No se pudo persistir el fallo de {run_id}: {persist_exc}"
            )


async def stop_workflow_runs() -> None:
    for task in tuple(_tasks):
        task.cancel()
    if _tasks:
        await asyncio.gather(*tuple(_tasks), return_exceptions=True)


async def workflow_run_maintenance_loop() -> None:
    """Reconcile dead workers and apply retention without client activity."""
    purge_tick = 0
    while True:
        await asyncio.sleep(30)
        await _storage.fail_stale()
        purge_tick += 1
        if purge_tick >= 120:  # hourly
            purge_tick = 0
            await _storage.purge()
