"""Los hechos en crudo: cómo se describe un recurso relacionado.

`item` y `payload` son la forma que tiene una relación —qué cuelga de qué, con
qué nombre— y las usan por igual el catálogo público y el inventario de admin.
Los dos helpers de packs también: un pack se recorre igual se mire desde donde
se mire, y que no fuera así es justo el bug que originó este servicio.

`admin_labels` está aquí y no en `admin.py` porque la usan los tres módulos de
la vista de Admin; dejarla en el de entrada los ponía a importar en círculo.
"""


from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.sql import sql
from app.storage.db import open_db


def item(
    resource_type: str,
    resource_id: str,
    label: str,
    *,
    relation: str,
    description: str = "",
    via: Optional[tuple[str, str]] = None,
    path: str = "",
    inverse: bool = False,
) -> Dict[str, Any]:
    """Un hecho: qué recurso cuelga de qué otro y con qué relación.

    `via` es el recurso del que cuelga, como (tipo, id); `None` significa que
    cuelga de la raíz. Va como par y no como id suelto porque el mismo id
    puede existir en dos tipos distintos.

    `inverse` invierte la dirección de la arista: un propietario, una fuente
    oficial o el agente que usa el recurso apuntan *hacia* aquello de lo que
    cuelgan, no al revés.
    """
    payload: Dict[str, Any] = {
        "type": resource_type,
        "id": resource_id,
        "label": label or resource_id,
        "description": description,
        "relation": relation,
        "via": {"type": via[0], "id": via[1]} if via else None,
        "inverse": inverse,
    }
    if path:
        payload["path"] = path
    return payload

def payload(
    *,
    root_type: str,
    root_id: str,
    root_label: str,
    root_description: str = "",
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "root": {
            "type": root_type,
            "id": root_id,
            "label": root_label or root_id,
            "description": root_description,
        },
        "items": items,
    }

def node_id(resource_type: str, resource_id: str) -> str:
    return f"{resource_type}:{resource_id}"

async def _knowledge_pack_of(knowledge_id: str) -> tuple[str, str]:
    """Pack y ruta relativa de un fichero de knowledge, o ("", "")."""
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/resource_relations:knowledge_pack_of"), (knowledge_id,)
        )
    if not row:
        return "", ""
    return str(row["pack_id"] or ""), str(row["pack_relative_path"] or "")

async def _pack_member_items(
    pack_id: str, *, via: tuple[str, str]
) -> List[Dict[str, Any]]:
    """Ficheros de un pack con su ruta: el árbol lo arma el cliente."""
    async with open_db() as conn:
        rows = await conn.fetchall(
            sql("queries/resource_relations:pack_members"), (pack_id,)
        )
    return [
        item(
            "knowledge",
            str(row["id"]),
            str(row["pack_relative_path"] or row["name"] or row["id"]),
            description=str(row["pack_kind"] or ""),
            relation="contains",
            via=via,
            path=str(row["pack_relative_path"] or ""),
        )
        for row in rows
    ]

# Tabla y columna de nombre de cada tipo de recurso del panel de
# administración. La tabla nunca viene del usuario: la ruta valida el tipo
# contra este mapa antes de llegar aquí.
_ADMIN_TABLES: Dict[str, tuple[str, str]] = {
    "agent": ("agents", "name"),
    "skill": ("skills", "name"),
    "prompt": ("prompts", "name"),
    "tool": ("tools", "name"),
    "connection": ("connections", "name"),
    "knowledge": ("knowledge_items", "title"),
    "knowledge_pack": ("knowledge_packs", "name"),
    "workflow": ("agent_workflows", "name"),
    "llm_orchestration": ("llm_orchestrations", "name"),
    "user": ("users", "username"),
    "group": ("groups", "name"),
}

async def admin_labels(resource_type: str, resource_ids: List[str]) -> Dict[str, str]:
    """Nombre de varios recursos del mismo tipo, en una sola consulta.

    Sin filtro de visibilidad: es la vista de administración. Lo que sí tiene
    es filtro por id — el endpoint anterior resolvía estos nombres cargando el
    inventario completo de la instalación.
    """
    unique = [value for value in dict.fromkeys(resource_ids) if value]
    if not unique or resource_type not in _ADMIN_TABLES:
        return {}
    table, name_column = _ADMIN_TABLES[resource_type]
    placeholders = ",".join("?" for _ in unique)
    async with open_db() as conn:
        rows = await conn.fetchall(
            f"SELECT id, {name_column} AS label FROM {table} "
            f"WHERE id IN ({placeholders})",
            tuple(unique),
        )
    return {str(row["id"]): str(row["label"] or row["id"]) for row in rows}
