"""Rutas del catálogo social: visibilidad pública (publicar/despublicar con
cascada a skills/knowledge dependientes) y stars.

Ver explore.py (descubrimiento/perfil/follow) y resource_linking.py
(link/fork/sync/try), extraídos de este mismo archivo — ver admin.py para el
motivo completo del split.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import app.config.data as _cfg
from app.api.routes.auth import WorkspaceContext, require_auth, require_workspace
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.storage.db import IS_PG, open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import (
    AgentStorage,
    MemoryStorage,
    SkillStorage,
)
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
            clone["labels"] = [
                lbl for lbl in (clone.get("labels") or ["private"])
                if lbl not in ("linked", "public")
            ] or ["private"]
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
                clone_payload["labels"] = [
                    lbl for lbl in (clone_payload.get("labels") or ["private"])
                    if lbl not in ("linked", "fork", "public")
                ] or ["private"]
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
        row = await conn.fetchone(
            "SELECT linked_to_id FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND owner=?",
            ("skill", skill_id, username),
        )
        if row and row["linked_to_id"]:
            return
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
        async with open_db() as conn:
            linked_row = await conn.fetchone(
                "SELECT linked_to_id FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("agent", agent_id, username),
            )
        if linked_row and linked_row["linked_to_id"]:
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


async def _assert_not_linked_copy(
    conn: Any, resource_type: str, resource_id: str, owner: str
) -> None:
    """Impide publicar una copia enlazada (creada vía "Enlazar" de un recurso ajeno):
    republicarla generaría una entrada duplicada del original en Explorar."""
    row = await conn.fetchone(
        "SELECT linked_to_id FROM resource_social "
        "WHERE resource_type=? AND resource_id=? AND owner=?",
        (resource_type, resource_id, owner),
    )
    if row and row["linked_to_id"]:
        raise APIError(
            400,
            "linked_copy_not_publishable",
            "No puedes publicar una copia enlazada de un recurso ajeno",
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
            await _assert_not_linked_copy(conn, "agent", agent_id, username)
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
            await _assert_not_linked_copy(conn, "skill", skill_id, username)
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
            await _assert_not_linked_copy(conn, "workflow", workflow_id, ctx.user)
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
