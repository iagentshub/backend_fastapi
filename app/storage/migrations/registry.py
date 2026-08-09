"""Infraestructura común para ejecutar migraciones una sola vez y en orden."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Literal

MigrationRunner = Callable[[Any], Awaitable[None]]
Dialect = Literal["sqlite", "postgres"]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    run: MigrationRunner
    repeatable: bool = False


async def _ensure_registry(conn: Any, dialect: Dialect) -> None:
    applied_at = (
        "TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        if dialect == "sqlite"
        else "TEXT NOT NULL DEFAULT (NOW()::TEXT)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, "
        f"applied_at {applied_at})"
    )


async def _applied_versions(conn: Any, dialect: Dialect) -> set[int]:
    if dialect == "postgres":
        rows = await conn.fetch("SELECT version FROM schema_migrations")
    else:
        cursor = await conn.execute("SELECT version FROM schema_migrations")
        rows = await cursor.fetchall()
    return {int(row[0]) for row in rows}


async def run_migrations(
    conn: Any, dialect: Dialect, migrations: Iterable[Migration]
) -> list[int]:
    """Ejecuta pasos pendientes en orden y persiste cada versión completada."""
    ordered = sorted(migrations, key=lambda migration: migration.version)
    versions = [migration.version for migration in ordered]
    if len(versions) != len(set(versions)):
        raise RuntimeError("Hay versiones de migración duplicadas")

    await _ensure_registry(conn, dialect)
    applied = await _applied_versions(conn, dialect)
    completed: list[int] = []
    for migration in ordered:
        if migration.version in applied and not migration.repeatable:
            continue
        await migration.run(conn)
        if migration.version in applied:
            continue
        if dialect == "postgres":
            await conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                migration.version,
                migration.name,
            )
        else:
            await conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
        completed.append(migration.version)
    if dialect == "sqlite":
        await conn.commit()
    return completed
