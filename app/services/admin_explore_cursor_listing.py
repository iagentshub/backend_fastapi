"""Inventario administrativo unificado paginado antes de hidratar recursos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.errors import APIError
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.services.admin_explore_hydration import _hydrate
from app.storage.composite_cursor_page import (
    KeysetColumn,
    SnapshotColumn,
    fetch_composite_cursor_page,
)
from app.storage.db import open_db
from app.utils import now_iso

ADMIN_EXPLORE_TYPES = (
    "user",
    "group",
    "agent",
    "connection",
    "knowledge",
    "workflow",
    "llm_orchestration",
    "skill",
    "prompt",
    "tool",
    "memory",
)


@dataclass(frozen=True, slots=True)
class AdminExploreCursorResult:
    page: CursorPage[dict[str, Any]]
    counts: dict[str, int] | None


def _catalog_union() -> str:
    selects = (
        # Sin los invitados: son cuentas efímeras que se borran solas, así que
        # en el inventario aparecerían y desaparecerían entre dos recargas.
        # Es la misma exclusión que ya llevan `auth:list_users`,
        # `admin_stats:user_counts` y el buscador de personas.
        "SELECT 'user' resource_type,id item_id,id owner_id,username "
        "owner_username,created_at sort_at,(username||' '||COALESCE(email,'')||' '||"
        "COALESCE(display_name,'')) search_text,role subtype,is_active active,"
        "is_verified verified FROM users WHERE role <> 'guest'",
        "SELECT 'group',g.id,g.created_by,COALESCE(u.username,g.created_by),"
        "g.created_at,(g.name||' '||COALESCE(u.username,'')),'',g.is_active,NULL "
        "FROM groups g LEFT JOIN users u ON u.id=g.created_by",
        "SELECT 'agent',s.id,s.owner_id,COALESCE(u.username,s.owner_id),s.updated_at,"
        "(s.name||' '||s.id||' '||s.data),'',s.is_active,NULL FROM agents s "
        "LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'connection',s.id,s.owner_id,COALESCE(u.username,s.owner_id),"
        "s.updated_at,(s.name||' '||s.id||' '||s.data),'',s.is_active,NULL "
        "FROM connections s LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'knowledge',s.id,s.owner_id,COALESCE(u.username,s.owner_id),"
        "s.updated_at,(s.title||' '||s.id||' '||s.source),s.type,s.is_active,NULL "
        "FROM knowledge_items s LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'workflow',s.id,s.owner_id,COALESCE(u.username,s.owner_id),"
        "s.updated_at,(s.name||' '||s.id||' '||s.description),'',s.is_active,NULL "
        "FROM agent_workflows s LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'llm_orchestration',s.id,s.owner_id,COALESCE(u.username,s.owner_id),"
        "s.updated_at,(s.name||' '||s.id||' '||s.description),'',s.is_active,NULL "
        "FROM llm_orchestrations s LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'skill',s.id,s.owner_id,COALESCE(u.username,s.owner_id),s.updated_at,"
        "(s.name||' '||s.id||' '||COALESCE(s.category,'')),COALESCE(s.category,''),"
        "s.is_active,NULL FROM skills s LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'prompt',s.id,s.owner_id,COALESCE(u.username,s.owner_id),s.updated_at,"
        "(s.name||' '||s.id||' '||s.alias),s.alias,s.is_active,NULL FROM prompts s "
        "LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'tool',s.id,s.owner_id,COALESCE(u.username,s.owner_id),s.updated_at,"
        "(s.name||' '||s.id||' '||s.language),s.language,s.is_active,NULL FROM tools s "
        "LEFT JOIN users u ON u.id=s.owner_id",
        "SELECT 'memory',(s.owner_id||'::'||s.id),s.owner_id,"
        "COALESCE(u.username,s.owner_id),s.updated_at,(s.id||' '||"
        "COALESCE(u.username,'')),'',CAST(1 AS SMALLINT),NULL FROM memory_files s "
        "LEFT JOIN users u ON u.id=s.owner_id",
    )
    return " UNION ALL ".join(selects)


def _filters(
    *,
    resource_types: Sequence[str],
    query: str | None,
    owner: str | None,
    role: str | None,
    active: bool | None,
    verified: bool | None,
    knowledge_type: str | None,
    include_types: bool,
) -> tuple[str, tuple[Any, ...]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if include_types and resource_types:
        clauses.append(
            "resource_type IN (" + ",".join("?" for _ in resource_types) + ")"
        )
        params.extend(resource_types)
    normalized_query = (query or "").strip().lower()
    if normalized_query:
        clauses.append("LOWER(search_text) LIKE ?")
        params.append(f"%{normalized_query}%")
    normalized_owner = (owner or "").strip().lower()
    if normalized_owner:
        clauses.append("LOWER(owner_username)=?")
        params.append(normalized_owner)
    if role:
        clauses.append("resource_type='user' AND subtype=?")
        params.append(role)
    if active is not None:
        clauses.append("active=?")
        params.append(int(active))
    if verified is not None:
        clauses.append("resource_type='user' AND verified=?")
        params.append(int(verified))
    if knowledge_type:
        clauses.append("resource_type='knowledge' AND subtype=?")
        params.append(knowledge_type)
    return " AND ".join(clauses), tuple(params)


async def list_admin_explore_cursor(
    *,
    admin: str,
    resource_types: Sequence[str],
    query: str | None,
    owner: str | None,
    role: str | None,
    active: bool | None,
    verified: bool | None,
    knowledge_type: str | None,
    include_counts: bool,
    page: CursorParams,
) -> AdminExploreCursorResult:
    invalid = set(resource_types).difference(ADMIN_EXPLORE_TYPES)
    if invalid:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no valido",
            extra={"field": "type"},
        )
    selected = tuple(resource_types or ADMIN_EXPLORE_TYPES)
    where, params = _filters(
        resource_types=selected,
        query=query,
        owner=owner,
        role=role,
        active=active,
        verified=verified,
        knowledge_type=knowledge_type,
        include_types=True,
    )
    union = _catalog_union()
    context = cursor_context_signature(
        {
            "resource": "admin_explore",
            "admin": admin,
            "types": selected,
            "q": query or "",
            "owner": owner or "",
            "role": role or "",
            "active": active,
            "verified": verified,
            "knowledge_type": knowledge_type or "",
            "consistent": page.consistent,
        }
    )
    async with open_db() as conn:
        raw_page = await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM ({union}) admin_catalog WHERE {where}",
            select_sql=(
                "SELECT resource_type,item_id,owner_id,owner_username,sort_at FROM "
                f"({union}) admin_catalog WHERE {where}"
            ),
            params=params,
            columns=(
                KeysetColumn("sort_at", "sort_at"),
                KeysetColumn("resource_type", "resource_type", descending=False),
                KeysetColumn("item_id", "item_id", descending=False),
                KeysetColumn("owner_id", "owner_id", descending=False),
            ),
            context=context,
            resource="admin_explore",
            page=page,
            decode=lambda row: dict(row),
            snapshot=SnapshotColumn("sort_at", now_iso()),
        )
        counts = None
        if include_counts:
            counts_where, counts_params = _filters(
                resource_types=selected,
                query=query,
                owner=owner,
                role=role,
                active=active,
                verified=verified,
                knowledge_type=knowledge_type,
                include_types=False,
            )
            rows = await conn.fetchall(
                "SELECT resource_type,COUNT(*) FROM "
                f"({union}) admin_catalog WHERE {counts_where} GROUP BY resource_type",
                counts_params,
            )
            counts = {resource_type: 0 for resource_type in ADMIN_EXPLORE_TYPES}
            counts.update({str(row[0]): int(row[1]) for row in rows})
    items = await _hydrate(raw_page.items)
    return AdminExploreCursorResult(
        page=CursorPage(
            items=items,
            next_cursor=raw_page.next_cursor,
            has_more=raw_page.has_more,
            total=raw_page.total,
            snapshot_at=raw_page.snapshot_at,
        ),
        counts=counts,
    )


