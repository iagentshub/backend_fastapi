"""Paginacion keyset segura para el visor administrativo de tablas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.errors import APIError
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.sql import sql
from app.storage.composite_cursor_page import KeysetColumn, fetch_composite_cursor_page
from app.storage.db import IS_PG, open_db
from app.storage.schema import columnas_sensibles

# Cuánto de un valor binario se resume en lugar de volcarlo. Las filas se
# serializaban tal cual, así que abrir `user_avatars` devolvía las imágenes
# convertidas a la representación de texto de sus bytes.
_RESUMEN_BINARIO = "@{n} bytes@"


def _valor_visible(valor: Any) -> Any:
    """El contenido de una columna, o su tamaño si es binaria."""
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return _RESUMEN_BINARIO.format(n=len(bytes(valor)))
    return valor


@dataclass(frozen=True, slots=True)
class MetadataCursorResult:
    columns: list[str]
    page: CursorPage[list[Any]]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


async def _tables(conn: Any) -> set[str]:
    rows = await conn.fetchall(
        sql("queries/admin_stats:pg_table_names")
        if IS_PG
        else sql("queries/admin_stats:sqlite_table_names")
    )
    return {str(row[0]) for row in rows}


async def _columns_and_pk(conn: Any, table_name: str) -> tuple[list[str], list[str]]:
    if IS_PG:
        columns = await conn.fetchall(
            sql("queries/admin_stats:pg_column_names"),
            (table_name,),
        )
        primary = await conn.fetchall(
            sql("queries/admin_stats:pg_primary_key_columns"),
            (table_name,),
        )
        return [str(row[0]) for row in columns], [str(row[0]) for row in primary]
    rows = await conn.fetchall(f"PRAGMA table_info({_quote(table_name)})")
    columns = [str(row[1]) for row in rows]
    primary = [
        str(row[1])
        for row in sorted(rows, key=lambda value: int(value[5] or 0))
        if int(row[5] or 0) > 0
    ]
    return columns, primary


async def list_metadata_cursor(
    *,
    admin: str,
    table_name: str,
    query: str | None,
    page: CursorParams,
) -> MetadataCursorResult:
    """Página de una tabla, con las columnas sensibles fuera.

    Lo sensible se declara con `-- sensitive-columns:` en el propio DDL, el
    mismo mecanismo que `-- gdpr-identity:`. Aquí había una lista negra de siete
    nombres literales, y una lista negra de secretos solo es correcta el día que
    se escribe: desde entonces habían entrado `refresh_hash`, `token_hash`,
    `code_hash`, `p256dh` y `auth` —ninguna lleva «token» ni «secret» como
    nombre completo—, y dos de los siete ya no correspondían a ninguna columna.
    La columna nueva se declara donde se crea, no en un fichero que hay que
    acordarse de visitar.
    """
    hidden_columns = columnas_sensibles().get(table_name, frozenset())
    async with open_db() as conn:
        if table_name not in await _tables(conn):
            raise APIError(
                404, "not_found", "Tabla no encontrada", extra={"resource": "table"}
            )
        columns, primary = await _columns_and_pk(conn, table_name)
        if not primary:
            raise APIError(
                409,
                "pagination_key_unavailable",
                "La tabla no dispone de una clave estable para paginar",
                extra={"resource": "table"},
            )
        exposed = [column for column in columns if column not in hidden_columns]
        if not exposed:
            raise APIError(404, "table_no_columns", "Sin columnas visibles")
        table_sql = _quote(table_name)
        selected = ",".join(_quote(column) for column in exposed)
        clauses = ["1=1"]
        params: list[Any] = []
        normalized_query = (query or "").strip()
        if normalized_query:
            clauses.append(
                "("
                + " OR ".join(
                    f"CAST({_quote(column)} AS TEXT) LIKE ?" for column in exposed
                )
                + ")"
            )
            params.extend([f"%{normalized_query}%"] * len(exposed))
        where = " AND ".join(clauses)
        context = cursor_context_signature(
            {
                "resource": "admin_metadata",
                "admin": admin,
                "table": table_name,
                "q": normalized_query,
                "primary": primary,
            }
        )
        result = await fetch_composite_cursor_page(
            conn,
            count_sql=f"SELECT COUNT(*) FROM {table_sql} WHERE {where}",
            select_sql=f"SELECT {selected} FROM {table_sql} WHERE {where}",
            params=tuple(params),
            columns=tuple(
                KeysetColumn(_quote(column), column, descending=False)
                for column in primary
            ),
            context=context,
            resource="admin_metadata",
            page=page,
            decode=lambda row: [_valor_visible(row[column]) for column in exposed],
        )
    return MetadataCursorResult(columns=exposed, page=result)
