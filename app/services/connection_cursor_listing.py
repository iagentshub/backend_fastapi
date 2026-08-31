"""Catalogo cursor de conexiones persistidas y orquestaciones virtuales."""

from __future__ import annotations

import asyncio
from typing import Any

from app.auth.auth import get_user_role
from app.connections import get_provider
from app.models.llm_orchestration import orchestration_connection_id
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.services.connection_access import connection_access
from app.storage.composite_cursor_page import (
    KeysetColumn,
    SnapshotColumn,
    fetch_composite_cursor_page,
)
from app.storage.crypto import UNREADABLE_FLAG
from app.storage.db import open_db
from app.storage.groups import GroupStorage
from app.utils import flog, now_iso
from app.utils.origin import compute_origin_type

_groups = GroupStorage()


def _visible_sql(*, shared_only: bool) -> str:
    if shared_only:
        return (
            "EXISTS (SELECT 1 FROM resource_group_shares share "
            "WHERE share.resource_type=? AND share.resource_id=source.id "
            "AND share.group_id=?)"
        )
    return (
        "(source.owner_id IN (?,?) OR EXISTS (SELECT 1 FROM "
        "resource_group_shares share WHERE share.resource_type=? "
        "AND share.resource_id=source.id AND share.group_id=?))"
    )


def _visible_params(
    *, user: str, group_id: str, shared_only: bool, resource_type: str
) -> tuple[Any, ...]:
    if shared_only:
        return (resource_type, group_id)
    return (group_id, user, resource_type, group_id)


async def _model_variants(item: dict[str, Any]) -> list[dict[str, str]]:
    provider = get_provider(str(item.get("type") or ""))
    if (
        provider is None
        or not provider.expand_models_on_list
        or item.get(UNREADABLE_FLAG)
        or str(item.get("model") or "").strip()
    ):
        return []
    try:
        models = await asyncio.to_thread(provider.fetch_models, item)
    except (OSError, ValueError) as exc:
        flog.warning(
            f"[{item.get('type')}] Catalogo no obtenido de la conexion "
            f"{item.get('id')}: {exc}"
        )
        return []
    base_id = str(item.get("id") or "")
    return [
        {
            "id": f"{base_id}::{model}",
            "connection_id": base_id,
            "name": str(model),
            "model": str(model),
        }
        for model in dict.fromkeys(str(value) for value in models if str(value))
    ]


async def list_connections_cursor(
    *,
    user: str,
    group_id: str,
    requested_group_id: str | None,
    include_inactive: bool,
    include_models: bool,
    page: CursorParams,
) -> CursorPage[dict[str, Any]]:
    effective_group = requested_group_id or group_id
    shared_only = requested_group_id is not None
    connection_where = _visible_sql(shared_only=shared_only)
    orchestration_where = _visible_sql(shared_only=shared_only)
    active_connection = "" if include_inactive else " AND source.is_active=1"
    active_orchestration = "" if include_inactive else " AND source.is_active=1"
    connection_params = _visible_params(
        user=user,
        group_id=effective_group,
        shared_only=shared_only,
        resource_type="connection",
    )
    orchestration_params = _visible_params(
        user=user,
        group_id=effective_group,
        shared_only=shared_only,
        resource_type="llm_orchestration",
    )
    union_sql = (
        "SELECT 'connection' AS source_type,source.id AS item_id,source.owner_id,"
        "source.updated_at AS updated_at FROM connections source WHERE "
        f"{connection_where}{active_connection} UNION ALL "
        "SELECT 'llm_orchestration' AS source_type,source.id AS item_id,source.owner_id,"
        "source.updated_at AS updated_at FROM llm_orchestrations source WHERE "
        f"{orchestration_where}{active_orchestration}"
    )
    params = (*connection_params, *orchestration_params)
    context = cursor_context_signature(
        {
            "resource": "connections",
            "user": user,
            "group": effective_group,
            "shared_only": shared_only,
            "include_inactive": include_inactive,
            "include_models": include_models,
            "consistent": page.consistent,
        }
    )
    async with open_db() as conn:
        raw_page = await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM ({union_sql}) connection_catalog WHERE 1=1",
            select_sql=(
                "SELECT source_type,item_id,owner_id,updated_at FROM "
                f"({union_sql}) connection_catalog WHERE 1=1"
            ),
            params=params,
            columns=(
                KeysetColumn("updated_at", "updated_at"),
                KeysetColumn("source_type", "source_type", descending=False),
                KeysetColumn("item_id", "item_id", descending=False),
            ),
            context=context,
            resource="connection",
            page=page,
            decode=lambda row: dict(row),
            snapshot=SnapshotColumn("updated_at", now_iso()),
        )

    visible_identities = list(raw_page.items)
    role = await get_user_role(user)
    if not shared_only and effective_group != user and role != "admin":
        permitted = await _groups.permission_checker(effective_group, user)
        visible_identities = [
            identity
            for identity in visible_identities
            if identity.get("owner_id") == user
            or permitted(
                "connections",
                orchestration_connection_id(str(identity["item_id"]))
                if identity["source_type"] == "llm_orchestration"
                else str(identity["item_id"]),
                "direct",
            )
        ]

    items: list[dict[str, Any]] = []
    for identity in visible_identities:
        item_id = str(identity["item_id"])
        lookup_id = (
            orchestration_connection_id(item_id)
            if identity["source_type"] == "llm_orchestration"
            else item_id
        )
        item = await connection_access.get_accessible(
            lookup_id,
            user,
            effective_group,
            include_inactive=include_inactive,
        )
        if item is None:
            continue
        clean = {key: value for key, value in item.items() if key != "api_key"}
        clean["origin_type"] = compute_origin_type(item)
        if include_models:
            clean["model_variants"] = await _model_variants(item)
        items.append(clean)
    return CursorPage(
        items=items,
        next_cursor=raw_page.next_cursor,
        has_more=raw_page.has_more,
        total=raw_page.total,
        snapshot_at=raw_page.snapshot_at,
    )
