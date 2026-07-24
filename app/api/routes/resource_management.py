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
from app.storage.workflows import WorkflowStorage

router = APIRouter(prefix="/api", tags=["resource-management"])
_agents = AgentStorage(AGENTS_DIR)
_skills = SkillStorage(DB_FILE)
_versions = ResourceVersionStorage()
_workflows = WorkflowStorage()


class WorkflowBody(BaseModel):
    id: str | None = Field(default=None, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    definition: Dict[str, Any]


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
    return await _workflows.list(ctx.workspace_id)


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    item = await _workflows.get(workflow_id, ctx.workspace_id)
    if not item:
        raise HTTPException(status_code=404, detail="Orquestación no encontrada")
    return item


@router.post("/workflows")
async def save_workflow(
    body: WorkflowBody,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
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
    workflow = await _workflows.get(workflow_id, ctx.workspace_id)
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
        if not agent or agent.get("owner_id") != ctx.workspace_id:
            raise RuntimeError(f"El agente {agent_id} no pertenece al workspace")
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
