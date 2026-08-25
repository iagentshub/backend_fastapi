"""Foto de perfil: lectura, escritura y borrado.

Vive en `user_avatars` y no en una columna de `users` — el porqué está en
`app/sql/schema/user_avatars.sql`. Aquí solo se concentra el acceso para que
ninguna ruta vuelva a escribir el SQL por su cuenta.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import NamedTuple

from app.sql import sql
from app.storage import db as _db
from app.storage.db import open_db


class Avatar(NamedTuple):
    content: bytes
    mime: str
    checksum: str


def checksum_of(content: bytes) -> str:
    """sha256 del contenido: identifica la imagen y sirve de ETag."""
    return hashlib.sha256(content).hexdigest()


async def save(owner_id: str, content: bytes, mime: str) -> str:
    """Guarda la foto y devuelve su checksum."""
    digest = checksum_of(content)
    # `IS_PG` se lee por el módulo y no importando el nombre: los tests lo
    # cambian con monkeypatch y una copia local se quedaría con el valor del
    # import (`tests/storage/test_is_pg_en_tiempo_de_llamada.py`).
    identificador = (
        "queries/user_avatars:upsert_pg"
        if _db.IS_PG
        else "queries/user_avatars:upsert_sqlite"
    )
    async with open_db() as conn:
        await conn.execute(
            sql(identificador),
            (
                owner_id,
                content,
                mime,
                digest,
                len(content),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await conn.commit()
    return digest


async def delete(owner_id: str) -> None:
    async with open_db() as conn:
        await conn.execute(sql("queries/user_avatars:delete_of"), (owner_id,))
        await conn.commit()


async def get_by_username(username: str) -> Avatar | None:
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/user_avatars:content_of"), (username,)
        )
    if not row or not row[0]:
        return None
    # asyncpg devuelve `bytes` y aiosqlite `bytes`, pero un driver puede dar
    # `memoryview`: normalizar aquí evita que cada llamante se acuerde.
    return Avatar(bytes(row[0]), row[1], row[2])


async def checksum_by_username(username: str) -> str | None:
    """El hash sin traer la imagen. Es lo que necesita quien solo va a decidir
    si hay foto y qué URL publicar."""
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/user_avatars:checksum_of"), (username,)
        )
    return row[0] if row else None


async def checksum_by_owner(owner_id: str) -> str | None:
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/user_avatars:checksum_by_owner"), (owner_id,)
        )
    return row[0] if row else None


def public_url(username: str, checksum: str | None) -> str | None:
    """URL pública de la foto, o `None` si no hay.

    Lleva el checksum como versión: al cambiar la foto cambia la URL, así que
    la caché del navegador deja de ser un problema y pasa a ser una ventaja.
    Antes esa versión era un contador en memoria del cliente que volvía a cero
    en cada recarga, con lo que la URL reaparecía apuntando a la foto anterior.
    """
    if not checksum:
        return None
    from urllib.parse import quote

    return f"/api/users/{quote(username)}/avatar?v={checksum[:16]}"
