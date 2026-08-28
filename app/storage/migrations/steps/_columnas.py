"""Saber si una columna existe, en los dos motores.

Un paso que retira una columna tiene que ser idempotente: el esquema se
reejecuta entero en cada arranque y el registro de migraciones puede quedar
marcado en bases que ya pasaron por ahí. Preguntar antes de tocar es lo que
permite volver a ejecutar el paso sin que falle.
"""

from __future__ import annotations

from typing import Any


async def columna_existe_sqlite(conn: Any, table: str, column: str) -> bool:
    rows = await conn.execute_fetchall(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in rows)


async def columna_existe_pg(conn: Any, table: str, column: str) -> bool:
    return (
        await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2",
            table,
            column,
        )
        is not None
    )
