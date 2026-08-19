"""Probar un agente público sin enlazarlo.

El módulo se llama `trial` y no `try_agent` para no chocar con el nombre del
handler que contiene.
"""


from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import Depends
from pydantic import BaseModel

from app.api.routes.auth import GroupContext, require_group
from app.api.routes.resource_linking._router import router
from app.api.routes.resource_linking._shared import (
    _agents_store,
    _conns_store,
    _skills_store,
)
from app.errors import APIError
from app.services.chat import stream_chat
from app.services.social_catalog import _PUBLIC_VAL
from app.sql import sql

# db se importa como MÓDULO a propósito: IS_PG debe leerse en el momento de
# la llamada. Traerlo por valor congela el dialecto en el arranque y el
# monkeypatch de los tests no llega — ver
# tests/storage/test_is_pg_en_tiempo_de_llamada.py.
from app.storage.db import open_db
from app.utils import flog


class _AgentTryBody(BaseModel):
    connection_id: str
    message: str

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
            sql("queries/resource_linking:trial_missing_deps"),
            ("agent", agent_id, _PUBLIC_VAL),
        )
    if not row:
        raise APIError(
            404,
            "not_found",
            "Agente no encontrado o no es público",
            extra={"resource": "agent"},
        )

    trial_missing_deps: str = row["trial_missing_deps"] or "warn"

    # Step 2: Get agent config from DB storage
    agents = _agents_store
    agent_data = await agents.get(agent_id, scope)
    if not agent_data:
        raise APIError(
            404, "not_found", "Agente no encontrado", extra={"resource": "agent"}
        )

    # Step 3: Resolve caller's connection (group first, then personal fallback)
    conn_storage = _conns_store
    conn_data = await conn_storage.get(body.connection_id, ctx.group_id)
    if conn_data is None and ctx.group_id != ctx.user:
        conn_data = await conn_storage.get(body.connection_id, ctx.user)
    if conn_data is None:
        raise APIError(
            400,
            "not_found",
            "Connection no encontrada",
            extra={"resource": "connection"},
        )

    # Step 4: Filter skills based on trial_missing_deps policy
    skills_storage = _skills_store
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
            except (json.JSONDecodeError, AttributeError) as exc:
                flog.warning(
                    f"[resource-linking] Evento SSE inválido para {agent_id}: {exc}"
                )

    return {"reply": "".join(reply_parts), "warnings": warnings}
