from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.storage.workflow_runs as workflow_runs_module
from app.storage.db import open_db
from app.storage.schema import SCHEMA_PG, SCHEMA_SQLITE
from app.storage.workflow_runs import WorkflowRunStorage


async def _create(storage: WorkflowRunStorage, user: str, suffix: str):
    return await storage.create(
        workflow_id=f"workflow-{suffix}",
        started_by=user,
        group_id="shared-group",
        workflow_name=f"Workflow {suffix}",
        definition={"nodes": [{"id": "agent-1"}]},
        agents=[{"id": "agent-1", "name": "Agent"}],
        input_text="hello",
    )


async def test_events_are_ordered_replayable_and_private():
    storage = WorkflowRunStorage()
    run = await _create(storage, "alice", "events")

    await storage.append_event(run["id"], {"type": "stage_started", "node_id": "a"})
    await storage.append_event(run["id"], {"type": "stage_done", "node_id": "a"})
    await storage.append_event(run["id"], {"type": "workflow_done", "output": "ok"})

    assert await storage.get_for_user(run["id"], "bob") is None
    replay = await storage.events_after(run["id"], 1)
    assert [event["sequence"] for event in replay] == [2, 3]
    assert [event["type"] for event in replay] == ["stage_done", "workflow_done"]


async def test_fail_stale_marks_only_expired_active_runs():
    storage = WorkflowRunStorage()
    stale = await _create(storage, "alice", "stale")
    healthy = await _create(storage, "alice", "healthy")
    await storage.mark_running(stale["id"])
    await storage.mark_running(healthy["id"])
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    async with open_db() as conn:
        await conn.execute(
            "UPDATE workflow_runs SET heartbeat_at=? WHERE id=?",
            (old, stale["id"]),
        )
        await conn.commit()

    assert await storage.fail_stale(seconds=120) == 1
    assert (await storage.get(stale["id"]))["status"] == "failed"
    assert (await storage.get(healthy["id"]))["status"] == "running"


async def test_purge_limits_terminal_history_and_never_removes_active(monkeypatch):
    monkeypatch.setattr(workflow_runs_module, "HISTORY_LIMIT", 2)
    storage = WorkflowRunStorage()
    terminal_ids = []
    for index in range(3):
        run = await _create(storage, "alice", str(index))
        terminal_ids.append(run["id"])
        await storage.set_status(run["id"], "completed")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE workflow_runs SET created_at=? WHERE id=?",
                (f"2026-01-0{index + 1}T00:00:00+00:00", run["id"]),
            )
            await conn.commit()
    active = await _create(storage, "alice", "active")
    await storage.mark_running(active["id"])

    assert await storage.purge() == 1
    assert await storage.get(terminal_ids[0]) is None
    assert await storage.get(active["id"]) is not None
    assert len(await storage.list_for_user("alice")) == 3


def test_workflow_run_tables_exist_in_sqlite_and_postgresql_schemas():
    for schema in (SCHEMA_SQLITE, SCHEMA_PG):
        assert "CREATE TABLE IF NOT EXISTS workflow_runs" in schema
        assert "CREATE TABLE IF NOT EXISTS workflow_run_events" in schema
