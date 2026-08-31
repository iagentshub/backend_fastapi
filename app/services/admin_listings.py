"""Qué pagina el listado de conexiones del panel y cómo decodifica su fila.

Hubo aquí un spec por cada pestaña, cuando `/api/v2/admin` publicaba un listado
por tipo. Se retiraron con sus rutas: el inventario del panel se pide por
`/api/v2/admin/explore` y nadie llamaba a los otros diez.

`data` viaja porque de ahí salen `type` y `model`, que es lo que distingue una
conexión de otra en la pantalla. Ninguna proyección de este módulo lleva una
columna de contenido: el cuerpo de una skill o de un documento no se pinta en
una tabla, y arrastrarlo por la red para descartarlo en Python es la pérdida
que ya costó una vez la memoria de todos los usuarios.
"""

from __future__ import annotations

import json
from typing import Any

from app.connections import is_chat_provider
from app.services.admin_resource_cursor_listing import OWNER, ROW, AdminListingSpec


def _owner_name(row: Any) -> str:
    """El `LEFT JOIN` deja `NULL` cuando el dueño ya no es una cuenta
    (`__public__`, `admin`, o un usuario borrado). Antes el `.get(id, id)` del
    diccionario en memoria daba el propio id; se conserva ese comportamiento."""
    return str(row["owner_username"] or row["owner_id"])


def _connection_row(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(row["data"] or "{}")
    except (json.JSONDecodeError, TypeError):
        data = {}
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "owner_username": _owner_name(row),
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


def connections_spec() -> AdminListingSpec:
    return AdminListingSpec(
        table="connections",
        columns=(
            f"{ROW}.id, {ROW}.owner_id, {ROW}.provider_account_id, {ROW}.name, "
            f"{ROW}.data, {ROW}.tokens_in, {ROW}.tokens_out, {ROW}.is_active, "
            f"{ROW}.created_at, {OWNER}.username AS owner_username"
        ),
        resource="admin_connections",
        decode=_connection_row,
        position="created_at",
        key_columns=("id",),
    )
