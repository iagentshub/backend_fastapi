"""Contrato compartido para migraciones legacy ejecutadas una sola vez."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from app.config import database as database_config

# El estado de migración es del PROCESO, no del objeto.
#
# `_migration_complete` era un atributo de instancia, así que cada storage
# recién construido volvía a ejecutar `_ensure_migrated()` —y con él un
# SELECT COUNT(*) sobre su tabla— en su primera operación. Como varios routers
# construyen storages dentro de los handlers (explore_preview llegaba a crear
# seis en una sola petición), una migración pensada para correr una vez en la
# vida del proceso se ejecutaba una vez por petición.
#
# La clave incluye la BD activa: la suite crea una base nueva por test y espera
# que la migración se reevalúe en cada una.
_COMPLETADAS: set[tuple[str, str]] = set()

# El lock va atado al event loop en curso: un asyncio.Lock creado en un loop y
# usado en otro lanza "is bound to a different event loop", y la suite arranca
# un loop nuevo por test.
_LOCKS: "dict[int, asyncio.Lock]" = {}


def _lock_del_loop() -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    lock = _LOCKS.get(loop_id)
    if lock is None:
        lock = _LOCKS[loop_id] = asyncio.Lock()
    return lock


def _bd_activa() -> str:
    """Identifica la base de datos en uso (fichero SQLite o DSN de PostgreSQL)."""
    from app.storage import db as _db

    return str(
        getattr(_db, "_sqlite_path", "") or database_config.database_url() or "-"
    )


def reset_migraciones() -> None:
    """Olvida las migraciones ya hechas. Para tests que rehacen la BD."""
    _COMPLETADAS.clear()


class LegacyMigrationStorage(ABC):
    async def _ensure_migrated(self) -> None:
        clave = (type(self).__name__, _bd_activa())
        if clave in _COMPLETADAS:
            return
        async with _lock_del_loop():
            if clave in _COMPLETADAS:
                return
            await self._migrate_legacy_data()
            _COMPLETADAS.add(clave)

    @abstractmethod
    async def _migrate_legacy_data(self) -> None:
        """Importa datos de la representación anterior cuando sea necesario."""
