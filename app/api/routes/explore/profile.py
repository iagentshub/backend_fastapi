"""Perfil social: recursos propios y ajenos, follow y feed."""


from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import Depends, Query, Response

from app.api.routes.auth import require_auth
from app.api.routes.explore._router import router
from app.api.routes.explore._shared import (
    STARRED_BY_REQUESTER,
    _add_owner_usernames,
)
from app.api.routes.social._router import _social_limiter
from app.errors import APIError
from app.pagination.http import TOTAL_HEADER
from app.services.social_catalog import _PUBLIC_VAL, PUBLICLY_AVAILABLE_SQL
from app.sql import sql
from app.storage.db import IS_PG, open_db


@router.get("/api/social/me/resources")
async def my_resources(
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """All resource_social rows owned by the current user."""

    async with open_db() as conn:
        conditions: List[str] = ["owner = ?"]
        params: List[Any] = [username]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, is_public, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY updated_at DESC",
            tuple(params),
        )

    rows = []
    for r in raw:
        row = dict(r)
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except (ValueError, TypeError):
            row["tags"] = []
        try:
            row["labels"] = json.loads(row.get("labels") or '["private"]')
        except (ValueError, TypeError):
            row["labels"] = ["private"]
        rows.append(row)

    await _add_owner_usernames(rows)

    # Annotate linked rows with linked_broken flag
    linked_ids = [r["linked_to_id"] for r in rows if r.get("linked_to_id")]
    if linked_ids:
        placeholders = ",".join("?" * len(linked_ids))
        async with open_db() as conn2:
            pub_rows = await conn2.fetchall(
                f"SELECT resource_id FROM resource_social WHERE resource_id IN ({placeholders}) AND is_public = ?",
                tuple(linked_ids) + (_PUBLIC_VAL,),
            )
        still_public = {r["resource_id"] for r in pub_rows}
        for row in rows:
            if row.get("linked_to_id"):
                linked_owner = row.get("linked_to_user") or ""
                still_accessible = linked_owner == username
                row["linked_broken"] = (
                    row["linked_to_id"] not in still_public and not still_accessible
                )

    return {"resources": rows}

@router.get("/api/users/{target_username}/resources")
async def user_resources(
    target_username: str,
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:

    async with open_db() as conn:
        target_id = await conn.fetchval(
            sql("queries/explore:user_id_by_username"), (target_username,)
        )
        if not target_id:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        # Las publicaciones nuevas usan el id interno; las creadas antes de la
        # migracion de identidad conservan el username en owner. El perfil
        # publico debe mostrar ambas sin exigir republicar los recursos.
        conditions: List[str] = [
            "is_public = ?",
            "owner IN (?, ?)",
            PUBLICLY_AVAILABLE_SQL,
        ]
        params: List[Any] = [_PUBLIC_VAL, target_id, target_username]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY stars_count DESC, updated_at DESC",
            tuple(params),
        )

    rows = []
    for r in raw:
        row = dict(r)
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except (ValueError, TypeError):
            row["tags"] = []
        try:
            row["labels"] = json.loads(row.get("labels") or '["private"]')
        except (ValueError, TypeError):
            row["labels"] = ["private"]
        rows.append(row)
    await _add_owner_usernames(rows)
    return rows

@router.post("/api/users/{target}/follow")
async def follow_user(
    target: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:
    async with open_db() as conn:
        row = await conn.fetchone(sql("queries/explore:user_id_by_username"), (target,))
        if not row:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        target_id = row["id"]
        if target_id == username:
            raise APIError(400, "cannot_follow_self", "No puedes seguirte a ti mismo")
        if IS_PG:
            await conn.execute(
                sql("queries/explore:follow_insert_pg"),
                (username, target_id),
            )
        else:
            await conn.execute(
                sql("queries/explore:follow_insert_sqlite"),
                (username, target_id),
            )
        await conn.commit()
    return {"ok": True}

@router.delete("/api/users/{target}/follow")
async def unfollow_user(
    target: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:

    async with open_db() as conn:
        target_id = await conn.fetchval(
            sql("queries/explore:user_id_by_username"), (target,)
        )
        if not target_id:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        await conn.execute(
            sql("queries/explore:unfollow"),
            (username, target_id),
        )
        await conn.commit()
    return {"ok": True}

@router.get("/api/users/{target}/follow-status")
async def follow_status(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:

    async with open_db() as conn:
        target_id = await conn.fetchval(
            sql("queries/explore:user_id_by_username"), (target,)
        )
        if not target_id:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        is_following_row = await conn.fetchone(
            sql("queries/explore:is_following"),
            (username, target_id),
        )
        followers_count = await conn.fetchval(
            sql("queries/explore:count_followers"),
            (target_id,),
        )
        following_count = await conn.fetchval(
            sql("queries/explore:count_following"),
            (target_id,),
        )

    return {
        "following": is_following_row is not None,
        "followers_count": followers_count or 0,
        "following_count": following_count or 0,
    }

@router.get("/api/feed")
async def get_feed(
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    async with open_db() as conn:
        conditions: List[str] = [
            "owner IN (SELECT following FROM user_follows WHERE follower = ?)",
            "is_public = ?",
            PUBLICLY_AVAILABLE_SQL,
        ]
        params: List[Any] = [username, _PUBLIC_VAL]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        if response is not None:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM resource_social WHERE {where}", tuple(params)
            )
            response.headers[TOTAL_HEADER] = str(total or 0)
        # El parámetro de la estrella va delante de los del WHERE: la
        # sustitución de marcadores es posicional.
        page_params = [username, *params, limit, offset]
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, tags, labels, updated_at, "
            f"{STARRED_BY_REQUESTER} AS starred "
            f"FROM resource_social "
            f"WHERE {where} "
            # Mismo desempate por clave primaria que el catálogo: el feed
            # también se recorre por páginas.
            f"ORDER BY updated_at DESC, "
            f"resource_type ASC, resource_id ASC, owner ASC "
            f"LIMIT ? OFFSET ?",
            tuple(page_params),
        )

    rows = []
    for r in raw:
        row = dict(r)
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except (ValueError, TypeError):
            row["tags"] = []
        try:
            row["labels"] = json.loads(row.get("labels") or '["private"]')
        except (ValueError, TypeError):
            row["labels"] = ["private"]
        # SQLite devuelve 0/1 y PostgreSQL un boolean.
        row["starred"] = bool(row.get("starred"))
        rows.append(row)
    await _add_owner_usernames(rows)
    return rows
