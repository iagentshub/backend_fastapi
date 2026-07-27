"""Version history and visual multi-agent workflows."""

from __future__ import annotations

from typing import Any, Dict, Literal

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import WorkspaceContext, require_workspace
from app.config.data import AGENTS_DIR, DB_FILE
from app.services.workflow_validator import validate_workflow
from app.services.workflow_runner import run_workflow
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.storage import AgentStorage, SkillStorage
from app.storage.workspace_shares import WorkspaceShareStorage
from app.storage.workspaces import WorkspaceStorage
from app.storage.workflows import WorkflowStorage
from app.utils.origin import compute_origin_type

router = APIRouter(prefix="/api", tags=["resource-management"])
_agents = AgentStorage(AGENTS_DIR)
_skills = SkillStorage(DB_FILE)
_versions = ResourceVersionStorage()
_workflows = WorkflowStorage()
_shares = WorkspaceShareStorage(DB_FILE)
_workspace_storage = WorkspaceStorage(DB_FILE)


class WorkflowBody(BaseModel):
    id: str | None = Field(default=None, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    definition: Dict[str, Any]
    labels: list[str] = Field(default_factory=lambda: ["private"])


class WorkflowRunBody(BaseModel):
    input: str = Field(min_length=1, max_length=30_000)


def _check_type(resource_type: str) -> Literal["agent", "skill"]:
    if resource_type not in ("agent", "skill"):
        raise HTTPException(status_code=404, detail="Tipo de recurso no válido")
    return resource_type  # type: ignore[return-value]


async def _owned_resource(
    resource_type: str, resource_id: str, owner_id: str
) -> Dict[str, Any]:
    kind = _check_type(resource_type)
    resource = (
        await _agents.get(resource_id)
        if kind == "agent"
        else await _skills.get_any(resource_id)
    )
    if not resource:
        raise HTTPException(status_code=404, detail="Recurso no encontrado")
    if resource.get("owner_id") != owner_id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede modificarlo")
    return resource


@router.get("/resources/{resource_type}/{resource_id}/versions")
async def versions(
    resource_type: str,
    resource_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> list[Dict[str, Any]]:
    await _owned_resource(resource_type, resource_id, ctx.workspace_id)
    return await _versions.list(resource_type, resource_id, ctx.workspace_id)


@router.get("/resources/{resource_type}/{resource_id}/versions/{version}")
async def version_detail(
    resource_type: str,
    resource_id: str,
    version: int,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    await _owned_resource(resource_type, resource_id, ctx.workspace_id)
    item = await _versions.get(
        _check_type(resource_type), resource_id, ctx.workspace_id, version
    )
    if not item:
        raise HTTPException(status_code=404, detail="Versión no encontrada")
    return item


@router.post("/resources/{resource_type}/{resource_id}/versions/{version}/restore")
async def restore_version(
    resource_type: str,
    resource_id: str,
    version: int,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    await _owned_resource(resource_type, resource_id, ctx.workspace_id)
    item = await _versions.get(
        _check_type(resource_type), resource_id, ctx.workspace_id, version
    )
    if not item:
        raise HTTPException(status_code=404, detail="Versión no encontrada")
    snapshot = {**item["snapshot"], "id": resource_id}
    if resource_type == "agent":
        saved = await _agents.save(snapshot, "private", ctx.workspace_id)
    else:
        saved = await _skills.save("private", snapshot, ctx.workspace_id)
    await _versions.create(
        resource_type,
        resource_id,
        ctx.workspace_id,
        saved,
        ctx.user,
        reason=f"restore:{version}",
    )
    return saved


@router.get("/workflows")
async def list_workflows(
    ctx: WorkspaceContext = Depends(require_workspace),
) -> list[Dict[str, Any]]:
    owner_ids = {ctx.user, ctx.workspace_id}
    own: list[Dict[str, Any]] = []
    for owner_id in owner_ids:
        own.extend(await _workflows.list(owner_id))
    own_keys = {(item["id"], item["owner_id"]) for item in own}

    shared_map: dict[str, list[str]] = {}
    for workspace in await _workspace_storage.list_for_user(ctx.user):
        group_id = str(workspace["id"])
        for resource_id in await _shares.get_workspace_shared_resource_ids(
            group_id, "workflow"
        ):
            shared_map.setdefault(resource_id, []).append(group_id)

    for item in own:
        if item["id"] in shared_map:
            item["_group_ids"] = shared_map[item["id"]]

    shared: list[Dict[str, Any]] = []
    for item in await _workflows.list_by_ids(list(shared_map)):
        if (item["id"], item["owner_id"]) in own_keys:
            continue
        if not await _workspace_storage.owner_is_active(item["owner_id"]):
            continue
        item["_shared"] = True
        item["_group_ids"] = shared_map[item["id"]]
        item["_group_id"] = shared_map[item["id"]][0]
        shared.append(item)
    result = own + shared
    for item in result:
        item["origin_type"] = compute_origin_type(item)
    return sorted(result, key=lambda item: item["updated_at"], reverse=True)


async def _accessible_workflow(
    workflow_id: str, ctx: WorkspaceContext
) -> Dict[str, Any] | None:
    item = await _workflows.get_any(workflow_id)
    if not item:
        return None
    if item["owner_id"] in {ctx.user, ctx.workspace_id}:
        return item
    for workspace in await _workspace_storage.list_for_user(ctx.user):
        shared_ids = await _shares.get_workspace_shared_resource_ids(
            str(workspace["id"]), "workflow"
        )
        if workflow_id in shared_ids:
            item["_shared"] = True
            item["_group_id"] = str(workspace["id"])
            return item
    return None


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    item = await _accessible_workflow(workflow_id, ctx)
    if not item:
        raise HTTPException(status_code=404, detail="Orquestación no encontrada")
    item["origin_type"] = compute_origin_type(item)
    return item


@router.post("/workflows")
async def save_workflow(
    body: WorkflowBody,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    if body.id:
        existing = await _workflows.get_any(body.id)
        if existing and existing["owner_id"] != ctx.workspace_id:
            raise HTTPException(
                status_code=403,
                detail="Las orquestaciones compartidas son de solo lectura",
            )
    try:
        definition = validate_workflow(body.definition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _workflows.save(
        ctx.workspace_id,
        {
            "id": body.id,
            "name": body.name,
            "description": body.description,
            "definition": definition,
            "labels": body.labels,
        },
    )


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, bool]:
    if not await _workflows.delete(workflow_id, ctx.workspace_id):
        raise HTTPException(status_code=404, detail="Orquestación no encontrada")
    return {"ok": True}


@router.post("/workflows/{workflow_id}/run")
async def run_saved_workflow(
    workflow_id: str,
    body: WorkflowRunBody,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> StreamingResponse:
    workflow = await _accessible_workflow(workflow_id, ctx)
    if not workflow:
        raise HTTPException(status_code=404, detail="Orquestación no encontrada")
    try:
        definition = validate_workflow(workflow["definition"])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"La orquestación guardada ya no es válida: {exc}",
        ) from exc

    async def resolve(agent_id: str):
        agent = await _agents.get(agent_id)
        if not agent:
            raise RuntimeError(f"El agente {agent_id} no está disponible")
        if agent.get("owner_id") not in {ctx.user, ctx.workspace_id}:
            shared_agent = False
            for workspace in await _workspace_storage.list_for_user(ctx.user):
                shared_ids = await _shares.get_workspace_shared_resource_ids(
                    str(workspace["id"]), "agent"
                )
                if agent_id in shared_ids:
                    shared_agent = True
                    break
            if not shared_agent:
                raise RuntimeError(f"El agente {agent_id} no está disponible para tu grupo")
        connection_id = str(agent.get("connection_id") or "")
        if not connection_id:
            raise RuntimeError(f"El agente {agent.get('name')} no tiene conexión")
        from app.api.routes.connections import _get_conn_any

        connection = await _get_conn_any(connection_id, ctx.user, ctx.workspace_id)
        if not connection:
            raise RuntimeError(f"La conexión del agente {agent.get('name')} no está disponible")
        return agent, connection

    async def events():
        try:
            async for event in run_workflow(
                definition, body.input, resolve
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
