"""Storage de ficheros de memoria. La propiedad interna se guarda con users.id."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.sql import sql

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage.db import DB_ERRORS, AsyncConn, open_db
from app.storage.migration import LegacyMigrationStorage
from app.utils import flog
from app.utils import now_iso as _now


def _safe_mem_id(filename: str) -> str:
    """Sanitiza el nombre de fichero de memoria para la DB."""
    name = re.sub(r"[^a-z0-9\-]", "-", filename.lower().removesuffix(".md")).strip("-")
    return name or "memory"


class MemoryStorage(LegacyMigrationStorage):
    """Async DB-backed memory storage (SQLite / PostgreSQL)."""

    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self._root_dir = Path(root_dir)  # solo para la migración única desde ficheros

    # ── one-time file→DB migration ───────────────────────────────────────────

    async def _migrate_legacy_data(self) -> None:

        async with open_db() as conn:
            try:
                count = await conn.fetchval(sql("queries/memory_files:count_all"))
                if count:
                    return
            except DB_ERRORS as exc:
                # Tabla aún inexistente (arranque previo a la migración de
                # esquema) o BD caída. Ver agent_storage para el razonamiento.
                flog.debug(f"[memory] Migración legacy omitida: {exc}")
                return
            now = _now()
            for p in sorted(self._root_dir.glob("*.md")):
                try:
                    content = p.read_text(encoding="utf-8")
                    mem_id = p.stem
                    await conn.execute(
                        sql(
                            "queries/memory_files:insert_ignore_pg"
                            if _db.IS_PG
                            else "queries/memory_files:insert_ignore_sqlite"
                        ),
                        (mem_id, "admin", content, now),
                    )
                except Exception as exc:  # noqa: BLE001
                    # Ancho a propósito: un .md ilegible no puede parar la
                    # migración del resto. Ver agent_storage.
                    flog.warning(f"[memory] Migración fallida {p}: {exc}")
            await conn.commit()

    # ── public API ───────────────────────────────────────────────────────────

    async def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/memory_files:list_by_owner"),
                (owner_id,),
            )
        return [
            {
                "id": r["id"],
                "filename": f"{r['id']}.md",
                "size": len(r["content"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    async def get(self, filename: str, owner_id: str = "admin") -> Optional[str]:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)

        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/memory_files:content_of"),
                (mem_id, owner_id),
            )
        return row["content"] if row else None

    async def save(
        self,
        filename: str,
        content: str,
        owner_id: str = "admin",
        *,
        conn: Optional[AsyncConn] = None,
    ) -> Dict[str, Any]:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)
        now = _now()

        async def write(target: AsyncConn) -> None:
            if _db.IS_PG:
                await target.execute(
                    sql("queries/memory_files:upsert_pg"),
                    (mem_id, owner_id, content, now),
                )
            else:
                await target.execute(
                    sql("queries/memory_files:upsert_sqlite"),
                    (mem_id, owner_id, content, now),
                )

        if conn is not None:
            await write(conn)
        else:
            async with open_db() as own_conn:
                await write(own_conn)
                await own_conn.commit()
        return {
            "id": mem_id,
            "filename": f"{mem_id}.md",
            "size": len(content),
            "updated_at": now,
        }

    async def delete(self, filename: str, owner_id: str = "admin") -> bool:
        await self._ensure_migrated()
        mem_id = _safe_mem_id(filename)

        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/memory_files:exists"),
                (mem_id, owner_id),
            )
            if not row:
                return False
            await conn.execute(
                sql("queries/memory_files:delete"), (mem_id, owner_id)
            )
            await conn.commit()
        return True
