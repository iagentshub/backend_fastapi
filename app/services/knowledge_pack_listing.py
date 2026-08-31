"""Listado cursor autenticado de Knowledge Packs."""

from __future__ import annotations

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.pagination.metrics import increment
from app.pagination.models import CursorPage, CursorParams
from app.pagination.total import ExactTotalTimeout
from app.storage.groups import GroupStorage
from app.storage.knowledge_packs import KnowledgePackStorage


async def list_authenticated_knowledge_packs_cursor(
    storage: KnowledgePackStorage,
    *,
    ctx: GroupContext,
    page: CursorParams,
    requested_group_id: str | None,
) -> CursorPage[dict]:
    groups = GroupStorage()
    role = await get_user_role(ctx.user)
    if requested_group_id is not None and role != "admin":
        if not await groups.can_access(requested_group_id, ctx.user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
    try:
        return await storage.list_visible_cursor_page(
            ctx.group_id,
            ctx.user,
            page=page,
            requested_group_id=requested_group_id,
        )
    except ExactTotalTimeout as exc:
        raise APIError(
            503,
            "pagination_total_timeout",
            "El total exacto no estuvo disponible dentro del tiempo permitido",
            extra={"resource": "knowledge_pack"},
        ) from exc
    except ValueError as exc:
        increment("knowledge_pack", "invalid_cursors")
        raise APIError(422, "invalid_cursor", "Cursor no válido") from exc
