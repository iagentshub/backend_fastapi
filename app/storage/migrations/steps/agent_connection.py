"""Promueve `connection_id` del blob JSON de un agente a columna propia.

La pregunta «¿qué agentes usan esta conexión?» se resolvía trayendo **todos**
los agentes de la instalación y filtrándolos en Python, con la pregunta
equivalente sobre `user_agent_preferences` resuelta con un `COUNT(*)` dos
líneas más abajo, en la misma función.

El campo vive en `data`, así que no había columna que consultar. Aquí se
promueve, como ya estaban `name`, `scope` y `official_source_id`. El JSON sigue
siendo la fuente: la columna es su espejo, y el upsert la mantiene.

El relleno lee el JSON en Python en vez de con `json_extract`, que existe en
los dos motores pero con sintaxis distinta: una migración que corre una vez
sobre una tabla acotada no compensa mantener dos variantes dialectales de una
expresión que además no usaría el índice.
"""

from __future__ import annotations

import json
from typing import Any


def _connection_id(blob: Any) -> str | None:
    try:
        datos = json.loads(blob or "{}")
    except (json.JSONDecodeError, TypeError):
        # Un agente con el blob corrupto no puede parar la migración de los
        # que sí son legibles: se queda sin columna y el listado lo sigue
        # enseñando, que es como estaba antes de este paso.
        return None
    return str(datos.get("connection_id") or "").strip() or None


async def _rellenar_sqlite(conn: Any) -> None:
    cursor = await conn.execute("SELECT id, owner_id, data FROM agents")
    filas = await cursor.fetchall()
    for agent_id, owner_id, blob in filas:
        connection_id = _connection_id(blob)
        if connection_id:
            await conn.execute(
                "UPDATE agents SET connection_id = ? WHERE id = ? AND owner_id = ?",
                (connection_id, agent_id, owner_id),
            )


async def _rellenar_pg(conn: Any) -> None:
    filas = await conn.fetch("SELECT id, owner_id, data FROM agents")
    for fila in filas:
        connection_id = _connection_id(fila["data"])
        if connection_id:
            await conn.execute(
                "UPDATE agents SET connection_id = $1 "
                "WHERE id = $2 AND owner_id = $3",
                connection_id,
                fila["id"],
                fila["owner_id"],
            )


async def _agent_connection_column_sqlite(conn: Any) -> None:
    cursor = await conn.execute("PRAGMA table_info(agents)")
    columnas = {fila[1] for fila in await cursor.fetchall()}
    if "connection_id" not in columnas:
        await conn.execute("ALTER TABLE agents ADD COLUMN connection_id TEXT")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_connection ON agents(connection_id)"
    )
    await _rellenar_sqlite(conn)


async def _agent_connection_column_pg(conn: Any) -> None:
    await conn.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS connection_id TEXT")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_connection ON agents(connection_id)"
    )
    await _rellenar_pg(conn)
