"""Fuentes oficiales: alta, sincronización y marcado manual.

Lo que una fuente trae se materializa como recurso normal del admin (ver
services/official_source_sync), así que aquí no hay catálogo paralelo que
revisar ni publicar: solo la fuente y qué componentes suyos se quedan.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.api.routes.llm_limits import official_llm_limiter
from app.config.tool_runtimes import TOOL_RUNTIMES
from app.errors import APIError
from app.models.official_source import INTERNAL_SOURCE_ID, MATERIALIZABLE_TYPES
from app.services.official_source_drafts import OfficialImportDraftService
from app.services.official_source_importer import (
    GitHubImportError,
    OfficialSourceImporter,
    parse_repository_url,
)
from app.services.official_source_sync import OfficialSourceMaterializer
from app.sql import sql
from app.storage.db import open_db
from app.storage.official_source_storage import (
    OFFICIAL_RESOURCE_TABLES,
    OfficialSourceStorage,
)
from app.utils import flog

_storage = OfficialSourceStorage()
_importer = OfficialSourceImporter(_storage)
_materializer = OfficialSourceMaterializer(_storage)
_drafts = OfficialImportDraftService(_storage, _importer, _materializer)


class ImportSourceBody(BaseModel):
    repository_url: str = Field(min_length=1, max_length=500)
    tracking_mode: Literal["release", "branch"] = "release"
    tracking_ref: str = Field(default="", max_length=200)
    import_mode: Literal["deterministic", "llm"] = "deterministic"
    llm_connection_id: Optional[str] = Field(default=None, max_length=300)


class UpdateSourceBody(ImportSourceBody):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    license: str = Field(default="", max_length=100)


class SyncSourceBody(BaseModel):
    """``component_ids`` ausente = solo mirar qué trae la fuente.

    Con lista, esa es exactamente la selección que queda materializada: lo que
    no aparezca se borra.
    """

    component_ids: Optional[List[str]] = Field(default=None, max_length=500)


class MarkOfficialBody(BaseModel):
    official: bool = True


class UpdateDraftComponentBody(BaseModel):
    selected: Optional[bool] = None
    forced_type: Optional[
        Literal["agent", "skill", "prompt", "knowledge", "tool", "memory", "workflow"]
    ] = None
    forced_language: Optional[str] = Field(default=None, max_length=40)
    forced_tool_language: Optional[str] = Field(default=None, max_length=40)
    security_accepted: Optional[bool] = None
    dependencies: Optional[List[str]] = Field(default=None, max_length=500)

    @field_validator("forced_tool_language")
    @classmethod
    def validate_tool_runtime(cls, value: Optional[str]) -> Optional[str]:
        if value not in {None, ""} and value not in TOOL_RUNTIMES:
            raise ValueError("unsupported tool runtime")
        return value


class TransferSourceBody(BaseModel):
    owner_id: str = Field(min_length=1, max_length=100)


def _not_found() -> APIError:
    return APIError(
        404,
        "not_found",
        "Fuente oficial no encontrada",
        extra={"resource": "official_source"},
    )


async def _owned_draft(draft_id: str, admin: str) -> Dict[str, Any]:
    draft = await _storage.get_draft(draft_id)
    if not draft:
        raise APIError(404, "not_found", "Borrador de importación no encontrado")
    if draft["owner_id"] != admin:
        raise APIError(403, "forbidden", "El borrador pertenece a otro administrador")
    return draft


async def _fetch_payload(
    source_id: str, body: Optional[SyncSourceBody], admin: str
) -> Dict[str, Any]:
    """Descarga la fuente y, si hay selección, la aplica."""
    fetched = await _importer.fetch(source_id)
    source = fetched["source"]
    components = fetched["components"]
    materialized = {
        str(item["component_id"]): item
        for item in await _storage.list_resources(source_id)
    }
    applied: Optional[Dict[str, Any]] = None
    if body is not None and body.component_ids is not None:
        applied = await _materializer.materialize(
            source, components, body.component_ids, owner_id=admin
        )
        materialized = {
            str(item["component_id"]): item
            for item in await _storage.list_resources(source_id)
        }
    return {
        "source": source,
        "version": fetched["version"],
        "components": [
            {
                **component.as_dict(),
                "materializable": component.component_type in MATERIALIZABLE_TYPES,
            }
            for component in components
        ],
        # Lo que ya está en la aplicación: el panel lo usa para preseleccionar.
        "selected": sorted(materialized),
        "errors": fetched["errors"],
        "security_warnings": fetched["security_warnings"],
        "applied": applied,
    }


async def _draft_payload(
    draft: Dict[str, Any], *, applied: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    page = await _storage.list_draft_components(str(draft["id"]), limit=500)
    items = page["items"]
    return {
        **draft,
        "draft_id": draft["id"],
        "components": items,
        "selected": sorted(
            str(item["component_id"]) for item in items if item["selected"]
        ),
        "applied": applied,
    }


@admin_router.get("/official-sources")
async def admin_list_official_sources(
    _: str = Depends(require_admin),
) -> List[Dict[str, Any]]:
    sources = await _storage.list_sources()
    for source in sources:
        source["resources"] = await _storage.list_resources(str(source["id"]))
    return sources


@admin_router.post("/official-sources/import")
async def admin_import_official_source(
    body: ImportSourceBody, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    try:
        draft = await _drafts.inspect(
            body.repository_url,
            admin,
            tracking_mode=body.tracking_mode,
            tracking_ref=body.tracking_ref,
            import_mode=body.import_mode,
            llm_connection_id=body.llm_connection_id or "",
        )
    except GitHubImportError as exc:
        raise APIError(422, "official_source_import_failed", str(exc)) from exc
    except ValueError as exc:
        raise APIError(422, "official_source_import_failed", str(exc)) from exc
    source = await _storage.save_source({**draft["source"], "owner_id": admin})
    attached = await _storage.attach_draft_source(
        str(draft["id"]),
        {
            **draft["source"],
            **source,
            "resolved_version": draft["resolved_version"],
            "commit_sha": draft["commit_sha"],
        },
    )
    assert attached is not None
    return await _draft_payload(attached)


@admin_router.post("/official-sources/inspect")
async def admin_inspect_official_source(
    request: Request,
    body: ImportSourceBody,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    if body.import_mode == "llm":
        await official_llm_limiter(request)
    try:
        draft = await _drafts.inspect(
            body.repository_url,
            admin,
            tracking_mode=body.tracking_mode,
            tracking_ref=body.tracking_ref,
            import_mode=body.import_mode,
            llm_connection_id=body.llm_connection_id or "",
        )
    except GitHubImportError as exc:
        raise APIError(422, "official_source_import_failed", str(exc)) from exc
    except ValueError as exc:
        raise APIError(422, "official_source_import_failed", str(exc)) from exc
    return await _draft_payload(draft)


@admin_router.post("/official-sources/inspect-stream")
async def admin_inspect_official_source_stream(
    request: Request,
    body: ImportSourceBody,
    admin: str = Depends(require_admin),
) -> StreamingResponse:
    """Inspección larga con progreso SSE y latidos para evitar timeouts."""
    if body.import_mode == "llm":
        await official_llm_limiter(request)

    async def generate() -> AsyncIterator[str]:
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

        async def progress(event: Dict[str, Any]) -> None:
            await queue.put({"type": "progress", **event})

        async def inspect() -> None:
            try:
                draft = await _drafts.inspect(
                    body.repository_url,
                    admin,
                    tracking_mode=body.tracking_mode,
                    tracking_ref=body.tracking_ref,
                    import_mode=body.import_mode,
                    llm_connection_id=body.llm_connection_id or "",
                    progress=progress,
                )
                await queue.put(
                    {"type": "result", "draft": await _draft_payload(draft)}
                )
            except (GitHubImportError, ValueError) as exc:
                await queue.put({"type": "error", "message": str(exc)})
            except Exception as exc:  # noqa: BLE001
                # Al usuario se le da un mensaje genérico a propósito (el
                # análisis de un repo ajeno puede filtrar rutas o tokens en
                # str(exc)), pero el motivo real tiene que quedar en el log o
                # "no se pudo completar" no es diagnosticable.
                flog.error(
                    f"[official-sources] Análisis fallido: {type(exc).__name__}: {exc}"
                )
                await queue.put(
                    {
                        "type": "error",
                        "message": "No se pudo completar el análisis del repositorio",
                    }
                )

        yield f"data: {json.dumps({'type': 'started'}, ensure_ascii=False)}\n\n"
        task = asyncio.create_task(inspect())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    event = {"type": "heartbeat"}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in {"result", "error"}:
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@admin_router.put("/official-sources/{source_id}")
async def admin_update_official_source(
    source_id: str,
    body: UpdateSourceBody,
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        repository = parse_repository_url(body.repository_url)
        updated = await _storage.update_source(
            source_id,
            {
                **body.model_dump(),
                **repository,
            },
        )
    except GitHubImportError as exc:
        raise APIError(
            422, "invalid_field", str(exc), extra={"field": "repository_url"}
        ) from exc
    except ValueError as exc:
        raise APIError(
            409,
            "already_exists",
            "Ya existe una fuente oficial para este repositorio",
            extra={"resource": "official_source"},
        ) from exc
    if not updated:
        raise _not_found()
    return updated


@admin_router.post("/official-sources/{source_id}/sync")
async def admin_sync_official_source(
    source_id: str,
    request: Request,
    body: SyncSourceBody | None = None,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    source = await _storage.get_source(source_id)
    if not source:
        raise _not_found()
    if source.get("import_mode") == "llm":
        await official_llm_limiter(request)
    try:
        draft = await _drafts.inspect_source(source_id, admin)
        if body is None or body.component_ids is None:
            return await _draft_payload(draft)
        await _storage.replace_draft_selection(
            str(draft["id"]), selected=set(), explicit=set()
        )
        for component_id in body.component_ids:
            updated = await _drafts.update_component(
                str(draft["id"]), component_id, {"selected": True}
            )
            if not updated:
                raise ValueError(f"Componente no encontrado: {component_id}")
        applied = await _drafts.apply(str(draft["id"]), admin)
        refreshed = await _storage.get_draft(str(draft["id"]))
        assert refreshed is not None
        return await _draft_payload(refreshed, applied=applied)
    except KeyError as exc:
        raise _not_found() from exc
    except GitHubImportError as exc:
        raise APIError(422, "official_source_sync_failed", str(exc)) from exc
    except ValueError as exc:
        raise APIError(
            422, "invalid_field", str(exc), extra={"field": "component_ids"}
        ) from exc


@admin_router.get("/official-source-drafts/{draft_id}")
async def admin_get_official_source_draft(
    draft_id: str, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    draft = await _owned_draft(draft_id, admin)
    return await _draft_payload(draft)


@admin_router.get("/official-source-drafts/{draft_id}/components")
async def admin_list_official_source_draft_components(
    draft_id: str,
    offset: int = 0,
    limit: int = 100,
    component_type: Optional[str] = None,
    state: Optional[str] = None,
    q: str = "",
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    await _owned_draft(draft_id, admin)
    return await _storage.list_draft_components(
        draft_id,
        offset=offset,
        limit=limit,
        component_type=component_type,
        state=state,
        query=q,
    )


@admin_router.patch("/official-source-drafts/{draft_id}/components/{component_key}")
async def admin_update_official_source_draft_component(
    draft_id: str,
    component_key: str,
    body: UpdateDraftComponentBody,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    await _owned_draft(draft_id, admin)
    try:
        updated = await _drafts.update_component(
            draft_id, component_key, body.model_dump(exclude_none=True)
        )
    except ValueError as exc:
        raise APIError(422, "invalid_field", str(exc)) from exc
    if not updated:
        raise APIError(404, "not_found", "Componente del borrador no encontrado")
    return updated


@admin_router.get("/official-source-drafts/{draft_id}/diff")
async def admin_get_official_source_draft_diff(
    draft_id: str, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    await _owned_draft(draft_id, admin)
    try:
        return await _drafts.diff(draft_id)
    except KeyError as exc:
        raise APIError(
            404, "not_found", "Borrador de importación no encontrado"
        ) from exc


@admin_router.get("/official-source-drafts/{draft_id}/relations")
async def admin_get_official_source_draft_relations(
    draft_id: str, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    await _owned_draft(draft_id, admin)
    try:
        return await _drafts.relations(draft_id)
    except KeyError as exc:
        raise APIError(
            404, "not_found", "Borrador de importación no encontrado"
        ) from exc


@admin_router.post("/official-source-drafts/{draft_id}/apply")
async def admin_apply_official_source_draft(
    draft_id: str, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    try:
        return await _drafts.apply(draft_id, admin)
    except KeyError as exc:
        raise APIError(
            404, "not_found", "Borrador de importación no encontrado"
        ) from exc
    except PermissionError as exc:
        raise APIError(403, "forbidden", str(exc)) from exc
    except ValueError as exc:
        raise APIError(409, "official_import_not_applicable", str(exc)) from exc


@admin_router.put("/official-sources/{source_id}/owner")
async def admin_transfer_official_source_owner(
    source_id: str,
    body: TransferSourceBody,
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    async with open_db() as conn:
        owner = await conn.fetchone(
            sql("queries/admin_official_sources:active_admin_exists"),
            (body.owner_id,),
        )
    if not owner:
        raise APIError(
            422,
            "invalid_field",
            "El nuevo propietario debe ser un administrador activo",
            extra={"field": "owner_id"},
        )
    try:
        if not await _storage.transfer_owner(source_id, body.owner_id):
            raise _not_found()
    except ValueError as exc:
        raise APIError(409, "owner_transfer_conflict", str(exc)) from exc
    source = await _storage.get_source(source_id)
    assert source is not None
    return source


@admin_router.get("/resources/{resource_type}/{resource_id}/origin")
async def admin_get_resource_origin(
    resource_type: str,
    resource_id: str,
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    origin = await _storage.get_origin(resource_type, resource_id)
    if not origin:
        raise APIError(404, "not_found", "El recurso no tiene procedencia registrada")
    return origin


@admin_router.delete("/official-sources/{source_id}")
async def admin_delete_official_source(
    source_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    if not await _storage.get_source(source_id):
        raise _not_found()
    removed = await _materializer.delete_source(source_id)
    return {"ok": True, "removed_resources": removed}


@admin_router.post("/resources/{resource_type}/{resource_id}/official")
async def admin_mark_resource_official(
    resource_type: str,
    resource_id: str,
    body: MarkOfficialBody | None = None,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    """Marca a mano un recurso como oficial, sin repositorio detrás."""
    if resource_type not in OFFICIAL_RESOURCE_TABLES:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de recurso no válido",
            extra={"field": "resource_type"},
        )
    official = body.official if body else True
    source_id = None
    if official:
        source_id = str((await _storage.ensure_internal_source())["id"])
    await _storage.mark_resource(
        resource_type,
        resource_id,
        admin,
        source_id=source_id,
        component_id=resource_id if official else None,
    )
    return {
        "ok": True,
        "official_source_id": source_id,
        "internal_source_id": INTERNAL_SOURCE_ID,
    }
