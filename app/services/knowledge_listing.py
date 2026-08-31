"""Orquestación paginada de Knowledge y shares mediante packs."""

from __future__ import annotations

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.pagination.metrics import increment
from app.pagination.models import CursorPage, CursorParams
from app.pagination.total import ExactTotalTimeout
from app.services.resource_visibility import build_permission_filter
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage


async def list_authenticated_knowledge_cursor(
    storage: KnowledgeStorage,
    *,
    ctx: GroupContext,
    owner_scope: str,
    type: str | None,
    page: CursorParams,
    requested_group_id: str | None,
) -> CursorPage[dict]:
    """Contrato v2 de Knowledge con la misma autorización que el listado v1."""

    groups = GroupStorage()
    role = await get_user_role(ctx.user)
    if requested_group_id is not None and role != "admin":
        if not await groups.can_access(requested_group_id, ctx.user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
    permission_group_id = requested_group_id or ctx.group_id
    permission_filter = None
    if permission_group_id != ctx.user and role != "admin":
        member = await groups.get_member(permission_group_id, ctx.user)
        permission_filter = build_permission_filter(
            member,
            alias="k",
            section="knowledge",
            action="view",
        )
    owner_id = ctx.user if owner_scope == "personal" else ctx.group_id
    try:
        return await storage.list_visible_cursor_page(
            user=ctx.user,
            owner_id=owner_id,
            type=type,
            page=page,
            permission_filter=permission_filter,
            requested_group_id=requested_group_id,
        )
    except ExactTotalTimeout as exc:
        raise APIError(
            503,
            "pagination_total_timeout",
            "El total exacto no estuvo disponible dentro del tiempo permitido",
            extra={"resource": "knowledge"},
        ) from exc
    except ValueError as exc:
        increment("knowledge", "invalid_cursors")
        raise APIError(422, "invalid_cursor", "Cursor no válido") from exc
