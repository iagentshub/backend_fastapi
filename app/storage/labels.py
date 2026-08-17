"""Índice transversal de etiquetas (resource_labels).

Fuente de verdad: el campo ``labels`` dentro del blob ``data`` de cada recurso.
Esta tabla es solo un índice de consulta para responder "¿qué objetos, de
cualquier tipo, llevan la etiqueta X?" — el enlace entre objetos por etiqueta.
Se mantiene por dual-write desde ``save`` y es repoblable de forma idempotente.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.resource_types import normalize_resource_type
from app.sql import sql
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
            sql("queries/labels:delete_labels"),
            (rtype, resource_id),
        )
        for label in clean:
            await conn.execute(
                sql("queries/labels:insert_label"),
                (rtype, resource_id, owner, label),
            )
        await conn.commit()


async def clear_labels(resource_type: str, resource_id: str) -> None:
    """Elimina las etiquetas indexadas de un recurso borrado."""
    rtype = normalize_resource_type(resource_type)
    async with open_db() as conn:
        await conn.execute(
            sql("queries/labels:delete_labels"),
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
                sql("queries/labels:by_label"),
                (label,),
            )
        else:
            rows = await conn.fetchall(
                sql("queries/labels:by_label_and_owner"),
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
