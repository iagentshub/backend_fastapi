"""Inventario administrativo unificado paginado antes de hidratar recursos."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Sequence

from app.errors import APIError
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
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


_TABLES = {
    "user": "users",
    "group": "groups",
    "agent": "agents",
    "connection": "connections",
    "knowledge": "knowledge_items",
    "workflow": "agent_workflows",
    "llm_orchestration": "llm_orchestrations",
    "skill": "skills",
    "prompt": "prompts",
    "tool": "tools",
    "memory": "memory_files",
}

_COMPOSITE_TYPES = {
    "agent",
    "workflow",
    "llm_orchestration",
    "skill",
    "prompt",
    "tool",
    "memory",
}

_PRIVATE_COLUMNS = {
    "password_hash",
    "verification_token",
    "reset_token",
    "reset_token_expires",
    "deletion_token",
    "content",
    "binary_b64",
    "data",
    "definition",
}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _public_row(
    resource_type: str,
    row: Any,
    owner_username: str,
) -> dict[str, Any]:
    raw = dict(row)
    data = _json_object(raw.get("data"))
    definition = _json_object(raw.get("definition"))
    if resource_type == "connection":
        for secret in ("api_key", "token", "password", "secret"):
            data.pop(secret, None)
    item = dict(data)
    item.update(
        {key: value for key, value in raw.items() if key not in _PRIVATE_COLUMNS}
    )
    item["resource_type"] = resource_type
    if resource_type == "group":
        item["owner_id"] = raw.get("created_by")
        item["created_by_username"] = owner_username
    else:
        item["owner_username"] = owner_username
    if resource_type == "workflow":
        item["steps"] = len(definition.get("nodes") or [])
    elif resource_type == "llm_orchestration":
        item["candidate_count"] = len(definition.get("candidates") or [])
    elif resource_type == "group":
        item["status"] = "active" if item.get("is_active") else "disabled"
    elif resource_type == "user":
        # El checksum llega del JOIN y aquí se convierte en la URL, que es lo
        # que el cliente sabe pintar. Mismo criterio que `decode_user_row`.
        from app.storage import avatars

        item["avatar_url"] = avatars.public_url(
            str(item.get("username") or ""), item.pop("avatar_checksum", None)
        )
    elif resource_type == "memory":
        item["filename"] = raw.get("id")
        item["id"] = f"{raw.get('owner_id')}::{raw.get('id')}"
        item["size"] = int(raw.get("content_size") or 0)
    for boolean in ("is_active", "is_verified"):
        if boolean in item:
            item[boolean] = bool(item[boolean])
    return item


def _proyeccion(resource_type: str, table: str) -> str:
    """`SELECT *` sirve para casi todo, pero no para estos dos.

    `memory_files.content` es la memoria de largo plazo de cada agente de cada
    usuario —texto libre, sin cota— y aquí solo se necesita su tamaño: traerlo
    entero para hacerle `len()` es la misma pérdida que ya costó una vez, y la
    que este listado seguía pagando después de que se corrigiera en su gemelo.

    `users.avatar_url` no es una columna: la foto vive en `user_avatars` desde
    que se sacó de `users`, así que sin el `JOIN` el panel de personas pinta la
    inicial y nunca la foto.
    """
    if table == "memory_files":
        return "id, owner_id, LENGTH(content) AS content_size, updated_at"
    if table == "users":
        return "u.*, a.checksum AS avatar_checksum"
    return "*"


def _join(resource_type: str) -> str:
    return (
        "u LEFT JOIN user_avatars a ON a.owner_id = u.id "
        if resource_type == "user"
        else ""
    )


async def _load_type(
    resource_type: str, identities: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    table = _TABLES[resource_type]
    clauses: list[str] = []
    params: list[str] = []
    identity_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for identity in identities:
        item_id = str(identity["item_id"])
        owner_id = str(identity["owner_id"])
        storage_id = item_id.split("::", 1)[1] if resource_type == "memory" else item_id
        if resource_type in _COMPOSITE_TYPES:
            clauses.append("(id=? AND owner_id=?)")
            params.extend((storage_id, owner_id))
        elif resource_type == "user":
            clauses.append("u.id=?")
            params.append(storage_id)
        else:
            clauses.append("id=?")
            params.append(storage_id)
        identity_by_key[(storage_id, owner_id)] = identity
    async with open_db() as conn:
        rows = await conn.fetchall(
            f"SELECT {_proyeccion(resource_type, table)} FROM {table} "
            f"{_join(resource_type)}WHERE " + " OR ".join(clauses),
            tuple(params),
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        raw = dict(row)
        owner_id = str(raw.get("owner_id") or raw.get("created_by") or raw.get("id"))
        identity = identity_by_key.get((str(raw.get("id")), owner_id))
        if identity is None:
            identity = next(
                (
                    value
                    for (storage_id, _), value in identity_by_key.items()
                    if storage_id == str(raw.get("id"))
                ),
                None,
            )
        if identity is not None:
            result.append(
                _public_row(
                    resource_type,
                    row,
                    str(identity.get("owner_username") or owner_id),
                )
            )
    return result


async def _hydrate(identities: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    types = list(dict.fromkeys(str(item["resource_type"]) for item in identities))
    by_type = {
        resource_type: [
            identity
            for identity in identities
            if identity["resource_type"] == resource_type
        ]
        for resource_type in types
    }
    loaded = await asyncio.gather(
        *(_load_type(resource_type, by_type[resource_type]) for resource_type in types)
    )
    for resource_type, values in zip(types, loaded, strict=True):
        if resource_type == "group":
            await enriquecer_grupos(values)
    catalogs: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for resource_type, values in zip(types, loaded, strict=True):
        catalogs[resource_type] = {
            (str(value.get("id") or ""), str(value.get("owner_id") or "")): value
            for value in values
        }
    result: list[dict[str, Any]] = []
    for identity in identities:
        resource_type = str(identity["resource_type"])
        item_id = str(identity["item_id"])
        owner_id = str(identity["owner_id"])
        value = catalogs.get(resource_type, {}).get((item_id, owner_id))
        if value is None:
            value = next(
                (
                    candidate
                    for (candidate_id, _), candidate in catalogs.get(
                        resource_type, {}
                    ).items()
                    if candidate_id == item_id
                ),
                None,
            )
        if value is not None:
            result.append({**value, "resource_type": resource_type})
    return result


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


async def enriquecer_grupos(items: list[dict[str, Any]]) -> None:
    """Recuentos de la tarjeta de grupo, pedidos solo para los de la página.

    La tarjeta pinta miembros, conexiones, agentes, conocimiento y tokens. El
    inventario nunca los sirvió —los daba el listado por tipo, que el panel
    dejó de usar—, así que salían todos a cero sin que nada fallara.
    """
    from app.sql import sql

    ids = [str(item["id"]) for item in items if item.get("id")]
    if not ids:
        return
    marcadores = ",".join("?" for _ in ids)
    claves = tuple(ids)

    async def agregado(consulta: str) -> dict[str, Any]:
        async with open_db() as conn:
            filas = await conn.fetchall(consulta.replace("@ids@", marcadores), claves)
        return {fila[0]: fila for fila in filas}

    # Identificadores literales, no compuestos: `test_sql_en_ficheros` busca la
    # cadena en el código para saber qué secciones tienen consumidor, y una
    # armada con f-string la deja pareciendo muerta.
    miembros = await agregado(sql("queries/admin_resources:members_per_group"))
    conexiones = await agregado(sql("queries/admin_resources:connections_per_owner"))
    conocimiento = await agregado(sql("queries/admin_resources:knowledge_per_owner"))
    agentes = await agregado(sql("queries/admin_resources:agents_per_owner"))
    for item in items:
        clave = item["id"]
        item["member_count"] = (miembros.get(clave) or (None, 0))[1]
        fila = conexiones.get(clave)
        item["connections_count"] = fila[1] if fila else 0
        item["tokens_in"] = fila[2] if fila else 0
        item["tokens_out"] = fila[3] if fila else 0
        item["knowledge_count"] = (conocimiento.get(clave) or (None, 0))[1]
        item["agents_count"] = (agentes.get(clave) or (None, 0))[1]
