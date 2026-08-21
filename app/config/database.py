"""Configuración de los motores y pools de base de datos."""

from __future__ import annotations

import os
from pathlib import Path

from app.config import data as data_config

DATABASE_URL_ENV = "DATABASE_URL"
SCHEMA_MIGRATED_ENV = "GAIA_SCHEMA_MIGRATED"
DB_FILE: Path = data_config.DATA_DIR / "hub.db"

SQLITE_POOL_DEFAULT_SIZE = 3
SQLITE_POOL_MIN_SIZE = 1
SQLITE_POOL_MAX_SIZE = 8
SQLITE_JOURNAL_MODE = "WAL"
SQLITE_FOREIGN_KEYS = True
SQLITE_BUSY_TIMEOUT_MS = 5000
SQLITE_PARAMETER_PLACEHOLDER = "?"

POSTGRES_POOL_MIN_SIZE = 2
POSTGRES_POOL_MAX_SIZE = 20
POSTGRES_COMMAND_TIMEOUT_SECONDS = 30
POSTGRES_LOG_OPERATION_TIMEOUT_SECONDS = 10.0
POSTGRES_LOG_CLOSE_TIMEOUT_SECONDS = 5.0


def database_url() -> str:
    """URL PostgreSQL vigente; vacía selecciona SQLite."""
    return os.getenv(DATABASE_URL_ENV, "").strip()


def uses_postgresql() -> bool:
    """Indica si el proceso debe usar PostgreSQL."""
    return bool(database_url())


def schema_already_migrated() -> bool:
    """Evita repetir la migración ejecutada por el proceso maestro."""
    return os.getenv(SCHEMA_MIGRATED_ENV) == "1"


def sqlite_pool_size() -> int:
    """Devuelve el tamaño SQLite configurado, acotado a valores seguros."""
    raw = os.getenv("GAIA_SQLITE_POOL_SIZE", str(SQLITE_POOL_DEFAULT_SIZE))
    try:
        requested = int(raw)
    except ValueError:
        return SQLITE_POOL_DEFAULT_SIZE
    return max(SQLITE_POOL_MIN_SIZE, min(requested, SQLITE_POOL_MAX_SIZE))
