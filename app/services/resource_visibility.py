"""Visibilidad común de listados sin cargar catálogos completos en Python."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from app.storage._storage_helpers import _PUBLIC_OWNER
from app.storage.db import AsyncConn


@dataclass(frozen=True, slots=True)
class VisibilityFilter:
    sql: str
    params: tuple[Any, ...]


def build_permission_filter(
    member: dict[str, Any] | None,
    *,
    alias: str,
    section: str,
    action: str,
) -> VisibilityFilter:
    """Traduce las excepciones de permisos del miembro a un predicado SQL."""

    if member is None:
        return VisibilityFilter("0 = 1", ())
    if member.get("role") in ("owner", "admin"):
        return VisibilityFilter("1 = 1", ())
    try:
        permissions = json.loads(member.get("permissions") or "{}")
    except (TypeError, ValueError):
        permissions = {}
    config = permissions.get(section) or {}
    default = bool(config.get("default", True))
    item_config = config.get("items") or {}
    explicit = {
        str(resource_id): bool(actions[action])
        for resource_id, actions in item_config.items()
        if isinstance(actions, dict) and action in actions
    }
    selected = [
        resource_id
        for resource_id, allowed in explicit.items()
        if allowed is not default
    ]
    if not selected:
        return VisibilityFilter("1 = 1" if default else "0 = 1", ())
    placeholders = ",".join("?" for _ in selected)
    operator = "NOT IN" if default else "IN"
    return VisibilityFilter(
        f"{alias}.id {operator} ({placeholders})",
        tuple(selected),
    )


def build_visibility_filter(
    *,
    alias: str,
    user: str,
    active_group_id: str,
    resource_type: str,
    requested_group_id: str | None = None,
    include_public: bool = True,
) -> VisibilityFilter:
    """Construye el predicado común de owner/public/shares.

    Los nombres de alias y tipo proceden exclusivamente de especificaciones
    internas. Todos los valores de sesión siguen parametrizados.
    """

    if requested_group_id is not None:
        return VisibilityFilter(
            sql=(
                "EXISTS (SELECT 1 FROM resource_group_shares visible_share "
                f"WHERE visible_share.resource_id = {alias}.id "
                "AND visible_share.resource_type = ? "
                "AND visible_share.group_id = ?) "
                "AND NOT EXISTS (SELECT 1 FROM groups inactive_owner "
                f"WHERE inactive_owner.id = {alias}.owner_id "
                "AND inactive_owner.is_active = 0)"
            ),
            params=(resource_type, requested_group_id),
        )

    public_sql = (
        f"{alias}.scope = 'public' OR {alias}.owner_id = ? OR "
        if include_public
        else ""
    )
    public_params: tuple[Any, ...] = (_PUBLIC_OWNER,) if include_public else ()
    return VisibilityFilter(
        sql=(
            "("
            f"{public_sql}{alias}.owner_id = ? OR {alias}.owner_id = ? "
            "OR (EXISTS ("
            "SELECT 1 FROM resource_group_shares visible_share "
            "JOIN group_members visible_member "
            "ON visible_member.group_id = visible_share.group_id "
            "JOIN groups visible_group ON visible_group.id = visible_share.group_id "
            f"WHERE visible_share.resource_id = {alias}.id "
            "AND visible_share.resource_type = ? "
            "AND visible_member.username = ? AND visible_group.is_active = 1"
            ") AND NOT EXISTS (SELECT 1 FROM groups inactive_owner "
            f"WHERE inactive_owner.id = {alias}.owner_id "
            "AND inactive_owner.is_active = 0))"
            ")"
        ),
        params=(*public_params, user, active_group_id, resource_type, user),
    )


async def annotate_shared_items(
    conn: AsyncConn,
    items: Iterable[dict[str, Any]],
    *,
    user: str,
    active_group_id: str,
    resource_type: str,
    requested_group_id: str | None = None,
) -> None:
    """Añade metadatos de share a una página con una sola consulta acotada."""

    candidates = [
        item
        for item in items
        if requested_group_id is not None
        or (
            item.get("scope") != "public"
            and item.get("owner_id") not in (None, user, active_group_id)
        )
    ]
    if not candidates:
        return
    ids = [str(item["id"]) for item in candidates]
    placeholders = ",".join("?" for _ in ids)
    params: list[Any] = [resource_type, *ids]
    group_sql = ""
    if requested_group_id is not None:
        group_sql = "AND visible_share.group_id = ? "
        params.append(requested_group_id)
    else:
        group_sql = (
            "AND EXISTS (SELECT 1 FROM group_members visible_member "
            "JOIN groups visible_group ON visible_group.id = visible_member.group_id "
            "WHERE visible_member.group_id = visible_share.group_id "
            "AND visible_member.username = ? AND visible_group.is_active = 1) "
        )
        params.append(user)
    rows = await conn.fetchall(
        "SELECT visible_share.resource_id, MIN(visible_share.group_id) AS group_id "
        "FROM resource_group_shares visible_share "
        "WHERE visible_share.resource_type = ? "
        f"AND visible_share.resource_id IN ({placeholders}) "
        f"{group_sql}GROUP BY visible_share.resource_id",
        tuple(params),
    )
    groups = {str(row[0]): str(row[1]) for row in rows}
    for item in candidates:
        group_id = groups.get(str(item["id"]))
        if group_id is not None:
            item["_shared"] = True
            item["_group_id"] = group_id
