"""CRUD de orquestaciones de conexiones LLM."""

from __future__ import annotations

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import GroupContext, require_group
from app.api.routes.connections import _get_conn_any
from app.config.data import AGENTS_DIR
from app.config.providers import OPENAI_COMPAT_URLS
from app.errors import APIError
from app.storage.agent_storage import AgentStorage
from app.storage.db import open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.llm_orchestrations import LLMOrchestrationStorage
from app.utils import flog

router = APIRouter(prefix="/api/llm-orchestrations", tags=["llm-orchestrations"])
_storage = LLMOrchestrationStorage()
_agents = AgentStorage(AGENTS_DIR)
_shares = GroupShareStorage()
_groups = GroupStorage()
_SUPPORTED_TYPES = {*OPENAI_COMPAT_URLS, "claude", "ollama"}


class CandidateBody(BaseModel):
    connection_id: str = Field(min_length=1, max_length=300)
    routing_hint: str = Field(default="", max_length=1_000)


class LLMOrchestrationBody(BaseModel):
    id: str | None = Field(default=None, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    mode: Literal["stack", "balanced"] = "stack"
    candidates: list[CandidateBody] = Field(min_length=2, max_length=20)
    router_connection_id: str | None = Field(default=None, max_length=300)
    labels: list[str] = Field(default_factory=lambda: ["private"])

    @model_validator(mode="after")
    def validate_definition(self) -> "LLMOrchestrationBody":
        ids = [item.connection_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Las conexiones candidatas no pueden repetirse")
        if self.mode == "balanced" and not self.router_connection_id:
            raise ValueError("El modo balanceado necesita una conexión enrutadora")
        if self.mode == "stack":
            self.router_connection_id = None
        return self


async def _owned(item_id: str, ctx: GroupContext) -> Dict[str, Any]:
    item = await _storage.get(item_id, ctx.group_id)
    if not item:
        raise APIError(
            404,
            "not_found",
            "Orquestación LLM no encontrada",
            extra={"resource": "llm_orchestration"},
        )
    return item


async def _accessible(item_id: str, ctx: GroupContext) -> Dict[str, Any]:
    item = await _storage.get_any(item_id)
    if not item:
        raise APIError(
            404, "not_found", "Orquestación LLM no encontrada",
            extra={"resource": "llm_orchestration"},
        )
    if item.get("owner_id") in {ctx.user, ctx.group_id}:
        return item
    for group in await _groups.list_for_user(ctx.user):
        group_id = str(group["id"])
        shared_ids = await _shares.get_group_shared_resource_ids(
            group_id, "llm_orchestration"
        )
        if item_id in shared_ids and await _groups.owner_is_active(item["owner_id"]):
            item["_shared"] = True
            item["_group_id"] = group_id
            return item
    raise APIError(
        404, "not_found", "Orquestación LLM no encontrada",
        extra={"resource": "llm_orchestration"},
    )


async def _validate_connections(body: LLMOrchestrationBody, ctx: GroupContext) -> None:
    ids = [item.connection_id for item in body.candidates]
    if body.router_connection_id:
        ids.append(body.router_connection_id)
    for connection_id in set(ids):
        connection = await _get_conn_any(connection_id, ctx.user, ctx.group_id)
        if not connection:
            raise APIError(
                422,
                "invalid_field",
                "Una de las conexiones no está disponible",
                extra={"field": "connection_id", "id": connection_id},
            )
        if not connection.get("is_active", True):
            raise APIError(
                422,
                "resource_inactive",
                "Una de las conexiones está desactivada",
                extra={"resource": "connection", "id": connection_id},
            )
        if str(connection.get("type") or "").lower() not in _SUPPORTED_TYPES:
            raise APIError(
                422,
                "invalid_field",
                "La orquestación solo admite conexiones LLM",
                extra={"field": "connection_id", "id": connection_id},
            )


@router.get("")
async def list_llm_orchestrations(
    include_inactive: bool = Query(default=False),
    ctx: GroupContext = Depends(require_group),
) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    for owner_id in {ctx.user, ctx.group_id}:
        items.extend(await _storage.list(owner_id))
    own_ids = {item["id"] for item in items}
    shared_ids: set[str] = set()
    for group in await _groups.list_for_user(ctx.user):
        shared_ids.update(
            await _shares.get_group_shared_resource_ids(
                str(group["id"]), "llm_orchestration"
            )
        )
    for item_id in shared_ids - own_ids:
        item = await _storage.get_any(item_id)
        if item and await _groups.owner_is_active(item["owner_id"]):
            item["_shared"] = True
            items.append(item)
    if not include_inactive:
        items = [item for item in items if item.get("is_active", True)]
    return sorted(items, key=lambda item: item["updated_at"], reverse=True)


@router.get("/{item_id}")
async def get_llm_orchestration(
    item_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _accessible(item_id, ctx)


@router.post("")
async def save_llm_orchestration(
    body: LLMOrchestrationBody, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    await _validate_connections(body, ctx)
    item_id = body.id
    if item_id:
        existing = await _storage.get_any(item_id)
        if existing and existing.get("owner_id") != ctx.group_id:
            raise APIError(403, "forbidden", "Solo el propietario puede editarla")
        if not existing:
            item_id = None
    payload = body.model_dump()
    payload["id"] = item_id
    saved = await _storage.save(ctx.group_id, payload)
    flog.info(f"Orquestación LLM guardada: {saved['id']}", username=ctx.user)
    return saved


@router.delete("/{item_id}")
async def delete_llm_orchestration(
    item_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, bool]:
    await _owned(item_id, ctx)
    referenced = [
        agent
        for agent in await _agents.list("all")
        if agent.get("llm_orchestration_id") == item_id
    ]
    async with open_db() as conn:
        preference_count = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM user_agent_preferences "
                "WHERE llm_orchestration_id=?",
                (item_id,),
            )
            or 0
        )
    if referenced or preference_count:
        raise APIError(
            409,
            "already_exists",
            "La orquestación está asignada a uno o más agentes",
            extra={
                "resource": "agent",
                "count": len(referenced) + preference_count,
            },
        )
    await _storage.delete(item_id, ctx.group_id)
    return {"ok": True}


async def _set_active(item_id: str, active: bool, ctx: GroupContext) -> Dict[str, Any]:
    await _owned(item_id, ctx)
    await _storage.set_active(item_id, ctx.group_id, active)
    return {"ok": True, "is_active": active}


@router.post("/{item_id}/activate")
async def activate_llm_orchestration(
    item_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_active(item_id, True, ctx)


@router.post("/{item_id}/deactivate")
async def deactivate_llm_orchestration(
    item_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_active(item_id, False, ctx)
