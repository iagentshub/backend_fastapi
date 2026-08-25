"""Resolve Tool dependencies with authorization at the consumption boundary."""

from __future__ import annotations

from typing import Any, Iterable

from app.errors import APIError
from app.services.tool_policy import assert_tool_consumable, assert_tool_distributable
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.tool_storage import ToolStorage


def _normalized_tool_ids(tool_ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(raw_id) for raw_id in tool_ids if raw_id))


def _tools_by_id(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(str(row.get("id") or ""), []).append(row)
    return by_id


async def assert_tools_distributable_by_ids(
    tool_ids: Iterable[str],
    *,
    storage: ToolStorage,
    require_all: bool = True,
) -> list[dict[str, Any]]:
    """Preflight a Tool cascade before any destination is mutated."""
    ids = _normalized_tool_ids(tool_ids)
    if not ids:
        return []
    by_id = _tools_by_id(await storage.list_by_ids(ids))
    resolved: list[dict[str, Any]] = []
    for tool_id in ids:
        candidates = by_id.get(tool_id) or []
        if not candidates:
            if require_all:
                raise APIError(
                    409,
                    "not_found",
                    "Una Tool que se intenta distribuir ya no existe",
                    extra={"resource": "tool", "resource_id": tool_id},
                )
            continue
        tool = candidates[0]
        assert_tool_distributable(tool)
        resolved.append(tool)
    return resolved


async def resolve_accessible_tools(
    tool_ids: Iterable[str],
    *,
    user_id: str,
    group_id: str,
    is_admin: bool,
    storage: ToolStorage,
    shares: GroupShareStorage,
    groups: GroupStorage,
) -> list[dict[str, Any]]:
    """Resolve, authorize and de-duplicate Tools immediately before use."""
    ids = _normalized_tool_ids(tool_ids)
    if not ids:
        return []
    rows = await storage.list_by_ids(ids)
    by_id = _tools_by_id(rows)
    shared_map = (
        {}
        if is_admin
        else await shares.get_user_shared_resource_groups(user_id, "tool")
    )
    resolved: list[dict[str, Any]] = []
    for tool_id in ids:
        candidates = by_id.get(tool_id) or []
        if not candidates:
            raise APIError(
                409,
                "not_found",
                "Una Tool requerida ya no existe",
                extra={"resource": "tool", "resource_id": tool_id},
            )
        tool = next(
            (
                candidate
                for candidate in candidates
                if is_admin
                or candidate.get("scope") == "public"
                or candidate.get("owner_id") in {user_id, group_id}
                or tool_id in shared_map
            ),
            None,
        )
        if tool is None:
            raise APIError(
                403,
                "forbidden",
                "Ya no tienes acceso a una Tool requerida por el agente",
                extra={"resource": "tool", "resource_id": tool_id},
            )
        assert_tool_consumable(
            tool,
            user_id=user_id,
            group_id=group_id,
            is_admin=is_admin,
        )
        resolved.append(tool)
    return resolved
