"""Orquestación común de listados scoped autenticados."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from fastapi import Response

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.pagination.http import publish_offset_page
from app.pagination.models import OffsetPage, OffsetParams
from app.storage.groups import GroupStorage


class VisiblePageStorage(Protocol):
    def list_visible_page(
        self,
        *,
        user: str,
        active_group_id: str,
        scope: str,
        page: OffsetParams,
        requested_group_id: str | None = None,
    ) -> Awaitable[OffsetPage[dict[str, Any]]]: ...


async def list_authenticated_scoped_resources(
    storage: VisiblePageStorage,
    *,
    ctx: GroupContext,
    scope: str,
    page: OffsetParams,
    response: Response | None,
    requested_group_id: str | None,
    mark_origin: Callable[[dict[str, Any], str, str], None],
) -> list[dict[str, Any]]:
    """Lista una página visible y conserva los metadatos de origen del dominio."""

    if requested_group_id is not None:
        role = await get_user_role(ctx.user)
        groups = GroupStorage()
        if role != "admin" and not await groups.can_access(
            requested_group_id, ctx.user
        ):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
    result = await storage.list_visible_page(
        user=ctx.user,
        active_group_id=ctx.group_id,
        scope=scope,
        page=page,
        requested_group_id=requested_group_id,
    )
    for item in result.items:
        mark_origin(item, ctx.user, ctx.group_id)
    publish_offset_page(response, result)
    return list(result.items)
