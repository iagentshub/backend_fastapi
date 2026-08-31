"""Orquestación común de listados scoped autenticados."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.pagination.metrics import increment
from app.pagination.models import CursorPage, CursorParams
from app.pagination.total import ExactTotalTimeout
from app.storage.groups import GroupStorage


class VisiblePageStorage(Protocol):
    def list_visible_page(
        self,
        *,
        user: str,
        active_group_id: str,
        scope: str,
        page: CursorParams,
        requested_group_id: str | None = None,
    ) -> Awaitable[CursorPage[dict[str, Any]]]: ...


async def load_authenticated_scoped_resource_page(
    storage: VisiblePageStorage,
    *,
    ctx: GroupContext,
    scope: str,
    page: CursorParams,
    requested_group_id: str | None,
    mark_origin: Callable[[dict[str, Any], str, str], None],
) -> CursorPage[dict[str, Any]]:
    """Lista una página visible y conserva los metadatos de origen del dominio."""

    if requested_group_id is not None:
        role = await get_user_role(ctx.user)
        groups = GroupStorage()
        if role != "admin" and not await groups.can_access(
            requested_group_id, ctx.user
        ):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
    try:
        result = await storage.list_visible_page(
            user=ctx.user,
            active_group_id=ctx.group_id,
            scope=scope,
            page=page,
            requested_group_id=requested_group_id,
        )
    except ExactTotalTimeout as exc:
        raise APIError(
            503,
            "pagination_total_timeout",
            "El total exacto no estuvo disponible dentro del tiempo permitido",
        ) from exc
    except ValueError as exc:
        increment(type(storage).__name__, "invalid_cursors")
        raise APIError(422, "invalid_cursor", "Cursor no válido") from exc
    for item in result.items:
        mark_origin(item, ctx.user, ctx.group_id)
    return result
