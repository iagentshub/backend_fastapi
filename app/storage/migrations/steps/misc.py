"""Pasos que no forman dominio: helpers, orígenes, cuentas e índices sueltos.

`_table_exists_*` es el helper que usan varios pasos para no fallar sobre una
instalación que nunca tuvo la tabla vieja.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from app.storage.migrations.origin_labels import (
    normalize_labels,
    normalize_resource_data,
)
from app.storage.migrations.steps.shared import (
    _DUEÑOS_SIN_CUENTA,
    _TABLAS_CON_HUÉRFANOS,
)


async def _table_exists_sqlite(conn: Any, table: str) -> bool:
    """Las tablas del catálogo oficial antiguo ya no están en el esquema.

    Las migraciones que las tocaban solo tienen sentido sobre bases de datos
    que las traían: en una nueva no existen y el ALTER/SELECT fallaría.
    """
    rows = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return bool(rows)


async def _table_exists_pg(conn: Any, table: str) -> bool:
    """Ver el homónimo de sqlite.py: las tablas del catálogo oficial antiguo
    ya no forman parte del esquema, así que en una base nueva no existen."""
    return await conn.fetchval("SELECT to_regclass($1)", table) is not None


async def _tool_artifacts_sqlite(conn: Any) -> None:
    """Move executable bytes out of resource rows into deduplicated BLOBs."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_artifacts ("
        "sha256 TEXT PRIMARY KEY, binary_data BLOB NOT NULL, size INTEGER NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_artifact_links ("
        "tool_id TEXT NOT NULL, owner_id TEXT NOT NULL, sha256 TEXT NOT NULL, "
        "PRIMARY KEY (tool_id, owner_id), "
        "FOREIGN KEY (sha256) REFERENCES tool_artifacts(sha256))"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_version_artifacts ("
        "version_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
        "FOREIGN KEY (version_id) REFERENCES resource_versions(id) ON DELETE CASCADE, "
        "FOREIGN KEY (sha256) REFERENCES tool_artifacts(sha256))"
    )
    rows = await conn.execute_fetchall(
        "SELECT id, owner_id, binary_b64, data, binary_uploaded_at FROM tools "
        "WHERE binary_b64 IS NOT NULL AND binary_b64 <> ''"
    )
    for row in rows:
        metadata = json.loads(row[3] or "{}")
        try:
            binary = base64.b64decode(row[2], validate=True)
        except (ValueError, TypeError):
            continue
        digest = hashlib.sha256(binary).hexdigest()
        if metadata.get("binary_sha256") != digest:
            metadata["binary_sha256"] = digest
            await conn.execute(
                "UPDATE tools SET data=? WHERE id=? AND owner_id=?",
                (json.dumps(metadata, ensure_ascii=False), row[0], row[1]),
            )
        await conn.execute(
            "INSERT OR IGNORE INTO tool_artifacts "
            "(sha256, binary_data, size, created_at) VALUES (?, ?, ?, ?)",
            (digest, binary, len(binary), row[4] or ""),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO tool_artifact_links (tool_id, owner_id, sha256) "
            "VALUES (?, ?, ?)",
            (row[0], row[1], digest),
        )


async def _tool_artifacts_pg(conn: Any) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_artifacts ("
        "sha256 TEXT PRIMARY KEY, binary_data BYTEA NOT NULL, size BIGINT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_artifact_links ("
        "tool_id TEXT NOT NULL, owner_id TEXT NOT NULL, sha256 TEXT NOT NULL "
        "REFERENCES tool_artifacts(sha256), PRIMARY KEY (tool_id, owner_id))"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_version_artifacts ("
        "version_id TEXT PRIMARY KEY REFERENCES resource_versions(id) ON DELETE CASCADE, "
        "sha256 TEXT NOT NULL REFERENCES tool_artifacts(sha256))"
    )
    rows = await conn.fetch(
        "SELECT id, owner_id, binary_b64, data, binary_uploaded_at FROM tools "
        "WHERE binary_b64 IS NOT NULL AND binary_b64 <> ''"
    )
    for row in rows:
        metadata = json.loads(row["data"] or "{}")
        try:
            binary = base64.b64decode(row["binary_b64"], validate=True)
        except (ValueError, TypeError):
            continue
        digest = hashlib.sha256(binary).hexdigest()
        if metadata.get("binary_sha256") != digest:
            metadata["binary_sha256"] = digest
            await conn.execute(
                "UPDATE tools SET data=$1 WHERE id=$2 AND owner_id=$3",
                json.dumps(metadata, ensure_ascii=False),
                row["id"],
                row["owner_id"],
            )
        await conn.execute(
            "INSERT INTO tool_artifacts (sha256, binary_data, size, created_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (sha256) DO NOTHING",
            digest,
            binary,
            len(binary),
            row["binary_uploaded_at"] or "",
        )
        await conn.execute(
            "INSERT INTO tool_artifact_links (tool_id, owner_id, sha256) "
            "VALUES ($1, $2, $3) ON CONFLICT (tool_id, owner_id) "
            "DO UPDATE SET sha256=EXCLUDED.sha256",
            row["id"],
            row["owner_id"],
            digest,
        )


async def _chat_message_interrupted_sqlite(conn: Any) -> None:
    columns = {
        str(row[1])
        for row in await conn.execute_fetchall("PRAGMA table_info(messages)")
    }
    if "interrupted" not in columns:
        await conn.execute(
            "ALTER TABLE messages ADD COLUMN interrupted INTEGER NOT NULL DEFAULT 0"
        )
    if "usage_estimated" not in columns:
        await conn.execute(
            "ALTER TABLE messages ADD COLUMN usage_estimated INTEGER NOT NULL DEFAULT 0"
        )


async def _chat_message_interrupted_pg(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS "
        "interrupted BOOLEAN NOT NULL DEFAULT FALSE"
    )
    await conn.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS "
        "usage_estimated BOOLEAN NOT NULL DEFAULT FALSE"
    )


async def _app_logs_structured_audit_sqlite(conn: Any) -> None:
    """Añade metadatos auditables sin separar el registro central."""
    columns = {
        str(row[1])
        for row in await conn.execute_fetchall("PRAGMA table_info(app_logs)")
    }
    definitions = {
        "category": "TEXT NOT NULL DEFAULT 'DIAGNOSTIC'",
        "action": "TEXT",
        "resource_type": "TEXT",
        "resource_id": "TEXT",
        "outcome": "TEXT",
        "details_json": "TEXT",
    }
    for column, definition in definitions.items():
        if column not in columns:
            await conn.execute(f"ALTER TABLE app_logs ADD COLUMN {column} {definition}")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_al_category_ts ON app_logs(category, ts DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_al_action_ts ON app_logs(action, ts DESC) "
        "WHERE action IS NOT NULL"
    )


async def _app_logs_structured_audit_pg(conn: Any) -> None:
    definitions = {
        "category": "TEXT NOT NULL DEFAULT 'DIAGNOSTIC'",
        "action": "TEXT",
        "resource_type": "TEXT",
        "resource_id": "TEXT",
        "outcome": "TEXT",
        "details_json": "TEXT",
    }
    for column, definition in definitions.items():
        await conn.execute(
            f"ALTER TABLE app_logs ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_al_category_ts ON app_logs(category, ts DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_al_action_ts ON app_logs(action, ts DESC) "
        "WHERE action IS NOT NULL"
    )


async def _remove_content_activation_sqlite(conn: Any) -> None:
    """Retira el borrado suave de skills, prompts y tools.

    Estos recursos son contenido reutilizable: su uso se controla enlazándolos
    o desenlazándolos de un agente, no mediante un interruptor global.
    """
    for table in ("skills", "prompts", "tools"):
        columns = {
            str(row[1])
            for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")
        }
        if "deactivated_at" in columns:
            await conn.execute(f"ALTER TABLE {table} DROP COLUMN deactivated_at")
        if "is_active" in columns:
            await conn.execute(f"ALTER TABLE {table} DROP COLUMN is_active")


async def _remove_content_activation_pg(conn: Any) -> None:
    for table in ("skills", "prompts", "tools"):
        await conn.execute(
            f"ALTER TABLE {table} DROP COLUMN IF EXISTS deactivated_at, "
            "DROP COLUMN IF EXISTS is_active"
        )


async def _content_activation_sqlite(conn: Any) -> None:
    """Restaura el interruptor global de contenido reutilizable."""
    for table in ("skills", "prompts", "tools"):
        columns = {
            str(row[1])
            for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")
        }
        if "is_active" not in columns:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
        if "deactivated_at" not in columns:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN deactivated_at TEXT")


async def _content_activation_pg(conn: Any) -> None:
    for table in ("skills", "prompts", "tools"):
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "is_active SMALLINT NOT NULL DEFAULT 1"
        )
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deactivated_at TEXT"
        )


async def _resource_origin_labels_sqlite(conn: Any) -> None:
    for table in ("agents", "skills", "prompts", "tools"):
        rows = await conn.execute_fetchall(f"SELECT id, owner_id, data FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET data=? WHERE id=? AND owner_id=?",
            [(normalize_resource_data(row[2]), row[0], row[1]) for row in rows],
        )
    for table in ("knowledge_items", "agent_workflows", "resource_social"):
        rows = await conn.execute_fetchall(f"SELECT rowid, labels FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET labels=? WHERE rowid=?",
            [(normalize_labels(row[1], origin="community"), row[0]) for row in rows],
        )
    if await _table_exists_sqlite(conn, "official_package_components"):
        rows = await conn.execute_fetchall(
            "SELECT rowid, labels FROM official_package_components"
        )
        await conn.executemany(
            "UPDATE official_package_components SET labels=? WHERE rowid=?",
            [
                (
                    normalize_labels(row[1], origin="official", drop_production=True),
                    row[0],
                )
                for row in rows
            ],
        )
    await conn.execute(
        "DELETE FROM resource_labels WHERE label IN ('official', 'community')"
    )
    for resource_type, table in (
        ("agent", "agents"),
        ("skill", "skills"),
        ("prompt", "prompts"),
        ("tool", "tools"),
        ("knowledge", "knowledge_items"),
        ("workflow", "agent_workflows"),
    ):
        await conn.execute(
            "INSERT OR IGNORE INTO resource_labels "
            "(resource_type, resource_id, owner_id, label) "
            f"SELECT '{resource_type}', id, owner_id, 'community' FROM {table}"
        )


async def _resource_origin_labels_pg(conn: Any) -> None:
    for table in ("agents", "skills", "prompts", "tools"):
        rows = await conn.fetch(f"SELECT id, owner_id, data FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET data=$1 WHERE id=$2 AND owner_id=$3",
            [
                (normalize_resource_data(row["data"]), row["id"], row["owner_id"])
                for row in rows
            ],
        )
    for table in ("knowledge_items", "agent_workflows"):
        rows = await conn.fetch(f"SELECT id, labels FROM {table}")
        await conn.executemany(
            f"UPDATE {table} SET labels=$1 WHERE id=$2",
            [
                (normalize_labels(row["labels"], origin="community"), row["id"])
                for row in rows
            ],
        )
    rows = await conn.fetch(
        "SELECT resource_type, resource_id, owner, labels FROM resource_social"
    )
    await conn.executemany(
        "UPDATE resource_social SET labels=$1 WHERE resource_type=$2 AND resource_id=$3 AND owner=$4",
        [
            (
                normalize_labels(row["labels"], origin="community"),
                row["resource_type"],
                row["resource_id"],
                row["owner"],
            )
            for row in rows
        ],
    )
    if await _table_exists_pg(conn, "official_package_components"):
        rows = await conn.fetch(
            "SELECT package_id, version, component_id, labels FROM official_package_components"
        )
        await conn.executemany(
            "UPDATE official_package_components SET labels=$1 "
            "WHERE package_id=$2 AND version=$3 AND component_id=$4",
            [
                (
                    normalize_labels(
                        row["labels"], origin="official", drop_production=True
                    ),
                    row["package_id"],
                    row["version"],
                    row["component_id"],
                )
                for row in rows
            ],
        )
    await conn.execute(
        "DELETE FROM resource_labels WHERE label IN ('official', 'community')"
    )
    for resource_type, table in (
        ("agent", "agents"),
        ("skill", "skills"),
        ("prompt", "prompts"),
        ("tool", "tools"),
        ("knowledge", "knowledge_items"),
        ("workflow", "agent_workflows"),
    ):
        await conn.execute(
            "INSERT INTO resource_labels (resource_type, resource_id, owner_id, label) "
            f"SELECT '{resource_type}', id, owner_id, 'community' FROM {table} "
            "ON CONFLICT (resource_type, resource_id, label) DO NOTHING"
        )


async def _connection_provider_accounts_sqlite(conn: Any) -> None:
    columns = {
        str(row[1])
        for row in await conn.execute_fetchall("PRAGMA table_info(connections)")
    }
    if "provider_account_id" not in columns:
        await conn.execute(
            "ALTER TABLE connections ADD COLUMN provider_account_id TEXT"
        )
    rows = await conn.execute_fetchall(
        "SELECT id,owner_id,data FROM connections WHERE provider_account_id IS NULL"
    )
    for row in rows:
        try:
            payload = json.loads(row[2] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        account_id = str(payload.get("_account_id") or "").strip()
        if not account_id:
            continue
        accounts = await conn.execute_fetchall(
            "SELECT 1 FROM accounts WHERE id=? AND owner_id=?", (account_id, row[1])
        )
        if accounts:
            await conn.execute(
                "UPDATE connections SET provider_account_id=? WHERE id=?",
                (account_id, row[0]),
            )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_connections_provider_account "
        "ON connections(owner_id,provider_account_id)"
    )


async def _connection_provider_accounts_pg(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE connections ADD COLUMN IF NOT EXISTS provider_account_id TEXT"
    )
    # Esta función llegó copiada de sqlite.py sin traducir: llamaba a
    # `fetchall`/`fetchone` —que asyncpg no tiene— con marcadores `?` y una
    # tupla de parámetros. Aquí la conexión es la de asyncpg en crudo (ver
    # db.py::migrate_schema), no el envoltorio AsyncConn, así que reventaba con
    # AttributeError y dejaba sin arrancar cualquier instalación nueva sobre
    # PostgreSQL. Lo destapó preparar el catálogo contra una base real.
    rows = await conn.fetch(
        "SELECT id,owner_id,data FROM connections WHERE provider_account_id IS NULL"
    )
    for row in rows:
        try:
            payload = json.loads(row["data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        account_id = str(payload.get("_account_id") or "").strip()
        if not account_id:
            continue
        account = await conn.fetchrow(
            "SELECT 1 FROM accounts WHERE id=$1 AND owner_id=$2",
            account_id,
            row["owner_id"],
        )
        if account:
            await conn.execute(
                "UPDATE connections SET provider_account_id=$1 WHERE id=$2",
                account_id,
                row["id"],
            )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_connections_provider_account "
        "ON connections(owner_id,provider_account_id)"
    )


async def _public_agents_in_social_catalog_sqlite(conn: Any) -> None:
    """Repara agentes de usuario guardados como públicos pero no publicados.

    Versiones anteriores permitían ``agents.scope='public'`` sin crear la fila
    de ``resource_social``. Solo se recuperan agentes con label pública y se
    excluyen los agentes de sistema para no alterar su exposición histórica.
    """
    await conn.execute("""
        INSERT INTO resource_social (
            resource_type, resource_id, owner, name, description, is_public,
            category, trial_missing_deps, tags, labels, updated_at
        )
        SELECT
            'agent', a.id, a.owner_id, a.name,
            COALESCE(json_extract(a.data, '$.description'), ''), 1,
            'Other', 'warn',
            COALESCE(json_extract(a.data, '$.tags'), '[]'),
            COALESCE(json_extract(a.data, '$.labels'), '["public","community"]'),
            a.updated_at
        FROM agents a
        WHERE a.scope='public'
          AND a.owner_id!='__public__'
          AND json_valid(a.data)
          AND EXISTS (
              SELECT 1 FROM json_each(json_extract(a.data, '$.labels'))
              WHERE value='public'
          )
          AND NOT EXISTS (
              SELECT 1 FROM resource_social rs
              WHERE rs.resource_type='agent' AND rs.resource_id=a.id
                AND rs.owner=a.owner_id
          )
    """)


async def _public_agents_in_social_catalog_pg(conn: Any) -> None:
    """Repara agentes de usuario públicos que quedaron fuera de Explore."""
    await conn.execute("""
        INSERT INTO resource_social (
            resource_type, resource_id, owner, name, description, is_public,
            category, trial_missing_deps, tags, labels, updated_at
        )
        SELECT
            'agent', a.id, a.owner_id, a.name,
            COALESCE(a.data::jsonb ->> 'description', ''), 1,
            'Other', 'warn',
            COALESCE((a.data::jsonb -> 'tags')::text, '[]'),
            COALESCE((a.data::jsonb -> 'labels')::text, '["public","community"]'),
            a.updated_at::timestamptz
        FROM agents a
        WHERE a.scope='public'
          AND a.owner_id!='__public__'
          AND (a.data::jsonb -> 'labels') ? 'public'
          AND NOT EXISTS (
              SELECT 1 FROM resource_social rs
              WHERE rs.resource_type='agent' AND rs.resource_id=a.id
                AND rs.owner=a.owner_id
          )
    """)


async def _group_share_cascade_flag_sqlite(conn: Any) -> None:
    """Distingue lo compartido a mano de lo que arrastró un agente.

    Sin esta marca, retirar un agente no puede saber qué dependencias vinieron
    con él: o no retira nada —el acceso se queda vivo— o retira también lo que
    el usuario había compartido por su cuenta antes.
    """
    cursor = await conn.execute("PRAGMA table_info(resource_group_shares)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "via_cascade" not in columns:
        await conn.execute(
            "ALTER TABLE resource_group_shares "
            "ADD COLUMN via_cascade INTEGER NOT NULL DEFAULT 0"
        )


async def _group_share_cascade_flag_pg(conn: Any) -> None:
    """Distingue lo compartido a mano de lo que arrastró un agente.

    Sin esta marca, retirar un agente no puede saber qué dependencias vinieron
    con él: o no retira nada —el acceso se queda vivo— o retira también lo que
    el usuario había compartido por su cuenta antes.
    """
    await conn.execute(
        "ALTER TABLE resource_group_shares "
        "ADD COLUMN IF NOT EXISTS via_cascade INTEGER NOT NULL DEFAULT 0"
    )


async def _gdpr_orphan_resources_sqlite(conn: Any) -> None:
    """Limpia lo que el borrado RGPD dejó atrás antes de conocer estas tablas."""
    cursor = await conn.execute("SELECT COUNT(*) FROM users")
    fila = await cursor.fetchone()
    if not fila or not fila[0]:
        # Instalación recién creada: sin usuarios, "no está en users" es cierto
        # para todo y esto vaciaría las tablas en vez de limpiarlas.
        return
    marcadores = ",".join("?" for _ in _DUEÑOS_SIN_CUENTA)
    for tabla, columna in _TABLAS_CON_HUÉRFANOS:
        await conn.execute(
            f"DELETE FROM {tabla} "
            f"WHERE {columna} NOT IN (SELECT id FROM users) "
            f"AND {columna} NOT IN ({marcadores})",
            _DUEÑOS_SIN_CUENTA,
        )


async def _gdpr_orphan_resources_pg(conn: Any) -> None:
    """Limpia lo que el borrado RGPD dejó atrás antes de conocer estas tablas."""
    total = await conn.fetchval("SELECT COUNT(*) FROM users")
    if not total:
        # Instalación recién creada: sin usuarios, "no está en users" es cierto
        # para todo y esto vaciaría las tablas en vez de limpiarlas.
        return
    for tabla, columna in _TABLAS_CON_HUÉRFANOS:
        await conn.execute(
            f"DELETE FROM {tabla} "
            f"WHERE {columna} NOT IN (SELECT id FROM users) "
            f"AND {columna} <> ALL($1::text[])",
            list(_DUEÑOS_SIN_CUENTA),
        )


async def _gdpr_legacy_owner_orphans_sqlite(conn: Any) -> None:
    """Limpia filas huérfanas en tablas cuyo dueño no se llama owner_id."""
    cursor = await conn.execute("SELECT COUNT(*) FROM users")
    fila = await cursor.fetchone()
    if not fila or not fila[0]:
        return

    reservados = ",".join("?" for _ in _DUEÑOS_SIN_CUENTA)
    condicion = f"NOT IN (SELECT id FROM users) AND {{columna}} NOT IN ({reservados})"

    # Dependencias antes que padres, también cuando foreign_keys está apagado.
    await conn.execute(
        "DELETE FROM workflow_run_events WHERE run_id IN ("
        "SELECT id FROM workflow_runs WHERE started_by "
        + condicion.format(columna="started_by")
        + ")",
        _DUEÑOS_SIN_CUENTA,
    )
    await conn.execute(
        "DELETE FROM subscription_license_assignments WHERE subscription_id IN ("
        "SELECT id FROM subscriptions WHERE username "
        + condicion.format(columna="username")
        + ")",
        _DUEÑOS_SIN_CUENTA,
    )
    for columna in ("username", "assigned_by"):
        await conn.execute(
            "DELETE FROM subscription_license_assignments WHERE "
            f"{columna} " + condicion.format(columna=columna),
            _DUEÑOS_SIN_CUENTA,
        )
    for tabla, columna in (
        ("personal_access_tokens", "username"),
        ("vscode_auth_codes", "username"),
        ("workflow_runs", "started_by"),
        ("subscriptions", "username"),
    ):
        await conn.execute(
            f"DELETE FROM {tabla} WHERE {columna} " + condicion.format(columna=columna),
            _DUEÑOS_SIN_CUENTA,
        )


async def _gdpr_legacy_owner_orphans_pg(conn: Any) -> None:
    """Variante asyncpg de la limpieza de dueños históricos."""
    if not await conn.fetchval("SELECT COUNT(*) FROM users"):
        return

    condicion = "NOT IN (SELECT id FROM users) AND {columna} <> ALL($1::text[])"
    reservados = list(_DUEÑOS_SIN_CUENTA)
    await conn.execute(
        "DELETE FROM workflow_run_events WHERE run_id IN ("
        "SELECT id FROM workflow_runs WHERE started_by "
        + condicion.format(columna="started_by")
        + ")",
        reservados,
    )
    await conn.execute(
        "DELETE FROM subscription_license_assignments WHERE subscription_id IN ("
        "SELECT id FROM subscriptions WHERE username "
        + condicion.format(columna="username")
        + ")",
        reservados,
    )
    for columna in ("username", "assigned_by"):
        await conn.execute(
            "DELETE FROM subscription_license_assignments WHERE "
            f"{columna} " + condicion.format(columna=columna),
            reservados,
        )
    for tabla, columna in (
        ("personal_access_tokens", "username"),
        ("vscode_auth_codes", "username"),
        ("workflow_runs", "started_by"),
        ("subscriptions", "username"),
    ):
        await conn.execute(
            f"DELETE FROM {tabla} WHERE {columna} " + condicion.format(columna=columna),
            reservados,
        )


async def _unused_indexes_audit_sqlite(conn: Any) -> None:
    """Retira tres índices que ninguna consulta elige.

    Pasadas las 457 secciones de app/sql/queries/ por EXPLAIN QUERY PLAN,
    estos tres no aparecen en ningún plan: `stripe_customer_id` solo se
    escribe —toda lectura de subscriptions entra por username o por el UNIQUE
    de stripe_subscription_id— y `official_source_id` es trazabilidad de solo
    escritura, porque el recorrido por fuente entra por resource_source_links.
    Se quedan los de agents y skills, que su count_all sí elige como covering
    para contar filas; prompts y tools no tienen count_all.

    Aquí solo se borra. El índice que faltaba —expires_at en
    official_import_drafts, por donde entran count_expired_drafts y
    delete_expired_drafts— se declara en app/sql/schema/ y nada más hay que
    hacer: migrate_schema ejecuta el esquema completo, con sus CREATE INDEX IF
    NOT EXISTS, justo antes de llegar aquí. Una ausencia es lo único que el
    esquema no sabe expresar, y por eso los DROP sí necesitan migración.

    (El de source_id de esa misma tabla no se retira aunque ninguna consulta lo
    elija: sostiene la cascada de su FOREIGN KEY, y PostgreSQL no indexa las
    claves foráneas por su cuenta.)

    El barrido por EXPLAIN no ve el SQL que se arma en Python: los dos índices
    de app_logs quedaron fuera de él y sí se usan en cada carga del visor
    (ver la migración 26, que los midió). Por eso esta migración no los toca.
    """
    for indice in (
        "idx_subscriptions_customer",
        "idx_prompts_official",
        "idx_tools_official",
    ):
        await conn.execute(f"DROP INDEX IF EXISTS {indice}")


async def _unused_indexes_audit_pg(conn: Any) -> None:
    """Retira tres índices que ninguna consulta elige.

    Gemela de la migración 29 de SQLite; el razonamiento completo está allí.
    Aquí importa especialmente conservar idx_official_drafts_source: PostgreSQL
    no indexa las claves foráneas por su cuenta, así que sin él cada borrado en
    official_sources recorre entera la tabla de borradores por la cascada.

    El índice que faltaba (expires_at) lo crea el esquema, que migrate_schema
    ejecuta justo antes que estas migraciones; aquí solo se borra.

    El barrido por EXPLAIN que detectó estos tres solo ve el SQL estático de
    app/sql/queries/, y además se ejecutó contra SQLite: los planes de
    PostgreSQL siguen sin medirse contra una instancia real.
    """
    for indice in (
        "idx_subscriptions_customer",
        "idx_prompts_official",
        "idx_tools_official",
    ):
        await conn.execute(f"DROP INDEX IF EXISTS {indice}")
