"""Traer al recurso enlazado los cambios del original.

Solo aplica a copias con `linked_to_id`: es lo que evita que enlazar sea una
foto fija del recurso el día que se copió.
"""


from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, HTTPException

from app.api.routes.auth import require_session
from app.api.routes.resource_linking._router import router
from app.api.routes.resource_linking._shared import (
    _agents_store,
    _prompts_store,
    _skills_store,
    _tools_store,
)
from app.errors import APIError
from app.services.social_catalog import _assert_public
from app.sql import sql

# db se importa como MÓDULO a propósito: IS_PG debe leerse en el momento de
# la llamada. Traerlo por valor congela el dialecto en el arranque y el
# monkeypatch de los tests no llega — ver
# tests/storage/test_is_pg_en_tiempo_de_llamada.py.
from app.storage.db import open_db


@router.post("/api/agents/private/{agent_id}/sync")
async def sync_linked_agent(
    agent_id: str,
    username: str = Depends(require_session),
) -> Dict[str, Any]:
    agents = _agents_store
    local = await agents.get(agent_id, "private")
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/resource_linking:linked_ref"),
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
    username: str = Depends(require_session),
) -> Dict[str, Any]:
    skills = _skills_store
    local = await skills.get("private", skill_id)
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Skill no encontrada", extra={"resource": "skill"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/resource_linking:linked_ref"),
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
    username: str = Depends(require_session),
) -> Dict[str, Any]:
    tools = _tools_store
    local = await tools.get("private", tool_id)
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Tool no encontrada", extra={"resource": "tool"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/resource_linking:linked_ref"),
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
    username: str = Depends(require_session),
) -> Dict[str, Any]:
    prompts = _prompts_store
    local = await prompts.get("private", prompt_id)
    if not local or local.get("owner_id") != username:
        raise APIError(
            404, "not_found", "Prompt no encontrado", extra={"resource": "prompt"}
        )

    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/resource_linking:linked_ref"),
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
