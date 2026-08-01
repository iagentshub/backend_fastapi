"""Índice transversal de etiquetas (resource_labels).

Fuente de verdad: el campo ``labels`` dentro del blob ``data`` de cada recurso.
Esta tabla es solo un índice de consulta para responder "¿qué objetos, de
cualquier tipo, llevan la etiqueta X?" — el enlace entre objetos por etiqueta.
Se mantiene por dual-write desde ``save`` y es repoblable de forma idempotente.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.resource_types import normalize_resource_type
from app.storage.db import open_db


async def sync_labels(
    resource_type: str,
    resource_id: str,
    owner_id: Optional[str],
    labels: List[str],
) -> None:
    """Reemplaza (delete + insert) las etiquetas indexadas de un recurso."""
    rtype = normalize_resource_type(resource_type)
    owner = owner_id or ""
    clean = sorted({str(lbl).strip() for lbl in (labels or []) if str(lbl).strip()})
    async with open_db() as conn:
        await conn.execute(
            "DELETE FROM resource_labels WHERE resource_type=? AND resource_id=?",
            (rtype, resource_id),
        )
        for label in clean:
            await conn.execute(
                "INSERT INTO resource_labels (resource_type, resource_id, owner_id, label) "
                "VALUES (?, ?, ?, ?)",
                (rtype, resource_id, owner, label),
            )
        await conn.commit()


async def clear_labels(resource_type: str, resource_id: str) -> None:
    """Elimina las etiquetas indexadas de un recurso borrado."""
    rtype = normalize_resource_type(resource_type)
    async with open_db() as conn:
        await conn.execute(
            "DELETE FROM resource_labels WHERE resource_type=? AND resource_id=?",
            (rtype, resource_id),
        )
        await conn.commit()


async def resources_with_label(
    label: str, owner_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Devuelve los recursos que llevan una etiqueta, de cualquier tipo.

    owner_id=None → sin filtro de propietario (uso admin).
    """
    async with open_db() as conn:
        if owner_id is None:
            rows = await conn.fetchall(
                "SELECT resource_type, resource_id, owner_id FROM resource_labels "
                "WHERE label=? ORDER BY resource_type, resource_id",
                (label,),
            )
        else:
            rows = await conn.fetchall(
                "SELECT resource_type, resource_id, owner_id FROM resource_labels "
                "WHERE label=? AND owner_id=? ORDER BY resource_type, resource_id",
                (label, owner_id),
            )
    return [
        {
            "resource_type": r["resource_type"],
            "resource_id": r["resource_id"],
            "owner_id": r["owner_id"] or None,
        }
        for r in rows
    ]
