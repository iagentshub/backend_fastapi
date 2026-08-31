"""El catálogo público: listado, vista previa y relaciones de un recurso.

`relation` (new|linked|all) es lo que separa «lo que aún no tengo» de «lo que
ya enlacé»: sin ese filtro el catálogo repetía al usuario lo que ya era suyo.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import Depends

import app.config.data as _cfg
import app.services.resource_relations as _relations
from app.api.routes.auth import require_session
from app.api.routes.explore._router import router
from app.api.routes.explore._shared import (
    _add_owner_usernames,
)
from app.errors import APIError
from app.services.social_catalog import _PUBLIC_VAL
from app.services.tool_policy import assert_tool_distributable, tool_security_labels
from app.sql import sql
from app.storage.agent_storage import AgentStorage
from app.storage.db import open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage

# Singletons de módulo, como en agents.py y connections.py. Construir un storage
# dentro del handler no cuesta por el objeto, sino por lo que arrastra: el flag
# de migración era de instancia, así que cada uno recién creado reejecutaba
# _ensure_migrated() —y su SELECT COUNT(*)— en la primera operación.
# explore_preview llegaba a construir seis en una sola petición.
_agents = AgentStorage(_cfg.AGENTS_DIR)

_skills = SkillStorage(_cfg.SKILLS_DIR)

_prompts = PromptStorage()

_tools = ToolStorage()

_knowledge = KnowledgeStorage()

_knowledge_packs = KnowledgePackStorage()

_workflows = WorkflowStorage()

_shares = GroupShareStorage()

_groups = GroupStorage()


@router.get("/api/explore/{resource_type}/{resource_id}/preview")
async def explore_preview(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_session),  # ver explore(): catálogo público
) -> Dict[str, Any]:
    """Rich preview data for a single public resource.

    Lo oficial no necesita rama propia: es una fila de resource_social como
    cualquier otra, marcada con la label ``official``.
    """

    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/explore:social_card"),
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
    await _add_owner_usernames([base])

    if resource_type == "agent":
        agent = await _agents.get(resource_id)
        if not agent or not agent.get("is_active", True):
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado o no es público",
                extra={"resource": "agent"},
            )
        if agent:
            skill_names = []
            for sid in agent.get("skills", []):
                sk = await _skills.get_any(sid)
                if not sk or not sk.get("is_active", True):
                    continue
                # No revelar nombres de skills privadas ajenas en la vista
                # previa pública, aunque el agente que las usa sí sea público.
                if sk.get("scope") != "public" and not await _shares.is_accessible(
                    _groups,
                    resource_type="skill",
                    resource_id=sid,
                    owner_id=sk.get("owner_id"),
                    requester=username,
                ):
                    continue
                skill_names.append(sk.get("name", sid))
            knowledge_titles = []
            for kid in agent.get("knowledge", []):
                item = await _knowledge.get(kid)
                if not item or not item.get("is_active", True):
                    continue
                if not await _shares.is_accessible(
                    _groups,
                    resource_type="knowledge",
                    resource_id=kid,
                    owner_id=item.get("owner_id"),
                    requester=username,
                ):
                    continue
                knowledge_titles.append(item.get("title", kid))
            prompt_names = []
            for pid in agent.get("prompts", []):
                pr = await _prompts.get_any(pid)
                if not pr or not pr.get("is_active", True):
                    continue
                # No revelar nombres de prompts privados ajenos en la vista
                # previa pública, aunque el agente que los usa sí sea público.
                if pr.get("scope") != "public" and not await _shares.is_accessible(
                    _groups,
                    resource_type="prompt",
                    resource_id=pid,
                    owner_id=pr.get("owner_id"),
                    requester=username,
                ):
                    continue
                prompt_names.append(pr.get("name", pid))
            tool_ids = list(dict.fromkeys(str(tid) for tid in agent.get("tools", [])))
            tool_rows = await _tools.list_by_ids(tool_ids)
            tools_by_id: dict[str, list[dict[str, Any]]] = {}
            for tool in tool_rows:
                tools_by_id.setdefault(str(tool.get("id") or ""), []).append(tool)
            shared_tools = (
                await _shares.get_user_shared_resource_groups(username, "tool")
                if tool_ids
                else {}
            )
            tool_names = []
            for tid in tool_ids:
                tl = next(
                    (
                        candidate
                        for candidate in (tools_by_id.get(tid) or [])
                        if candidate.get("scope") == "public"
                        or candidate.get("owner_id") == username
                        or tid in shared_tools
                    ),
                    None,
                )
                if not tl or not tl.get("is_active", True) or tool_security_labels(tl):
                    continue
                tool_names.append(tl.get("name", tid))
            base["system_prompt"] = (agent.get("system_prompt") or "")[:600]
            base["skills"] = skill_names
            base["knowledge"] = knowledge_titles
            base["prompts"] = prompt_names
            base["tools"] = tool_names
            base["use_memory"] = agent.get("use_memory", False)
            base["temperature"] = agent.get("temperature", 0.7)
            base["agent_type"] = agent.get("agent_type", "")

    elif resource_type == "skill":
        sk = await _skills.get_any(resource_id)
        if not sk or not sk.get("is_active", True):
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado o no es público",
                extra={"resource": "skill"},
            )
        # El campo de la skill es `content`. La vista previa mandaba `body` y
        # `parameters`, del modelo viejo, que hoy no escribe nadie: enseñaba el
        # cuerpo vacío y una lista de parámetros vacía.
        base["content"] = (sk.get("content") or "")[:3000]
        base["icon"] = sk.get("icon", "")

    elif resource_type == "prompt":
        pr = await _prompts.get_any(resource_id)
        if not pr or not pr.get("is_active", True):
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado o no es público",
                extra={"resource": "prompt"},
            )
        base["content"] = (pr.get("content") or "")[:3000]
        base["alias"] = pr.get("alias", "")
        base["icon"] = pr.get("icon", "")

    elif resource_type == "tool":
        tl = await _tools.get_any(resource_id)
        if not tl or not tl.get("is_active", True):
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado o no es público",
                extra={"resource": "tool"},
            )
        assert_tool_distributable(tl)
        base["language"] = tl.get("language", "")
        base["binary_filename"] = tl.get("binary_filename")
        base["binary_size"] = tl.get("binary_size")
        base["icon"] = tl.get("icon", "")
        if tl.get("language") != "cpp":
            base["content"] = (tl.get("content") or "")[:3000]

    elif resource_type == "knowledge":
        item = await _knowledge.get(resource_id)
        if not item or not item.get("is_active", True):
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado o no es público",
                extra={"resource": "knowledge"},
            )
        base["content"] = (item.get("content") or "")[:2000]
        base["type"] = item.get("type", "")
        base["source"] = item.get("source", "")
        base["char_count"] = item.get("char_count", 0)

    elif resource_type == "knowledge_pack":
        pack = await _knowledge_packs.get(resource_id)
        if not pack or not pack.get("is_active", True):
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado o no es público",
                extra={"resource": "knowledge_pack"},
            )
        base["file_count"] = pack.get("file_count", 0)
        base["size_bytes"] = pack.get("size_bytes", 0)
        base["items"] = pack.get("items", [])

    elif resource_type == "workflow":
        workflow = await _workflows.get_any(resource_id)
        if workflow:
            nodes = workflow.get("definition", {}).get("nodes", [])
            agent_names = []
            for node in nodes:
                agent = await _agents.get(str(node.get("agent_id") or ""))
                agent_names.append(
                    (agent.get("name") if agent else None)
                    or node.get("label")
                    or node.get("agent_id")
                )
            base["steps"] = len(nodes)
            base["agent_names"] = agent_names

    return base


_GRAPH_TYPES = {"agent", "workflow", "knowledge_pack"}


def _validar_tipo_de_grafo(resource_type: str) -> None:
    if resource_type not in _GRAPH_TYPES:
        raise APIError(
            422,
            "invalid_field",
            "Este tipo de recurso no dispone de grafo público",
            extra={"field": "resource_type"},
        )


@router.get("/api/explore/{resource_type}/{resource_id}/relations")
async def explore_resource_relations(
    resource_type: str,
    resource_id: str,
    _: str = Depends(require_session),
) -> Dict[str, Any]:
    """Relaciones públicas de un recurso: el cliente arma el grafo con ellas."""
    if resource_type == "official_source":
        relations = await _relations.official_pack_relations(_, resource_id)
    else:
        _validar_tipo_de_grafo(resource_type)
        relations = await _relations.public_relations(resource_type, resource_id)
    if relations is None:
        raise APIError(
            404,
            "not_found",
            "Recurso no encontrado o no es público",
            extra={"resource": resource_type},
        )
    return relations
