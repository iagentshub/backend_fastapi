"""Piezas sueltas que usan los pasos históricos: columnas, nombres y blobs.

`_compact_resource_data` la importan además cinco storages a través de
`app.storage.db_migrations`, que es este mismo módulo bajo otro nombre.
"""


from __future__ import annotations

import json
from typing import Any

# ── Schema DDL ─────────────────────────────────────────────────────────────────


# ── Schema DDL ─────────────────────────────────────────────────────────────────

_SCHEMA_INDEX_DEPS: list[tuple[str, str, str]] = [
    ("users", "stripe_customer_id", "TEXT"),
    # idx_<tabla>_official se crea en el esquema sobre esta columna, así que
    # una base anterior a las fuentes oficiales necesita tenerla antes de que
    # corra executescript (la migración 7 la añadiría demasiado tarde).
    ("agents", "official_source_id", "TEXT"),
    ("skills", "official_source_id", "TEXT"),
    ("prompts", "official_source_id", "TEXT"),
    ("tools", "official_source_id", "TEXT"),
    ("knowledge_items", "official_source_id", "TEXT"),
    ("agent_workflows", "official_source_id", "TEXT"),
]

# Tablas de recursos que reciben el borrado suave (is_active + deactivated_at)
_RESOURCE_TABLES: tuple[str, ...] = (
    "agents",
    "connections",
    "knowledge_items",
    "agent_workflows",
    "llm_orchestrations",
)

_NAMED_RESOURCE_TABLES: tuple[str, ...] = ("agents", "skills", "connections")

_RESOURCE_BLOB_DUPLICATES = frozenset(
    {
        "id",
        "name",
        "owner_id",
        "resource_type",
        "scope",
        "tokens_in",
        "tokens_out",
        "is_active",
        "deactivated_at",
        "created_at",
        "updated_at",
    }
)

async def _sqlite_columns(conn: Any, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}

async def _add_sqlite_column(
    conn: Any, table: str, column: str, definition: str
) -> bool:
    """Add a column idempotently, tolerating only a concurrent migration race."""
    if column in await _sqlite_columns(conn, table):
        return False
    try:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        await conn.commit()
    except Exception:
        if column in await _sqlite_columns(conn, table):
            return False
        raise
    return True

def _resource_name_from_data(raw_data: Any, resource_id: str) -> str:
    """Return the canonical display name stored in a legacy resource blob."""
    try:
        data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except (json.JSONDecodeError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("name", "label", "type"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return resource_id

def _compact_resource_data(raw_data: Any) -> str:
    """Remove fields whose canonical value lives in relational columns."""
    try:
        data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
    except (json.JSONDecodeError, TypeError):
        return str(raw_data)
    if not isinstance(data, dict):
        return str(raw_data)
    compact = {
        key: value
        for key, value in data.items()
        if key not in _RESOURCE_BLOB_DUPLICATES
    }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
