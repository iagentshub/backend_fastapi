"""Piezas que comparten el catálogo, los packs oficiales y el perfil.

`_validate_relation` la usan el catálogo y los packs oficiales; resolver el
nombre de usuario del propietario lo necesitan el catálogo y el perfil.
"""


from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.errors import APIError
from app.storage.db import open_db

# Relación entre el catálogo y lo que el usuario ya tiene enlazado.
# `all` es el valor por defecto porque es el comportamiento que la ruta tuvo
# siempre: quien no envíe el parámetro sigue viendo el catálogo entero. Es el
# cliente el que decide que descubrir significa "lo que todavía no tengo".
# Ver docs/adr/004-explorar-esconde-lo-que-ya-tienes.md
RELATION_MODES = ("all", "new", "linked")

def _validate_relation(relation: Optional[str]) -> str:
    value = (relation or "all").strip().lower()
    if value not in RELATION_MODES:
        raise APIError(
            422,
            "invalid_field",
            "Modo de relación no soportado",
            extra={"field": "relation", "invalid": [relation]},
        )
    return value

async def _add_owner_usernames(rows: List[Dict[str, Any]]) -> None:
    """Attach the public username while keeping the internal owner id intact."""
    owner_ids = {str(row.get("owner") or "") for row in rows} - {"", "__public__"}
    if not owner_ids:
        return
    placeholders = ",".join("?" for _ in owner_ids)
    async with open_db() as conn:
        users = await conn.fetchall(
            f"SELECT id, username FROM users WHERE id IN ({placeholders})",
            tuple(owner_ids),
        )
    usernames = {row["id"]: row["username"] for row in users}
    for row in rows:
        row["owner_username"] = usernames.get(str(row.get("owner") or ""))
