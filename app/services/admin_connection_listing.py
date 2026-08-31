"""Listado paginado de conexiones para el panel, sin filtro de visibilidad.

Los `GET` de `/api/admin` devolvían `SELECT … FROM tabla` sin `WHERE` y sin
cota: el único sitio del producto donde el resultado no lo acota lo que tiene
un usuario, sino lo que tiene la instalación entera. Se retiraron los once, y
de sus equivalentes por tipo solo quedó este, que es el que tiene consumidor:
el selector de conexiones LLM de la importación oficial.

Hubo aquí un `spec` parametrizable y un catálogo aparte con una entrada por
pestaña. Con un solo listado esa indirección solo añadía saltos, así que la
consulta vive ahora al lado de su decodificación.

Dos cosas que respetar si algún día vuelve a haber más de uno.

**El nombre del dueño sale del `JOIN`.** Cada listado llamaba a
`_username_map`, que era `SELECT id, username FROM users` entera, y el panel
pinta varias pestañas por carga: nueve copias de la tabla de usuarios en la
misma sesión.

**La clave keyset tiene que desempatar de verdad.** Aquí basta `id` porque la
PK de `connections` es simple, y por eso se puede usar el motor de dos
columnas. Media docena de tablas de recursos —agents, skills, prompts, tools,
memory_files, llm_orchestrations— tienen PK compuesta `(id, owner_id)`: para un
usuario da igual, porque solo ve los suyos, pero el administrador los ve todos
a la vez y ahí `(created_at, id)` deja de ser única. Un keyset con clave
repetida **se salta filas en el corte de página sin que nada falle**.

`content` no aparece en la proyección. `data` sí, porque de ahí salen `type` y
`model`, que es lo que distingue una conexión de otra en la pantalla.
"""

from __future__ import annotations

import json
from typing import Any

from app.connections import is_chat_provider
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.storage.cursor_page_query import fetch_cursor_page
from app.storage.db import open_db

ROW = "resource_row"
OWNER = "owner_user"
RESOURCE = "admin_connections"

_COLUMNS = (
    f"{ROW}.id, {ROW}.owner_id, {ROW}.provider_account_id, {ROW}.name, "
    f"{ROW}.data, {ROW}.tokens_in, {ROW}.tokens_out, {ROW}.is_active, "
    f"{ROW}.created_at, {OWNER}.username AS owner_username"
)
_SOURCE = (
    f"FROM connections {ROW} "
    f"LEFT JOIN users {OWNER} ON {OWNER}.id = {ROW}.owner_id "
    "WHERE 1=1"
)


def _decode(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(row["data"] or "{}")
    except (json.JSONDecodeError, TypeError):
        data = {}
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        # El `LEFT JOIN` deja NULL cuando el dueño ya no es una cuenta
        # (`__public__`, `admin`, o un usuario borrado). El diccionario en
        # memoria devolvía el propio id; se conserva ese comportamiento.
        "owner_username": str(row["owner_username"] or row["owner_id"]),
        "provider_account_id": row["provider_account_id"],
        "name": row["name"],
        "type": data.get("type", ""),
        "model": data.get("model", ""),
        "supports_chat": is_chat_provider(str(data.get("type") or "").lower()),
        "is_active": bool(row["is_active"]),
        "tokens_in": row["tokens_in"],
        "tokens_out": row["tokens_out"],
        "created_at": row["created_at"],
    }


async def list_admin_connections_cursor(
    *, page: CursorParams
) -> CursorPage[dict[str, Any]]:
    """Una página de conexiones, de la más reciente a la más antigua."""
    async with open_db() as conn:
        context = cursor_context_signature(
            {"table": "connections", "consistent": page.consistent}
        )
        return await fetch_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) {_SOURCE}",
            select_sql=f"SELECT {_COLUMNS} {_SOURCE}",
            params=(),
            position_column=f"{ROW}.created_at",
            id_column=f"{ROW}.id",
            context=context,
            resource=RESOURCE,
            page=page,
            decode=_decode,
        )
