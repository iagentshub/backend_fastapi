"""Storage for linked provider accounts — DB-backed with owner_id."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.crypto import decrypt, encrypt
from app.storage.db import IS_PG, open_db
from app.utils import flog
from app.utils import now_iso as _now


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:6] + "..." + key[-4:]


class AccountStorage:
    """DB-backed account storage. Accepts the DB file path."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    async def _migrate_files(self) -> None:
        """One-time import from per-provider JSON files."""
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
                    provider = p.stem
                    d["provider"] = provider
                    await self._upsert_with_conn(conn, "admin", provider, d)
                    p.rename(p.with_suffix(".migrated"))
                except Exception as exc:
                    flog.warning(f"[accounts] Migración de {p.name} fallida: {exc}")

    async def _upsert_with_conn(self, conn, owner_id: str, provider: str, data: Dict[str, Any]) -> None:
        if IS_PG:
            await conn.execute(
                "INSERT INTO accounts (owner_id, provider, data, linked_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (owner_id, provider) DO UPDATE SET data=EXCLUDED.data, linked_at=EXCLUDED.linked_at",
                (
                    owner_id,
                    provider,
                    json.dumps(data, ensure_ascii=False),
                    str(data.get("linked_at") or _now()),
                ),
            )
        else:
            await conn.execute(
                "INSERT OR REPLACE INTO accounts (owner_id, provider, data, linked_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    owner_id,
                    provider,
                    json.dumps(data, ensure_ascii=False),
                    str(data.get("linked_at") or _now()),
                ),
            )
        await conn.commit()

    async def get(self, provider: str, owner_id: str = "admin") -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT data FROM accounts WHERE owner_id = ? AND provider = ?",
                (owner_id, provider),
            )
            if not row:
                return None
            d = json.loads(row["data"])
            if d.get("api_key"):
                d["api_key"] = decrypt(d["api_key"])
            return d

    async def save(self, provider: str, data: Dict[str, Any], owner_id: str = "admin") -> Dict[str, Any]:
        existing = await self.get(provider, owner_id) or {}
        data["provider"] = provider
        data.setdefault("linked_at", existing.get("linked_at") or _now())
        if not data.get("api_key") and existing.get("api_key"):
            data["api_key"] = existing["api_key"]
        stored = dict(data)
        if stored.get("api_key"):
            stored["api_key"] = encrypt(stored["api_key"])
        async with open_db() as conn:
            await self._upsert_with_conn(conn, owner_id, provider, stored)
        return data

    async def delete(self, provider: str, owner_id: str = "admin") -> bool:
        async with open_db() as conn:
            exists = await conn.fetchone(
                "SELECT owner_id FROM accounts WHERE owner_id = ? AND provider = ?",
                (owner_id, provider),
            )
            if not exists:
                return False
            await conn.execute(
                "DELETE FROM accounts WHERE owner_id = ? AND provider = ?",
                (owner_id, provider),
            )
            await conn.commit()
            return True

    async def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT data FROM accounts WHERE owner_id = ? ORDER BY provider",
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
