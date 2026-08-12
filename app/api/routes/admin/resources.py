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
    VerificationBody,
)
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


async def _username_map(conn: Any) -> dict[str, str]:
    rows = await conn.fetchall("SELECT id, username FROM users")
    return {str(row[0]): str(row[1]) for row in rows}


@admin_router.get("/connections")
async def admin_list_connections(
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    import json as _json

    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, created_at "
            "FROM connections ORDER BY created_at DESC"
        )
        username_map = await _username_map(conn)
    result = []
    for row in rows:
        d = (
            dict(row)
            if isinstance(row, dict)
            else {
                "id": row[0],
                "owner_id": row[1],
                "provider_account_id": row[2],
                "name": row[3],
                "data": row[4],
                "tokens_in": row[5],
                "tokens_out": row[6],
                "created_at": row[7],
            }
        )
        try:
            data = _json.loads(d.get("data") or "{}")
        except (_json.JSONDecodeError, TypeError):
            data = {}
        result.append(
            {
                "id": d["id"],
                "owner_id": d["owner_id"],
                "owner_username": username_map.get(d["owner_id"], d["owner_id"]),
                "provider_account_id": d.get("provider_account_id"),
                "name": d["name"],
                "type": data.get("type", ""),
                "tokens_in": d["tokens_in"],
                "tokens_out": d["tokens_out"],
                "created_at": d["created_at"],
            }
        )
    return result


@admin_router.delete("/connections/{conn_id}")
async def admin_delete_connection(
    conn_id: str, _: str = Depends(require_admin)
) -> dict[str, Any]:
    from app.storage.connection_storage import ConnectionStorage

    if not await ConnectionStorage().delete(conn_id, owner_id=None):
        raise APIError(
            404, "not_found", "Conexión no encontrada", extra={"resource": "connection"}
        )
    return {"ok": True}


@admin_router.get("/agents")
async def admin_list_agents(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    agents = await _agents.list(scope="all")
    async with open_db() as conn:
        username_map = await _username_map(conn)
        conn_rows = await conn.fetchall(
            "SELECT id, owner_id, tokens_in, tokens_out FROM connections"
        )
    conn_data = {
        r[0]: {"owner_id": r[1], "tokens_in": r[2], "tokens_out": r[3]}
        for r in conn_rows
    }
    for a in agents:
        conn_id = a.get("connection_id")
        owner = a.get("owner_id")
        if not owner and conn_id and conn_id in conn_data:
            owner = conn_data[conn_id]["owner_id"]
        a["owner_username"] = username_map.get(owner, owner) if owner else None
        # Agregar tokens de la conexión asociada
        if conn_id and conn_id in conn_data:
            a["tokens_in"] = conn_data[conn_id]["tokens_in"]
            a["tokens_out"] = conn_data[conn_id]["tokens_out"]
        else:
            a["tokens_in"] = 0
            a["tokens_out"] = 0
    return agents


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
    deleted = await _agents.delete(agent_id, scope=scope, allow_public=True)
    if not deleted:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )
    return {"ok": True}


@admin_router.get("/skills")
async def admin_list_skills(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    from app.config.data import SKILLS_DIR
    from app.storage.skill_storage import SkillStorage

    async with open_db() as conn:
        username_map = await _username_map(conn)
    items = await SkillStorage(SKILLS_DIR).list("all")
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item.pop("content", None)
    return items


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


@admin_router.get("/prompts")
async def admin_list_prompts(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    from app.storage.prompt_storage import PromptStorage

    async with open_db() as conn:
        username_map = await _username_map(conn)
    items = await PromptStorage().list("all")
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item.pop("content", None)
    return items


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
    await storage.delete(prompt["scope"], item_id, owner_id=None, allow_public=True)
    return {"ok": True}


@admin_router.get("/tools")
async def admin_list_tools(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    from app.storage.tool_storage import ToolStorage

    async with open_db() as conn:
        username_map = await _username_map(conn)
    items = await ToolStorage().list("all")
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item.pop("content", None)
        item.pop("binary_b64", None)
    return items


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
    await storage.delete(tool["scope"], item_id, owner_id=None, allow_public=True)
    return {"ok": True}


@admin_router.get("/memory")
async def admin_list_memory(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    """A diferencia del resto de recursos, el nombre de un fichero de memoria
    lo elige el usuario (no un ID generado) y solo es único por dueño (PK
    compuesta (id, owner_id) en memory_files) — dos usuarios pueden tener
    ambos un fichero "notes". Para que Admin (que asume IDs únicas
    globalmente) pueda listar/enlazar/borrar cada uno sin ambigüedad, el
    "id" que se expone aquí es "{owner_id}::{filename}"."""
    async with open_db() as conn:
        username_map = await _username_map(conn)
        rows = await conn.fetchall(
            "SELECT id, owner_id, content, updated_at FROM memory_files "
            "ORDER BY updated_at DESC"
        )
    return [
        {
            "id": f"{r['owner_id']}::{r['id']}",
            "filename": r["id"],
            "owner_id": r["owner_id"],
            "owner_username": username_map.get(r["owner_id"], r["owner_id"]),
            "size": len(r["content"] or ""),
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


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


@admin_router.get("/knowledge")
async def admin_list_knowledge(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    from app.storage.knowledge import KnowledgeStorage

    async with open_db() as conn:
        username_map = await _username_map(conn)
    items = await KnowledgeStorage().list(owner_id=None)
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item.pop("content", None)
    return items


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


@admin_router.get("/workflows")
async def admin_list_workflows(_: str = Depends(require_admin)) -> list[dict[str, Any]]:
    items = await _workflows.list_all()
    async with open_db() as conn:
        username_map = await _username_map(conn)
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        definition = item.pop("definition", None) or {}
        item["steps"] = len(definition.get("nodes") or [])
    return items


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


@admin_router.get("/llm-orchestrations")
async def admin_list_llm_orchestrations(
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    items = await _llm_orchestrations.list_all()
    async with open_db() as conn:
        username_map = await _username_map(conn)
    for item in items:
        item["owner_username"] = username_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item["candidate_count"] = len(item.get("candidates") or [])
    return items


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


@admin_router.get("/groups")
async def admin_list_groups(
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    import contextlib
    import json as _json

    from app.config.data import AGENTS_DIR

    async with open_db() as conn:
        group_rows = await conn.fetchall(
            "SELECT g.id, g.name, g.created_by, u.username, g.created_at, g.is_active "
            "FROM groups g LEFT JOIN users u ON u.id = g.created_by "
            "ORDER BY g.created_at DESC"
        )
        groups = [
            {
                "id": r[0],
                "name": r[1],
                "created_by": r[2],
                "created_by_username": r[3],
                "created_at": r[4],
                "is_active": bool(r[5]),
                "status": "active" if r[5] else "disabled",
            }
            for r in group_rows
        ]
        mc_rows = await conn.fetchall(
            "SELECT group_id, COUNT(*) FROM group_members GROUP BY group_id"
        )
        member_counts = {r[0]: r[1] for r in mc_rows}
        cs_rows = await conn.fetchall(
            "SELECT owner_id, COUNT(*), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
        conn_stats = {
            r[0]: {"count": r[1], "tokens_in": r[2], "tokens_out": r[3]}
            for r in cs_rows
        }
        kc_rows = await conn.fetchall(
            "SELECT owner_id, COUNT(*) FROM knowledge_items GROUP BY owner_id"
        )
        know_counts = {r[0]: r[1] for r in kc_rows}

    agent_counts: dict[str, int] = {}
    if AGENTS_DIR.exists():
        for cfg_path in AGENTS_DIR.glob("private/*/config.json"):
            with contextlib.suppress(OSError, _json.JSONDecodeError, AttributeError):
                data = _json.loads(cfg_path.read_text())
                owner = data.get("owner_id")
                if owner:
                    agent_counts[owner] = agent_counts.get(owner, 0) + 1

    result = []
    for group in groups:
        group_id = group["id"]
        stats = conn_stats.get(group_id, {"count": 0, "tokens_in": 0, "tokens_out": 0})
        result.append(
            {
                **group,
                "member_count": member_counts.get(group_id, 0),
                "connections_count": stats["count"],
                "tokens_in": stats["tokens_in"],
                "tokens_out": stats["tokens_out"],
                "knowledge_count": know_counts.get(group_id, 0),
                "agents_count": agent_counts.get(group_id, 0),
            }
        )
    return result


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
            "SELECT 1 FROM resource_social WHERE resource_type=? AND resource_id=?",
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
            "UPDATE resource_social SET verified=? WHERE resource_type=? AND resource_id=?",
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
            "SELECT id, username, is_active FROM users WHERE username=?", (new_owner,)
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
