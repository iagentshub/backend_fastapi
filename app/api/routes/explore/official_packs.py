"""Packs oficiales dentro de Explorar: listado, detalle y enlazado."""


from __future__ import annotations

from typing import List, Optional

from fastapi import Depends, Query

from app.api.routes.auth import require_session
from app.api.routes.explore._router import router
from app.api.routes.explore._shared import (
    _validate_relation,
)
from app.config.content_languages import (
    CONTENT_LANGUAGE_SET,
)
from app.errors import APIError
from app.models.official_source import (
    LinkOfficialPackRequest,
    LinkOfficialPackResult,
    PublicOfficialPack,
    PublicOfficialPackDetail,
)
from app.services.official_pack_service import OfficialPackService

_official_packs = OfficialPackService()

@router.get("/api/explore/official-packs", response_model=List[PublicOfficialPack])
async def explore_official_packs(
    type: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    label: Optional[List[str]] = Query(None),
    language: Optional[List[str]] = Query(None),
    relation: Optional[str] = None,
    username: str = Depends(require_session),
) -> List[PublicOfficialPack]:
    relation_mode = _validate_relation(relation)
    normalized_languages = [
        str(value).strip().lower() for value in (language or []) if str(value).strip()
    ]
    invalid_languages = [
        value for value in normalized_languages if value not in CONTENT_LANGUAGE_SET
    ]
    if invalid_languages:
        raise APIError(
            422,
            "invalid_field",
            "Idioma de contenido no soportado",
            extra={"field": "language", "invalid": invalid_languages},
        )
    return await _official_packs.list_packs(
        username,
        resource_type=type or "all",
        category=category or "",
        query=q or "",
        tag=tag or "",
        labels=label,
        languages=normalized_languages,
        relation=relation_mode,
    )

@router.get(
    "/api/explore/official-packs/{source_id}",
    response_model=PublicOfficialPackDetail,
)
async def explore_official_pack_detail(
    source_id: str,
    username: str = Depends(require_session),
) -> PublicOfficialPackDetail:
    detail = await _official_packs.detail(username, source_id)
    if detail is None:
        raise APIError(
            404,
            "not_found",
            "Pack oficial no encontrado o sin recursos publicos",
            extra={"resource": "official_pack"},
        )
    return detail

@router.post(
    "/api/explore/official-packs/{source_id}/link",
    response_model=LinkOfficialPackResult,
)
async def link_official_pack(
    source_id: str,
    body: LinkOfficialPackRequest,
    username: str = Depends(require_session),
) -> LinkOfficialPackResult:
    try:
        result = await _official_packs.link(username, source_id, body)
    except PermissionError as exc:
        raise APIError(
            400,
            "already_owner",
            "Ya eres el propietario de los recursos de este pack",
            extra={"resource": "official_pack"},
        ) from exc
    except ValueError as exc:
        code = str(exc)
        if code == "official_pack_stale":
            raise APIError(
                409,
                "conflict",
                "El repositorio ha cambiado; vuelve a abrir el pack",
                extra={"resource": "official_pack"},
            ) from exc
        raise APIError(
            422,
            "invalid_field",
            "La seleccion del pack oficial no es valida",
            extra={"field": "component_keys"},
        ) from exc
    if result is None:
        raise APIError(
            404,
            "not_found",
            "Pack oficial no encontrado o sin recursos publicos",
            extra={"resource": "official_pack"},
        )
    return result
