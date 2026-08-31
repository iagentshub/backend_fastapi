"""Historial de versiones de agentes, skills y tools.

Vivía en `resource_management.py`, cuyo docstring ya decía que eran dos cosas
—«Version history and visual multi-agent workflows»—. Se separa porque el
fichero estaba en la lista de deuda de tamaño y la regla de esa guarda es que a
un fichero de la lista lo que se le añade sale de ahí, no se suma. El corte no
inventa una frontera: los dos ayudantes de abajo solo los usaba esta mitad.

Las rutas no cambian: mismo prefijo y mismo router del contrato.
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.routes.auth import GroupContext, require_group_session
from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.errors import APIError
from app.pagination.http import publish_offset_page
from app.pagination.models import OffsetParams
from app.storage.agent_storage import AgentStorage
from app.storage.db import open_db
from app.storage.resource_versions import ResourceVersionStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.utils.origin import assert_resource_writable

router = APIRouter(prefix="/api", tags=["resource-management"])
_agents = AgentStorage(AGENTS_DIR)
_skills = SkillStorage(SKILLS_DIR)
_tools = ToolStorage()
_versions = ResourceVersionStorage()


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
