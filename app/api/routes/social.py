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
from pydantic import BaseModel, Field

import app.config.data as _cfg
from app.api.routes.auth import GroupContext, require_auth, require_group
from app.config.session import RATE_IP_FACTOR
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter, principal_key
from app.models.resource_types import SOCIAL_RESOURCE_TYPES
from app.sql import sql
from app.storage.agent_storage import AgentStorage
from app.storage.db import IS_PG, open_db
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage
from app.utils.generators import generate_date, generate_id
from app.utils.origin import assert_resource_writable

router = APIRouter(tags=["social"])

# A4: tipos de recurso válidos para star/unstar y endpoints sociales
_VALID_SOCIAL_RESOURCE_TYPES = SOCIAL_RESOURCE_TYPES

# N2: rate limiting para endpoints sociales (star, follow). Todos exigen
# sesión, así que la cuota va por cuenta: quien infla contadores lo hace desde
# una cuenta, y cambiar de IP no le devuelve el cupo.
_social_limiter = RateLimiter(
    calls=30,
    window=60,
    key_func=principal_key,
    shared=True,
    name="social",
    ip_calls=30 * RATE_IP_FACTOR,
)


async def _assert_public(resource_type: str, source_id: str) -> None:
    """Enlazar solo está disponible para contenido público del marketplace."""
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/social:public_flag_exists"),
            (resource_type, source_id, _PUBLIC_VAL),
        )
    if not row:
        raise APIError(403, "forbidden", "No tienes acceso a este recurso")


# Singletons de módulo: construir un storage dentro de cada handler reejecutaba
# su migración legacy (el flag era de instancia), y con ella un SELECT COUNT(*)
# por petición. Mismo patrón que agents.py y connections.py.
_agents_store = AgentStorage(_cfg.AGENTS_DIR)
_skills_store = SkillStorage(_cfg.SKILLS_DIR)
_prompts_store = PromptStorage()
_tools_store = ToolStorage()
_knowledge_store = KnowledgeStorage()
_knowledge_packs_store = KnowledgePackStorage()
_groups_store = GroupStorage()
_workflows_store = WorkflowStorage()
_memory_store = MemoryStorage(_cfg.MEMORY_DIR)

# Los storages que usa _inherit_resource_ids son estos mismos: eran una segunda
# tanda de instancias con la misma configuración.
_inherit_skills_store = _skills_store
_inherit_knowledge_store = _knowledge_store
_inherit_memory_store = _memory_store
_inherit_prompts_store = _prompts_store
_inherit_tools_store = _tools_store


def _agent_public_dependency_keys(agent: Dict[str, Any]) -> set[str]:
    keys = {
        f"{kind}:{resource_id}"
        for kind, field_name in (
            ("skill", "skills"),
            ("knowledge", "knowledge"),
            ("knowledge_pack", "knowledge_packs"),
            ("prompt", "prompts"),
            ("tool", "tools"),
        )
        for resource_id in (agent.get(field_name) or [])
        if resource_id
    }
    memory_file = str(agent.get("memory_file") or "").strip()
    if agent.get("use_memory") and memory_file:
        keys.add(f"memory:{memory_file}")
    return keys


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
    agents_storage = _agents_store
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
                clone_payload["connection_id"] = None
                clone_payload["op_connections"] = []
                raw_selection = agent.get("public_dependencies")
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
                    clone_payload[field_name] = [
                        resource_id
                        for resource_id in (clone_payload.get(field_name) or [])
                        if selected is None or f"{kind}:{resource_id}" in selected
                    ]
                clone_payload["skills"] = await _inherit_resource_ids(
                    clone_payload.get("skills") or [], "skill", target_owner_id
                )
                clone_payload["knowledge"] = await _inherit_resource_ids(
                    clone_payload.get("knowledge") or [], "knowledge", target_owner_id
                )
                clone_payload["prompts"] = await _inherit_resource_ids(
                    clone_payload.get("prompts") or [], "prompt", target_owner_id
                )
                clone_payload["tools"] = await _inherit_resource_ids(
                    clone_payload.get("tools") or [], "tool", target_owner_id
                )
                memory_file = str(agent.get("memory_file") or "").strip()
                copy_memory = bool(
                    agent.get("use_memory")
                    and memory_file
                    and (selected is None or f"memory:{memory_file}" in selected)
                )
                clone_payload["memory_file"] = (
                    f"{clone_payload['id']}.md" if copy_memory else None
                )
                if not copy_memory:
                    clone_payload["use_memory"] = False
                saved = await agents_storage.save(
                    clone_payload, "private", owner_id=target_owner_id
                )
                if copy_memory:
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
    skill_storage = _skills_store
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
            sql("queries/social:linked_to_id"),
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
    tool_storage = _tools_store
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
            sql("queries/social:linked_to_id"),
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
    prompt_storage = _prompts_store
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
            sql("queries/social:linked_to_id"),
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


async def _publish_knowledge_cascade(
    knowledge_id: str, username: str, owner_ids: set[str]
) -> None:
    item = await _knowledge_store.get(knowledge_id)
    if not item or item.get("owner_id") not in owner_ids:
        return
    labels = list(item.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
        item = await _knowledge_store.save(
            type=item.get("type", "text"),
            title=item.get("title", knowledge_id),
            source=item.get("source", ""),
            content=item.get("content", ""),
            owner_id=item["owner_id"],
            labels=labels,
            item_id=knowledge_id,
        )
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/social:linked_to_id"),
            ("knowledge", knowledge_id, username),
        )
        if row and row["linked_to_id"]:
            return
        await _upsert_social(
            conn,
            "knowledge",
            knowledge_id,
            username,
            item.get("title", knowledge_id),
            item.get("source", ""),
            "Other",
            "warn",
            "[]",
            1,
            json.dumps(labels),
        )
        await conn.commit()


async def _publish_knowledge_pack_cascade(
    pack_id: str, username: str, owner_ids: set[str]
) -> None:
    pack = await _knowledge_packs_store.get(pack_id)
    if not pack or pack.get("owner_id") not in owner_ids:
        return
    labels = list(pack.get("labels") or ["private"])
    if "public" not in labels:
        labels.append("public")
    async with open_db() as conn:
        await conn.execute(
            sql("queries/social:pack_make_public"),
            (json.dumps(labels), generate_date(), pack_id),
        )
        await _upsert_social(
            conn,
            "knowledge_pack",
            pack_id,
            username,
            pack.get("name", pack_id),
            pack.get("description", ""),
            "Other",
            "warn",
            "[]",
            _PUBLIC_VAL,
            json.dumps(labels),
        )
        await conn.commit()
    for item in pack.get("items") or []:
        await _publish_knowledge_cascade(str(item.get("id") or ""), username, owner_ids)


async def sync_knowledge_visibility_from_labels(
    *,
    resource_type: str,
    resource_id: str,
    username: str,
    owner_ids: set[str],
    is_public: bool,
) -> None:
    """Sincroniza Explorar con la etiqueta de visibilidad de Knowledge."""
    if resource_type == "knowledge_pack":
        if is_public:
            await _publish_knowledge_pack_cascade(resource_id, username, owner_ids)
            return
        async with open_db() as conn:
            await conn.execute(
                sql("queries/social:pack_make_private"),
                (generate_date(), resource_id),
            )
            await conn.execute(
                sql("queries/social:delete_social_pack_cascade"),
                (resource_id, resource_id),
            )
            await conn.commit()
        return

    if resource_type != "knowledge":
        raise ValueError(f"Tipo de conocimiento no soportado: {resource_type}")
    if is_public:
        await _publish_knowledge_cascade(resource_id, username, owner_ids)
        return
    async with open_db() as conn:
        await conn.execute(
            sql("queries/social:delete_social_knowledge"),
            (resource_id,),
        )
        await conn.commit()


async def _cascade_publish_agent(
    agent: Dict[str, Any],
    username: str,
    group_id: str = "",
    selected: set[str] | None = None,
) -> None:
    """Publica únicamente las dependencias elegidas por el propietario.

    ``None`` mantiene compatibilidad con agentes públicos creados antes de que
    existiera la selección explícita. Un conjunto vacío publica solo el agente.
    Las conexiones no forman parte de este catálogo ni se aceptan como claves.
    """
    owner_ids = {username, group_id} - {""}
    for skill_id in agent.get("skills") or []:
        if selected is None or f"skill:{skill_id}" in selected:
            await _publish_skill_cascade(skill_id, username, owner_ids)
    for knowledge_id in agent.get("knowledge") or []:
        if selected is None or f"knowledge:{knowledge_id}" in selected:
            await _publish_knowledge_cascade(knowledge_id, username, owner_ids)
    for pack_id in agent.get("knowledge_packs") or []:
        if selected is None or f"knowledge_pack:{pack_id}" in selected:
            await _publish_knowledge_pack_cascade(pack_id, username, owner_ids)
    for prompt_id in agent.get("prompts") or []:
        if selected is None or f"prompt:{prompt_id}" in selected:
            await _publish_prompt_cascade(prompt_id, username, owner_ids)
    for tool_id in agent.get("tools") or []:
        if selected is None or f"tool:{tool_id}" in selected:
            await _publish_tool_cascade(tool_id, username, owner_ids)


async def _cascade_publish_workflow(
    workflow: Dict[str, Any], username: str, group_id: str = ""
) -> None:
    """Al publicar una orquestación, publica en cascada los agentes propios que usa
    (y, para cada uno, sus skills/conocimiento — ver _cascade_publish_agent)."""
    owner_ids = {str(workflow.get("owner_id") or ""), username, group_id} - {""}
    agents_storage = _agents_store
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
                sql("queries/social:linked_to_id"),
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
                _PUBLIC_VAL,
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


def _assert_publicable(resource_labels: List[str], resource_type: str) -> None:
    """La label ``public`` manda: publicar sin ella no puede responder ``ok``.

    Las cinco rutas de visibilidad calculaban ``is_public`` a partir de las
    labels del recurso, no del ``is_public`` del cuerpo, así que un recurso sin
    la label se insertaba en ``resource_social`` con ``is_public = 0`` y el
    endpoint devolvía ``{"ok": true}``. Como un agente nace con
    ``labels: ["private"]``, ese era el camino por defecto: el usuario pulsaba
    «publicar», veía la confirmación, y su agente no aparecía en el catálogo ni
    había nada que se lo explicara.

    Se mantiene la label como fuente de verdad —cambiar eso invertiría la
    decisión de diseño y afectaría a ``resource_labels``— pero se deja de
    responder afirmativamente a una petición que no se ha atendido.
    """
    if "public" not in (resource_labels or []):
        raise APIError(
            409,
            "resource_not_marked_public",
            "Marca el recurso como público antes de publicarlo en el catálogo.",
            extra={"resource": resource_type, "missing_label": "public"},
        )


async def _assert_not_linked_copy(
    conn: Any, resource_type: str, resource_id: str, owner: str
) -> None:
    """Impide publicar una copia enlazada (creada vía "Enlazar" de un recurso ajeno):
    republicarla generaría una entrada duplicada del original en Explorar."""
    row = await conn.fetchone(
        sql("queries/social:linked_to_id"),
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
            sql("queries/social:upsert_social_pg"),
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
            sql("queries/social:upsert_social_sqlite"),
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
    publish_dependencies: List[str] = Field(default_factory=list)


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


class _KnowledgePackVisibilityBody(BaseModel):
    is_public: bool
    category: str


@router.put("/api/knowledge-packs/{pack_id}/visibility")
async def set_knowledge_pack_visibility(
    pack_id: str,
    body: _KnowledgePackVisibilityBody,
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    """Publica o retira un pack y todos sus archivos como una unidad."""
    _check_category(body.category)
    pack = await _knowledge_packs_store.get(pack_id)
    if not pack:
        raise APIError(
            404,
            "not_found",
            "Pack no encontrado",
            extra={"resource": "knowledge_pack"},
        )
    owner_id = str(pack.get("owner_id") or "")
    if owner_id not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    if owner_id == ctx.group_id and not await _groups_store.has_resource_permission(
        ctx.group_id, ctx.user, "knowledge", pack_id, "edit"
    ):
        raise APIError(403, "forbidden", "Sin permisos sobre este pack")
    assert_resource_writable(pack, "knowledge_pack")
    labels = list(pack.get("labels") or ["private"])
    async with open_db() as conn:
        if body.is_public:
            await _assert_not_linked_copy(conn, "knowledge_pack", pack_id, ctx.user)
            labels = [label for label in labels if label != "private"]
            if "public" not in labels:
                labels.append("public")
            await conn.execute(
                sql("queries/social:pack_publish_owned"),
                (json.dumps(labels), generate_date(), pack_id, owner_id),
            )
            await _upsert_social(
                conn,
                "knowledge_pack",
                pack_id,
                ctx.user,
                pack.get("name", pack_id),
                pack.get("description", ""),
                body.category,
                "warn",
                "[]",
                _PUBLIC_VAL,
                json.dumps(labels),
            )
            await conn.commit()
            for item in pack.get("items") or []:
                await _publish_knowledge_cascade(
                    str(item.get("id") or ""),
                    ctx.user,
                    {ctx.user, ctx.group_id},
                )
        else:
            labels = [label for label in labels if label != "public"]
            if "private" not in labels:
                labels.append("private")
            await conn.execute(
                sql("queries/social:pack_unpublish_owned"),
                (json.dumps(labels), generate_date(), pack_id, owner_id),
            )
            await conn.execute(
                sql("queries/social:delete_social_pack_cascade"),
                (pack_id, pack_id),
            )
            await conn.commit()
    if not body.is_public:
        for member in pack.get("items") or []:
            knowledge_id = str(member.get("id") or "")
            item = await _knowledge_store.get(knowledge_id)
            if not item or item.get("owner_id") not in {ctx.user, ctx.group_id}:
                continue
            item_labels = [
                label for label in (item.get("labels") or []) if label != "public"
            ]
            await _knowledge_store.save(
                type=item.get("type", "pack_item"),
                title=item.get("title", knowledge_id),
                source=item.get("source", ""),
                content=item.get("content", ""),
                owner_id=str(item.get("owner_id") or owner_id),
                labels=item_labels,
                item_id=knowledge_id,
            )
    return {"ok": True}


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
    agents = _agents_store
    agent = await agents.get(agent_id, scope)
    if not agent:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    assert_resource_writable(agent, "agent")
    resource_labels = agent.get("labels") or ["private"]
    selected_dependencies = (
        list(dict.fromkeys(body.publish_dependencies)) if body.is_public else []
    )
    invalid_dependencies = sorted(
        set(selected_dependencies) - _agent_public_dependency_keys(agent)
    )
    if invalid_dependencies:
        raise APIError(
            422,
            "invalid_field",
            "Hay dependencias seleccionadas que no pertenecen al agente",
            extra={"field": "publish_dependencies", "invalid": invalid_dependencies},
        )

    async with open_db() as conn:
        agent = await agents.save(
            {**agent, "public_dependencies": selected_dependencies},
            scope,
            owner_id=str(agent.get("owner_id") or username),
            conn=conn,
        )
        if body.is_public:
            await _assert_not_linked_copy(conn, "agent", agent_id, username)
            _assert_publicable(resource_labels, "agent")
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
                _PUBLIC_VAL,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                sql("queries/social:delete_social_entry"),
                ("agent", agent_id, username),
            )
        await conn.commit()
    if body.is_public:
        await _cascade_publish_agent(
            agent,
            username,
            str(agent.get("owner_id") or ""),
            selected=set(selected_dependencies),
        )
    return {"ok": True}


@router.put("/api/skills/{scope}/{skill_id}/visibility")
async def set_skill_visibility(
    scope: str,
    skill_id: str,
    body: _SkillVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    skills = _skills_store
    skill = await skills.get(scope, skill_id)
    if not skill:
        raise APIError(
            404, "not_found", "Skill no encontrada", extra={"resource": "skill"}
        )
    assert_resource_writable(skill, "skill")
    resource_labels = skill.get("labels") or ["private"]

    async with open_db() as conn:
        if body.is_public:
            await _assert_not_linked_copy(conn, "skill", skill_id, username)
            _assert_publicable(resource_labels, "skill")
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
                _PUBLIC_VAL,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                sql("queries/social:delete_social_entry"),
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
    prompts = _prompts_store
    prompt = await prompts.get(scope, prompt_id)
    if not prompt:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )
    assert_resource_writable(prompt, "prompt")
    resource_labels = prompt.get("labels") or ["private"]

    async with open_db() as conn:
        if body.is_public:
            await _assert_not_linked_copy(conn, "prompt", prompt_id, username)
            _assert_publicable(resource_labels, "prompt")
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
                _PUBLIC_VAL,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                sql("queries/social:delete_social_entry"),
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
    tools = _tools_store
    tool = await tools.get(scope, tool_id)
    if not tool:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    assert_resource_writable(tool, "tool")
    resource_labels = tool.get("labels") or ["private"]

    async with open_db() as conn:
        if body.is_public:
            await _assert_not_linked_copy(conn, "tool", tool_id, username)
            _assert_publicable(resource_labels, "tool")
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
                _PUBLIC_VAL,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                sql("queries/social:delete_social_entry"),
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
    workflows = _workflows_store
    workflow = await workflows.get(workflow_id, ctx.group_id)
    if not workflow:
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
    assert_resource_writable(workflow, "workflow")
    resource_labels = workflow.get("labels") or ["private"]

    async with open_db() as conn:
        if body.is_public:
            await _assert_not_linked_copy(conn, "workflow", workflow_id, ctx.user)
            _assert_publicable(resource_labels, "workflow")
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
                _PUBLIC_VAL,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                sql("queries/social:delete_social_entry"),
                ("workflow", workflow_id, ctx.user),
            )
        await conn.commit()

    if body.is_public:
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
                sql("queries/social:star_insert_pg"),
                (username, resource_type, resource_id),
            )
        else:
            await conn.execute(
                sql("queries/social:star_insert_sqlite"),
                (username, resource_type, resource_id),
            )
        await conn.execute(
            sql("queries/social:refresh_stars_count"),
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            sql("queries/social:count_stars"),
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
            sql("queries/social:star_delete"),
            (username, resource_type, resource_id),
        )
        await conn.execute(
            sql("queries/social:refresh_stars_count"),
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            sql("queries/social:count_stars"),
            (resource_type, resource_id),
        )
    return {"ok": True, "stars": count or 0}
