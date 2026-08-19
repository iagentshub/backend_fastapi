"""Pasos históricos sobre los recursos: nombre propio y etiquetas de idioma.

Las dos variantes de cada operación van seguidas: es la única forma de que
corregir una obligue a mirar la otra.
"""


from __future__ import annotations

import json
from typing import Any

from app.config.content_languages import language_label

# ── Schema DDL ─────────────────────────────────────────────────────────────────
from app.storage.migrations.legacy._helpers import (
    _NAMED_RESOURCE_TABLES,
    _compact_resource_data,
    _resource_name_from_data,
)


async def _migrate_legacy_agent_language_labels(conn: Any, *, postgres: bool) -> None:
    """Mirror the legacy scalar agent language into the canonical label list."""
    if postgres:
        rows = await conn.fetch("SELECT id, owner_id, data FROM agents")
    else:
        cursor = await conn.execute("SELECT id, owner_id, data FROM agents")
        rows = await cursor.fetchall()
    for row in rows:
        try:
            data = json.loads(row["data"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        canonical = language_label(str(data.get("language") or ""))
        if not canonical:
            continue
        labels = [str(value) for value in (data.get("labels") or ["private"]) if value]
        changed = False
        if not any(label in {"official", "community"} for label in labels):
            insert_at = next(
                (
                    index + 1
                    for index, label in enumerate(labels)
                    if label in {"private", "public"}
                ),
                0,
            )
            labels.insert(insert_at, "community")
            changed = True
        if canonical not in labels:
            labels.append(canonical)
            changed = True
        if not changed:
            continue
        data["labels"] = labels
        compact_data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        serialized_labels = json.dumps(labels, ensure_ascii=False)
        if postgres:
            await conn.execute(
                "UPDATE agents SET data=$1 WHERE id=$2 AND owner_id=$3",
                compact_data,
                row["id"],
                row["owner_id"],
            )
            await conn.execute(
                "INSERT INTO resource_labels "
                "(resource_type, resource_id, owner_id, label) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT(resource_type, resource_id, label) DO NOTHING",
                "agent",
                row["id"],
                row["owner_id"],
                canonical,
            )
            await conn.execute(
                "INSERT INTO resource_labels "
                "(resource_type, resource_id, owner_id, label) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT(resource_type, resource_id, label) DO NOTHING",
                "agent",
                row["id"],
                row["owner_id"],
                "official" if "official" in labels else "community",
            )
            await conn.execute(
                "UPDATE resource_social SET labels=$1 "
                "WHERE resource_type='agent' AND resource_id=$2",
                serialized_labels,
                row["id"],
            )
        else:
            await conn.execute(
                "UPDATE agents SET data=? WHERE id=? AND owner_id=?",
                (compact_data, row["id"], row["owner_id"]),
            )
            await conn.execute(
                "INSERT INTO resource_labels "
                "(resource_type, resource_id, owner_id, label) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(resource_type, resource_id, label) DO NOTHING",
                ("agent", row["id"], row["owner_id"], canonical),
            )
            await conn.execute(
                "INSERT INTO resource_labels "
                "(resource_type, resource_id, owner_id, label) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(resource_type, resource_id, label) DO NOTHING",
                (
                    "agent",
                    row["id"],
                    row["owner_id"],
                    "official" if "official" in labels else "community",
                ),
            )
            await conn.execute(
                "UPDATE resource_social SET labels=? "
                "WHERE resource_type='agent' AND resource_id=?",
                (serialized_labels, row["id"]),
            )
    if not postgres:
        await conn.commit()

async def _migrate_named_resources_sqlite(conn: Any) -> None:
    """Add and backfill SQL names for resources formerly stored as JSON only."""
    for table in _NAMED_RESOURCE_TABLES:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cur.fetchall()}
        if "name" not in columns:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN name TEXT NOT NULL DEFAULT ''"
            )
        cur = await conn.execute(f"SELECT id, owner_id, name, data FROM {table}")
        for resource_id, owner_id, stored_name, raw_data in await cur.fetchall():
            name = str(stored_name or "").strip() or _resource_name_from_data(
                raw_data, resource_id
            )
            compact_data = _compact_resource_data(raw_data)
            if name != stored_name or compact_data != raw_data:
                await conn.execute(
                    f"UPDATE {table} SET name=?, data=? WHERE id=? AND owner_id=?",
                    (name, compact_data, resource_id, owner_id),
                )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_owner_name "
            f"ON {table}(owner_id, name)"
        )

async def _migrate_named_resources_pg(conn: Any) -> None:
    """PostgreSQL counterpart of :func:`_migrate_named_resources_sqlite`."""
    for table in _NAMED_RESOURCE_TABLES:
        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "name TEXT NOT NULL DEFAULT ''"
        )
        rows = await conn.fetch(f"SELECT id, owner_id, name, data FROM {table}")
        for row in rows:
            name = str(row["name"] or "").strip() or _resource_name_from_data(
                row["data"], row["id"]
            )
            compact_data = _compact_resource_data(row["data"])
            if name != row["name"] or compact_data != row["data"]:
                await conn.execute(
                    f"UPDATE {table} SET name=$1, data=$2 WHERE id=$3 AND owner_id=$4",
                    name,
                    compact_data,
                    row["id"],
                    row["owner_id"],
                )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_owner_name "
            f"ON {table}(owner_id, name)"
        )
