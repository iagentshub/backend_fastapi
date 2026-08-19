"""Acceso a `resource_social`, el catálogo que alimenta Explorar.

Era la mitad de `routes/social.py` que no tenía nada de ruta: consultas y
upsert sobre una tabla, más el catálogo de categorías. Lo importan tanto las
rutas de visibilidad como las cascadas de publicación.
"""

from __future__ import annotations

from typing import Any, List

from app.errors import APIError
from app.sql import sql
from app.storage.db import IS_PG, open_db

CATEGORIES = [
    "Coding",
    "Writing",
    "Research",
    "Data",
    "DevOps",
    "Support",
    "Education",
    "Productivity",
    "Marketing",
    "Finance",
    "Other",
]

_PUBLIC_VAL = 1

async def _assert_public(resource_type: str, source_id: str) -> None:
    """Enlazar solo está disponible para contenido público del marketplace."""
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/social:public_flag_exists"),
            (resource_type, source_id, _PUBLIC_VAL),
        )
    if not row:
        raise APIError(403, "forbidden", "No tienes acceso a este recurso")

def _check_category(cat: str) -> None:
    if cat not in CATEGORIES:
        raise APIError(
            422,
            "invalid_field",
            f"Categoría inválida. Opciones: {CATEGORIES}",
            extra={"field": "category"},
        )

def _assert_publicable(resource_labels: List[str], resource_type: str) -> None:
    """La label ``public`` manda: publicar sin ella no puede responder ``ok``.

    Las cinco rutas de visibilidad calculaban ``is_public`` a partir de las
    labels del recurso, no del ``is_public`` del cuerpo, así que un recurso sin
    la label se insertaba en ``resource_social`` con ``is_public = 0`` y el
    endpoint devolvía ``{"ok": true}``. Como un agente nace con
    ``labels: ["private"]``, ese era el camino por defecto: el usuario pulsaba
    «publicar», veía la confirmación, y su agente no aparecía en el catálogo ni
    había nada que se lo explicara.

    Se mantiene la label como fuente de verdad —cambiar eso invertiría la
    decisión de diseño y afectaría a ``resource_labels``— pero se deja de
    responder afirmativamente a una petición que no se ha atendido.
    """
    if "public" not in (resource_labels or []):
        raise APIError(
            409,
            "resource_not_marked_public",
            "Marca el recurso como público antes de publicarlo en el catálogo.",
            extra={"resource": resource_type, "missing_label": "public"},
        )

async def _assert_not_linked_copy(
    conn: Any, resource_type: str, resource_id: str, owner: str
) -> None:
    """Impide publicar una copia enlazada (creada vía "Enlazar" de un recurso ajeno):
    republicarla generaría una entrada duplicada del original en Explorar."""
    row = await conn.fetchone(
        sql("queries/social:linked_to_id"),
        (resource_type, resource_id, owner),
    )
    if row and row["linked_to_id"]:
        raise APIError(
            400,
            "linked_copy_not_publishable",
            "No puedes publicar una copia enlazada de un recurso ajeno",
        )

async def _upsert_social(
    conn: Any,
    resource_type: str,
    resource_id: str,
    owner: str,
    name: str,
    description: str,
    category: str,
    trial_missing_deps: str,
    tags: str = "[]",
    is_public: int = 0,
    labels: str = '["private"]',
) -> None:
    if IS_PG:
        await conn.execute(
            sql("queries/social:upsert_social_pg"),
            (
                resource_type,
                resource_id,
                owner,
                name,
                description,
                1 if is_public else 0,
                category,
                trial_missing_deps,
                tags,
                labels,
            ),
        )
    else:
        await conn.execute(
            sql("queries/social:upsert_social_sqlite"),
            (
                resource_type,
                resource_id,
                owner,
                name,
                description,
                is_public,
                category,
                trial_missing_deps,
                tags,
                labels,
            ),
        )
