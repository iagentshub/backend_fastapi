"""User directory routes — búsqueda pública, avatar y perfil público básico.

Extraído de auth.py (ver admin.py para el motivo completo del split).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from app.api.pagination import TOTAL_HEADER
from app.api.routes.auth import require_auth
from app.auth.auth import get_user_by_username
from app.errors import APIError
from app.storage.db import open_db

router = APIRouter(prefix="/api/users", tags=["users"])


async def _get_social_fields(username: str) -> dict[str, Any]:
    import json

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT id, avatar, bio, languages, email, is_email_public, github, cv, created_at "
            "FROM users WHERE username = ?",
            (username,),
        )
        if not row:
            return {}
        user_id = row[0]
        followers_count = (
            await conn.fetchval(
                "SELECT COUNT(*) FROM user_follows WHERE following = ?",
                (user_id,),
            )
            or 0
        )
        following_count = (
            await conn.fetchval(
                "SELECT COUNT(*) FROM user_follows WHERE follower = ?",
                (user_id,),
            )
            or 0
        )
    try:
        langs = json.loads(row[3] or "[]")
    except (json.JSONDecodeError, TypeError):
        langs = []
    return {
        "avatar_url": f"/api/users/{username}/avatar" if row[1] else None,
        "bio": row[2],
        "languages": langs,
        "email_public": row[4] if row[5] else None,
        "github": row[6],
        "cv": row[7],
        "joined_at": row[8],
        "followers_count": followers_count,
        "following_count": following_count,
    }


@router.get("")
async def search_users(
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
    username: str = Depends(require_auth),
) -> list[dict[str, Any]]:
    limit = min(limit, 50)
    async with open_db() as conn:
        # El total va en cabecera (ver app/api/pagination.py). La página se
        # recorta en SQL, así que hace falta su propio COUNT con el mismo WHERE.
        if response is not None:
            if q:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM users u "
                    "WHERE u.id != ? AND LOWER(u.username) LIKE LOWER(?)",
                    (username, f"%{q}%"),
                )
            else:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM users u WHERE u.id != ?", (username,)
                )
            response.headers[TOTAL_HEADER] = str(total or 0)
        if q:
            pattern = f"%{q}%"
            rows = await conn.fetchall(
                "SELECT u.username, u.avatar, "
                "(SELECT COUNT(*) FROM user_follows WHERE following = u.id) AS followers_count, "
                "(SELECT COUNT(*) FROM resource_social WHERE owner IN (u.id, u.username) AND is_public = 1) AS public_resources_count "
                "FROM users u "
                "WHERE u.id != ? AND LOWER(u.username) LIKE LOWER(?) "
                "ORDER BY u.username LIMIT ? OFFSET ?",
                (username, pattern, limit, offset),
            )
        else:
            rows = await conn.fetchall(
                "SELECT u.username, u.avatar, "
                "(SELECT COUNT(*) FROM user_follows WHERE following = u.id) AS followers_count, "
                "(SELECT COUNT(*) FROM resource_social WHERE owner IN (u.id, u.username) AND is_public = 1) AS public_resources_count "
                "FROM users u "
                "WHERE u.id != ? "
                "ORDER BY u.username LIMIT ? OFFSET ?",
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
async def get_avatar(username: str, _: str = Depends(require_auth)):
    import base64
    import binascii

    from fastapi.responses import Response

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT avatar FROM users WHERE username=?", (username,)
        )

    if not row or not row[0]:
        return Response(status_code=204)

    try:
        data = base64.b64decode(row[0])
    except (binascii.Error, TypeError):
        return Response(status_code=204)

    # Canvas always exports PNG; detect jpeg by magic bytes as fallback
    mime = "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
    return Response(content=data, media_type=mime)


@router.get("/{username}")
async def get_public_profile(
    username: str,
    _: str = Depends(require_auth),
) -> dict[str, Any]:
    user = await get_user_by_username(username)
    if not user:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    fields = await _get_social_fields(username)
    return {"username": username, **fields}
