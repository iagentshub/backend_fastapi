"""CRUD de orquestaciones de conexiones LLM."""

from __future__ import annotations

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import GroupContext, require_group_session
from app.config.data import AGENTS_DIR
from app.connections import is_chat_provider
from app.errors import APIError
from app.models.llm_orchestration import orchestration_connection_id
from app.services.connection_access import connection_access
from app.storage.agent_storage import AgentStorage
from app.storage.db import PH, open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.llm_orchestration_bindings import LLMOrchestrationBindingStorage
from app.storage.llm_orchestrations import LLMOrchestrationStorage
from app.utils import flog
from app.utils.origin import assert_resource_writable

router = APIRouter(prefix="/api/llm-orchestrations", tags=["llm-orchestrations"])
_storage = LLMOrchestrationStorage()
_bindings = LLMOrchestrationBindingStorage()
_agents = AgentStorage(AGENTS_DIR)
_shares = GroupShareStorage()
_groups = GroupStorage()


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


class LLMOrchestrationBindingBody(BaseModel):
    candidates: list[CandidateBody] = Field(min_length=2, max_length=20)
    router_connection_id: str | None = Field(default=None, max_length=300)


async def _shared_view(item: Dict[str, Any], user: str) -> Dict[str, Any]:
    """Expose a shared definition with only the current user's private binding."""
    binding = await _bindings.get(str(item["id"]), user)
    result = dict(item)
    result["_shared"] = True
    result["_binding_configured"] = binding is not None
    if binding:
        result["candidates"] = binding["candidates"]
        result["router_connection_id"] = binding.get("router_connection_id")
    else:
        result["candidates"] = [
            {
                "connection_id": "",
                "routing_hint": str(candidate.get("routing_hint") or ""),
            }
            for candidate in item.get("candidates") or []
        ]
        result["router_connection_id"] = None
    return result


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
            item["_group_id"] = group_id
            return await _shared_view(item, ctx.user)
    raise APIError(
        404, "not_found", "Orquestación LLM no encontrada",
        extra={"resource": "llm_orchestration"},
    )


async def _validate_connections(body: LLMOrchestrationBody, ctx: GroupContext) -> None:
    ids = [item.connection_id for item in body.candidates]
    if body.router_connection_id:
        ids.append(body.router_connection_id)
    for connection_id in set(ids):
        connection = await connection_access.get_accessible(
            connection_id, ctx.user, ctx.group_id
        )
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
        if not is_chat_provider(str(connection.get("type") or "").lower()):
            raise APIError(
                422,
                "invalid_field",
                "La orquestación solo admite conexiones LLM",
                extra={"field": "connection_id", "id": connection_id},
            )


@router.get("")
async def list_llm_orchestrations(
    include_inactive: bool = Query(default=False),
    ctx: GroupContext = Depends(require_group_session),
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
            items.append(await _shared_view(item, ctx.user))
    if not include_inactive:
        items = [item for item in items if item.get("is_active", True)]
    return sorted(items, key=lambda item: item["updated_at"], reverse=True)


@router.get("/{item_id}")
async def get_llm_orchestration(
    item_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _accessible(item_id, ctx)


@router.put("/{item_id}/binding")
async def save_llm_orchestration_binding(
    item_id: str,
    body: LLMOrchestrationBindingBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    item = await _accessible(item_id, ctx)
    if not item.get("_shared"):
        raise APIError(
            422,
            "invalid_field",
            "La vinculación personal solo se aplica a orquestaciones compartidas",
            extra={"field": "orchestration_id"},
        )
    if len(body.candidates) != len(item.get("candidates") or []):
        raise APIError(
            422,
            "invalid_field",
            "Debes asignar una conexión a cada candidata",
            extra={"field": "candidates"},
        )
    ids = [candidate.connection_id for candidate in body.candidates]
    if len(ids) != len(set(ids)):
        raise APIError(
            422,
            "invalid_field",
            "Las conexiones candidatas no pueden repetirse",
            extra={"field": "candidates"},
        )
    source = await _storage.get_any(item_id)
    if not source:
        raise APIError(404, "not_found", "Orquestación LLM no encontrada")
    if source.get("mode") == "balanced" and not body.router_connection_id:
        raise APIError(
            422,
            "invalid_field",
            "El modo balanceado necesita una conexión enrutadora",
            extra={"field": "router_connection_id"},
        )
    definition = {
        "candidates": [
            {
                "connection_id": binding_candidate.connection_id,
                "routing_hint": str(source_candidate.get("routing_hint") or ""),
            }
            for binding_candidate, source_candidate in zip(
                body.candidates, source.get("candidates") or [], strict=True
            )
        ],
        "router_connection_id": body.router_connection_id
        if source.get("mode") == "balanced"
        else None,
    }
    validation_body = LLMOrchestrationBody(
        name=str(source.get("name") or item_id),
        mode=source.get("mode") or "stack",
        candidates=definition["candidates"],
        router_connection_id=definition["router_connection_id"],
    )
    await _validate_connections(validation_body, ctx)
    await _bindings.save(item_id, ctx.user, definition)
    return await _shared_view(source, ctx.user)


@router.post("")
async def save_llm_orchestration(
    body: LLMOrchestrationBody, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    await _validate_connections(body, ctx)
    item_id = body.id
    if item_id:
        existing = await _storage.get_any(item_id)
        if existing:
            assert_resource_writable(existing, "llm_orchestration")
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
    item_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, bool]:
    item = await _owned(item_id, ctx)
    assert_resource_writable(item, "llm_orchestration")
    virtual_connection_id = orchestration_connection_id(item_id)
    referenced = [
        agent
        for agent in await _agents.list("all")
        if agent.get("connection_id") == virtual_connection_id
    ]
    async with open_db() as conn:
        preference_count = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM user_agent_preferences "
                f"WHERE connection_id={PH}",
                (virtual_connection_id,),
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
    item = await _owned(item_id, ctx)
    assert_resource_writable(item, "llm_orchestration")
    await _storage.set_active(item_id, ctx.group_id, active)
    return {"ok": True, "is_active": active}


@router.post("/{item_id}/activate")
async def activate_llm_orchestration(
    item_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_active(item_id, True, ctx)


@router.post("/{item_id}/deactivate")
async def deactivate_llm_orchestration(
    item_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_active(item_id, False, ctx)
