"""Migraciones de la foto de perfil.

La foto vivía en `users.avatar`, un TEXT con el fichero en base64 dentro de la
tabla que toca cada petición autenticada. Aquí está el trasvase a
`user_avatars` —en bytes— y la retirada de la columna vieja. El porqué completo
está en `app/sql/schema/user_avatars.sql`.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any


async def _columna_existe_sqlite(conn: Any, table: str, column: str) -> bool:
    rows = await conn.execute_fetchall(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in rows)


async def _columna_existe_pg(conn: Any, table: str, column: str) -> bool:
    return (
        await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2",
            table,
            column,
        )
        is not None
    )


def _avatar_migrado(raw: Any) -> tuple[bytes, str, str] | None:
    """Decodifica el base64 de `users.avatar` y deduce su tipo real.

    Devuelve `None` para lo que no sea una imagen reconocible: la columna venía
    de años de subidas y una fila ilegible no puede abortar el arranque.
    """
    if not raw:
        return None
    try:
        binario = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        return None
    if not binario:
        return None

    from app.utils.images import detect_avatar_mime

    mime = detect_avatar_mime(binario)
    if mime is None:
        return None
    return binario, mime, hashlib.sha256(binario).hexdigest()


async def _user_avatars_sqlite(conn: Any) -> None:
    """Saca la foto de perfil de `users` a su propia tabla, en bytes.

    Era un TEXT con el fichero en base64 dentro de la tabla que toca cada
    petición autenticada. El esquema ya crea `user_avatars`; aquí solo se
    trasvasan las filas existentes y se retira la columna vieja.
    """
    if not await _columna_existe_sqlite(conn, "users", "avatar"):
        return

    filas = await conn.execute_fetchall(
        "SELECT id, avatar FROM users WHERE avatar IS NOT NULL AND avatar <> ''"
    )
    for fila in filas:
        migrado = _avatar_migrado(fila[1])
        if migrado is None:
            continue
        binario, mime, digest = migrado
        await conn.execute(
            "INSERT OR REPLACE INTO user_avatars "
            "(owner_id, content, mime, checksum, size_bytes, updated_at) "
            "VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
            (fila[0], binario, mime, digest, len(binario)),
        )
    await conn.execute("ALTER TABLE users DROP COLUMN avatar")


async def _user_avatars_pg(conn: Any) -> None:
    """Gemela de la de SQLite; el razonamiento completo está allí."""
    if not await _columna_existe_pg(conn, "users", "avatar"):
        return

    filas = await conn.fetch(
        "SELECT id, avatar FROM users WHERE avatar IS NOT NULL AND avatar <> ''"
    )
    for fila in filas:
        migrado = _avatar_migrado(fila[1])
        if migrado is None:
            continue
        binario, mime, digest = migrado
        await conn.execute(
            "INSERT INTO user_avatars "
            "(owner_id, content, mime, checksum, size_bytes, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, "
            "to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')) "
            "ON CONFLICT (owner_id) DO UPDATE SET "
            "content = EXCLUDED.content, mime = EXCLUDED.mime, "
            "checksum = EXCLUDED.checksum, size_bytes = EXCLUDED.size_bytes",
            fila[0],
            binario,
            mime,
            digest,
            len(binario),
        )
    await conn.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar")
