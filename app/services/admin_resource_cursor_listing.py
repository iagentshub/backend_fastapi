"""Paginación keyset para un listado del panel, sin filtro de visibilidad.

El panel es el único sitio del producto donde el resultado no lo acota lo que
tiene un usuario, sino lo que tiene la instalación entera. Once `GET` devolvían
`SELECT … FROM tabla` sin `WHERE` y sin cota, y se retiraron; hoy el único que
pasa por aquí es el listado de conexiones, que sí tiene consumidor.

Dos cosas que fija este módulo, y que hay que respetar al añadir otro listado.

**El nombre del dueño sale del `JOIN`.** Cada listado llamaba a
`_username_map`, que era `SELECT id, username FROM users` entera, y el panel
pinta varias pestañas por carga: nueve copias de la tabla de usuarios en
memoria en la misma sesión.

**`key_columns` tiene que desempatar de verdad.** Es la parte fácil de
equivocar: varias tablas de recursos —agents, skills, prompts, tools,
memory_files, llm_orchestrations— tienen PK compuesta `(id, owner_id)`, así que
dos filas pueden compartir `id` con distinto dueño. Para un usuario eso da
igual, porque solo ve los suyos; el administrador los ve todos a la vez, y ahí
`(updated_at, id)` deja de ser única. Un keyset con clave repetida **se salta
filas en el corte de página sin que nada falle**, que es la peor forma de
fallar. El valor por defecto incluye `owner_id` por eso; `connections`, cuya PK
es solo `id`, lo declara explícitamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.storage.composite_cursor_page import (
    KeysetColumn,
    SnapshotColumn,
    fetch_composite_cursor_page,
)
from app.storage.db import open_db
from app.utils import now_iso

ROW = "resource_row"
OWNER = "owner_user"


@dataclass(frozen=True, slots=True)
class AdminListingSpec:
    """Cómo se pagina una tabla del panel."""

    table: str
    columns: str
    resource: str
    decode: Callable[[Any], dict[str, Any]]
    position: str = "updated_at"
    # `id` solo no basta donde la PK es compuesta; ver el módulo.
    key_columns: tuple[str, ...] = ("id", "owner_id")
    owner_column: str = "owner_id"
    search_columns: tuple[str, ...] = field(default=("name",))


def _keyset_columns(spec: AdminListingSpec) -> list[KeysetColumn]:
    columns = [KeysetColumn(f"{ROW}.{spec.position}", spec.position)]
    columns.extend(
        KeysetColumn(f"{ROW}.{column}", column) for column in spec.key_columns
    )
    return columns


def _search_clause(spec: AdminListingSpec, query: str) -> tuple[str, list[Any]]:
    """Busca en servidor. Filtrar en cliente sobre una página devuelve
    resultados incompletos sin que se note, que es el aviso que ya estaba
    escrito en `ScopedResourceRepository.list`."""
    terms = [f"LOWER({ROW}.{column}) LIKE ?" for column in spec.search_columns]
    terms.append(f"LOWER({OWNER}.username) LIKE ?")
    needle = f"%{query.lower()}%"
    return "(" + " OR ".join(terms) + ")", [needle] * len(terms)


async def list_admin_resource_cursor(
    spec: AdminListingSpec,
    *,
    page: CursorParams,
    query: str | None = None,
    owner: str | None = None,
) -> CursorPage[dict[str, Any]]:
    """Una página del panel, ordenada por `position` y desempatada por la PK."""

    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    if query:
        clause, search_params = _search_clause(spec, query)
        clauses.append(clause)
        params.extend(search_params)
    if owner:
        clauses.append(f"{ROW}.{spec.owner_column} = ?")
        params.append(owner)
    where = " AND ".join(clauses)
    source = (
        f"FROM {spec.table} {ROW} "
        f"LEFT JOIN users {OWNER} ON {OWNER}.id = {ROW}.{spec.owner_column} "
        f"WHERE {where}"
    )
    async with open_db() as conn:
        context = cursor_context_signature(
            {
                "table": spec.table,
                "where": where,
                "params": tuple(params),
                "consistent": page.consistent,
            }
        )
        return await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) {source}",
            select_sql=f"SELECT {spec.columns} {source}",
            params=tuple(params),
            columns=_keyset_columns(spec),
            context=context,
            resource=spec.resource,
            page=page,
            decode=spec.decode,
            snapshot=(
                SnapshotColumn(f"{ROW}.{spec.position}", now_iso())
                if page.consistent
                else None
            ),
        )
