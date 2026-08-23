from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.storage.db import open_db
from app.storage.resource_executions import ResourceExecutionStorage


async def _social(
    resource_id: str,
    owner: str,
    *,
    linked_to_user: str | None = None,
    linked_to_id: str | None = None,
) -> None:
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO resource_social "
            "(resource_type,resource_id,owner,name,linked_to_user,linked_to_id) "
            "VALUES (?,?,?,?,?,?)",
            (
                "agent",
                resource_id,
                owner,
                resource_id,
                linked_to_user,
                linked_to_id,
            ),
        )
        await conn.commit()


async def test_same_user_and_canonical_resource_has_one_execution():
    storage = ResourceExecutionStorage()
    first, second = await asyncio.gather(
        storage.acquire(
            resource_type="workflow",
            resource_id="wf-1",
            resource_owner="alice",
            started_by="alice",
        ),
        storage.acquire(
            resource_type="workflow",
            resource_id="wf-1",
            resource_owner="alice",
            started_by="alice",
        ),
    )

    assert sum(lease is not None for lease in (first, second)) == 1


async def test_linked_copy_and_public_source_share_canonical_lock():
    storage = ResourceExecutionStorage()
    await _social("public-agent", "publisher")
    await _social(
        "local-copy",
        "alice",
        linked_to_user="publisher",
        linked_to_id="public-agent",
    )

    source = await storage.acquire(
        resource_type="agent",
        resource_id="public-agent",
        resource_owner="publisher",
        started_by="alice",
    )
    linked = await storage.acquire(
        resource_type="agent",
        resource_id="local-copy",
        resource_owner="alice",
        started_by="alice",
    )

    assert source is not None
    assert linked is None
    state = await storage.list_for_user("alice", "alice")
    assert state[0]["status"] == "in_progress"
    assert set(state[0]["resource_ids"]) == {"public-agent", "local-copy"}


async def test_different_users_can_run_same_public_resource():
    storage = ResourceExecutionStorage()
    await _social("public-agent", "publisher")

    alice = await storage.acquire(
        resource_type="agent",
        resource_id="public-agent",
        resource_owner="publisher",
        started_by="alice",
    )
    bob = await storage.acquire(
        resource_type="agent",
        resource_id="public-agent",
        resource_owner="publisher",
        started_by="bob",
    )

    assert alice is not None
    assert bob is not None
    await storage.release(alice)
    again = await storage.acquire(
        resource_type="agent",
        resource_id="public-agent",
        resource_owner="publisher",
        started_by="alice",
    )
    assert again is not None


async def test_listing_ignores_stale_lease_without_writing_and_acquire_reclaims_it():
    storage = ResourceExecutionStorage()
    stale = await storage.acquire(
        resource_type="workflow",
        resource_id="wf-stale",
        resource_owner="alice",
        started_by="alice",
    )
    assert stale is not None
    old_heartbeat = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    async with open_db() as conn:
        await conn.execute(
            "UPDATE resource_executions SET heartbeat_at=? WHERE execution_id=?",
            (old_heartbeat, stale.execution_id),
        )
        await conn.commit()

    assert await storage.list_for_user("alice", "alice") == []
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT COUNT(*) AS total FROM resource_executions WHERE execution_id=?",
            (stale.execution_id,),
        )
    assert row["total"] == 1

    replacement = await storage.acquire(
        resource_type="workflow",
        resource_id="wf-stale",
        resource_owner="alice",
        started_by="alice",
    )
    assert replacement is not None
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT COUNT(*) AS total FROM resource_executions"
        )
    assert row["total"] == 1


async def test_heartbeat_is_throttled(monkeypatch):
    import app.storage.resource_executions as executions_module

    storage = ResourceExecutionStorage()
    lease = await storage.acquire(
        resource_type="agent",
        resource_id="agent-heartbeat",
        resource_owner="alice",
        started_by="alice",
    )
    assert lease is not None
    async with open_db() as conn:
        await conn.execute(
            "UPDATE resource_executions SET heartbeat_at=? WHERE execution_id=?",
            ("2000-01-01T00:00:00+00:00", lease.execution_id),
        )
        await conn.commit()

    lease._last_heartbeat = 100.0
    monkeypatch.setattr(executions_module.time, "monotonic", lambda: 105.0)
    await storage.heartbeat(lease)
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT heartbeat_at FROM resource_executions WHERE execution_id=?",
            (lease.execution_id,),
        )
    assert row["heartbeat_at"] == "2000-01-01T00:00:00+00:00"

    monkeypatch.setattr(executions_module.time, "monotonic", lambda: 1_000.0)
    await storage.heartbeat(lease)
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT heartbeat_at FROM resource_executions WHERE execution_id=?",
            (lease.execution_id,),
        )
    assert row["heartbeat_at"] != "2000-01-01T00:00:00+00:00"


async def test_safe_release_does_not_hide_the_original_result(monkeypatch):
    storage = ResourceExecutionStorage()
    lease = await storage.acquire(
        resource_type="agent",
        resource_id="agent-release",
        resource_owner="alice",
        started_by="alice",
    )
    assert lease is not None
    release = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(storage, "release", release)

    await storage.safe_release(lease)

    release.assert_awaited_once_with(lease)
