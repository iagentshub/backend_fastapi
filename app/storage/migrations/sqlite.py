"""Migraciones de SQLite: solo el runner.

Los pasos viven en `steps/`, agrupados por dominio y con la variante de
PostgreSQL al lado. La lista es única (`steps.MIGRATION_PAIRS`) y de ella se
deriva la de cada motor, así que aquí no hay nada que mantener en paralelo.
"""

from __future__ import annotations

from typing import Any

from app.storage.migrations.registry import migrations_for, run_migrations
from app.storage.migrations.steps import MIGRATION_PAIRS

SQLITE_MIGRATIONS = migrations_for("sqlite", MIGRATION_PAIRS)


async def run_sqlite_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "sqlite", SQLITE_MIGRATIONS)
