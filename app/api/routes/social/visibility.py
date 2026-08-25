"""Publicar y despublicar un recurso en el catálogo del marketplace.

Las seis rutas hacen lo mismo con distinto tipo: comprueban que el recurso es
publicable, escriben en `resource_social` y lanzan la cascada que corresponda.
La cascada vive en `services/publication_cascade.py`.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import Depends
from pydantic import BaseModel, Field

from app.api.routes.auth import GroupContext, require_auth, require_group
from app.api.routes.social._router import (
    router,
)
from app.errors import APIError
from app.services.publication_cascade import (
    _agent_public_dependency_keys,
    _assert_workflow_tools_distributable,
    _cascade_publish_agent,
    _cascade_publish_workflow,
    _publish_knowledge_cascade,
)
from app.services.publishing import assert_can_publish
from app.services.resource_stores import (
    _agents_store,
    _groups_store,
    _knowledge_packs_store,
    _knowledge_store,
    _prompts_store,
    _skills_store,
    _tools_store,
    _workflows_store,
)
from app.services.social_catalog import (
    _PUBLIC_VAL,
    _assert_not_linked_copy,
    _assert_publicable,
    _check_category,
    _upsert_social,
)
from app.services.tool_access import assert_tools_distributable_by_ids
from app.services.tool_policy import assert_tool_distributable
from app.sql import sql
from app.storage.db import open_db
from app.utils.generators import generate_date
from app.utils.origin import assert_resource_writable


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
    if body.is_public:
        assert_can_publish(ctx.user)
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
    if body.is_public:
        assert_can_publish(username)
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

    if body.is_public:
        await assert_tools_distributable_by_ids(
            [
                str(tool_id)
                for tool_id in (agent.get("tools") or [])
                if f"tool:{tool_id}" in set(selected_dependencies)
            ],
            storage=_tools_store,
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
    if body.is_public:
        assert_can_publish(username)
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
    if body.is_public:
        assert_can_publish(username)
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
    ctx: GroupContext = Depends(require_group),
) -> Dict[str, Any]:
    username = ctx.user
    if body.is_public:
        assert_can_publish(username)
    _check_category(body.category)
    tools = _tools_store
    tool = await tools.get(scope, tool_id)
    if not tool:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )
    assert_resource_writable(tool, "tool")
    if tool.get("owner_id") not in {ctx.user, ctx.group_id}:
        raise APIError(403, "forbidden", "No tienes permisos sobre esta Tool")
    resource_labels = tool.get("labels") or ["private"]
    if body.is_public:
        assert_tool_distributable(tool)

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
    if body.is_public:
        assert_can_publish(ctx.user)
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
    if body.is_public:
        await _assert_workflow_tools_distributable(workflow, ctx.user, ctx.group_id)

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
