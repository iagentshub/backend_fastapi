"""Link / fork / sync / try routes: copiar un recurso público a tu propia
cuenta, mantenerlo sincronizado con el original, o probarlo sin copiarlo.

Extraído de social.py (ver admin.py para el motivo completo del split de
auth.py — mismo problema de tamaño/responsabilidades mezcladas).
"""

from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import app.config.data as _cfg
from app.api.routes.auth import GroupContext, require_auth, require_group
from app.api.routes.social import (
    _PUBLIC_VAL,
    _assert_public,
    _inherit_agent_memory,
    _inherit_resource_ids,
    _inherit_workflow_agents,
)
from app.errors import APIError
from app.services.chat import stream_chat

# db se importa como MÓDULO a propósito: IS_PG debe leerse en el momento de
# la llamada. Traerlo por valor congela el dialecto en el arranque y el
# monkeypatch de los tests no llega — ver
# tests/storage/test_is_pg_en_tiempo_de_llamada.py.
from app.storage import db as _db
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage
from app.utils import flog
from app.utils.generators import generate_id

router = APIRouter(tags=["resource-linking"])

# Singletons de módulo: construir un storage dentro de cada handler reejecutaba
# su migración legacy (el flag era de instancia), y con ella un SELECT COUNT(*)
# por petición. Mismo patrón que agents.py y connections.py.
_agents_store = AgentStorage(_cfg.AGENTS_DIR)
_skills_store = SkillStorage(_cfg.SKILLS_DIR)
_prompts_store = PromptStorage()
_tools_store = ToolStorage()
_knowledge_store = KnowledgeStorage()
_workflows_store = WorkflowStorage()
_conns_store = ConnectionStorage()


class _AgentTryBody(BaseModel):
    connection_id: str
    message: str


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
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?) "
                "ON CONFLICT DO NOTHING",
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
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?)",
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
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
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
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
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
    if _db.IS_PG:
        sql = f"INSERT INTO {tabla} ({columnas}) VALUES ({valores_sql}) ON CONFLICT DO NOTHING"
    else:
        sql = f"INSERT OR IGNORE INTO {tabla} ({columnas}) VALUES ({valores_sql})"
    await conn.execute(sql, params)


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
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
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
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
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


@router.post("/api/agents/private/{agent_id}/sync")
async def sync_linked_agent(
    agent_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    agents = _agents_store
    local = await agents.get(agent_id, "private")
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("agent", agent_id),
        )

    if not row or not row[0]:
        raise APIError(
            400, "agent_not_linked", "El agente no tiene enlace a un original"
        )

    original_id = row[0]
    original = await agents.get(original_id)
    if not original:
        raise APIError(
            404,
            "not_found",
            "El agente original ya no existe",
            extra={"resource": "agent"},
        )
    try:
        if original.get("scope") != "public":
            await _assert_public("agent", original_id)
    except HTTPException:
        raise APIError(403, "forbidden", "El agente original ya no es accesible")

    sync_fields = {
        k: v
        for k, v in original.items()
        if k
        not in (
            "id",
            "scope",
            "owner_id",
            "created_at",
            "name",
            "connection_id",
            "op_connections",
            "skills",
            "knowledge",
            "prompts",
            "tools",
            "use_memory",
            "memory_file",
            "public_dependencies",
        )
    }
    updated = {
        **local,
        **sync_fields,
        "connection_id": None,
        "op_connections": [],
    }
    await agents.save(updated, "private", owner_id=local.get("owner_id"))

    return {"ok": True, "synced_from": original_id}


@router.post("/api/skills/private/{skill_id}/sync")
async def sync_linked_skill(
    skill_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    skills = _skills_store
    local = await skills.get("private", skill_id)
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Skill no encontrada", extra={"resource": "skill"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("skill", skill_id),
        )

    if not row or not row[0]:
        raise APIError(
            400, "skill_not_linked", "La skill no tiene enlace a un original"
        )

    original_id = row[0]
    original = await skills.get_any(original_id)
    if not original:
        raise APIError(
            404,
            "not_found",
            "La skill original ya no existe",
            extra={"resource": "skill"},
        )
    try:
        if original.get("scope") != "public":
            await _assert_public("skill", original_id)
    except HTTPException:
        raise APIError(403, "forbidden", "La skill original ya no es accesible")

    sync_fields = {
        k: v
        for k, v in original.items()
        if k not in ("id", "scope", "owner_id", "name")
    }
    updated = {**local, **sync_fields}
    await skills.save("private", updated, owner_id=local.get("owner_id"))

    return {"ok": True, "synced_from": original_id}


@router.post("/api/tools/private/{tool_id}/sync")
async def sync_linked_tool(
    tool_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    tools = _tools_store
    local = await tools.get("private", tool_id)
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("tool", tool_id),
        )

    if not row or not row[0]:
        raise APIError(
            400, "tool_not_linked", "La tool no tiene enlace a un original"
        )

    original_id = row[0]
    original = await tools.get_any(original_id)
    if not original:
        raise APIError(
            404,
            "not_found",
            "La tool original ya no existe",
            extra={"resource": "tool"},
        )
    try:
        if original.get("scope") != "public":
            await _assert_public("tool", original_id)
    except HTTPException:
        raise APIError(403, "forbidden", "La tool original ya no es accesible")

    # El dict que devuelve get_any() ya trae binary_b64/binary_filename/
    # binary_size/binary_uploaded_at (mismo mecanismo que content) — se
    # sincronizan directo, sin llamada aparte al binario.
    sync_fields = {
        k: v
        for k, v in original.items()
        if k not in ("id", "scope", "owner_id", "name")
    }
    updated = {**local, **sync_fields}
    await tools.save("private", updated, owner_id=local.get("owner_id"))

    return {"ok": True, "synced_from": original_id}


@router.post("/api/prompts/private/{prompt_id}/sync")
async def sync_linked_prompt(
    prompt_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    prompts = _prompts_store
    local = await prompts.get("private", prompt_id)
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("prompt", prompt_id),
        )

    if not row or not row[0]:
        raise APIError(
            400, "prompt_not_linked", "El prompt no tiene enlace a un original"
        )

    original_id = row[0]
    original = await prompts.get_any(original_id)
    if not original:
        raise APIError(
            404,
            "not_found",
            "El prompt original ya no existe",
            extra={"resource": "prompt"},
        )
    try:
        if original.get("scope") != "public":
            await _assert_public("prompt", original_id)
    except HTTPException:
        raise APIError(403, "forbidden", "El prompt original ya no es accesible")

    # alias se excluye: sincronizar contenido no debe pisar el alias local
    # redefinido por el usuario tras el enlace.
    sync_fields = {
        k: v
        for k, v in original.items()
        if k not in ("id", "scope", "owner_id", "name", "alias")
    }
    updated = {**local, **sync_fields}
    await prompts.save("private", updated, owner_id=local.get("owner_id"))

    return {"ok": True, "synced_from": original_id}


@router.post("/api/agents/{scope}/{agent_id}/try")
async def try_agent(
    scope: str,
    agent_id: str,
    body: _AgentTryBody,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    """Prueba un agente público usando la connection propia del caller, sin guardar historial."""

    # Step 1: Validate the agent is public in resource_social
    async with open_db() as db:
        row = await db.fetchone(
            "SELECT trial_missing_deps FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND is_public=?",
            ("agent", agent_id, _PUBLIC_VAL),
        )
    if not row:
        raise APIError(
            404,
            "not_found",
            "Agente no encontrado o no es público",
            extra={"resource": "agent"},
        )

    trial_missing_deps: str = row["trial_missing_deps"] or "warn"

    # Step 2: Get agent config from DB storage
    agents = _agents_store
    agent_data = await agents.get(agent_id, scope)
    if not agent_data:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )

    # Step 3: Resolve caller's connection (group first, then personal fallback)
    conn_storage = _conns_store
    conn_data = await conn_storage.get(body.connection_id, ctx.group_id)
    if conn_data is None and ctx.group_id != ctx.user:
        conn_data = await conn_storage.get(body.connection_id, ctx.user)
    if conn_data is None:
        raise APIError(
            400,
            "not_found",
            "Connection no encontrada",
            extra={"resource": "connection"},
        )

    # Step 4: Filter skills based on trial_missing_deps policy
    skills_storage = _skills_store
    warnings: list[str] = []
    agent_skills: list[str] = list(agent_data.get("skills") or [])

    accessible: list[str] = []
    for skill_id in agent_skills:
        if await skills_storage.get("public", skill_id):
            accessible.append(skill_id)
            continue
        priv = await skills_storage.get("private", skill_id)
        if priv and priv.get("owner_id") == ctx.group_id:
            accessible.append(skill_id)
            continue
        if trial_missing_deps == "warn":
            warnings.append(skill_id)
    agent_data = {**agent_data, "skills": accessible}

    # Step 5: Stream chat and collect reply (no history saved)
    reply_parts: list[str] = []
    async for chunk in stream_chat(
        agent_data,
        conn_data,
        [{"role": "user", "content": body.message}],
        skills_storage,
        None,
        None,
    ):
        if chunk.startswith("data:"):
            try:
                ev = json.loads(chunk[5:].strip())
                if ev.get("type") == "chunk":
                    reply_parts.append(ev.get("content", ""))
            except (json.JSONDecodeError, AttributeError) as exc:
                flog.warning(
                    f"[resource-linking] Evento SSE inválido para {agent_id}: {exc}"
                )

    return {"reply": "".join(reply_parts), "warnings": warnings}
