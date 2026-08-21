"""Configuración centralizada de la base de datos."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.config import database
from app.storage import db as db_mod


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, database.SQLITE_POOL_DEFAULT_SIZE),
        ("invalid", database.SQLITE_POOL_DEFAULT_SIZE),
        ("0", database.SQLITE_POOL_MIN_SIZE),
        ("2", 2),
        ("99", database.SQLITE_POOL_MAX_SIZE),
    ],
)
def test_sqlite_pool_size_centraliza_default_y_limites(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int
) -> None:
    if raw is None:
        monkeypatch.delenv("GAIA_SQLITE_POOL_SIZE", raising=False)
    else:
        monkeypatch.setenv("GAIA_SQLITE_POOL_SIZE", raw)

    assert database.sqlite_pool_size() == expected


@pytest.mark.parametrize(
    ("raw", "url", "uses_postgresql"),
    [(None, "", False), ("  ", "", False), (" postgresql://db/hub ", "postgresql://db/hub", True)],
)
def test_database_url_y_motor_se_resuelven_en_configuracion(
    monkeypatch: pytest.MonkeyPatch,
    raw: str | None,
    url: str,
    uses_postgresql: bool,
) -> None:
    if raw is None:
        monkeypatch.delenv(database.DATABASE_URL_ENV, raising=False)
    else:
        monkeypatch.setenv(database.DATABASE_URL_ENV, raw)

    assert database.database_url() == url
    assert database.uses_postgresql() is uses_postgresql


def test_marca_de_migracion_se_resuelve_en_configuracion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(database.SCHEMA_MIGRATED_ENV, "1")
    assert database.schema_already_migrated() is True

    monkeypatch.setenv(database.SCHEMA_MIGRATED_ENV, "true")
    assert database.schema_already_migrated() is False


def test_parametros_sqlite_y_postgresql_estan_centralizados() -> None:
    assert database.SQLITE_JOURNAL_MODE == "WAL"
    assert database.SQLITE_FOREIGN_KEYS is True
    assert database.SQLITE_BUSY_TIMEOUT_MS > 0
    assert database.SQLITE_PARAMETER_PLACEHOLDER == "?"
    assert 1 <= database.POSTGRES_POOL_MIN_SIZE <= database.POSTGRES_POOL_MAX_SIZE
    assert database.POSTGRES_COMMAND_TIMEOUT_SECONDS > 0
    assert database.POSTGRES_LOG_OPERATION_TIMEOUT_SECONDS > 0
    assert database.POSTGRES_LOG_CLOSE_TIMEOUT_SECONDS > 0


@pytest.mark.asyncio
async def test_init_postgresql_consume_el_pool_centralizado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakePool:
        async def close(self) -> None:
            captured["closed"] = True

    async def create_pool(url: str, **kwargs: object) -> FakePool:
        captured["url"] = url
        captured.update(kwargs)
        return FakePool()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(create_pool=create_pool))
    monkeypatch.setenv(database.DATABASE_URL_ENV, "postgresql://db/hub")
    monkeypatch.setenv(database.SCHEMA_MIGRATED_ENV, "1")
    monkeypatch.setattr(db_mod, "IS_PG", True)

    await db_mod.init_db()

    assert captured == {
        "url": "postgresql://db/hub",
        "min_size": database.POSTGRES_POOL_MIN_SIZE,
        "max_size": database.POSTGRES_POOL_MAX_SIZE,
        "command_timeout": database.POSTGRES_COMMAND_TIMEOUT_SECONDS,
    }

    await db_mod.close_db_pool()
    assert captured["closed"] is True
