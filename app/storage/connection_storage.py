"""Storage de conexiones a proveedores LLM (claves cifradas en la BD)."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage.crypto import decrypt, encrypt
from app.storage.db import open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.resource_base import ResourceStorage
from app.utils import flog
from app.utils import now_iso as _now
from app.utils.generators import generate_id


def _display_name(data: Dict[str, Any], resource_id: str) -> str:
    """Canonical name, with legacy connection fields as compatibility fallbacks."""
    for key in ("name", "label", "type"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return resource_id


# Campos sensibles que se cifran en la BD
_ENCRYPTED_FIELDS = ("api_key", "password", "ssh_key")


class ConnectionStorage(ResourceStorage):
    """DB-backed async connection storage."""
    table = "connections"
    resource_type = "connection"

    async def _migrate_legacy_data(self) -> None:
        """One-time import from connections.json if table is empty."""
        # Este SÍ tiene que quedarse dentro de la función: DATA_DIR se reescribe
        # en cada test (conftest: patch_data_dir) y subirlo arriba lo congelaría
        # al directorio de la fase de colección.
        from app.config.data import DATA_DIR

        async with open_db() as conn:
            try:
                count = await conn.fetchval("SELECT COUNT(*) FROM connections")
                if count:
                    return
            except Exception:
                return
            old = DATA_DIR / "connections" / "connections.json"
            if not old.exists():
                return
            try:
                items = json.loads(old.read_text(encoding="utf-8"))
                for item in items:
                    await self._upsert(conn, item)
                await conn.commit()
                old.rename(old.with_suffix(".migrated"))
            except Exception as exc:
                flog.warning(f"[storage] Migración de connections.json fallida: {exc}")

    async def _upsert(
        self, conn: Any, payload: Dict[str, Any], owner_id: str = "admin"
    ) -> None:
        """Insert or replace a connection row (uses AsyncConn, ? placeholders)."""
        conn_id = str(payload.get("id") or "").strip() or generate_id()
        payload["id"] = conn_id
        name = _display_name(payload, conn_id)
        provider_account_id = str(
            payload.get("provider_account_id") or payload.get("_account_id") or ""
        ).strip() or None
        is_active = 1 if payload.get("is_active", True) else 0
        deactivated_at = payload.get("deactivated_at")
        data_json = _compact_resource_data(payload)
        if _db.IS_PG:
            await conn.execute(
                "INSERT INTO connections (id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET owner_id=EXCLUDED.owner_id, provider_account_id=EXCLUDED.provider_account_id, "
                "name=EXCLUDED.name, data=EXCLUDED.data, "
                "tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out, "
                "is_active=EXCLUDED.is_active, deactivated_at=EXCLUDED.deactivated_at, "
                "updated_at=EXCLUDED.updated_at",
                (
                    conn_id,
                    owner_id,
                    provider_account_id,
                    name,
                    data_json,
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    is_active,
                    deactivated_at,
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO connections "
                "(id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conn_id,
                    owner_id,
                    provider_account_id,
                    name,
                    data_json,
                    int(payload.get("tokens_in") or 0),
                    int(payload.get("tokens_out") or 0),
                    is_active,
                    deactivated_at,
                    str(payload.get("created_at") or _now()),
                    str(payload.get("updated_at") or _now()),
                ),
            )

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        for field in _ENCRYPTED_FIELDS:
            if d.get(field):
                d[field] = decrypt(d[field])
        d.update(
            {
                "id": row["id"],
                "name": row["name"],
                "resource_type": "connection",
                "scope": "private",
                "owner_id": row["owner_id"],
                "provider_account_id": row["provider_account_id"],
                "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        d.setdefault("description", "")
        d.setdefault("icon", "")
        d.setdefault("labels", ["private"])
        if d.get("provider_account_id"):
            d.setdefault("_account_id", d["provider_account_id"])
        d["is_active"] = bool(row["is_active"])
        d["deactivated_at"] = row["deactivated_at"]
        return d

    async def list(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """owner_id=None → admin sees all. owner_id=str → own connections only."""
        await self._ensure_migrated()

        async with open_db() as conn:
            if owner_id is None:
                rows = await conn.fetchall(
                    "SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections ORDER BY created_at ASC"
                )
            else:
                rows = await conn.fetchall(
                    "SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections "
                    "WHERE owner_id = ? ORDER BY created_at ASC",
                    (owner_id,),
                )
        return [self._row_to_dict(r) for r in rows]

    async def get(
        self, conn_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:

        async with open_db() as conn:
            if owner_id is None:
                row = await conn.fetchone(
                    "SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections WHERE id = ?",
                    (conn_id,),
                )
            else:
                row = await conn.fetchone(
                    "SELECT id, owner_id, provider_account_id, name, data, tokens_in, tokens_out, is_active, "
                    "deactivated_at, created_at, updated_at FROM connections "
                    "WHERE id = ? AND owner_id = ?",
                    (conn_id, owner_id),
                )
        return self._row_to_dict(row) if row else None

    async def get_owner_id(self, conn_id: str) -> Optional[str]:
        """owner_id de una conexión sin traer el resto de la fila.

        Usado para resolver conexiones compartidas con un group: solo hace
        falta saber si el dueño sigue activo antes de exponer la conexión.
        """
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT owner_id FROM connections WHERE id = ?", (conn_id,)
            )
        return row[0] if row else None

    async def save(
        self, payload: Dict[str, Any], owner_id: str = "admin"
    ) -> Dict[str, Any]:

        conn_id = str(payload.get("id") or "").strip() or generate_id()
        payload["id"] = conn_id
        payload["name"] = _display_name(payload, conn_id)
        existing = await self.get(conn_id, owner_id)
        if existing:
            payload["created_at"] = existing.get("created_at", _now())
            # Conservar campos cifrados si no se envían nuevos
            for field in _ENCRYPTED_FIELDS:
                if not payload.get(field) and existing.get(field):
                    payload[field] = existing[field]
            # Conservar el borrado suave a través de las ediciones.
            payload["is_active"] = existing.get("is_active", True)
            payload["deactivated_at"] = existing.get("deactivated_at")
            if not payload.get("provider_account_id") and not payload.get("_account_id"):
                payload["provider_account_id"] = existing.get("provider_account_id")
        else:
            payload.setdefault("created_at", _now())
            payload.setdefault("is_active", True)
        payload["updated_at"] = _now()
        payload.update(
            {
                "resource_type": "connection",
                "scope": "private",
                "owner_id": owner_id,
            }
        )
        payload.setdefault("description", "")
        payload.setdefault("icon", "")
        payload.setdefault("labels", ["private"])
        stored = dict(payload)
        for field in _ENCRYPTED_FIELDS:
            if stored.get(field):
                stored[field] = encrypt(stored[field])
        async with open_db() as conn:
            await self._upsert(conn, stored, owner_id)
            await conn.commit()
        await self.sync_labels(conn_id, owner_id, payload.get("labels") or [])
        return payload

    async def delete(self, conn_id: str, owner_id: Optional[str] = None) -> bool:

        async with open_db() as conn:
            if owner_id is None:
                existing = await conn.fetchone(
                    "SELECT id FROM connections WHERE id = ?", (conn_id,)
                )
            else:
                existing = await conn.fetchone(
                    "SELECT id FROM connections WHERE id = ? AND owner_id = ?",
                    (conn_id, owner_id),
                )
            if not existing:
                return False
            if owner_id is None:
                await conn.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
            else:
                await conn.execute(
                    "DELETE FROM connections WHERE id = ? AND owner_id = ?",
                    (conn_id, owner_id),
                )
            await conn.commit()
        await self.clear_labels(conn_id)
        return True

    async def add_tokens(
        self, conn_id: str, input_tokens: int, output_tokens: int
    ) -> None:

        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE connections SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ? WHERE id = ?",
                    (input_tokens, output_tokens, conn_id),
                )
                total = input_tokens + output_tokens
                if total > 0:
                    row = await conn.fetchone(
                        "SELECT owner_id FROM connections WHERE id = ?", (conn_id,)
                    )
                    if row:
                        today = date.today().isoformat()
                        owner = row["owner_id"]
                        if _db.IS_PG:
                            await conn.execute(
                                "INSERT INTO token_daily (day, owner_id, tokens) VALUES (?, ?, ?) "
                                "ON CONFLICT (day, owner_id) DO UPDATE SET tokens = token_daily.tokens + EXCLUDED.tokens",
                                (today, owner, total),
                            )
                        else:
                            await conn.execute(
                                "INSERT INTO token_daily (day, owner_id, tokens) VALUES (?, ?, ?) "
                                "ON CONFLICT(day, owner_id) DO UPDATE SET tokens = token_daily.tokens + excluded.tokens",
                                (today, owner, total),
                            )
