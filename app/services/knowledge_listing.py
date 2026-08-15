"""Orquestación paginada de Knowledge y shares mediante packs."""

from __future__ import annotations

from fastapi import Response

from app.api.routes.auth import GroupContext
from app.auth.auth import get_user_role
from app.errors import APIError
from app.pagination.http import publish_offset_page
from app.pagination.models import OffsetParams
from app.services.resource_visibility import build_permission_filter
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage


async def list_authenticated_knowledge(
    storage: KnowledgeStorage,
    *,
    ctx: GroupContext,
    owner_scope: str,
    type: str | None,
    page: OffsetParams,
    response: Response | None,
    requested_group_id: str | None,
) -> list[dict]:
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
    result = await storage.list_visible_page(
        user=ctx.user,
        owner_id=owner_id,
        type=type,
        page=page,
        permission_filter=permission_filter,
        requested_group_id=requested_group_id,
    )
    publish_offset_page(response, result)
    return list(result.items)
