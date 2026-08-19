"""Migraciones de PostgreSQL: solo el runner.

Los pasos viven en `steps/`, agrupados por dominio y con la variante de SQLite
al lado. La lista es única (`steps.MIGRATION_PAIRS`) y de ella se deriva la de
cada motor.

Ojo con lo que se escribe en un paso de este motor: `migrate_schema` abre la
conexión con `asyncpg.connect()` y se la pasa en crudo, sin el envoltorio
`AsyncConn` — ni `fetchall`, ni `fetchone`, ni marcadores `?`.
`tests/storage/test_migraciones_pg_traducidas.py` lo comprueba.
"""

from __future__ import annotations

from typing import Any

from app.storage.migrations.registry import migrations_for, run_migrations
from app.storage.migrations.steps import MIGRATION_PAIRS

POSTGRES_MIGRATIONS = migrations_for("postgres", MIGRATION_PAIRS)


async def run_postgres_migrations(conn: Any) -> list[int]:
    return await run_migrations(conn, "postgres", POSTGRES_MIGRATIONS)
