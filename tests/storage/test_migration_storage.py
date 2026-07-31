"""Tests del contrato compartido de migración legacy."""

from __future__ import annotations

import asyncio

import pytest

from app.storage.migration import LegacyMigrationStorage


class _TestStorage(LegacyMigrationStorage):
    def __init__(self, *, fail_first: bool = False) -> None:
        super().__init__()
        self.attempts = 0
        self.fail_first = fail_first

    async def _migrate_legacy_data(self) -> None:
        self.attempts += 1
        await asyncio.sleep(0)
        if self.fail_first and self.attempts == 1:
            raise RuntimeError("fallo temporal")


def test_concurrent_callers_execute_migration_once():
    storage = _TestStorage()

    async def scenario() -> None:
        await asyncio.gather(*(storage._ensure_migrated() for _ in range(10)))

    asyncio.run(scenario())
    assert storage.attempts == 1


def test_failed_migration_can_be_retried():
    storage = _TestStorage(fail_first=True)

    with pytest.raises(RuntimeError, match="fallo temporal"):
        asyncio.run(storage._ensure_migrated())

    asyncio.run(storage._ensure_migrated())
    assert storage.attempts == 2
