"""Pasos del dominio de conocimiento y packs, en sus dos dialectos."""



from __future__ import annotations

import hashlib
import mimetypes
import re
from typing import Any

from app.storage.migrations.steps.misc import _table_exists_pg, _table_exists_sqlite


async def _knowledge_packs_sqlite(conn: Any) -> None:
    """Añade packs a instalaciones creadas antes del DDL base."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_packs (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', labels TEXT NOT NULL DEFAULT '["private"]',
            scope TEXT NOT NULL DEFAULT 'private', is_active INTEGER NOT NULL DEFAULT 1,
            deactivated_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_packs_owner "
        "ON knowledge_packs(owner_id, created_at DESC)"
    )

async def _knowledge_packs_pg(conn: Any) -> None:
    """Añade packs a instalaciones creadas antes del DDL base."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_packs (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', labels TEXT NOT NULL DEFAULT '["private"]',
            scope TEXT NOT NULL DEFAULT 'private', is_active SMALLINT NOT NULL DEFAULT 1,
            deactivated_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_packs_owner "
        "ON knowledge_packs(owner_id, created_at DESC)"
    )

async def _knowledge_file_metadata_sqlite(conn: Any) -> None:
    """Guarda el MIME y peso original de documentos e imágenes individuales."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "mime_type" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_items ADD COLUMN mime_type TEXT NOT NULL DEFAULT ''"
        )
    if "size_bytes" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_items ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0"
        )
    await conn.execute(
        "UPDATE knowledge_items SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )
    await conn.execute(
        "UPDATE knowledge_packs SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )

async def _knowledge_file_metadata_pg(conn: Any) -> None:
    """Guarda el MIME y peso original de documentos e imágenes individuales."""
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "mime_type TEXT NOT NULL DEFAULT ''"
    )
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "size_bytes BIGINT NOT NULL DEFAULT 0"
    )
    await conn.execute(
        "UPDATE knowledge_items SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )
    await conn.execute(
        "UPDATE knowledge_packs SET is_active=1,deactivated_at=NULL WHERE is_active=0"
    )

async def _knowledge_pack_sources_sqlite(conn: Any) -> None:
    """Registra si un pack es copia, referencia o instantánea sincronizable."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_packs)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "source_mode" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_packs ADD COLUMN "
            "source_mode TEXT NOT NULL DEFAULT 'upload'"
        )
    if "last_synced_at" not in columns:
        await conn.execute("ALTER TABLE knowledge_packs ADD COLUMN last_synced_at TEXT")

async def _knowledge_pack_sources_pg(conn: Any) -> None:
    """Registra si un pack es copia, referencia o instantánea sincronizable."""
    await conn.execute(
        "ALTER TABLE knowledge_packs ADD COLUMN IF NOT EXISTS "
        "source_mode TEXT NOT NULL DEFAULT 'upload'"
    )
    await conn.execute(
        "ALTER TABLE knowledge_packs ADD COLUMN IF NOT EXISTS last_synced_at TEXT"
    )

async def _knowledge_pack_upload_sessions_sqlite(conn: Any) -> None:
    cursor = await conn.execute("PRAGMA table_info(knowledge_packs)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "upload_status" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_packs ADD COLUMN "
            "upload_status TEXT NOT NULL DEFAULT 'ready'"
        )

async def _knowledge_pack_upload_sessions_pg(conn: Any) -> None:
    await conn.execute(
        "ALTER TABLE knowledge_packs ADD COLUMN IF NOT EXISTS "
        "upload_status TEXT NOT NULL DEFAULT 'ready'"
    )

async def _knowledge_item_checksums_sqlite(conn: Any) -> None:
    """Añade SHA-256 y rellena los objetos existentes de forma idempotente."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "checksum" not in columns:
        await conn.execute(
            "ALTER TABLE knowledge_items ADD COLUMN checksum TEXT NOT NULL DEFAULT ''"
        )
    if await _table_exists_sqlite(conn, "knowledge_pack_items"):
        rows = await conn.execute_fetchall(
            "SELECT k.id,k.content,COALESCE(pi.checksum,'') AS pack_checksum "
            "FROM knowledge_items k LEFT JOIN knowledge_pack_items pi "
            "ON pi.knowledge_id=k.id WHERE k.checksum=''"
        )
    else:
        rows = await conn.execute_fetchall(
            "SELECT id,content,'' AS pack_checksum FROM knowledge_items "
            "WHERE checksum=''"
        )
    for row in rows:
        checksum = (
            str(row[2] or "") or hashlib.sha256(str(row[1] or "").encode()).hexdigest()
        )
        await conn.execute(
            "UPDATE knowledge_items SET checksum=? WHERE id=?", (checksum, row[0])
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_checksum "
        "ON knowledge_items(checksum) WHERE checksum <> ''"
    )

async def _knowledge_item_checksums_pg(conn: Any) -> None:
    """Añade SHA-256 y rellena los objetos existentes de forma idempotente."""
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "checksum TEXT NOT NULL DEFAULT ''"
    )
    if await _table_exists_pg(conn, "knowledge_pack_items"):
        rows = await conn.fetch(
            "SELECT k.id,k.content,COALESCE(pi.checksum,'') AS pack_checksum "
            "FROM knowledge_items k LEFT JOIN knowledge_pack_items pi "
            "ON pi.knowledge_id=k.id WHERE k.checksum=''"
        )
    else:
        rows = await conn.fetch(
            "SELECT id,content,'' AS pack_checksum FROM knowledge_items "
            "WHERE checksum=''"
        )
    for row in rows:
        checksum = (
            str(row["pack_checksum"] or "")
            or hashlib.sha256(str(row["content"] or "").encode()).hexdigest()
        )
        await conn.execute(
            "UPDATE knowledge_items SET checksum=$1 WHERE id=$2",
            checksum,
            row["id"],
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_checksum "
        "ON knowledge_items(checksum) WHERE checksum <> ''"
    )

async def _knowledge_items_pack_membership_sqlite(conn: Any) -> None:
    """Convierte la relación de packs en una relación uno-a-muchos directa."""
    cursor = await conn.execute("PRAGMA table_info(knowledge_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    for column, definition in (
        ("mime_type", "TEXT NOT NULL DEFAULT ''"),
        ("size_bytes", "INTEGER NOT NULL DEFAULT 0"),
        ("pack_id", "TEXT"),
        ("pack_relative_path", "TEXT NOT NULL DEFAULT ''"),
        ("pack_kind", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in columns:
            await conn.execute(
                f"ALTER TABLE knowledge_items ADD COLUMN {column} {definition}"
            )
    if await _table_exists_sqlite(conn, "knowledge_pack_items"):
        await conn.execute(
            "UPDATE knowledge_items SET "
            "pack_id=(SELECT i.pack_id FROM knowledge_pack_items i "
            "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),"
            "pack_relative_path=COALESCE((SELECT i.relative_path "
            "FROM knowledge_pack_items i WHERE i.knowledge_id=knowledge_items.id "
            "LIMIT 1),''),"
            "pack_kind=COALESCE((SELECT i.kind FROM knowledge_pack_items i "
            "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),'') "
            "WHERE EXISTS (SELECT 1 FROM knowledge_pack_items i "
            "WHERE i.knowledge_id=knowledge_items.id)"
        )
        item_cursor = await conn.execute("PRAGMA table_info(knowledge_pack_items)")
        item_columns = {row[1] for row in await item_cursor.fetchall()}
        if "mime_type" in item_columns:
            await conn.execute(
                "UPDATE knowledge_items SET mime_type=COALESCE(NULLIF(mime_type,''),"
                "(SELECT i.mime_type FROM knowledge_pack_items i "
                "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),'')"
            )
        if "size_bytes" in item_columns:
            await conn.execute(
                "UPDATE knowledge_items SET size_bytes=CASE WHEN size_bytes=0 THEN "
                "COALESCE((SELECT i.size_bytes FROM knowledge_pack_items i "
                "WHERE i.knowledge_id=knowledge_items.id LIMIT 1),0) "
                "ELSE size_bytes END"
            )
        await conn.execute("DROP TABLE knowledge_pack_items")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_pack "
        "ON knowledge_items(pack_id,pack_relative_path) WHERE pack_id IS NOT NULL"
    )

async def _knowledge_items_pack_membership_pg(conn: Any) -> None:
    """Convierte la relación de packs en una relación uno-a-muchos directa."""
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS pack_id TEXT"
    )
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "pack_relative_path TEXT NOT NULL DEFAULT ''"
    )
    await conn.execute(
        "ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS "
        "pack_kind TEXT NOT NULL DEFAULT ''"
    )
    if await _table_exists_pg(conn, "knowledge_pack_items"):
        await conn.execute(
            "UPDATE knowledge_items k SET pack_id=i.pack_id,"
            "pack_relative_path=i.relative_path,pack_kind=i.kind,"
            "mime_type=CASE WHEN k.mime_type='' THEN i.mime_type ELSE k.mime_type END,"
            "size_bytes=CASE WHEN k.size_bytes=0 THEN i.size_bytes ELSE k.size_bytes END "
            "FROM knowledge_pack_items i WHERE i.knowledge_id=k.id"
        )
        await conn.execute("DROP TABLE knowledge_pack_items")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_pack "
        "ON knowledge_items(pack_id,pack_relative_path) WHERE pack_id IS NOT NULL"
    )

async def _knowledge_item_metadata_repair_sqlite(conn: Any) -> None:
    """Recupera metadatos catalogados que sobrevivieron en el contenido."""
    rows = await conn.execute_fetchall(
        "SELECT id,source,content,mime_type,size_bytes FROM knowledge_items "
        "WHERE mime_type='' OR size_bytes=0"
    )
    for row in rows:
        content = str(row[2] or "")
        mime_match = re.search(r"(?:Tipo|Type):\s*([^\s]+)", content)
        size_match = re.search(r"(?:Tamano|Tamaño|Size):\s*(\d+)\s*bytes", content)
        mime_type = str(row[3] or "")
        if not mime_type:
            mime_type = (
                str(mime_match.group(1))
                if mime_match
                else mimetypes.guess_type(str(row[1] or ""))[0] or ""
            )
        size_bytes = int(row[4] or 0)
        if size_bytes == 0 and size_match:
            size_bytes = int(size_match.group(1))
        await conn.execute(
            "UPDATE knowledge_items SET mime_type=?,size_bytes=? WHERE id=?",
            (mime_type, size_bytes, row[0]),
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_mime_type "
        "ON knowledge_items(mime_type) WHERE mime_type <> ''"
    )

async def _knowledge_item_metadata_repair_pg(conn: Any) -> None:
    """Recupera metadatos catalogados que sobrevivieron en el contenido."""
    rows = await conn.fetch(
        "SELECT id,source,content,mime_type,size_bytes FROM knowledge_items "
        "WHERE mime_type='' OR size_bytes=0"
    )
    for row in rows:
        content = str(row["content"] or "")
        mime_match = re.search(r"(?:Tipo|Type):\s*([^\s]+)", content)
        size_match = re.search(r"(?:Tamano|Tamaño|Size):\s*(\d+)\s*bytes", content)
        mime_type = str(row["mime_type"] or "")
        if not mime_type:
            mime_type = (
                str(mime_match.group(1))
                if mime_match
                else mimetypes.guess_type(str(row["source"] or ""))[0] or ""
            )
        size_bytes = int(row["size_bytes"] or 0)
        if size_bytes == 0 and size_match:
            size_bytes = int(size_match.group(1))
        await conn.execute(
            "UPDATE knowledge_items SET mime_type=$1,size_bytes=$2 WHERE id=$3",
            mime_type,
            size_bytes,
            row["id"],
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_mime_type "
        "ON knowledge_items(mime_type) WHERE mime_type <> ''"
    )


# Su texto es idéntico en los dos motores, pero llama a un paso que NO lo es:
# unificarla haría que PostgreSQL ejecutara la variante de SQLite.

async def _remove_obsolete_knowledge_pack_items_sqlite(conn: Any) -> None:
    """Consolida cualquier relación legacy y elimina su tabla auxiliar."""
    await _knowledge_items_pack_membership_sqlite(conn)


async def _remove_obsolete_knowledge_pack_items_pg(conn: Any) -> None:
    """Consolida cualquier relación legacy y elimina su tabla auxiliar."""
    await _knowledge_items_pack_membership_pg(conn)
