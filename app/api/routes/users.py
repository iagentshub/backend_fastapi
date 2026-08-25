"""User directory routes — búsqueda pública, avatar y perfil público básico.

Extraído de auth.py (ver admin.py para el motivo completo del split).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_by_username
from app.errors import APIError
from app.pagination.http import TOTAL_HEADER
from app.sql import sql
from app.storage import avatars
from app.storage.db import open_db

router = APIRouter(prefix="/api/users", tags=["users"])


async def _get_social_fields(username: str) -> dict[str, Any]:
    import json

    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/users:public_profile"),
            (username,),
        )
        if not row:
            return {}
        user_id = row[0]
        followers_count = (
            await conn.fetchval(
                sql("queries/users:count_followers"),
                (user_id,),
            )
            or 0
        )
        following_count = (
            await conn.fetchval(
                sql("queries/users:count_following"),
                (user_id,),
            )
            or 0
        )
    try:
        langs = json.loads(row[2] or "[]")
    except (json.JSONDecodeError, TypeError):
        langs = []
    return {
        "avatar_url": avatars.public_url(
            username, await avatars.checksum_by_username(username)
        ),
        "bio": row[1],
        "languages": langs,
        "email_public": row[3] if row[4] else None,
        "github": row[5],
        "cv": row[6],
        "joined_at": row[7],
        "followers_count": followers_count,
        "following_count": following_count,
    }


@router.get("")
async def search_users(
    q: str | None = None,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    username: str = Depends(require_auth),
) -> list[dict[str, Any]]:
    async with open_db() as conn:
        # La página se recorta en SQL, así que el total requiere un COUNT con
        # el mismo WHERE.
        if response is not None:
            if q:
                total = await conn.fetchval(
                    sql("queries/users:count_matching"),
                    (username, f"%{q}%"),
                )
            else:
                total = await conn.fetchval(
                    sql("queries/users:count_all"), (username,)
                )
            response.headers[TOTAL_HEADER] = str(total or 0)
        if q:
            pattern = f"%{q}%"
            rows = await conn.fetchall(
                sql("queries/users:search_page"),
                (username, pattern, limit, offset),
            )
        else:
            rows = await conn.fetchall(
                sql("queries/users:list_page"),
                (username, limit, offset),
            )
    return [
        {
            "username": row[0],
            "avatar_url": f"/api/users/{row[0]}/avatar" if row[1] else None,
            "followers_count": row[2],
            "public_resources_count": row[3],
        }
        for row in rows
    ]


@router.get("/{username}/avatar")
async def get_avatar(
    username: str,
    request: Request,
    _: str = Depends(require_auth),
):
    """Sirve la foto con su ETag, para que la caché juegue a favor.

    Antes no salía ni una cabecera de caché, así que el navegador aplicaba su
    heurística y el cliente rompía la caché con un `?v=N` que era un contador en
    memoria: volvía a cero al recargar la pantalla y la URL reaparecía apuntando
    a la foto anterior. Ahora la versión es el sha256 del contenido —cambia la
    foto, cambia la URL— y una recarga se resuelve con un 304 de unos bytes en
    vez de descargar la imagen entera.

    `private` porque la respuesta depende de quién pregunta: la ruta exige
    sesión y un proxy compartido no debe guardarla para otros.
    """
    from fastapi.responses import Response

    cabeceras_de_cache = {
        "Cache-Control": "private, max-age=0, must-revalidate",
    }

    avatar = await avatars.get_by_username(username)
    if avatar is None:
        return Response(status_code=204, headers=cabeceras_de_cache)

    etag = f'"{avatar.checksum}"'
    # If-None-Match puede traer varios valores, y un proxy puede debilitarlos
    # con el prefijo W/. Se compara contra cada uno en vez de contra la cadena.
    entrantes = request.headers.get("if-none-match", "")
    if any(
        candidato.strip().removeprefix("W/") == etag
        for candidato in entrantes.split(",")
        if candidato.strip()
    ):
        return Response(
            status_code=304, headers={**cabeceras_de_cache, "ETag": etag}
        )

    return Response(
        content=avatar.content,
        media_type=avatar.mime,
        headers={**cabeceras_de_cache, "ETag": etag},
    )


@router.get("/{username}")
async def get_public_profile(
    username: str,
    _: str = Depends(require_auth),
) -> dict[str, Any]:
    user = await get_user_by_username(username)
    # El invitado tiene fila en `users` pero no perfil: es una cuenta efímera
    # que nadie puede seguir ni visitar dos veces. Para el resto del mundo no
    # existe, y 404 es exactamente eso.
    if not user or user.get("role") == "guest":
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    fields = await _get_social_fields(username)
    return {"username": username, **fields}
