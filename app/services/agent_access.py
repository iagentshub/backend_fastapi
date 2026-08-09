"""Autorización de lectura de agentes fuera de la capa HTTP."""

from __future__ import annotations

import asyncio
from typing import Any

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage


class AgentAccessService:
    def __init__(self) -> None:
        self._shares = GroupShareStorage()
        self._groups = GroupStorage()

    async def assert_can_read(
        self, agent_id: str, agent: dict[str, Any], ctx: GroupContext
    ) -> None:
        if agent.get("scope") == "public":
            return
        user = ctx.user
        if agent.get("owner_id") in (user, ctx.group_id):
            return
        if await get_user_role(user) == "admin":
            return
        groups = await self._groups.list_for_user(user)
        shared = await asyncio.gather(
            *[
                self._shares.get_group_shared_resource_ids(group["id"], "agent")
                for group in groups
            ]
        )
        if any(agent_id in resource_ids for resource_ids in shared):
            return
        raise APIError(403, "forbidden", "No tienes acceso a este agente")


agent_access = AgentAccessService()
