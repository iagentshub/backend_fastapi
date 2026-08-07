"""Rutas del catálogo social: visibilidad pública (publicar/despublicar con
cascada a skills/knowledge dependientes) y stars.

Ver explore.py (descubrimiento/perfil/follow) y resource_linking.py
(link/fork/sync/try), extraídos de este mismo archivo — ver admin.py para el
motivo completo del split.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import app.config.data as _cfg
from app.api.routes.auth import GroupContext, require_auth, require_group
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.models.resource_types import SOCIAL_RESOURCE_TYPES
from app.storage.db import IS_PG, open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import (
    AgentStorage,
    MemoryStorage,
    PromptStorage,
    SkillStorage,
    ToolStorage,
)
from app.storage.workflows import WorkflowStorage
from app.utils.generators import generate_id

router = APIRouter(tags=["social"])

# A4: tipos de recurso válidos para star/unstar y endpoints sociales
_VALID_SOCIAL_RESOURCE_TYPES = SOCIAL_RESOURCE_TYPES

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
_inherit_knowledge_store = KnowledgeStorage()
_inherit_memory_store = MemoryStorage(_cfg.MEMORY_DIR)
_inherit_prompts_store = PromptStorage()
_inherit_tools_store = ToolStorage()


async def _inherit_resource_ids(
    ids: List[str], resource_type: str, target_owner_id: str
) -> List[str]:
    """Al enlazar un agente, sus skills/conocimiento/prompts privados se clonan
    junto con él (heredados) para que sigan siendo accesibles desde el nuevo
    dueño. Los públicos, o los que ya pertenecen al destino, se referencian tal
    cual (sin clonar)."""
    new_ids: List[str] = []
    for rid in ids:
        if resource_type == "skill":
            item = await _inherit_skills_store.get_any(rid)
        elif resource_type == "prompt":
            item = await _inherit_prompts_store.get_any(rid)
        elif resource_type == "tool":
            item = await _inherit_tools_store.get_any(rid)
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
            clone = {
                k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")
            }
            clone["id"] = generate_id()
            clone["labels"] = [
                lbl
                for lbl in (clone.get("labels") or ["private"])
                if lbl not in ("linked", "public")
            ] or ["private"]
            saved = await _inherit_skills_store.save(
                "private", clone, owner_id=target_owner_id
            )
        elif resource_type == "prompt":
            clone = {
                k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")
            }
            clone["id"] = generate_id()
            clone["labels"] = [
                lbl
                for lbl in (clone.get("labels") or ["private"])
                if lbl not in ("linked", "public")
            ] or ["private"]
            # El alias de origen puede colisionar con uno ya existente del
            # destino — nunca debe romper el clonado, se sufija si hace falta.
            clone["alias"] = await _inherit_prompts_store.unique_alias(
                target_owner_id, str(clone.get("alias") or "")
            )
            saved = await _inherit_prompts_store.save(
                "private", clone, owner_id=target_owner_id
            )
        elif resource_type == "tool":
            # id propio (no derivado del nombre) — mismo motivo que skill: no
            # colisionar con una tool homónima de otro owner.
            clone = {
                k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")
            }
            clone["id"] = generate_id()
            clone["labels"] = [
                lbl
                for lbl in (clone.get("labels") or ["private"])
                if lbl not in ("linked", "public")
            ] or ["private"]
            saved = await _inherit_tools_store.save(
                "private", clone, owner_id=target_owner_id
            )
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
        await _inherit_memory_store.save(
            f"{new_agent_id}.md", content, owner_id=target_owner_id
        )


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
            elif (
                agent.get("owner_id") == target_owner_id
                or agent.get("scope") == "public"
            ):
                new_agent_id = old_agent_id
            else:
                clone_payload = {
                    k: v
                    for k, v in agent.items()
                    if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
                }
                clone_payload["id"] = generate_id()
                clone_payload["labels"] = [
                    lbl
                    for lbl in (clone_payload.get("labels") or ["private"])
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
                    agent,
                    str(agent.get("owner_id") or ""),
                    saved["id"],
                    target_owner_id,
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
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()


async def _publish_tool_cascade(
    tool_id: str, username: str, owner_ids: set[str]
) -> None:
    tool_storage = ToolStorage()
    tool = await tool_storage.get_any(tool_id)
    if not tool or tool.get("owner_id") not in owner_ids:
        return
    labels = list(tool.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        await tool_storage.save(
            "private", {**tool, "labels": labels}, owner_id=tool["owner_id"]
        )
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND owner=?",
            ("tool", tool_id, username),
        )
        if row and row["linked_to_id"]:
            return
        await _upsert_social(
            conn,
            "tool",
            tool_id,
            username,
            tool.get("name", tool_id),
            tool.get("description", ""),
            "Other",
            "warn",
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()


async def _publish_prompt_cascade(
    prompt_id: str, username: str, owner_ids: set[str]
) -> None:
    prompt_storage = PromptStorage()
    prompt = await prompt_storage.get_any(prompt_id)
    if not prompt or prompt.get("owner_id") not in owner_ids:
        return
    labels = list(prompt.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        await prompt_storage.save(
            "private", {**prompt, "labels": labels}, owner_id=prompt["owner_id"]
        )
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND owner=?",
            ("prompt", prompt_id, username),
        )
        if row and row["linked_to_id"]:
            return
        await _upsert_social(
            conn,
            "prompt",
            prompt_id,
            username,
            prompt.get("name", prompt_id),
            prompt.get("description", ""),
            "Other",
            "warn",
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()


async def _cascade_publish_agent(
    agent: Dict[str, Any], username: str, group_id: str = ""
) -> None:
    """Al publicar un agente, publica en cascada sus skills, conocimiento y
    prompts propios."""
    owner_ids = {username, group_id} - {""}
    for skill_id in agent.get("skills") or []:
        await _publish_skill_cascade(skill_id, username, owner_ids)
    for prompt_id in agent.get("prompts") or []:
        await _publish_prompt_cascade(prompt_id, username, owner_ids)
    for tool_id in agent.get("tools") or []:
        await _publish_tool_cascade(tool_id, username, owner_ids)


async def _cascade_publish_workflow(
    workflow: Dict[str, Any], username: str, group_id: str = ""
) -> None:
    """Al publicar una orquestación, publica en cascada los agentes propios que usa
    (y, para cada uno, sus skills/conocimiento — ver _cascade_publish_agent)."""
    owner_ids = {str(workflow.get("owner_id") or ""), username, group_id} - {""}
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
                {**agent, "labels": labels},
                agent.get("scope", "private"),
                owner_id=agent["owner_id"],
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
        await _cascade_publish_agent(agent, username, group_id)


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

_PUBLIC_VAL = 1


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
                1 if is_public else 0,
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


class _PromptVisibilityBody(BaseModel):
    is_public: bool
    category: str


class _ToolVisibilityBody(BaseModel):
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
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
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
        raise APIError(
            404, "not_found", "Skill no encontrada", extra={"resource": "skill"}
        )
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
                "[]",
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


@router.put("/api/prompts/{scope}/{prompt_id}/visibility")
async def set_prompt_visibility(
    scope: str,
    prompt_id: str,
    body: _PromptVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    prompts = PromptStorage()
    prompt = await prompts.get(scope, prompt_id)
    if not prompt:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )
    resource_labels = prompt.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0

    async with open_db() as conn:
        if body.is_public:
            await _assert_not_linked_copy(conn, "prompt", prompt_id, username)
            await _upsert_social(
                conn,
                "prompt",
                prompt_id,
                username,
                prompt.get("name", prompt_id),
                prompt.get("description", ""),
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
                ("prompt", prompt_id, username),
            )
        await conn.commit()
    return {"ok": True}


@router.put("/api/tools/{scope}/{tool_id}/visibility")
async def set_tool_visibility(
    scope: str,
    tool_id: str,
    body: _ToolVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    tools = ToolStorage()
    tool = await tools.get(scope, tool_id)
    if not tool:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    resource_labels = tool.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0

    async with open_db() as conn:
        if body.is_public:
            await _assert_not_linked_copy(conn, "tool", tool_id, username)
            await _upsert_social(
                conn,
                "tool",
                tool_id,
                username,
                tool.get("name", tool_id),
                tool.get("description", ""),
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
                ("tool", tool_id, username),
            )
        await conn.commit()
    return {"ok": True}


@router.put("/api/workflows/{workflow_id}/visibility")
async def set_workflow_visibility(
    workflow_id: str,
    body: _WorkflowVisibilityBody,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    _check_category(body.category)
    workflows = WorkflowStorage()
    workflow = await workflows.get(workflow_id, ctx.group_id)
    if not workflow:
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
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
        await _cascade_publish_workflow(workflow, ctx.user, ctx.group_id)

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
