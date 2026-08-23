"""Persisted workflow executions and their replayable event log."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.sql import sql
from app.storage.db import open_db
from app.storage.resource_executions import ResourceExecutionStorage
from app.utils import now_iso
from app.utils.generators import generate_id

ACTIVE_STATUSES = ("queued", "running", "cancelling")
TERMINAL_STATUSES = ("cancelled", "completed", "failed")
RETENTION_DAYS = max(1, int(os.getenv("GAIA_WORKFLOW_RUN_RETENTION_DAYS", "30")))
HISTORY_LIMIT = max(1, int(os.getenv("GAIA_WORKFLOW_RUN_HISTORY_LIMIT", "100")))
_resource_executions = ResourceExecutionStorage()


class WorkflowRunStorage:
    async def create(
        self,
        *,
        workflow_id: str,
        started_by: str,
        group_id: str,
        workflow_name: str,
        definition: dict[str, Any],
        agents: list[dict[str, Any]],
        input_text: str,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = now_iso()
        run_id = run_id or generate_id(24)
        total = len(definition.get("nodes") or [])
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/workflow_runs:insert_run"),
                    (
                        run_id,
                        workflow_id,
                        started_by,
                        group_id,
                        workflow_name,
                        json.dumps(definition, ensure_ascii=False),
                        json.dumps(agents, ensure_ascii=False),
                        input_text,
                        total,
                        now,
                        now,
                        now,
                    ),
                )
        return (await self.get_for_user(run_id, started_by)) or {}

    async def get(self, run_id: str) -> dict[str, Any] | None:
        async with open_db() as conn:
            row = await conn.fetchone(sql("queries/workflow_runs:get_run"), (run_id,))
        return self._decode(row) if row else None

    async def get_for_user(self, run_id: str, username: str) -> dict[str, Any] | None:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/workflow_runs:get_run_owned"),
                (run_id, username),
            )
        return self._decode(row) if row else None

    async def list_for_user(
        self, username: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with open_db() as conn:
            active = await conn.fetchall(
                sql("queries/workflow_runs:list_active_runs"),
                (username,),
            )
            history = await conn.fetchall(
                sql("queries/workflow_runs:list_finished_runs"),
                (username, min(max(limit, 1), HISTORY_LIMIT)),
            )
        return [self._decode(row, detail=False) for row in [*active, *history]]

    async def set_status(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
        final_output: str | None = None,
    ) -> None:
        now = now_iso()
        started_at = now if status == "running" else None
        finished_at = now if status in TERMINAL_STATUSES else None
        async with open_db() as conn:
            await conn.execute(
                sql("queries/workflow_runs:update_status"),
                (
                    status,
                    error,
                    final_output,
                    now,
                    now,
                    started_at,
                    finished_at,
                    run_id,
                ),
            )
            await conn.commit()

    async def mark_running(self, run_id: str) -> bool:
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/workflow_runs:mark_running"),
                (now, now, now, run_id),
            )
            await conn.commit()
        run = await self.get(run_id)
        return bool(run and run["status"] == "running")

    async def touch(self, run_id: str) -> None:
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/workflow_runs:heartbeat"),
                (now, now, run_id),
            )
            await conn.commit()

    async def append_event(self, run_id: str, event: dict[str, Any]) -> int:
        now = now_iso()
        event_type = str(event.get("type") or "")
        active_node = event.get("node_id") if event_type.endswith("_started") else None
        completed_delta = 1 if event_type in {"stage_done", "evaluation_done"} else 0
        async with open_db() as conn:
            async with conn.transaction():
                row = await conn.fetchone(
                    sql("queries/workflow_runs:progress_of"),
                    (run_id,),
                )
                if not row:
                    raise KeyError(run_id)
                sequence = int(row["last_sequence"]) + 1
                completed = min(
                    int(row["total_steps"]),
                    int(row["completed_steps"]) + completed_delta,
                )
                if event_type in {
                    "stage_done",
                    "evaluation_done",
                    "workflow_done",
                    "error",
                    "cancelled",
                }:
                    active_node = None
                await conn.execute(
                    sql("queries/workflow_runs:insert_event"),
                    (run_id, sequence, json.dumps(event, ensure_ascii=False), now),
                )
                await conn.execute(
                    sql("queries/workflow_runs:update_progress"),
                    (sequence, completed, active_node, now, now, run_id),
                )
        return sequence

    async def events_after(self, run_id: str, sequence: int) -> list[dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/workflow_runs:list_events_since"),
                (run_id, sequence),
            )
        result = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["sequence"] = int(row["sequence"])
            payload["recorded_at"] = row["created_at"]
            result.append(payload)
        return result

    async def request_cancel(self, run_id: str, username: str) -> dict[str, Any] | None:
        run = await self.get_for_user(run_id, username)
        if not run:
            return None
        if run["status"] in ("queued", "running"):
            await self.set_status(run_id, "cancelling")
        return await self.get_for_user(run_id, username)

    async def purge(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        ).isoformat()
        async with open_db() as conn:
            old_rows = await conn.fetchall(
                sql("queries/workflow_runs:finished_before"),
                (cutoff,),
            )
            users = await conn.fetchall(sql("queries/workflow_runs:distinct_users"))
            remove = {str(row["id"]) for row in old_rows}
            for user_row in users:
                rows = await conn.fetchall(
                    sql("queries/workflow_runs:finished_by_user"),
                    (user_row["started_by"],),
                )
                remove.update(str(row["id"]) for row in rows[HISTORY_LIMIT:])
            for run_id in remove:
                await conn.execute(
                    sql("queries/workflow_runs:delete_events"), (run_id,)
                )
                await conn.execute(sql("queries/workflow_runs:delete_run"), (run_id,))
            await conn.commit()
        return len(remove)

    async def fail_stale(self, seconds: int = 120) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        now = now_iso()
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/workflow_runs:stale_runs"),
                (cutoff,),
            )
            for row in rows:
                await conn.execute(
                    sql("queries/workflow_runs:mark_failed"),
                    (
                        "Ejecución interrumpida porque el worker dejó de responder",
                        now,
                        now,
                        row["id"],
                    ),
                )
            await conn.commit()
        for row in rows:
            await _resource_executions.safe_release_run(str(row["id"]))
        return len(rows)

    @staticmethod
    def _decode(row: Any, *, detail: bool = True) -> dict[str, Any]:
        item = dict(row)
        item["progress"] = {
            "completed": int(item.pop("completed_steps", 0)),
            "total": int(item.pop("total_steps", 0)),
            "active_node_id": item.pop("active_node_id", None),
        }
        item["last_sequence"] = int(item.get("last_sequence") or 0)
        if detail:
            item["definition"] = json.loads(item["definition"])
            item["agents"] = json.loads(item.get("agents") or "[]")
        else:
            item.pop("definition", None)
            item.pop("agents", None)
            item.pop("input", None)
        return item
