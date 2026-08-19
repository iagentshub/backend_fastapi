"""Compatibilidad histórica de migraciones SQLite y PostgreSQL.

Este paquete conserva, sin reordenarlos, los pasos idempotentes necesarios para
actualizar instalaciones antiguas. Las migraciones nuevas deben añadirse como
pasos pequeños al registro de `app.storage.migrations.steps`.

Partido en paquete porque el módulo único llegó a 1408 líneas. El reparto
respeta dos reglas que aquí importan más que el tamaño:

  * **Las dos secuencias de puesta al día no se trocean.** `_migrate_sqlite` y
    `_migrate_pg` son 434 y 383 líneas de pasos en un orden que se conserva; su
    fallo aparecería al actualizar una instalación vieja, no en la suite. Cada
    una vive en su fichero, entera.
  * **Las dos variantes de una misma operación van juntas.** Es lo que evita
    corregir una y olvidar la otra.

    _helpers.py         columnas, nombres de recurso y compactado de blobs.
    _resources.py       nombre propio y etiquetas de idioma, en ambos motores.
    _groups.py          el esquema viejo de grupos y su renombrado.
    _catchup_sqlite.py  la secuencia de SQLite.
    _catchup_pg.py      la secuencia de PostgreSQL.

`app.storage.db_migrations` es este mismo módulo bajo otro nombre (se sustituye
en `sys.modules`), así que todo lo que importen los storages tiene que seguir
estando aquí.
"""

from __future__ import annotations

from app.storage.migrations.legacy._catchup_pg import (
    _migrate_pg,
    _migrate_users_json_pg,
)
from app.storage.migrations.legacy._catchup_sqlite import (
    _migrate_sqlite,
    _migrate_users_json_sqlite,
    _pre_migrate_sqlite,
)
from app.storage.migrations.legacy._groups import (
    _legacy_group_indexes,
    _legacy_group_schema,
    _migrate_group_active_flag_pg,
    _migrate_group_active_flag_sqlite,
    _rename_legacy_group_schema_pg,
    _rename_legacy_group_schema_sqlite,
)
from app.storage.migrations.legacy._helpers import (
    _NAMED_RESOURCE_TABLES,
    _RESOURCE_BLOB_DUPLICATES,
    _RESOURCE_TABLES,
    _SCHEMA_INDEX_DEPS,
    _add_sqlite_column,
    _compact_resource_data,
    _resource_name_from_data,
    _sqlite_columns,
)
from app.storage.migrations.legacy._resources import (
    _migrate_legacy_agent_language_labels,
    _migrate_named_resources_pg,
    _migrate_named_resources_sqlite,
)

__all__ = [
    "_pre_migrate_sqlite",
    "_migrate_sqlite",
    "_migrate_users_json_sqlite",
    "_migrate_pg",
    "_migrate_users_json_pg",
    "_rename_legacy_group_schema_sqlite",
    "_rename_legacy_group_schema_pg",
    "_compact_resource_data",
    "_resource_name_from_data",
    "_add_sqlite_column",
    "_sqlite_columns",
    "_legacy_group_schema",
    "_legacy_group_indexes",
    "_migrate_named_resources_sqlite",
    "_migrate_named_resources_pg",
    "_migrate_group_active_flag_sqlite",
    "_migrate_group_active_flag_pg",
    "_migrate_legacy_agent_language_labels",
    "_SCHEMA_INDEX_DEPS",
    "_RESOURCE_TABLES",
    "_NAMED_RESOURCE_TABLES",
    "_RESOURCE_BLOB_DUPLICATES",
]
