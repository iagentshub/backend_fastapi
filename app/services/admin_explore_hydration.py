"""Cómo se rellena cada fila del inventario del panel.

La búsqueda decide **qué** identidades entran en la página —eso vive en
`admin_explore_cursor_listing`—; aquí se convierte cada identidad en la fila
completa que el cliente pinta. Separarlo es lo que mantiene los dos ficheros
por debajo del límite de tamaño del repo, y el corte cae donde ya estaba la
costura.

Tres cosas que este módulo arregló y no pueden volver:

- **`memory_files.content` no se proyecta.** Es la memoria de largo plazo de
  cada agente de cada usuario, texto libre sin cota, y de él solo se necesita
  el tamaño: traerlo entero para hacerle `len()` movía toda esa columna por el
  cable. Es la misma lección que dejó escrita la mudanza del avatar.
- **`users` llega con el `JOIN` de su foto.** `avatar_url` no es una columna
  —la imagen vive en `user_avatars`—, así que sin él la tarjeta de personas
  pinta la inicial y nunca la foto.
- **Los recuentos de un grupo se piden solo para los de la página.** La tarjeta
  los pinta y el inventario no los servía, así que salían todos a cero.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Sequence

from app.sql import sql
from app.storage.db import open_db

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



async def enriquecer_grupos(items: list[dict[str, Any]]) -> None:
    """Recuentos de la tarjeta de grupo, pedidos solo para los de la página.

    La tarjeta pinta miembros, conexiones, agentes, conocimiento y tokens. El
    inventario nunca los sirvió —los daba el listado por tipo, que el panel
    dejó de usar—, así que salían todos a cero sin que nada fallara.
    """

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
