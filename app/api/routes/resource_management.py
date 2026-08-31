"""Version history and visual multi-agent workflows."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes.auth import GroupContext, require_group_session
from app.api.routes.llm_limits import (
    workflow_node_quota,
    workflow_start_limiter,
)
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.errors import APIError
from app.models.llm_orchestration import orchestration_id_from_connection
from app.pagination.http import publish_offset_page
from app.pagination.models import OffsetParams
from app.services.connection_access import connection_access
from app.services.tool_access import resolve_accessible_tools
from app.services.workflow_errors import WorkflowPublicError, workflow_error_event
from app.services.workflow_run_executor import start_workflow_run
from app.services.workflow_runner import run_workflow
from app.services.workflow_validator import validate_workflow
from app.storage.agent_storage import AgentStorage
from app.storage.db import open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.resource_executions import ResourceExecutionStorage
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import (
    SKILL_ASSIGNABLE_LABELS,
    SKILL_LABELS,
    SkillStorage,
)
from app.storage.tool_storage import ToolStorage
from app.storage.workflow_runs import TERMINAL_STATUSES, WorkflowRunStorage
from app.storage.workflows import WorkflowStorage
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.origin import assert_resource_writable, compute_origin_type

router = APIRouter(prefix="/api", tags=["resource-management"])
_agents = AgentStorage(AGENTS_DIR)
_skills = SkillStorage(SKILLS_DIR)
_versions = ResourceVersionStorage()
_workflows = WorkflowStorage()
_shares = GroupShareStorage()
_group_storage = GroupStorage()
_tools = ToolStorage()
_workflow_runs = WorkflowRunStorage()
_executions = ResourceExecutionStorage()


class WorkflowBody(BaseModel):
    id: str | None = Field(default=None, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    definition: Dict[str, Any]
    labels: list[str] = Field(default_factory=lambda: ["private"])


class WorkflowRunBody(BaseModel):
    input: str = Field(min_length=1, max_length=30_000)


def _check_type(resource_type: str) -> Literal["agent", "skill", "tool"]:
    if resource_type not in ("agent", "skill", "tool"):
        raise APIError(
            404,
            "invalid_resource_type",
            "Tipo de recurso no válido",
            extra={"type": resource_type},
        )
    return resource_type  # type: ignore[return-value]


async def _owned_resource(
    resource_type: str, resource_id: str, owner_id: str
) -> Dict[str, Any]:
    kind = _check_type(resource_type)
    if kind == "agent":
        resource = await _agents.get(resource_id)
    elif kind == "skill":
        resource = await _skills.get_any(resource_id)
    else:
        resource = await _tools.get_any(resource_id)
    if not resource:
        raise APIError(
            404, "not_found", "Recurso no encontrado", extra={"resource": kind}
        )
    if resource.get("owner_id") != owner_id:
        raise APIError(403, "forbidden", "Solo el propietario puede modificarlo")
    return resource


@router.get("/resources/{resource_type}/{resource_id}/versions")
async def versions(
    resource_type: str,
    resource_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> list[Dict[str, Any]]:
    """Historial de un recurso, de la versión más reciente a la más antigua.

    Era la última ruta de listado que quedó sin paginar. Devuelve solo metadatos
    —id, versión, autor, motivo, fecha—, así que no arrastraba los snapshots,
    pero tampoco tenía cota.
    """
    await _owned_resource(resource_type, resource_id, ctx.group_id)
    page = await _versions.list(
        resource_type,
        resource_id,
        ctx.group_id,
        page=OffsetParams(limit=limit, offset=offset),
    )
    publish_offset_page(response, page)
    return list(page.items)


@router.get("/resources/{resource_type}/{resource_id}/versions/{version}")
async def version_detail(
    resource_type: str,
    resource_id: str,
    version: int,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    await _owned_resource(resource_type, resource_id, ctx.group_id)
    item = await _versions.get(
        _check_type(resource_type), resource_id, ctx.group_id, version
    )
    if not item:
        raise APIError(
            404, "not_found", "Versión no encontrada", extra={"resource": "version"}
        )
    return item


@router.post("/resources/{resource_type}/{resource_id}/versions/{version}/restore")
async def restore_version(
    resource_type: str,
    resource_id: str,
    version: int,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    resource = await _owned_resource(resource_type, resource_id, ctx.group_id)
    assert_resource_writable(resource, resource_type)
    item = await _versions.get(
        _check_type(resource_type), resource_id, ctx.group_id, version
    )
    if not item:
        raise APIError(
            404, "not_found", "Versión no encontrada", extra={"resource": "version"}
        )
    snapshot = {**item["snapshot"], "id": resource_id}
    if resource_type == "agent":
        saved = await _agents.save(snapshot, "private", ctx.group_id)
    elif resource_type == "skill":
        saved = await _skills.save("private", snapshot, ctx.group_id)
    else:
        async with open_db() as conn:
            async with conn.transaction(immediate=True):
                saved = await _tools.save("private", snapshot, ctx.group_id, conn=conn)
                restored = await _tools.restore_version_artifact(
                    resource_id,
                    ctx.group_id,
                    str(item["id"]),
                    snapshot,
                    conn=conn,
                )
                if snapshot.get("binary_sha256") and not restored:
                    raise APIError(
                        409,
                        "artifact_unavailable",
                        "El artefacto binario de esta versión ya no está disponible",
                        extra={"resource": "tool", "version": version},
                    )
                saved = (
                    await _tools.get("private", resource_id, ctx.group_id, conn=conn)
                    or saved
                )
                await _versions.create(
                    resource_type,
                    resource_id,
                    ctx.group_id,
                    saved,
                    ctx.user,
                    reason=f"restore:{version}",
                    conn=conn,
                )
        return saved
    await _versions.create(
        resource_type,
        resource_id,
        ctx.group_id,
        saved,
        ctx.user,
        reason=f"restore:{version}",
    )
    return saved


@router.get("/workflows")
async def list_workflows(
    include_inactive: bool = False,
    ctx: GroupContext = Depends(require_group_session),
) -> list[Dict[str, Any]]:
    owner_ids = {ctx.user, ctx.group_id}
    own: list[Dict[str, Any]] = []
    for owner_id in owner_ids:
        own.extend(await _workflows.list(owner_id))
    own_keys = {(item["id"], item["owner_id"]) for item in own}

    shared_map = await _shares.get_user_shared_resource_groups(ctx.user, "workflow")

    for item in own:
        if item["id"] in shared_map:
            item["_group_ids"] = shared_map[item["id"]]

    shared: list[Dict[str, Any]] = []
    for item in await _workflows.list_by_ids_with_active_owner(list(shared_map)):
        if (item["id"], item["owner_id"]) in own_keys:
            continue
        item["_shared"] = True
        item["_group_ids"] = shared_map[item["id"]]
        item["_group_id"] = shared_map[item["id"]][0]
        shared.append(item)
    result = own + shared
    if not include_inactive:
        result = [item for item in result if item.get("is_active", True)]
    for item in result:
        item["origin_type"] = compute_origin_type(item)
    return sorted(result, key=lambda item: item["updated_at"], reverse=True)


async def _accessible_workflow(
    workflow_id: str, ctx: GroupContext
) -> Dict[str, Any] | None:
    item = await _workflows.get_any(workflow_id)
    if not item:
        return None
    if item["owner_id"] in {ctx.user, ctx.group_id}:
        return item
    for group in await _group_storage.list_for_user(ctx.user):
        shared_ids = await _shares.get_group_shared_resource_ids(
            str(group["id"]), "workflow"
        )
        if workflow_id in shared_ids:
            item["_shared"] = True
            item["_group_id"] = str(group["id"])
            return item
    return None


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    item = await _accessible_workflow(workflow_id, ctx)
    if not item:
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
    item["origin_type"] = compute_origin_type(item)
    return item


@router.post("/workflows")
async def save_workflow(
    body: WorkflowBody,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    role = await get_user_role(ctx.user)
    allowed_labels = (
        SKILL_LABELS
        if role == "admin"
        else SKILL_ASSIGNABLE_LABELS | {"community", "fork"}
    )
    invalid_labels = [label for label in body.labels if label not in allowed_labels]
    if invalid_labels:
        raise APIError(
            422,
            "invalid_field",
            "El origen del recurso solo puede definirlo un administrador",
            extra={"field": "labels", "invalid": invalid_labels},
        )
    workflow_id = body.id
    if workflow_id:
        existing = await _workflows.get_any(workflow_id)
        if existing:
            assert_resource_writable(existing, "workflow")
        if existing and existing["owner_id"] != ctx.group_id:
            raise APIError(
                403,
                "forbidden",
                "Las orquestaciones compartidas son de solo lectura",
                extra={"resource": "workflow"},
            )
        if not existing:
            # Un id entrante solo es válido para editar una fila existente;
            # en altas el id lo genera siempre el servidor.
            workflow_id = None
    try:
        definition = validate_workflow(body.definition)
    except ValueError as exc:
        raise APIError(
            422, "invalid_workflow", str(exc), extra={"field": "definition"}
        ) from exc
    workflow_connection_id = str(
        definition.get("llm_orchestration_connection_id") or ""
    )
    if workflow_connection_id:
        if not orchestration_id_from_connection(workflow_connection_id):
            raise APIError(
                422,
                "invalid_field",
                "El workflow solo admite una orquestación LLM como conexión global",
                extra={"field": "llm_orchestration_connection_id"},
            )
        if not await connection_access.get_accessible(
            workflow_connection_id, ctx.user, ctx.group_id
        ):
            raise APIError(
                422,
                "invalid_field",
                "La orquestación LLM no está configurada o disponible",
                extra={"field": "llm_orchestration_connection_id"},
            )
    return await _workflows.save(
        ctx.group_id,
        {
            "id": workflow_id,
            "name": body.name,
            "description": body.description,
            "definition": definition,
            "labels": body.labels,
        },
    )


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, bool]:
    existing = await _workflows.get_any(workflow_id)
    if existing:
        assert_resource_writable(existing, "workflow")
    if not await _workflows.delete(workflow_id, ctx.group_id):
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
    return {"ok": True}


async def _set_workflow_active(
    workflow_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    existing = await _workflows.get_any(workflow_id)
    if existing:
        assert_resource_writable(existing, "workflow")
    if not await _workflows.set_active(workflow_id, ctx.group_id, active):
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
    estado = "activada" if active else "desactivada"
    flog.info(f"Orquestación {estado}: {workflow_id}", username=ctx.user)
    return {"ok": True, "is_active": active}


@router.post("/workflows/{workflow_id}/activate")
async def activate_workflow(
    workflow_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_workflow_active(workflow_id, True, ctx)


@router.post("/workflows/{workflow_id}/deactivate")
async def deactivate_workflow(
    workflow_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_workflow_active(workflow_id, False, ctx)


async def _prepare_workflow_run(workflow_id: str, ctx: GroupContext):
    workflow = await _accessible_workflow(workflow_id, ctx)
    if not workflow:
        raise APIError(
            404,
            "not_found",
            "Orquestación no encontrada",
            extra={"resource": "workflow"},
        )
    if not workflow.get("is_active", True):
        raise APIError(
            409,
            "resource_inactive",
            "Esta orquestación está desactivada",
            extra={"resource": "workflow"},
        )
    try:
        definition = validate_workflow(workflow["definition"])
    except ValueError as exc:
        raise APIError(
            422,
            "invalid_workflow",
            f"La orquestación guardada ya no es válida: {exc}",
            extra={"resource": "workflow"},
        ) from exc

    # Validación previa al StreamingResponse: si se esperase a resolver el
    # primer nodo dentro del SSE, el cliente recibiría HTTP 200 para un flujo
    # que ya sabemos que no puede ejecutarse.
    seen_agent_ids: set[str] = set()
    for node in definition.get("nodes") or []:
        agent_id = str(node.get("agent_id") or "")
        if not agent_id or agent_id in seen_agent_ids:
            continue
        seen_agent_ids.add(agent_id)
        agent = await _agents.get(agent_id)
        if agent and not agent.get("is_active", True):
            raise APIError(
                409,
                "resource_inactive",
                f"El agente {agent.get('name') or agent_id} está desactivado",
                extra={"resource": "agent", "resource_id": agent_id},
            )
        connection_id = str(
            definition.get("llm_orchestration_connection_id")
            or (agent or {}).get("connection_id")
            or ""
        )
        if connection_id:
            connection = await connection_access.get_accessible(
                connection_id, ctx.user, ctx.group_id
            )
            if connection and not connection.get("is_active", True):
                raise APIError(
                    409,
                    "resource_inactive",
                    "Una conexión requerida por la orquestación está desactivada",
                    extra={
                        "resource": "connection",
                        "resource_id": connection_id,
                    },
                )

    async def resolve(agent_id: str):
        agent = await _agents.get(agent_id)
        if not agent:
            raise RuntimeError(f"El agente {agent_id} no está disponible")
        if not agent.get("is_active", True):
            raise WorkflowPublicError(
                "resource_inactive",
                f"El agente {agent.get('name') or agent_id} está desactivado",
            )
        if agent.get("owner_id") not in {ctx.user, ctx.group_id}:
            shared_agent = False
            for group in await _group_storage.list_for_user(ctx.user):
                shared_ids = await _shares.get_group_shared_resource_ids(
                    str(group["id"]), "agent"
                )
                if agent_id in shared_ids:
                    shared_agent = True
                    break
            if not shared_agent:
                raise RuntimeError(
                    f"El agente {agent_id} no está disponible para tu grupo"
                )
        role = await get_user_role(ctx.user)
        agent = {
            **agent,
            "resolved_tools": await resolve_accessible_tools(
                agent.get("tools") or [],
                user_id=ctx.user,
                group_id=ctx.group_id,
                is_admin=role == "admin",
                storage=_tools,
                shares=_shares,
                groups=_group_storage,
            ),
        }
        connection_id = str(
            definition.get("llm_orchestration_connection_id")
            or agent.get("connection_id")
            or ""
        )
        if not connection_id:
            raise WorkflowPublicError(
                "invalid_field", f"El agente {agent.get('name')} no tiene conexión"
            )
        if (
            ctx.group_id != ctx.user
            and not orchestration_id_from_connection(connection_id)
            and not await _group_storage.has_resource_permission(
                ctx.group_id,
                ctx.user,
                "connections",
                connection_id,
                "via_agent",
            )
        ):
            raise WorkflowPublicError(
                "forbidden",
                f"No tienes permiso para usar la conexión del agente {agent.get('name')}",
            )
        connection = await connection_access.get_accessible(
            connection_id, ctx.user, ctx.group_id
        )
        if not connection:
            raise WorkflowPublicError(
                "upstream_error",
                f"La conexión del agente {agent.get('name')} no está disponible",
            )
        if not connection.get("is_active", True):
            raise WorkflowPublicError(
                "resource_inactive",
                f"La conexión del agente {agent.get('name')} está desactivada",
            )
        if connection.get("_llm_orchestration") and ctx.group_id != ctx.user:
            for target_id, target in (connection.get("_connections") or {}).items():
                if target.get("owner_id") == ctx.user:
                    continue
                if not await _group_storage.has_resource_permission(
                    ctx.group_id,
                    ctx.user,
                    "connections",
                    target_id,
                    "via_agent",
                ):
                    raise WorkflowPublicError(
                        "forbidden",
                        "No tienes permiso para usar una conexión de la "
                        "orquestación LLM",
                    )
        return agent, connection

    return workflow, definition, resolve


@router.post("/workflows/{workflow_id}/run")
async def run_saved_workflow(
    workflow_id: str,
    request: Request,
    body: WorkflowRunBody,
    ctx: GroupContext = Depends(require_group_session),
    _rl: None = Depends(workflow_start_limiter),
) -> StreamingResponse:
    workflow, definition, resolve = await _prepare_workflow_run(workflow_id, ctx)
    consume_quota = workflow_node_quota(request)
    execution_lease = await _executions.acquire(
        resource_type="workflow",
        resource_id=workflow_id,
        resource_owner=str(workflow.get("owner_id") or ctx.group_id),
        started_by=ctx.user,
    )
    if execution_lease is None:
        raise APIError(
            409,
            "resource_execution_in_progress",
            "Este workflow ya está en curso para tu usuario",
            extra={"resource": "workflow", "resource_id": workflow_id},
        )

    async def events():
        try:
            async for event in run_workflow(
                definition, body.input, resolve, consume_quota=consume_quota
            ):
                if event.get("type") == "heartbeat":
                    await _executions.heartbeat(execution_lease)
                    yield ": keep-alive\n\n"
                    continue
                await _executions.heartbeat(execution_lease)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            # El SSE ya empezó a emitir, así que el handler global de app.py
            # no puede intervenir: el fallo se convierte en trama de error.
            event = workflow_error_event(exc, context="workflow")
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            await _executions.safe_release(execution_lease)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/workflows/{workflow_id}/runs", status_code=202)
async def start_saved_workflow_run(
    workflow_id: str,
    request: Request,
    body: WorkflowRunBody,
    ctx: GroupContext = Depends(require_group_session),
    _rl: None = Depends(workflow_start_limiter),
) -> Dict[str, Any]:
    workflow, definition, resolve = await _prepare_workflow_run(workflow_id, ctx)
    agent_snapshots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in definition.get("nodes") or []:
        agent_id = str(node.get("agent_id") or "")
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        agent = await _agents.get(agent_id)
        agent_snapshots.append(
            {"id": agent_id, "name": (agent or {}).get("name") or agent_id}
        )
    run_id = generate_id(24)
    execution_lease = await _executions.acquire(
        resource_type="workflow",
        resource_id=workflow_id,
        resource_owner=str(workflow.get("owner_id") or ctx.group_id),
        started_by=ctx.user,
        run_id=run_id,
    )
    if execution_lease is None:
        raise APIError(
            409,
            "resource_execution_in_progress",
            "Este workflow ya está en curso para tu usuario",
            extra={"resource": "workflow", "resource_id": workflow_id},
        )
    try:
        run = await _workflow_runs.create(
            workflow_id=workflow_id,
            started_by=ctx.user,
            group_id=ctx.group_id,
            workflow_name=str(workflow.get("name") or workflow_id),
            definition=definition,
            agents=agent_snapshots,
            input_text=body.input,
            run_id=run_id,
        )
    except Exception:
        await _executions.safe_release(execution_lease)
        raise
    try:
        start_workflow_run(
            run["id"],
            definition,
            body.input,
            resolve,
            workflow_node_quota(request),
            execution_lease,
        )
    except Exception:
        await _workflow_runs.set_status(
            run["id"],
            "failed",
            error="No se pudo iniciar la tarea de ejecución",
        )
        await _executions.safe_release(execution_lease)
        raise
    return run


@router.get("/workflow-runs")
async def list_workflow_runs(
    limit: int = 100,
    ctx: GroupContext = Depends(require_group_session),
) -> list[Dict[str, Any]]:
    await _workflow_runs.fail_stale()
    return await _workflow_runs.list_for_user(ctx.user, limit=limit)


@router.get("/workflow-runs/{run_id}")
async def workflow_run_detail(
    run_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    run = await _workflow_runs.get_for_user(run_id, ctx.user)
    if not run:
        raise APIError(
            404,
            "not_found",
            "Ejecución no encontrada",
            extra={"resource": "workflow_run"},
        )
    return run


@router.get("/workflow-runs/{run_id}/events")
async def stream_workflow_run_events(
    run_id: str,
    after: int = 0,
    ctx: GroupContext = Depends(require_group_session),
) -> StreamingResponse:
    run = await _workflow_runs.get_for_user(run_id, ctx.user)
    if not run:
        raise APIError(
            404,
            "not_found",
            "Ejecución no encontrada",
            extra={"resource": "workflow_run"},
        )

    async def persisted_events():
        cursor = max(0, after)
        idle_ticks = 0
        while True:
            events = await _workflow_runs.events_after(run_id, cursor)
            for event in events:
                cursor = int(event["sequence"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            current = await _workflow_runs.get_for_user(run_id, ctx.user)
            if not current:
                return
            if (
                current["status"] in TERMINAL_STATUSES
                and cursor >= current["last_sequence"]
            ):
                return
            if not events:
                idle_ticks += 1
                if idle_ticks >= 30:
                    idle_ticks = 0
                    yield ": keep-alive\n\n"
            else:
                idle_ticks = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        persisted_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/workflow-runs/{run_id}/cancel")
async def cancel_workflow_run(
    run_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    run = await _workflow_runs.get_for_user(run_id, ctx.user)
    if not run:
        raise APIError(
            404,
            "not_found",
            "Ejecución no encontrada",
            extra={"resource": "workflow_run"},
        )
    if run["status"] in ("completed", "failed"):
        raise APIError(409, "workflow_run_not_active", "La ejecución ya ha terminado")
    if run["status"] != "cancelled":
        run = await _workflow_runs.request_cancel(run_id, ctx.user) or run
    return run
