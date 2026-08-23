"""Leases distribuidos: una ejecución por usuario y recurso canónico."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config.resource_executions import (
    RESOURCE_EXECUTION_HEARTBEAT_SECONDS,
    RESOURCE_EXECUTION_STALE_SECONDS,
)
from app.sql import sql
from app.storage import db as _db
from app.storage.db import open_db
from app.utils import flog, now_iso
from app.utils.generators import generate_id


@dataclass(slots=True)
class ResourceExecutionLease:
    execution_key: str
    execution_id: str
    _last_heartbeat: float = field(default_factory=time.monotonic, repr=False)


class ResourceExecutionStorage:
    @staticmethod
    async def _canonical_identity(
        conn: Any, resource_type: str, resource_id: str, owner_hint: str
    ) -> tuple[str, str]:
        row = await conn.fetchone(
            sql("queries/resource_executions:canonical_ref"),
            (resource_type, resource_id, owner_hint),
        )
        if not row:
            return owner_hint, resource_id
        return (
            str(row["linked_to_user"] or row["owner"] or owner_hint),
            str(row["linked_to_id"] or resource_id),
        )

    async def acquire(
        self,
        *,
        resource_type: str,
        resource_id: str,
        resource_owner: str,
        started_by: str,
        run_id: str | None = None,
    ) -> ResourceExecutionLease | None:
        execution_id = generate_id(24)
        now = now_iso()
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=RESOURCE_EXECUTION_STALE_SECONDS)
        ).isoformat()
        async with open_db() as conn:
            async with conn.transaction(immediate=True):
                canonical_owner, canonical_id = await self._canonical_identity(
                    conn, resource_type, resource_id, resource_owner
                )
                raw_key = "\0".join(
                    (started_by, resource_type, canonical_owner, canonical_id)
                )
                execution_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
                await conn.execute(
                    sql("queries/resource_executions:purge_stale_conflict"),
                    (execution_key, cutoff),
                )
                await conn.execute(
                    sql(
                        "queries/resource_executions:insert_pg"
                        if _db.IS_PG
                        else "queries/resource_executions:insert_sqlite"
                    ),
                    (
                        execution_key,
                        execution_id,
                        resource_type,
                        canonical_owner,
                        canonical_id,
                        resource_id,
                        started_by,
                        run_id,
                        now,
                        now,
                    ),
                )
                inserted = await conn.fetchone(
                    sql("queries/resource_executions:get_by_execution_id"),
                    (execution_id,),
                )
        if not inserted:
            return None
        return ResourceExecutionLease(execution_key, execution_id)

    async def heartbeat(self, lease: ResourceExecutionLease) -> None:
        monotonic_now = time.monotonic()
        if (
            monotonic_now - lease._last_heartbeat
            < RESOURCE_EXECUTION_HEARTBEAT_SECONDS
        ):
            return
        # Se marca el intento antes de escribir: si la BD falla no queremos
        # convertir cada token del stream en un nuevo intento y un nuevo log.
        lease._last_heartbeat = monotonic_now
        try:
            async with open_db() as conn:
                await conn.execute(
                    sql("queries/resource_executions:touch"),
                    (now_iso(), lease.execution_key, lease.execution_id),
                )
                await conn.commit()
        except Exception as exc:  # noqa: BLE001
            flog.error(
                "[resource-execution] No se pudo renovar "
                f"{lease.execution_id}: {exc}"
            )

    async def release(self, lease: ResourceExecutionLease) -> None:
        async with open_db() as conn:
            await conn.execute(
                sql("queries/resource_executions:release"),
                (lease.execution_key, lease.execution_id),
            )
            await conn.commit()

    async def safe_release(self, lease: ResourceExecutionLease) -> None:
        try:
            await self.release(lease)
        except Exception as exc:  # noqa: BLE001
            flog.error(
                "[resource-execution] No se pudo liberar "
                f"{lease.execution_id}: {exc}"
            )

    async def release_run(self, run_id: str) -> None:
        async with open_db() as conn:
            await conn.execute(
                sql("queries/resource_executions:release_run"), (run_id,)
            )
            await conn.commit()

    async def safe_release_run(self, run_id: str) -> None:
        try:
            await self.release_run(run_id)
        except Exception as exc:  # noqa: BLE001
            flog.error(
                "[resource-execution] No se pudo liberar el run "
                f"{run_id}: {exc}"
            )

    async def list_for_user(
        self, principal_id: str, group_id: str
    ) -> list[dict[str, Any]]:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=RESOURCE_EXECUTION_STALE_SECONDS)
        ).isoformat()
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/resource_executions:list_for_user_with_aliases"),
                (principal_id, group_id, principal_id, cutoff),
            )
            executions: dict[str, dict[str, Any]] = {}
            for row in rows:
                execution_id = str(row["execution_id"])
                item = executions.setdefault(
                    execution_id,
                    {
                        "execution_id": row["execution_id"],
                        "resource_type": row["resource_type"],
                        "resource_id": row["local_resource_id"],
                        "resource_ids": {
                            str(row["local_resource_id"]),
                            str(row["canonical_resource_id"]),
                        },
                        "run_id": row["run_id"],
                        "status": "in_progress",
                        "started_at": row["created_at"],
                    },
                )
                if row["alias_resource_id"]:
                    item["resource_ids"].add(str(row["alias_resource_id"]))
        return [
            {**item, "resource_ids": sorted(item["resource_ids"])}
            for item in executions.values()
        ]

    async def purge_stale(self) -> None:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=RESOURCE_EXECUTION_STALE_SECONDS)
        ).isoformat()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/resource_executions:purge_stale"), (cutoff,)
            )
            await conn.commit()
