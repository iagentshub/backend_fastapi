"""Stars: marcar y desmarcar un recurso del catálogo."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends

from app.api.routes.auth import require_auth
from app.api.routes.social._router import (
    _VALID_SOCIAL_RESOURCE_TYPES,
    _social_limiter,
    router,
)
from app.errors import APIError
from app.sql import sql
from app.storage.db import IS_PG, open_db


@router.post("/api/{resource_type}/{resource_id}/star")
async def star_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:
    # A4: validar resource_type para evitar contaminación de la tabla resource_stars
    if resource_type not in _VALID_SOCIAL_RESOURCE_TYPES:
        raise APIError(
            422,
            "invalid_field",
            f"Tipo de recurso no válido: {resource_type!r}",
            extra={"field": "resource_type"},
        )

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                sql("queries/social:star_insert_pg"),
                (username, resource_type, resource_id),
            )
        else:
            await conn.execute(
                sql("queries/social:star_insert_sqlite"),
                (username, resource_type, resource_id),
            )
        await conn.execute(
            sql("queries/social:refresh_stars_count"),
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            sql("queries/social:count_stars"),
            (resource_type, resource_id),
        )
    return {"ok": True, "stars": count or 0}

@router.delete("/api/{resource_type}/{resource_id}/star")
async def unstar_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:
    # A4: validar resource_type
    if resource_type not in _VALID_SOCIAL_RESOURCE_TYPES:
        raise APIError(
            422,
            "invalid_field",
            f"Tipo de recurso no válido: {resource_type!r}",
            extra={"field": "resource_type"},
        )

    async with open_db() as conn:
        await conn.execute(
            sql("queries/social:star_delete"),
            (username, resource_type, resource_id),
        )
        await conn.execute(
            sql("queries/social:refresh_stars_count"),
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            sql("queries/social:count_stars"),
            (resource_type, resource_id),
        )
    return {"ok": True, "stars": count or 0}
