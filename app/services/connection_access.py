"""Resolución y autorización de conexiones fuera de la capa HTTP."""

from __future__ import annotations

from typing import Any, Dict, List

from app.auth.auth import get_user_role
from app.models.llm_orchestration import (
    orchestration_connection_id,
    orchestration_id_from_connection,
)
from app.storage.connection_storage import ConnectionStorage
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.llm_orchestration_bindings import LLMOrchestrationBindingStorage
from app.storage.llm_orchestrations import LLMOrchestrationStorage


class ConnectionAccessService:
    def __init__(self) -> None:
        self._connections = ConnectionStorage()
        self._orchestrations = LLMOrchestrationStorage()
        self._bindings = LLMOrchestrationBindingStorage()
        self._shares = GroupShareStorage()
        self._groups = GroupStorage()

    async def get_accessible(
        self, connection_id: str, user: str, group_id: str
    ) -> Dict[str, Any] | None:
        if orchestration_id_from_connection(connection_id):
            return await self._get_orchestration(connection_id, user, group_id)
        return await self._get_physical(connection_id, user, group_id)

    async def list_accessible(
        self, user: str, group_id: str, *, include_shared: bool = True
    ) -> List[Dict[str, Any]]:
        connections = await self._connections.list(group_id)
        for connection in connections:
            connection["owner_id"] = group_id
        if group_id != user:
            seen = {connection["id"] for connection in connections}
            for connection in await self._connections.list(user):
                if connection["id"] not in seen:
                    connection["owner_id"] = user
                    connection["_personal_key"] = True
                    connections.append(connection)
        if include_shared:
            shared_ids = set(
                await self._shares.get_group_shared_resource_ids(group_id, "connection")
            )
            own_ids = {connection["id"] for connection in connections}
            for connection_id in shared_ids - own_ids:
                owner_id = await self._connections.get_owner_id(connection_id)
                if not owner_id or not await self._groups.owner_is_active(owner_id):
                    continue
                connection = await self._connections.get(connection_id)
                if connection:
                    connection["owner_id"] = owner_id
                    connection["_shared"] = True
                    connections.append(connection)
        return connections

    async def _get_physical(
        self, connection_id: str, user: str, group_id: str
    ) -> Dict[str, Any] | None:
        connection = await self._connections.get(connection_id, group_id)
        if connection is not None:
            connection["owner_id"] = group_id
            return connection
        if group_id != user:
            connection = await self._connections.get(connection_id, user)
            if connection is not None:
                connection["owner_id"] = user
                connection["_personal_key"] = True
                return connection
        granted_ids = await self._shares.get_group_shared_resource_ids(
            group_id, "connection"
        )
        if connection_id not in granted_ids:
            return None
        owner_id = await self._connections.get_owner_id(connection_id)
        if not owner_id or not await self._groups.owner_is_active(owner_id):
            return None
        connection = await self._connections.get(connection_id, None)
        if connection is not None:
            connection["owner_id"] = owner_id
            connection["_shared"] = True
        return connection

    @staticmethod
    def virtual_connection(
        item: Dict[str, Any], *, shared: bool = False, personal: bool = False
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": orchestration_connection_id(str(item["id"])),
            "name": str(item.get("name") or item["id"]),
            "description": str(item.get("description") or ""),
            "type": "llm_orchestration",
            "category": "llm",
            "model": "balanced" if item.get("mode") == "balanced" else "stack",
            "owner_id": item.get("owner_id"),
            "is_active": bool(item.get("is_active", True)),
            "is_virtual": True,
            "read_only": True,
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
        if shared:
            result["_shared"] = True
        if personal:
            result["_personal_key"] = True
        return result

    async def _get_orchestration(
        self, connection_id: str, user: str, group_id: str
    ) -> Dict[str, Any] | None:
        orchestration_id = orchestration_id_from_connection(connection_id)
        if not orchestration_id:
            return None
        item = await self._orchestrations.get_any(orchestration_id)
        if not item or not item.get("is_active", True):
            return None
        role = await get_user_role(user)
        shared_ids = await self._shares.get_group_shared_resource_ids(
            group_id, "llm_orchestration"
        )
        if (
            role != "admin"
            and item.get("owner_id") not in {user, group_id}
            and orchestration_id not in shared_ids
        ):
            return None
        effective_item = item
        if orchestration_id in shared_ids and item.get("owner_id") not in {user, group_id}:
            binding = await self._bindings.get(orchestration_id, user)
            if not binding:
                return None
            effective_item = {
                **item,
                "candidates": binding["candidates"],
                "router_connection_id": binding.get("router_connection_id"),
            }
        target_ids = {
            str(candidate.get("connection_id") or "")
            for candidate in effective_item.get("candidates") or []
        }
        router_id = str(effective_item.get("router_connection_id") or "")
        if router_id:
            target_ids.add(router_id)
        resolved: Dict[str, Dict[str, Any]] = {}
        for target_id in target_ids - {""}:
            if orchestration_id_from_connection(target_id):
                continue
            target = (
                await self._connections.get(target_id, None)
                if role == "admin"
                else await self._get_physical(target_id, user, group_id)
            )
            if target and target.get("is_active", True):
                resolved[target_id] = target
        if set(resolved) != target_ids - {""}:
            return None
        result = self.virtual_connection(
            item,
            shared=orchestration_id in shared_ids,
            personal=item.get("owner_id") == user and group_id != user,
        )
        result["_llm_orchestration"] = effective_item
        result["_connections"] = resolved
        return result


connection_access = ConnectionAccessService()
