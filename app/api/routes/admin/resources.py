"""CRUD administrativo de recursos: conexiones, agentes, skills, prompts,
memoria, conocimiento, orquestaciones y grupos."""

from __future__ import annotations

from typing import Any

from fastapi import Depends

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.config.data import AGENTS_DIR as _AGENTS_DIR
from app.errors import APIError
from app.models.request_bodies import (
    ResourceOwnerBody,
    ResourcePayload,
    StatusBody,
    ToolSecurityBody,
    VerificationBody,
)
from app.sql import sql
from app.storage.agent_storage import AgentStorage as _AgentStorage
from app.storage.db import open_db
from app.storage.groups import GroupStorage as _GroupStorage
from app.storage.llm_orchestrations import (
    LLMOrchestrationStorage as _LLMOrchestrationStorage,
)
from app.storage.workflows import WorkflowStorage as _WorkflowStorage

_groups = _GroupStorage()
_agents = _AgentStorage(_AGENTS_DIR)
_workflows = _WorkflowStorage()
_llm_orchestrations = _LLMOrchestrationStorage()


@admin_router.delete("/connections/{conn_id}")
async def admin_delete_connection(
    conn_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.storage.connection_storage import ConnectionStorage

    if not await ConnectionStorage().delete_as_admin(conn_id):
        raise APIError(
            404, "not_found", "Conexión no encontrada", extra={"resource": "connection"}
        )
    return {"ok": True}


@admin_router.put("/agents/{agent_id}")
async def admin_update_agent(
    agent_id: str,
    body: ResourcePayload,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    agent = await _agents.get(agent_id, scope="private")
    if not agent:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    payload = body.payload()
    protected = {"id", "owner_id", "created_at", "scope"}
    updated = {**agent, **{k: v for k, v in payload.items() if k not in protected}}
    new_name = str(updated.get("name") or "").strip()
    if not new_name:
        raise APIError(400, "agent_name_required", "El nombre es obligatorio")
    return await _agents.save(updated, "private", owner_id=agent.get("owner_id"))


@admin_router.delete("/agents/{agent_id}")
async def admin_delete_agent(
    agent_id: str,
    scope: str = "private",
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    if scope not in ("public", "private"):
        raise APIError(
            400,
            "invalid_field",
            "scope debe ser 'public' o 'private'",
            extra={"field": "scope"},
        )
    deleted = await _agents.delete_as_admin(agent_id, scope=scope, allow_public=True)
    if not deleted:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    return {"ok": True}


@admin_router.delete("/skills/{item_id}")
async def admin_delete_skill(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.config.data import SKILLS_DIR
    from app.storage.skill_storage import SkillStorage

    storage = SkillStorage(SKILLS_DIR)
    skill = await storage.get_any(item_id)
    if not skill:
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    await storage.delete(skill["scope"], item_id, owner_id=None, allow_public=True)
    return {"ok": True}


@admin_router.delete("/prompts/{item_id}")
async def admin_delete_prompt(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.storage.prompt_storage import PromptStorage

    storage = PromptStorage()
    prompt = await storage.get_any(item_id)
    if not prompt:
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    await storage.delete_as_admin(prompt["scope"], item_id, allow_public=True)
    return {"ok": True}


@admin_router.get("/tools/{item_id}")
async def admin_get_tool(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.storage.tool_storage import ToolStorage

    tool = await ToolStorage().get_any(item_id)
    if not tool:
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "tool"}
        )
    return tool


@admin_router.delete("/tools/{item_id}")
async def admin_delete_tool(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.storage.tool_storage import ToolStorage

    storage = ToolStorage()
    tool = await storage.get_any(item_id)
    if not tool:
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    await storage.delete(
        tool["scope"],
        item_id,
        owner_id=str(tool["owner_id"]) if tool.get("owner_id") else None,
        allow_public=True,
    )
    return {"ok": True}


@admin_router.put("/tools/{item_id}/security")
async def admin_set_tool_security(
    item_id: str,
    body: ToolSecurityBody,
    admin_user: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.services.tool_policy import TOOL_SECURITY_LABELS
    from app.storage.resource_versions import ResourceVersionStorage
    from app.storage.tool_storage import ToolStorage

    storage = ToolStorage()
    tool = await storage.get_any(item_id)
    if not tool:
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "tool"}
        )
    owner_id = tool.get("owner_id")
    if not owner_id:
        raise APIError(
            403,
            "public_tool_readonly",
            "Las tools públicas de sistema son de solo lectura",
        )
    labels = [
        str(label)
        for label in (tool.get("labels") or [])
        if str(label) not in TOOL_SECURITY_LABELS
    ]
    if body.state != "approved":
        labels.append(body.state)
    saved = await storage.save(
        str(tool.get("scope") or "private"),
        {**tool, "labels": labels},
        str(owner_id),
    )
    await ResourceVersionStorage().create(
        "tool",
        item_id,
        str(owner_id),
        saved,
        admin_user,
        reason=f"security:{body.state}",
    )
    return saved


@admin_router.delete("/memory/{item_id}")
async def admin_delete_memory(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.config.data import MEMORY_DIR
    from app.storage.memory_storage import MemoryStorage

    owner_id, sep, filename = item_id.partition("::")
    if not sep:
        raise APIError(
            422, "invalid_field", "id de memoria no válido", extra={"field": "item_id"}
        )
    if not await MemoryStorage(MEMORY_DIR).delete(filename, owner_id=owner_id):
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    return {"ok": True}


@admin_router.delete("/knowledge/{item_id}")
async def admin_delete_knowledge(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.storage.knowledge import KnowledgeStorage

    if not await KnowledgeStorage().delete(item_id, owner_id=None):
        raise APIError(
            404, "not_found", "Elemento no encontrado", extra={"resource": "item"}
        )
    return {"ok": True}


@admin_router.delete("/workflows/{workflow_id}")
async def admin_delete_workflow(
    workflow_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    if not await _workflows.delete_any(workflow_id):
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
    return {"ok": True}


@admin_router.delete("/llm-orchestrations/{item_id}")
async def admin_delete_llm_orchestration(
    item_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    if not await _llm_orchestrations.delete_any(item_id):
        raise APIError(
            404,
            "not_found",
            "Orquestación LLM no encontrada",
            extra={"resource": "llm_orchestration"},
        )
    return {"ok": True}


@admin_router.delete("/groups/{group_id}")
async def admin_delete_group(
    group_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    if not await _groups.get(group_id):
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    await _groups.delete(group_id)
    return {"ok": True}


@admin_router.post("/groups/{group_id}/status")
async def admin_set_group_status(
    group_id: str, body: StatusBody, _: str = Depends(require_admin)
) -> dict[str, Any]:
    body = body.payload()
    status = str(body.get("status") or "").strip()
    if status not in ("active", "disabled"):
        raise APIError(
            422,
            "invalid_field",
            "status debe ser 'active' o 'disabled'",
            extra={"field": "status"},
        )
    if not await _groups.get(group_id):
        raise APIError(
            404, "not_found", "Grupo no encontrado", extra={"resource": "group"}
        )
    await _groups.set_status(group_id, status)
    return {"ok": True, "status": status}


@admin_router.put("/resources/{resource_type}/{resource_id}/verify")
async def admin_verify_resource(
    resource_type: str,
    resource_id: str,
    body: VerificationBody,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    _valid_types = ("agent", "skill", "knowledge", "prompt", "tool")
    if resource_type not in _valid_types:
        raise APIError(
            422,
            "invalid_field",
            f"resource_type debe ser uno de {_valid_types}",
            extra={"field": "resource_type"},
        )
    body = body.payload()
    verified_val = bool(body.get("verified", False))
    db_val = 1 if verified_val else 0

    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/admin_resources:social_exists"),
            (resource_type, resource_id),
        )
        if not row:
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado en el catálogo social",
                extra={"resource": "resource"},
            )
        await conn.execute(
            sql("queries/admin_resources:set_verified"),
            (db_val, resource_type, resource_id),
        )
        await conn.commit()
    return {"ok": True}


@admin_router.put("/resources/{resource_type}/{resource_id}/owner")
async def admin_set_resource_owner(
    resource_type: str,
    resource_id: str,
    body: ResourceOwnerBody,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Reasigna el propietario de un recurso a otro usuario existente."""
    table_map = {
        "agent": "agents",
        "skill": "skills",
        "prompt": "prompts",
        "tool": "tools",
        "connection": "connections",
        "knowledge": "knowledge_items",
        "workflow": "agent_workflows",
    }
    table = table_map.get(resource_type)
    if not table:
        raise APIError(
            422,
            "invalid_field",
            f"resource_type debe ser uno de {list(table_map)}",
            extra={"field": "resource_type"},
        )
    body = body.payload()
    new_owner = str(body.get("username") or body.get("owner_id") or "").strip().lower()
    if not new_owner:
        raise APIError(
            400, "invalid_field", "owner_id es obligatorio", extra={"field": "owner_id"}
        )

    async with open_db() as conn:
        user_row = await conn.fetchone(
            sql("queries/admin_resources:user_by_username"), (new_owner,)
        )
        if not user_row:
            raise APIError(
                404,
                "not_found",
                "El usuario propietario no existe",
                extra={"resource": "user"},
            )
        if not user_row["is_active"]:
            raise APIError(
                400,
                "invalid_field",
                "El usuario propietario no está activo",
                extra={"field": "owner_id"},
            )
        row = await conn.fetchone(f"SELECT id FROM {table} WHERE id=?", (resource_id,))
        if not row:
            raise APIError(
                404,
                "not_found",
                "Recurso no encontrado",
                extra={"resource": resource_type},
            )
        await conn.execute(
            f"UPDATE {table} SET owner_id=? WHERE id=?",
            (user_row["id"], resource_id),
        )
        await conn.commit()
    return {"ok": True}
