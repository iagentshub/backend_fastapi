"""Contrato compartido para migraciones legacy ejecutadas una sola vez."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class LegacyMigrationStorage(ABC):
    def __init__(self) -> None:
        self._migration_complete = False
        self._migration_lock: asyncio.Lock | None = None

    async def _ensure_migrated(self) -> None:
        if self._migration_complete:
            return
        if self._migration_lock is None:
            self._migration_lock = asyncio.Lock()
        async with self._migration_lock:
            if self._migration_complete:
                return
            await self._migrate_legacy_data()
            self._migration_complete = True

    @abstractmethod
    async def _migrate_legacy_data(self) -> None:
        """Importa datos de la representación anterior cuando sea necesario."""
