"""Enlazar un recurso público: se copia al espacio de quien lo enlaza.

Enlazar no es referenciar: el recurso ajeno se duplica con `linked_to_id`
apuntando al original, y sus dependencias privadas se heredan
(`services/resource_inheritance.py`). Una orquestación además duplica los
agentes que usa.
"""


from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import Depends

from app.api.routes.auth import require_auth
from app.api.routes.resource_linking._router import router
from app.api.routes.resource_linking._shared import (
    _agents_store,
    _knowledge_store,
    _prompts_store,
    _skills_store,
    _tools_store,
    _workflows_store,
)
from app.errors import APIError
from app.services.resource_inheritance import (
    _inherit_agent_memory,
    _inherit_resource_ids,
    _inherit_workflow_agents,
)
from app.services.social_catalog import _assert_public
from app.sql import sql

# db se importa como MÓDULO a propósito: IS_PG debe leerse en el momento de
# la llamada. Traerlo por valor congela el dialecto en el arranque y el
# monkeypatch de los tests no llega — ver
# tests/storage/test_is_pg_en_tiempo_de_llamada.py.
from app.storage import db as _db
from app.storage.db import open_db
from app.utils.generators import generate_id


@router.post("/api/knowledge/{source_id}/link")
async def link_knowledge(
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    knowledge = _knowledge_store
    source = await knowledge.get(source_id)
    if not source:
        raise APIError(
            404, "not_found", "Knowledge no encontrado", extra={"resource": "knowledge"}
        )

    source_owner = source.get("owner_id") or ""
    await _assert_public("knowledge", source_id)
    if source_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario de este recurso")

    link_title = source.get("title", source_id)
    link_labels = [
        label
        for label in (source.get("labels") or ["private"])
        if label not in ("fork", "linked", "public")
    ]
    link_labels.append("linked")
    result = await knowledge.save(
        type=source.get("type", "url"),
        title=link_title,
        source=source.get("source", ""),
        content=source.get("content", ""),
        owner_id=username,
        labels=link_labels,
    )
    new_id = result["id"]

    async with open_db() as conn:
        if _db.IS_PG:
            await conn.execute(
                sql("queries/resource_linking:link_social_pg"),
                (
                    "knowledge",
                    new_id,
                    username,
                    link_title,
                    source.get("source", ""),
                    source_owner,
                    source_id,
                ),
            )
        else:
            await conn.execute(
                sql("queries/resource_linking:link_social_sqlite"),
                (
                    "knowledge",
                    new_id,
                    username,
                    link_title,
                    source.get("source", ""),
                    source_owner,
                    source_id,
                ),
            )
        await conn.commit()
    return {"ok": True, "knowledge_id": new_id, "name": link_title}

@router.post("/api/agents/{scope}/{source_id}/link")
async def link_agent(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    agents = _agents_store
    source = await agents.get(source_id, scope)
    if not source:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )

    source_owner = source.get("owner_id") or ""
    if scope != "public":
        await _assert_public("agent", source_id)
    if source_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario de este recurso")

    link_payload = {
        k: v
        for k, v in source.items()
        if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
    }
    # id propio (no derivado del nombre): al no renombrar la copia, un id basado en
    # el nombre colisionaría con el original (misma slug) y una lectura por id sin
    # filtro de owner devolvería cualquiera de los dos.
    link_payload["id"] = generate_id()
    # La conexión es configuración privada del propietario y nunca forma parte
    # del recurso publicado ni de una copia enlazada.
    link_payload["connection_id"] = None
    link_payload["op_connections"] = []
    link_labels = list(link_payload.get("labels") or ["private"])
    for ol in ("fork", "linked", "public"):
        if ol in link_labels:
            link_labels.remove(ol)
    link_labels.append("linked")
    link_payload["labels"] = link_labels
    raw_selection = source.get("public_dependencies")
    selected = (
        {str(value) for value in raw_selection if value}
        if raw_selection is not None
        else None
    )
    for kind, field_name in (
        ("skill", "skills"),
        ("knowledge", "knowledge"),
        ("prompt", "prompts"),
        ("tool", "tools"),
    ):
        link_payload[field_name] = [
            resource_id
            for resource_id in (link_payload.get(field_name) or [])
            if selected is None or f"{kind}:{resource_id}" in selected
        ]

    memory_file = str(source.get("memory_file") or "").strip()
    copy_memory = bool(
        source.get("use_memory")
        and memory_file
        and (selected is None or f"memory:{memory_file}" in selected)
    )
    if copy_memory:
        link_payload["memory_file"] = f"{link_payload['id']}.md"
    else:
        link_payload["use_memory"] = False
        link_payload["memory_file"] = None

    if username != source_owner:
        # Solo se heredan las dependencias que el autor publicó con el agente.
        link_payload["skills"] = await _inherit_resource_ids(
            link_payload.get("skills") or [], "skill", username
        )
        link_payload["knowledge"] = await _inherit_resource_ids(
            link_payload.get("knowledge") or [], "knowledge", username
        )
        link_payload["prompts"] = await _inherit_resource_ids(
            link_payload.get("prompts") or [], "prompt", username
        )
        link_payload["tools"] = await _inherit_resource_ids(
            link_payload.get("tools") or [], "tool", username
        )

    try:
        result = await agents.save(link_payload, "private", owner_id=username)
    except ValueError as exc:
        raise APIError(422, "agent_save_invalid", str(exc)) from exc

    if username != source_owner and copy_memory:
        await _inherit_agent_memory(source, source_owner, result["id"], username)

    new_id = result["id"]
    link_name = result["name"]
    link_tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if _db.IS_PG:
            await conn.execute(
                sql("queries/resource_linking:link_social_tags_pg"),
                (
                    "agent",
                    new_id,
                    username,
                    link_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    link_tags,
                ),
            )
        else:
            await conn.execute(
                sql("queries/resource_linking:link_social_tags_sqlite"),
                (
                    "agent",
                    new_id,
                    username,
                    link_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    link_tags,
                ),
            )
        await conn.commit()
    return {"ok": True, "agent_id": new_id, "name": link_name}

# Columnas de la fila que link_* escribe en resource_social. La lista estaba
# escrita dos veces por tipo (una por dialecto) y tres veces en total.
_SOCIAL_LINK_COLS = (
    "resource_type, resource_id, owner, name, description, is_public, category, "
    "trial_missing_deps, linked_to_user, linked_to_id, tags"
)

# (storage, mensaje de "no existe"). link_skill y link_tool eran un 97,4%
# textualmente idénticas y link_prompt un 90,9%: el mismo cuerpo copiado tres
# veces, con el riesgo habitual de corregir una copia y olvidar las otras dos.
#
# Las respuestas SÍ conservan su clave por tipo (skill_id, tool_id, prompt_id):
# unificarlas rompería a los clientes.
_ENLAZABLES = {
    "skill": (lambda: _skills_store, "Skill no encontrada"),
    "tool": (lambda: _tools_store, "Tool no encontrada"),
    "prompt": (lambda: _prompts_store, "Prompt no encontrado"),
}

async def _insert_ignore(conn, tabla: str, columnas: str, valores_sql: str, params: tuple) -> None:
    """Upsert dialectal en un solo sitio.

    `_db.IS_PG` se lee EN LA LLAMADA, no se importa por valor: los tests lo
    reescriben con monkeypatch y `tests/storage/test_is_pg_en_tiempo_de_llamada.py`
    vigila justo eso.
    """
    # `sentencia` y no `sql`: ese nombre es el del cargador de app/sql, que este
    # módulo también usa.
    if _db.IS_PG:
        sentencia = f"INSERT INTO {tabla} ({columnas}) VALUES ({valores_sql}) ON CONFLICT DO NOTHING"
    else:
        sentencia = f"INSERT OR IGNORE INTO {tabla} ({columnas}) VALUES ({valores_sql})"
    await conn.execute(sentencia, params)

async def _link_resource(tipo: str, scope: str, source_id: str, username: str) -> Dict[str, Any]:
    """Cuerpo único de link para skill, tool y prompt."""
    factory, no_existe = _ENLAZABLES[tipo]
    storage = factory()
    source = await storage.get(scope, source_id)
    if not source:
        raise APIError(404, "not_found", no_existe, extra={"resource": tipo})

    source_owner = source.get("owner_id") or ""
    if scope != "public":
        await _assert_public(tipo, source_id)
    if source_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario de este recurso")

    link_payload = {
        k: v for k, v in source.items() if k not in ("id", "scope", "owner_id")
    }
    # id propio (no derivado del nombre): al no renombrar la copia, un id basado en
    # el nombre colisionaría con el original (misma slug) y una lectura por id sin
    # filtro de owner devolvería cualquiera de los dos.
    link_payload["id"] = generate_id()
    link_labels = list(link_payload.get("labels") or ["private"])
    for ol in ("fork", "linked", "public"):
        if ol in link_labels:
            link_labels.remove(ol)
    link_labels.append("linked")
    link_payload["labels"] = link_labels
    if tipo == "prompt":
        # El alias de origen puede colisionar con uno ya existente del destino —
        # la copia debe crearse siempre, nunca fallar por esto ni tocar la fila
        # del propietario original: se sufija (alias-2, alias-3…) si hace falta.
        link_payload["alias"] = await storage.unique_alias(
            username, str(link_payload.get("alias") or "")
        )

    try:
        result = await storage.save("private", link_payload, owner_id=username)
    except ValueError as exc:
        raise APIError(422, f"{tipo}_save_invalid", str(exc)) from exc

    new_id = result["id"]
    link_name = result["name"]

    async with open_db() as conn:
        await _insert_ignore(
            conn,
            "resource_social",
            _SOCIAL_LINK_COLS,
            "?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?",
            (
                tipo,
                new_id,
                username,
                link_name,
                source.get("description", ""),
                source_owner,
                source_id,
                "[]",
            ),
        )
        await conn.commit()
    return {"ok": True, f"{tipo}_id": new_id, "name": link_name}

@router.post("/api/skills/{scope}/{source_id}/link")
async def link_skill(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    return await _link_resource("skill", scope, source_id, username)

@router.post("/api/tools/{scope}/{source_id}/link")
async def link_tool(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    return await _link_resource("tool", scope, source_id, username)

@router.post("/api/prompts/{scope}/{source_id}/link")
async def link_prompt(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    return await _link_resource("prompt", scope, source_id, username)

async def _duplicate_workflow(source_id: str, username: str) -> Dict[str, Any]:
    """Clona una orquestación pública para el usuario.

    Igual que link_agent: clona el workflow con id propio, hereda (clonando si
    son privados) los agentes que usa junto con sus skills/knowledge, y
    registra la copia en resource_social solo para trazar el origen (no queda
    pública ella misma)."""
    workflows = _workflows_store
    source = await workflows.get_any(source_id)
    if not source:
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
    source_owner = source.get("owner_id") or ""
    await _assert_public("workflow", source_id)
    if source_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario de este recurso")

    nodes = source.get("definition", {}).get("nodes", [])
    if username != source_owner:
        nodes = await _inherit_workflow_agents(nodes, username)
    definition = {
        "nodes": nodes,
        "edges": source.get("definition", {}).get("edges", []),
    }

    labels = [
        lbl
        for lbl in (source.get("labels") or ["private"])
        if lbl not in ("linked", "fork", "public")
    ]
    labels.append("linked")

    result = await workflows.save(
        username,
        {
            "id": generate_id(),
            "name": source["name"],
            "description": source.get("description", ""),
            "definition": definition,
            "labels": labels,
        },
    )
    new_id = result["id"]
    tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if _db.IS_PG:
            await conn.execute(
                sql("queries/resource_linking:link_social_tags_pg"),
                (
                    "workflow",
                    new_id,
                    username,
                    result["name"],
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    tags,
                ),
            )
        else:
            await conn.execute(
                sql("queries/resource_linking:link_social_tags_sqlite"),
                (
                    "workflow",
                    new_id,
                    username,
                    result["name"],
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    tags,
                ),
            )
        await conn.commit()
    return {"ok": True, "workflow_id": new_id, "name": result["name"]}

@router.post("/api/workflows/{source_id}/link")
async def link_workflow(
    source_id: str, username: str = Depends(require_auth)
) -> Dict[str, Any]:
    return await _duplicate_workflow(source_id, username)
