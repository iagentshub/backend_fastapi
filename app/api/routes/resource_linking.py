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
from app.storage.db import IS_PG, open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import (
    AgentStorage,
    ConnectionStorage,
    SkillStorage,
)
from app.storage.workflows import WorkflowStorage
from app.utils.generators import generate_id

router = APIRouter(tags=["resource-linking"])


class _AgentTryBody(BaseModel):
    connection_id: str
    message: str

@router.post("/api/knowledge/{source_id}/link")
async def link_knowledge(
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    knowledge = KnowledgeStorage(_cfg.DB_FILE)
    source = await knowledge.get(source_id)
    if not source:
        raise APIError(404, "not_found", "Knowledge no encontrado", extra={"resource": "knowledge"})

    source_owner = source.get("owner_id") or ""
    await _assert_public("knowledge", source_id)
    if source_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario de este recurso")

    link_title = source.get("title", source_id)
    result = await knowledge.save(
        type=source.get("type", "url"),
        title=link_title,
        source=source.get("source", ""),
        content=source.get("content", ""),
        owner_id=username,
    )
    new_id = result["id"]

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?) "
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
    agents = AgentStorage(_cfg.AGENTS_DIR)
    source = await agents.get(source_id, scope)
    if not source:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})

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
    link_labels = list(link_payload.get("labels") or ["private"])
    for ol in ("fork", "linked", "public"):
        if ol in link_labels:
            link_labels.remove(ol)
    link_labels.append("linked")
    link_payload["labels"] = link_labels
    if username != source_owner:
        # Skills/conocimiento privados y memoria se heredan junto con el agente
        link_payload["skills"] = await _inherit_resource_ids(
            link_payload.get("skills") or [], "skill", username
        )
        link_payload["knowledge"] = await _inherit_resource_ids(
            link_payload.get("knowledge") or [], "knowledge", username
        )
        link_payload["memory_file"] = None

    try:
        result = await agents.save(link_payload, "private", owner_id=username)
    except ValueError as exc:
        raise APIError(422, "agent_save_invalid", str(exc)) from exc

    if username != source_owner:
        await _inherit_agent_memory(source, source_owner, result["id"], username)

    new_id = result["id"]
    link_name = result["name"]
    link_tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?, ?) "
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


@router.post("/api/skills/{scope}/{source_id}/link")
async def link_skill(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    skills = SkillStorage(_cfg.SKILLS_DIR)
    source = await skills.get(scope, source_id)
    if not source:
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})

    source_owner = source.get("owner_id") or ""
    if scope != "public":
        await _assert_public("skill", source_id)
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

    try:
        result = await skills.save("private", link_payload, owner_id=username)
    except ValueError as exc:
        raise APIError(422, "skill_save_invalid", str(exc)) from exc

    new_id = result["id"]
    link_name = result["name"]
    link_tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    "skill",
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
                    "skill",
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
    return {"ok": True, "skill_id": new_id, "name": link_name}


async def _duplicate_workflow(source_id: str, username: str) -> Dict[str, Any]:
    """Clona una orquestación pública para el usuario.

    Igual que link_agent: clona el workflow con id propio, hereda (clonando si
    son privados) los agentes que usa junto con sus skills/knowledge, y
    registra la copia en resource_social solo para trazar el origen (no queda
    pública ella misma)."""
    workflows = WorkflowStorage()
    source = await workflows.get_any(source_id)
    if not source:
        raise APIError(
            404, "not_found", "Orquestación no encontrada", extra={"resource": "workflow"}
        )
    source_owner = source.get("owner_id") or ""
    await _assert_public("workflow", source_id)
    if source_owner == username:
        raise APIError(400, "already_owner", "Ya eres el propietario de este recurso")

    nodes = source.get("definition", {}).get("nodes", [])
    if username != source_owner:
        nodes = await _inherit_workflow_agents(nodes, username)
    definition = {"nodes": nodes, "edges": source.get("definition", {}).get("edges", [])}

    labels = [
        lbl for lbl in (source.get("labels") or ["private"])
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
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?, ?) "
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
    agents = AgentStorage(_cfg.AGENTS_DIR)
    local = await agents.get(agent_id, "private")
    if not local or local.get("owner_id") != username:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("agent", agent_id),
        )

    if not row or not row[0]:
        raise APIError(400, "agent_not_linked", "El agente no tiene enlace a un original")

    original_id = row[0]
    original = await agents.get(original_id)
    if not original:
        raise APIError(
            404, "not_found", "El agente original ya no existe", extra={"resource": "agent"}
        )
    try:
        if original.get("scope") != "public":
            await _assert_public("agent", original_id)
    except HTTPException:
        raise APIError(403, "forbidden", "El agente original ya no es accesible")

    sync_fields = {
        k: v
        for k, v in original.items()
        if k not in ("id", "scope", "owner_id", "created_at", "name")
    }
    updated = {**local, **sync_fields}
    await agents.save(updated, "private", owner_id=local.get("owner_id"))

    return {"ok": True, "synced_from": original_id}


@router.post("/api/skills/private/{skill_id}/sync")
async def sync_linked_skill(
    skill_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    skills = SkillStorage(_cfg.SKILLS_DIR)
    local = await skills.get("private", skill_id)
    if not local or local.get("owner_id") != username:
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("skill", skill_id),
        )

    if not row or not row[0]:
        raise APIError(400, "skill_not_linked", "La skill no tiene enlace a un original")

    original_id = row[0]
    original = await skills.get_any(original_id)
    if not original:
        raise APIError(
            404, "not_found", "La skill original ya no existe", extra={"resource": "skill"}
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
            404, "not_found", "Agente no encontrado o no es público", extra={"resource": "agent"}
        )

    trial_missing_deps: str = row["trial_missing_deps"] or "warn"

    # Step 2: Get agent config from DB storage
    agents = AgentStorage(_cfg.AGENTS_DIR)
    agent_data = await agents.get(agent_id, scope)
    if not agent_data:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})

    # Step 3: Resolve caller's connection (group first, then personal fallback)
    conn_storage = ConnectionStorage(_cfg.DB_FILE)
    conn_data = await conn_storage.get(body.connection_id, ctx.group_id)
    if conn_data is None and ctx.group_id != ctx.user:
        conn_data = await conn_storage.get(body.connection_id, ctx.user)
    if conn_data is None:
        raise APIError(400, "not_found", "Connection no encontrada", extra={"resource": "connection"})

    # Step 4: Filter skills based on trial_missing_deps policy
    skills_storage = SkillStorage(_cfg.SKILLS_DIR)
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
            except Exception:
                pass

    return {"reply": "".join(reply_parts), "warnings": warnings}
