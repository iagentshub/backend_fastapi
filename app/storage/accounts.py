"""Storage for linked provider accounts — DB-backed con owner_id.

Varias cuentas pueden compartir el mismo `provider` (ej. dos API keys de
OpenAI distintas): la clave de unicidad es `(id, owner_id)`, `provider` es
solo un campo más, no forma parte de la clave.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.storage.crypto import decrypt, encrypt
from app.storage.db import IS_PG, open_db
from app.utils import flog
from app.utils import now_iso as _now
from app.utils.generators import generate_id


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:6] + "..." + key[-4:]


class AccountStorage:
    """Almacén de cuentas en la base de datos configurada."""

    async def _migrate_files(self) -> None:
        """One-time import from per-provider JSON files (formato legado:
        una cuenta por proveedor, sin id propio)."""
        async with open_db() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM accounts")
            if count:
                return
            from app.config.data import DATA_DIR
            accounts_dir = DATA_DIR / "accounts"
            if not accounts_dir.exists():
                return
            for p in sorted(accounts_dir.glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    d["provider"] = p.stem
                    await self.save(d, owner_id="admin")
                    p.rename(p.with_suffix(".migrated"))
                except Exception as exc:
                    flog.warning(f"[accounts] Migración de {p.name} fallida: {exc}")

    async def _upsert_with_conn(
        self, conn: Any, owner_id: str, data: Dict[str, Any]
    ) -> None:
        if IS_PG:
            await conn.execute(
                "INSERT INTO accounts (id, owner_id, provider, data, linked_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET provider=EXCLUDED.provider, "
                "data=EXCLUDED.data, linked_at=EXCLUDED.linked_at",
                (
                    data["id"],
                    owner_id,
                    data["provider"],
                    json.dumps(data, ensure_ascii=False),
                    str(data.get("linked_at") or _now()),
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO accounts (id, owner_id, provider, data, linked_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data["id"],
                    owner_id,
                    data["provider"],
                    json.dumps(data, ensure_ascii=False),
                    str(data.get("linked_at") or _now()),
                ),
            )
        await conn.commit()

    async def get(self, account_id: str, owner_id: str = "admin") -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT data FROM accounts WHERE owner_id = ? AND id = ?",
                (owner_id, account_id),
            )
            if not row:
                return None
            d = json.loads(row["data"])
            if d.get("api_key"):
                d["api_key"] = decrypt(d["api_key"])
            return d

    async def save(self, data: Dict[str, Any], owner_id: str = "admin") -> Dict[str, Any]:
        """Crea una cuenta nueva o actualiza una existente.

        Con `data["id"]` presente y ya existente, actualiza esa cuenta
        (preservando `api_key`/`provider` si no se mandan de nuevo). Sin
        `id` (o con un `id` que no existe todavía), crea una cuenta nueva —
        así se permiten varias cuentas del mismo `provider` para el mismo
        owner, cada una con su propio id.
        """
        account_id = str(data.get("id") or "").strip()
        existing = await self.get(account_id, owner_id) if account_id else None
        if existing is None:
            account_id = generate_id()
        data["id"] = account_id
        data.setdefault("provider", (existing or {}).get("provider"))
        data.setdefault("linked_at", (existing or {}).get("linked_at") or _now())
        if not data.get("api_key") and (existing or {}).get("api_key"):
            data["api_key"] = existing["api_key"]
        stored = dict(data)
        if stored.get("api_key"):
            stored["api_key"] = encrypt(stored["api_key"])
        async with open_db() as conn:
            await self._upsert_with_conn(conn, owner_id, stored)
        return data

    async def delete(self, account_id: str, owner_id: str = "admin") -> bool:
        async with open_db() as conn:
            exists = await conn.fetchone(
                "SELECT id FROM accounts WHERE owner_id = ? AND id = ?",
                (owner_id, account_id),
            )
            if not exists:
                return False
            await conn.execute(
                "DELETE FROM accounts WHERE owner_id = ? AND id = ?",
                (owner_id, account_id),
            )
            await conn.commit()
            return True

    async def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT data FROM accounts WHERE owner_id = ? ORDER BY provider, linked_at",
                (owner_id,),
            )
        result = []
        for row in rows:
            d: Dict[str, Any] = json.loads(row["data"])
            if d.get("api_key"):
                d["api_key_masked"] = _mask(decrypt(d["api_key"]))
                del d["api_key"]
            result.append(d)
        return result
