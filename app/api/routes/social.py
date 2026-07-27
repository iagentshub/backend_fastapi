"""Rutas del catálogo social: visibilidad pública, exploración y stars."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import app.config.data as _cfg
from app.api.routes.auth import WorkspaceContext, require_auth, require_workspace
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.services.chat import stream_chat
from app.storage.db import IS_PG, open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import AgentStorage, ConnectionStorage, MemoryStorage, SkillStorage
from app.storage.workflows import WorkflowStorage

router = APIRouter(tags=["social"])

# A4: tipos de recurso válidos para star/unstar y endpoints sociales
_VALID_SOCIAL_RESOURCE_TYPES: frozenset[str] = frozenset({"agent", "skill", "knowledge", "workflow"})

# N2: rate limiting para endpoints sociales (star, follow)
_social_limiter = RateLimiter(calls=30, window=60)


async def _assert_public(resource_type: str, source_id: str) -> None:
    """Enlazar solo está disponible para contenido público del marketplace."""
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT 1 FROM resource_social WHERE resource_type=? AND resource_id=? AND is_public=?",
            (resource_type, source_id, _PUBLIC_VAL),
        )
    if not row:
        raise APIError(403, "forbidden", "No tienes acceso a este recurso")


_inherit_skills_store = SkillStorage(_cfg.SKILLS_DIR)
_inherit_knowledge_store = KnowledgeStorage(_cfg.DB_FILE)
_inherit_memory_store = MemoryStorage(_cfg.MEMORY_DIR)


async def _inherit_resource_ids(
    ids: List[str], resource_type: str, target_owner_id: str
) -> List[str]:
    """Al enlazar un agente, sus skills/conocimiento privados se clonan junto
    con él (heredados) para que sigan siendo accesibles desde el nuevo dueño. Los
    públicos, o los que ya pertenecen al destino, se referencian tal cual (sin clonar)."""
    new_ids: List[str] = []
    for rid in ids:
        if resource_type == "skill":
            item = await _inherit_skills_store.get_any(rid)
        else:
            item = await _inherit_knowledge_store.get(rid)
        if not item:
            continue
        if item.get("owner_id") == target_owner_id or item.get("scope") == "public":
            new_ids.append(rid)
            continue
        if resource_type == "skill":
            # id propio (no derivado del nombre) para no colisionar con una skill
            # homónima de otro owner — GET /api/skills/{scope}/{id} no filtra por
            # owner_id, así que dos ids iguales de dueños distintos son ambiguos.
            clone = {k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")}
            clone["id"] = uuid4().hex[:12]
            saved = await _inherit_skills_store.save("private", clone, owner_id=target_owner_id)
        else:
            saved = await _inherit_knowledge_store.save(
                type=item.get("type", "url"),
                title=item.get("title", rid),
                source=item.get("source", ""),
                content=item.get("content", ""),
                owner_id=target_owner_id,
            )
        new_ids.append(saved["id"])
    return new_ids


async def _inherit_agent_memory(
    source_agent: Dict[str, Any],
    source_owner: str,
    new_agent_id: str,
    target_owner_id: str,
) -> None:
    """Copia el contenido de memoria del agente original al nuevo, si usa memoria."""
    if not source_agent.get("use_memory"):
        return
    mem_name = source_agent.get("memory_file") or f"{source_agent.get('id')}.md"
    content = await _inherit_memory_store.get(mem_name, source_owner)
    if content:
        await _inherit_memory_store.save(f"{new_agent_id}.md", content, owner_id=target_owner_id)


async def _inherit_workflow_agents(
    nodes: List[Dict[str, Any]], target_owner_id: str
) -> List[Dict[str, Any]]:
    """Al enlazar una orquestación, clona (o referencia) los agentes que usa,
    igual que _inherit_resource_ids hace con skills/knowledge de un agente."""
    agents_storage = AgentStorage(_cfg.AGENTS_DIR)
    id_map: Dict[str, str] = {}
    new_nodes: List[Dict[str, Any]] = []
    for node in nodes:
        old_agent_id = str(node.get("agent_id") or "")
        if old_agent_id in id_map:
            new_agent_id = id_map[old_agent_id]
        else:
            agent = await agents_storage.get(old_agent_id)
            if not agent:
                new_agent_id = old_agent_id
            elif agent.get("owner_id") == target_owner_id or agent.get("scope") == "public":
                new_agent_id = old_agent_id
            else:
                clone_payload = {
                    k: v
                    for k, v in agent.items()
                    if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
                }
                clone_payload["id"] = uuid4().hex[:12]
                clone_payload["skills"] = await _inherit_resource_ids(
                    clone_payload.get("skills") or [], "skill", target_owner_id
                )
                clone_payload["knowledge"] = await _inherit_resource_ids(
                    clone_payload.get("knowledge") or [], "knowledge", target_owner_id
                )
                clone_payload["memory_file"] = None
                saved = await agents_storage.save(
                    clone_payload, "private", owner_id=target_owner_id
                )
                await _inherit_agent_memory(
                    agent, str(agent.get("owner_id") or ""), saved["id"], target_owner_id
                )
                new_agent_id = saved["id"]
            id_map[old_agent_id] = new_agent_id
        new_nodes.append({**node, "agent_id": new_agent_id})
    return new_nodes


async def _publish_skill_cascade(
    skill_id: str, username: str, owner_ids: set[str]
) -> None:
    skill_storage = SkillStorage(_cfg.SKILLS_DIR)
    skill = await skill_storage.get_any(skill_id)
    if not skill or skill.get("owner_id") not in owner_ids:
        return
    labels = list(skill.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        await skill_storage.save(
            "private", {**skill, "labels": labels}, owner_id=skill["owner_id"]
        )
    async with open_db() as conn:
        await _upsert_social(
            conn,
            "skill",
            skill_id,
            username,
            skill.get("name", skill_id),
            skill.get("description", ""),
            "Other",
            "warn",
            json.dumps(skill.get("tags") or []),
            1,
            json.dumps(labels),
        )
        await conn.commit()


async def _publish_knowledge_item_cascade(
    know_id: str, username: str, owner_ids: set[str]
) -> None:
    """El conocimiento se publica a nivel de carpeta, no de item individual —
    localiza la carpeta que contiene este item y la publica si es del mismo dueño."""
    async with open_db() as conn:
        item_owner_row = await conn.fetchone(
            "SELECT owner_id FROM knowledge_items WHERE id=?", (know_id,)
        )
        if not item_owner_row or item_owner_row[0] not in owner_ids:
            return
        folder_row = await conn.fetchone(
            "SELECT folder_id FROM resource_folder_items "
            "WHERE resource_type='knowledge' AND resource_id=?",
            (know_id,),
        )
        folder_id = folder_row[0] if folder_row else None
        if not folder_id:
            return
        folder = await conn.fetchone(
            "SELECT id, name, owner_id, is_public FROM resource_folders WHERE id=?",
            (folder_id,),
        )
        if not folder or folder["owner_id"] not in owner_ids:
            return
        if folder["is_public"]:
            return
        await _upsert_social(
            conn,
            "knowledge",
            folder_id,
            username,
            folder["name"],
            "",
            "Other",
            "warn",
            "[]",
            1,
            '["private"]',
        )
        await conn.execute(
            "UPDATE resource_folders SET is_public=1 WHERE id=?", (folder_id,)
        )
        await conn.commit()


async def _cascade_publish_agent(
    agent: Dict[str, Any], username: str, workspace_id: str = ""
) -> None:
    """Al publicar un agente, publica en cascada sus skills y conocimiento propios."""
    owner_ids = {username, workspace_id} - {""}
    for skill_id in agent.get("skills") or []:
        await _publish_skill_cascade(skill_id, username, owner_ids)
    for know_id in agent.get("knowledge") or []:
        await _publish_knowledge_item_cascade(know_id, username, owner_ids)


async def _cascade_publish_workflow(
    workflow: Dict[str, Any], username: str, workspace_id: str = ""
) -> None:
    """Al publicar una orquestación, publica en cascada los agentes propios que usa
    (y, para cada uno, sus skills/conocimiento — ver _cascade_publish_agent)."""
    owner_ids = {str(workflow.get("owner_id") or ""), username, workspace_id} - {""}
    agents_storage = AgentStorage(_cfg.AGENTS_DIR)
    seen: set[str] = set()
    for node in workflow.get("definition", {}).get("nodes", []):
        agent_id = str(node.get("agent_id") or "")
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        agent = await agents_storage.get(agent_id)
        if not agent or agent.get("owner_id") not in owner_ids:
            continue
        labels = list(agent.get("labels") or ["private"])
        if "public" not in labels:
            labels.append("public")
            agent = await agents_storage.save(
                {**agent, "labels": labels}, agent.get("scope", "private"), owner_id=agent["owner_id"]
            )
        async with open_db() as conn:
            await _upsert_social(
                conn,
                "agent",
                agent_id,
                username,
                agent.get("name", agent_id),
                agent.get("description", ""),
                "Other",
                "warn",
                json.dumps(agent.get("tags") or []),
                1,
                json.dumps(labels),
            )
            await conn.commit()
        await _cascade_publish_agent(agent, username, workspace_id)

CATEGORIES = [
    "Coding",
    "Writing",
    "Research",
    "Data",
    "DevOps",
    "Support",
    "Education",
    "Productivity",
    "Marketing",
    "Finance",
    "Other",
]

_PUBLIC_VAL = True if IS_PG else 1


def _check_category(cat: str) -> None:
    if cat not in CATEGORIES:
        raise APIError(
            422,
            "invalid_field",
            f"Categoría inválida. Opciones: {CATEGORIES}",
            extra={"field": "category"},
        )


async def _upsert_social(
    conn: Any,
    resource_type: str,
    resource_id: str,
    owner: str,
    name: str,
    description: str,
    category: str,
    trial_missing_deps: str,
    tags: str = "[]",
    is_public: int = 0,
    labels: str = '["private"]',
) -> None:
    if IS_PG:
        await conn.execute(
            "INSERT INTO resource_social "
            "(resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now()) "
            "ON CONFLICT (resource_type, resource_id, owner) DO UPDATE SET "
            "name=EXCLUDED.name, description=EXCLUDED.description, is_public=EXCLUDED.is_public, "
            "category=EXCLUDED.category, trial_missing_deps=EXCLUDED.trial_missing_deps, "
            "tags=EXCLUDED.tags, labels=EXCLUDED.labels, updated_at=now()",
            (
                resource_type,
                resource_id,
                owner,
                name,
                description,
                bool(is_public),
                category,
                trial_missing_deps,
                tags,
                labels,
            ),
        )
    else:
        await conn.execute(
            "INSERT INTO resource_social "
            "(resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(resource_type, resource_id, owner) DO UPDATE SET "
            "name=excluded.name, description=excluded.description, is_public=excluded.is_public, "
            "category=excluded.category, trial_missing_deps=excluded.trial_missing_deps, "
            "tags=excluded.tags, labels=excluded.labels, updated_at=excluded.updated_at",
            (
                resource_type,
                resource_id,
                owner,
                name,
                description,
                is_public,
                category,
                trial_missing_deps,
                tags,
                labels,
            ),
        )


class _AgentTryBody(BaseModel):
    connection_id: str
    message: str


class _AgentVisibilityBody(BaseModel):
    is_public: bool
    category: str
    trial_missing_deps: str = "warn"


class _SkillVisibilityBody(BaseModel):
    is_public: bool
    category: str


class _KnowledgeVisibilityBody(BaseModel):
    is_public: bool
    category: str


class _WorkflowVisibilityBody(BaseModel):
    is_public: bool
    category: str


@router.put("/api/agents/{scope}/{agent_id}/visibility")
async def set_agent_visibility(
    scope: str,
    agent_id: str,
    body: _AgentVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    if body.trial_missing_deps not in ("warn", "silent"):
        raise APIError(
            422,
            "invalid_field",
            "trial_missing_deps debe ser 'warn' o 'silent'",
            extra={"field": "trial_missing_deps"},
        )
    agents = AgentStorage(_cfg.AGENTS_DIR)
    agent = await agents.get(agent_id, scope)
    if not agent:
        raise APIError(404, "not_found", "Agente no encontrado", extra={"resource": "agent"})
    resource_labels = agent.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0

    async with open_db() as conn:
        if body.is_public:
            await _upsert_social(
                conn,
                "agent",
                agent_id,
                username,
                agent.get("name", agent_id),
                agent.get("description", ""),
                body.category,
                body.trial_missing_deps,
                json.dumps(agent.get("tags") or []),
                is_public_val,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("agent", agent_id, username),
            )
        await conn.commit()
    return {"ok": True}


@router.put("/api/skills/{scope}/{skill_id}/visibility")
async def set_skill_visibility(
    scope: str,
    skill_id: str,
    body: _SkillVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    skills = SkillStorage(_cfg.SKILLS_DIR)
    skill = await skills.get(scope, skill_id)
    if not skill:
        raise APIError(404, "not_found", "Skill no encontrada", extra={"resource": "skill"})
    resource_labels = skill.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0

    async with open_db() as conn:
        if body.is_public:
            await _upsert_social(
                conn,
                "skill",
                skill_id,
                username,
                skill.get("name", skill_id),
                skill.get("description", ""),
                body.category,
                "warn",
                json.dumps(skill.get("tags") or []),
                is_public_val,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("skill", skill_id, username),
            )
        await conn.commit()
    return {"ok": True}


@router.put("/api/knowledge/folders/{folder_id}/visibility")
async def set_knowledge_visibility(
    folder_id: str,
    body: _KnowledgeVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT name, section FROM resource_folders WHERE id=? AND owner_id=?",
            (folder_id, username),
        )
        if not row:
            raise APIError(404, "not_found", "Carpeta no encontrada", extra={"resource": "folder"})
        if row["section"] == "agents":
            raise APIError(
                422,
                "agent_folder_not_publishable",
                "Las carpetas de agentes no se publican como conocimiento",
            )
        folder_name = row["name"]
        if body.is_public:
            await _upsert_social(
                conn,
                "knowledge",
                folder_id,
                username,
                folder_name,
                "",
                body.category,
                "warn",
                "[]",
                1,
                '["private"]',
            )
        else:
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("knowledge", folder_id, username),
            )
        await conn.execute(
            "UPDATE resource_folders SET is_public=? WHERE id=? AND owner_id=?",
            (body.is_public, folder_id, username),
        )
        await conn.commit()
    return {"ok": True, "is_public": body.is_public}


@router.put("/api/workflows/{workflow_id}/visibility")
async def set_workflow_visibility(
    workflow_id: str,
    body: _WorkflowVisibilityBody,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    _check_category(body.category)
    workflows = WorkflowStorage()
    workflow = await workflows.get(workflow_id, ctx.workspace_id)
    if not workflow:
        raise APIError(
            404, "not_found", "Orquestación no encontrada", extra={"resource": "workflow"}
        )
    resource_labels = workflow.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0

    async with open_db() as conn:
        if body.is_public:
            await _upsert_social(
                conn,
                "workflow",
                workflow_id,
                ctx.user,
                workflow.get("name", workflow_id),
                workflow.get("description", ""),
                body.category,
                "warn",
                "[]",
                is_public_val,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("workflow", workflow_id, ctx.user),
            )
        await conn.commit()

    if body.is_public and is_public_val:
        await _cascade_publish_workflow(workflow, ctx.user, ctx.workspace_id)

    return {"ok": True}


@router.get("/api/explore")
async def explore(
    type: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    label: Optional[str] = None,
    limit: int = 40,
    offset: int = 0,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    limit = min(limit, 100)

    async with open_db() as conn:
        conditions: List[str] = ["is_public = ?"]
        params: List[Any] = [_PUBLIC_VAL]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if q:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if label:
            conditions.append("labels LIKE ?")
            params.append(f'%"{label}"%')
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY stars_count DESC, updated_at DESC "
            f"LIMIT ? OFFSET ?",
            tuple(params),
        )

    rows = []
    for r in raw:
        row = dict(r)
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except (ValueError, TypeError):
            row["tags"] = []
        try:
            row["labels"] = json.loads(row.get("labels") or '["private"]')
        except (ValueError, TypeError):
            row["labels"] = ["private"]
        rows.append(row)
    return rows


@router.get("/api/explore/{resource_type}/{resource_id}/preview")
async def explore_preview(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Rich preview data for a single public resource."""

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT name, description, owner, category, labels "
            "FROM resource_social WHERE resource_type=? AND resource_id=? AND is_public=?",
            (resource_type, resource_id, _PUBLIC_VAL),
        )

    if not row:
        raise APIError(
            404,
            "not_found",
            "Recurso no encontrado o no es público",
            extra={"resource": resource_type},
        )

    base: Dict[str, Any] = dict(row)
    try:
        base["labels"] = json.loads(base.get("labels") or '["private"]')
    except (ValueError, TypeError):
        base["labels"] = ["private"]
    base["resource_type"] = resource_type
    base["resource_id"] = resource_id

    if resource_type == "agent":
        agents = AgentStorage(_cfg.AGENTS_DIR)
        agent = await agents.get(resource_id)
        if agent:
            skills_storage = SkillStorage(_cfg.SKILLS_DIR)
            skill_names = []
            for sid in agent.get("skills", []):
                sk = await skills_storage.get_any(sid)
                if sk:
                    skill_names.append(sk.get("name", sid))
            knowledge_storage = KnowledgeStorage(_cfg.DB_FILE)
            knowledge_titles = []
            for kid in agent.get("knowledge", []):
                item = await knowledge_storage.get(kid)
                if item:
                    knowledge_titles.append(item.get("title", kid))
            base["system_prompt"] = (agent.get("system_prompt") or "")[:600]
            base["skills"] = skill_names
            base["knowledge"] = knowledge_titles
            base["use_memory"] = agent.get("use_memory", False)
            base["temperature"] = agent.get("temperature", 0.7)
            base["agent_type"] = agent.get("agent_type", "")

    elif resource_type == "skill":
        skills_storage = SkillStorage(_cfg.SKILLS_DIR)
        sk = await skills_storage.get_any(resource_id)
        if sk:
            base["body"] = (sk.get("body") or "")[:3000]
            base["parameters"] = sk.get("parameters", [])
            base["icon"] = sk.get("icon", "")

    elif resource_type == "knowledge":
        knowledge_storage = KnowledgeStorage(_cfg.DB_FILE)
        item = await knowledge_storage.get(resource_id)
        if item:
            base["content"] = (item.get("content") or "")[:2000]
            base["type"] = item.get("type", "")
            base["source"] = item.get("source", "")
            base["char_count"] = item.get("char_count", 0)
        else:
            async with open_db() as conn:
                folder = await conn.fetchone(
                    "SELECT id, name, section FROM resource_folders "
                    "WHERE id=? AND is_public=?",
                    (resource_id, _PUBLIC_VAL),
                )
                rows = await conn.fetchall(
                    "SELECT resource_type, resource_id "
                    "FROM resource_folder_items WHERE folder_id=?",
                    (resource_id,),
                )
            if folder:
                base["type"] = "folder"
                base["section"] = folder["section"]
                base["items"] = [dict(row) for row in rows]
                base["item_count"] = len(rows)

    elif resource_type == "workflow":
        workflow = await WorkflowStorage().get_any(resource_id)
        if workflow:
            agents_storage = AgentStorage(_cfg.AGENTS_DIR)
            nodes = workflow.get("definition", {}).get("nodes", [])
            agent_names = []
            for node in nodes:
                agent = await agents_storage.get(str(node.get("agent_id") or ""))
                agent_names.append(
                    (agent.get("name") if agent else None)
                    or node.get("label")
                    or node.get("agent_id")
                )
            base["steps"] = len(nodes)
            base["agent_names"] = agent_names

    return base


@router.get("/api/social/me/resources")
async def my_resources(
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """All resource_social rows owned by the current user."""

    async with open_db() as conn:
        conditions: List[str] = ["owner = ?"]
        params: List[Any] = [username]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, is_public, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY updated_at DESC",
            tuple(params),
        )

    rows = []
    for r in raw:
        row = dict(r)
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except (ValueError, TypeError):
            row["tags"] = []
        try:
            row["labels"] = json.loads(row.get("labels") or '["private"]')
        except (ValueError, TypeError):
            row["labels"] = ["private"]
        rows.append(row)

    # Annotate linked rows with linked_broken flag
    linked_ids = [r["linked_to_id"] for r in rows if r.get("linked_to_id")]
    if linked_ids:
        placeholders = ",".join("?" * len(linked_ids))
        async with open_db() as conn2:
            pub_rows = await conn2.fetchall(
                f"SELECT resource_id FROM resource_social WHERE resource_id IN ({placeholders}) AND is_public = ?",
                tuple(linked_ids) + (_PUBLIC_VAL,),
            )
        still_public = {r["resource_id"] for r in pub_rows}
        for row in rows:
            if row.get("linked_to_id"):
                linked_owner = row.get("linked_to_user") or ""
                still_accessible = linked_owner == username
                row["linked_broken"] = (
                    row["linked_to_id"] not in still_public and not still_accessible
                )

    return {"resources": rows}


@router.get("/api/users/{target_username}/resources")
async def user_resources(
    target_username: str,
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:

    async with open_db() as conn:
        conditions: List[str] = ["is_public = ?", "owner = ?"]
        params: List[Any] = [_PUBLIC_VAL, target_username]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY stars_count DESC, updated_at DESC",
            tuple(params),
        )

    rows = []
    for r in raw:
        row = dict(r)
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except (ValueError, TypeError):
            row["tags"] = []
        try:
            row["labels"] = json.loads(row.get("labels") or '["private"]')
        except (ValueError, TypeError):
            row["labels"] = ["private"]
        rows.append(row)
    return rows


@router.post("/api/users/{target}/follow")
async def follow_user(
    target: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:
    if target == username:
        raise APIError(400, "cannot_follow_self", "No puedes seguirte a ti mismo")

    async with open_db() as conn:
        row = await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (target,))
        if not row:
            raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
        if IS_PG:
            await conn.execute(
                "INSERT INTO user_follows (follower, following) VALUES (?, ?) "
                "ON CONFLICT DO NOTHING",
                (username, target),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO user_follows (follower, following) VALUES (?, ?)",
                (username, target),
            )
        await conn.commit()
    return {"ok": True}


@router.delete("/api/users/{target}/follow")
async def unfollow_user(
    target: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:

    async with open_db() as conn:
        await conn.execute(
            "DELETE FROM user_follows WHERE follower = ? AND following = ?",
            (username, target),
        )
        await conn.commit()
    return {"ok": True}


@router.get("/api/users/{target}/follow-status")
async def follow_status(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:

    async with open_db() as conn:
        is_following_row = await conn.fetchone(
            "SELECT 1 FROM user_follows WHERE follower = ? AND following = ?",
            (username, target),
        )
        followers_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE following = ?",
            (target,),
        )
        following_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE follower = ?",
            (target,),
        )

    return {
        "following": is_following_row is not None,
        "followers_count": followers_count or 0,
        "following_count": following_count or 0,
    }


@router.get("/api/feed")
async def get_feed(
    limit: int = 40,
    offset: int = 0,
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    limit = min(limit, 100)

    async with open_db() as conn:
        conditions: List[str] = [
            "owner IN (SELECT following FROM user_follows WHERE follower = ?)",
            "is_public = ?",
        ]
        params: List[Any] = [username, _PUBLIC_VAL]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, tags, labels, updated_at "
            f"FROM resource_social "
            f"WHERE {where} "
            f"ORDER BY updated_at DESC "
            f"LIMIT ? OFFSET ?",
            tuple(params),
        )

    rows = []
    for r in raw:
        row = dict(r)
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except (ValueError, TypeError):
            row["tags"] = []
        try:
            row["labels"] = json.loads(row.get("labels") or '["private"]')
        except (ValueError, TypeError):
            row["labels"] = ["private"]
        rows.append(row)
    return rows


@router.post("/api/{resource_type}/{resource_id}/star")
async def star_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:
    # A4: validar resource_type para evitar contaminación de la tabla resource_stars
    if resource_type not in _VALID_SOCIAL_RESOURCE_TYPES:
        raise APIError(
            422,
            "invalid_field",
            f"Tipo de recurso no válido: {resource_type!r}",
            extra={"field": "resource_type"},
        )

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_stars (username, resource_type, resource_id) "
                "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (username, resource_type, resource_id),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_stars (username, resource_type, resource_id) "
                "VALUES (?, ?, ?)",
                (username, resource_type, resource_id),
            )
        await conn.execute(
            "UPDATE resource_social SET stars_count = ("
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?"
            ") WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        )
    return {"ok": True, "stars": count or 0}


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

    link_payload = {
        k: v
        for k, v in source.items()
        if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
    }
    # id propio (no derivado del nombre): al no renombrar la copia, un id basado en
    # el nombre colisionaría con el original (misma slug) y una lectura por id sin
    # filtro de owner devolvería cualquiera de los dos.
    link_payload["id"] = uuid4().hex[:12]
    link_labels = list(link_payload.get("labels") or ["private"])
    for ol in ("fork", "linked"):
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

    link_payload = {
        k: v for k, v in source.items() if k not in ("id", "scope", "owner_id")
    }
    # id propio (no derivado del nombre): al no renombrar la copia, un id basado en
    # el nombre colisionaría con el original (misma slug) y una lectura por id sin
    # filtro de owner devolvería cualquiera de los dos.
    link_payload["id"] = uuid4().hex[:12]
    link_labels = list(link_payload.get("labels") or ["private"])
    for ol in ("fork", "linked"):
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

    nodes = source.get("definition", {}).get("nodes", [])
    if username != source_owner:
        nodes = await _inherit_workflow_agents(nodes, username)
    definition = {"nodes": nodes, "edges": source.get("definition", {}).get("edges", [])}

    labels = [lbl for lbl in (source.get("labels") or ["private"]) if lbl != "linked"]
    labels.append("linked")

    result = await workflows.save(
        username,
        {
            "id": uuid4().hex[:12],
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
    ctx: WorkspaceContext = Depends(require_workspace),
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

    # Step 3: Resolve caller's connection (workspace first, then personal fallback)
    conn_storage = ConnectionStorage(_cfg.DB_FILE)
    conn_data = await conn_storage.get(body.connection_id, ctx.workspace_id)
    if conn_data is None and ctx.workspace_id != ctx.user:
        conn_data = await conn_storage.get(body.connection_id, ctx.user)
    if conn_data is None:
        raise APIError(400, "not_found", "Connection no encontrada", extra={"resource": "connection"})

    # Step 4: Filter skills based on trial_missing_deps policy
    skills_storage = SkillStorage(_cfg.SKILLS_DIR)
    warnings: List[str] = []
    agent_skills: List[str] = list(agent_data.get("skills") or [])

    accessible: List[str] = []
    for skill_id in agent_skills:
        if await skills_storage.get("public", skill_id):
            accessible.append(skill_id)
            continue
        priv = await skills_storage.get("private", skill_id)
        if priv and priv.get("owner_id") == ctx.workspace_id:
            accessible.append(skill_id)
            continue
        if trial_missing_deps == "warn":
            warnings.append(skill_id)
    agent_data = {**agent_data, "skills": accessible}

    # Step 5: Stream chat and collect reply (no history saved)
    reply_parts: List[str] = []
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


@router.delete("/api/{resource_type}/{resource_id}/star")
async def unstar_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:
    # A4: validar resource_type
    if resource_type not in _VALID_SOCIAL_RESOURCE_TYPES:
        raise APIError(
            422,
            "invalid_field",
            f"Tipo de recurso no válido: {resource_type!r}",
            extra={"field": "resource_type"},
        )

    async with open_db() as conn:
        await conn.execute(
            "DELETE FROM resource_stars "
            "WHERE username=? AND resource_type=? AND resource_id=?",
            (username, resource_type, resource_id),
        )
        await conn.execute(
            "UPDATE resource_social SET stars_count = ("
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?"
            ") WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        )
    return {"ok": True, "stars": count or 0}
