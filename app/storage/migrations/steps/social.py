"""Migraciones del catálogo social y sus fechas canónicas."""

from __future__ import annotations

from typing import Any

from app.storage.migrations.steps._columnas import (
    columna_existe_pg,
    columna_existe_sqlite,
)
from app.storage.schema import tabla_ddl

_SOCIAL_DATE_COLUMNS: dict[str, str] = {
    "user_follows": "created_at",
    "resource_stars": "created_at",
    "resource_social": "updated_at",
}

_SOCIAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "user_follows": ("follower", "following", "created_at"),
    "resource_stars": (
        "username",
        "resource_type",
        "resource_id",
        "created_at",
    ),
    "resource_social": (
        "resource_type",
        "resource_id",
        "owner",
        "name",
        "description",
        "is_public",
        "category",
        "trial_missing_deps",
        "linked_to_user",
        "linked_to_id",
        "stars_count",
        "tags",
        "labels",
        "verified",
        "updated_at",
    ),
}

_SQLITE_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
_PG_NOW = (
    "to_char(NOW() AT TIME ZONE 'UTC', "
    "'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')"
)
_PG_PARSEABLE_DATE = (
    "^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
    "([.][0-9]+)?([+-][0-9]{2}(:[0-9]{2})?|Z)?$"
)


def _schema_statements(table: str, dialect: str) -> list[str]:
    return [part.strip() for part in tabla_ddl(table, dialect).split(";") if part.strip()]


async def _rebuild_social_table_sqlite(conn: Any, table: str) -> None:
    """Recrea una tabla social antigua sin duplicar su DDL canónico."""
    date_column = _SOCIAL_DATE_COLUMNS[table]
    columns = _SOCIAL_COLUMNS[table]
    old_table = f"_{table}_pre_iso_dates"
    statements = _schema_statements(table, "sqlite")
    select_columns = [
        (
            f"COALESCE(strftime('%Y-%m-%dT%H:%M:%fZ', {column}), "
            f"{column}, {_SQLITE_NOW}) AS {column}"
            if column == date_column
            else column
        )
        for column in columns
    ]

    await conn.execute(f"ALTER TABLE {table} RENAME TO {old_table}")
    await conn.execute(statements[0])
    await conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"SELECT {', '.join(select_columns)} FROM {old_table}"
    )
    await conn.execute(f"DROP TABLE {old_table}")
    for statement in statements[1:]:
        await conn.execute(statement)


async def _social_iso_dates_sqlite(conn: Any) -> None:
    """Normaliza defaults y filas legacy sin tocar tablas ya canónicas."""
    for table in _SOCIAL_DATE_COLUMNS:
        row = await conn.execute_fetchall(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if row and "strftime" not in str(row[0][0]):
            await _rebuild_social_table_sqlite(conn, table)


async def _social_iso_dates_pg(conn: Any) -> None:
    """Convierte el único timestamptz legacy y normaliza defaults PostgreSQL."""
    data_type = await conn.fetchval(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='resource_social' "
        "AND column_name='updated_at'"
    )
    await conn.execute(
        "UPDATE resource_social SET updated_at=NOW() WHERE updated_at IS NULL"
    )
    await conn.execute(
        "ALTER TABLE resource_social ALTER COLUMN updated_at DROP DEFAULT"
    )
    if data_type != "text":
        await conn.execute(
            "ALTER TABLE resource_social ALTER COLUMN updated_at TYPE TEXT USING "
            f"{_PG_NOW.replace('NOW()', 'updated_at')}"
        )
    else:
        await conn.execute(
            "UPDATE resource_social SET updated_at="
            f"{_PG_NOW.replace('NOW()', 'updated_at::timestamptz')} "
            f"WHERE updated_at ~ '{_PG_PARSEABLE_DATE}'"
        )
    await conn.execute(
        "ALTER TABLE resource_social ALTER COLUMN updated_at "
        f"SET DEFAULT ({_PG_NOW})"
    )
    await conn.execute(
        "ALTER TABLE resource_social ALTER COLUMN updated_at SET NOT NULL"
    )

    for table in ("user_follows", "resource_stars"):
        await conn.execute(
            f"UPDATE {table} SET created_at={_PG_NOW} "
            "WHERE created_at IS NULL"
        )
        await conn.execute(
            f"UPDATE {table} SET created_at="
            f"{_PG_NOW.replace('NOW()', 'created_at::timestamptz')} "
            f"WHERE created_at ~ '{_PG_PARSEABLE_DATE}'"
        )
        await conn.execute(
            f"ALTER TABLE {table} ALTER COLUMN created_at "
            f"SET DEFAULT ({_PG_NOW})"
        )
        await conn.execute(
            f"ALTER TABLE {table} ALTER COLUMN created_at SET NOT NULL"
        )


# `fork_of_user` y `fork_of_id` venían de un «fork» que nunca se implementó:
# ninguna consulta las escribía ni las leía, así que en toda instalación están
# a NULL. Se van del esquema; esto las retira de las bases que ya existen.
_COLUMNAS_FORK = ("fork_of_user", "fork_of_id")


async def _drop_social_fork_columns_sqlite(conn: Any) -> None:
    for columna in _COLUMNAS_FORK:
        if await columna_existe_sqlite(conn, "resource_social", columna):
            await conn.execute(
                f"ALTER TABLE resource_social DROP COLUMN {columna}"
            )


async def _drop_social_fork_columns_pg(conn: Any) -> None:
    for columna in _COLUMNAS_FORK:
        if await columna_existe_pg(conn, "resource_social", columna):
            await conn.execute(
                f"ALTER TABLE resource_social DROP COLUMN {columna}"
            )
