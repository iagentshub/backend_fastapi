"""Storage for linked provider accounts — DB-backed with owner_id."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.crypto import decrypt, encrypt
from app.storage.db import IS_PG, PH, close_db, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:6] + "..." + key[-4:]


class AccountStorage:
    """DB-backed account storage. Accepts the DB file path."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._migrate_files()

    def _conn(self):
        return open_db(self._db_path)

    def _migrate_files(self) -> None:
        """One-time import from per-provider JSON files."""
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM accounts")
            if cur.fetchone()[0]:
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
                    self._upsert_with_conn(conn, "admin", provider, d)
                    p.rename(p.with_suffix(".migrated"))
                except Exception:
                    pass
        finally:
            close_db(conn)

    def _upsert_with_conn(self, conn, owner_id: str, provider: str, data: Dict[str, Any]) -> None:
        if IS_PG:
            conn.cursor().execute(
                f"INSERT INTO accounts (owner_id, provider, data, linked_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}) "
                f"ON CONFLICT (owner_id, provider) DO UPDATE SET data=EXCLUDED.data, linked_at=EXCLUDED.linked_at",
                (
                    owner_id,
                    provider,
                    json.dumps(data, ensure_ascii=False),
                    str(data.get("linked_at") or _now()),
                ),
            )
        else:
            conn.execute(
                f"INSERT OR REPLACE INTO accounts (owner_id, provider, data, linked_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH})",
                (
                    owner_id,
                    provider,
                    json.dumps(data, ensure_ascii=False),
                    str(data.get("linked_at") or _now()),
                ),
            )
        conn.commit()

    def get(self, provider: str, owner_id: str = "admin") -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT data FROM accounts WHERE owner_id = {PH} AND provider = {PH}",
                (owner_id, provider),
            )
            row = cur.fetchone()
            if not row:
                return None
            d = json.loads(row["data"])
            if d.get("api_key"):
                d["api_key"] = decrypt(d["api_key"])
            return d
        finally:
            close_db(conn)

    def save(self, provider: str, data: Dict[str, Any], owner_id: str = "admin") -> Dict[str, Any]:
        existing = self.get(provider, owner_id) or {}
        data["provider"] = provider
        data.setdefault("linked_at", existing.get("linked_at") or _now())
        if not data.get("api_key") and existing.get("api_key"):
            data["api_key"] = existing["api_key"]
        stored = dict(data)
        if stored.get("api_key"):
            stored["api_key"] = encrypt(stored["api_key"])
        conn = self._conn()
        try:
            self._upsert_with_conn(conn, owner_id, provider, stored)
        finally:
            close_db(conn)
        return data

    def delete(self, provider: str, owner_id: str = "admin") -> bool:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM accounts WHERE owner_id = {PH} AND provider = {PH}",
                (owner_id, provider),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            close_db(conn)

    def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT data FROM accounts WHERE owner_id = {PH} ORDER BY provider",
                (owner_id,),
            )
            result = []
            for row in cur.fetchall():
                d: Dict[str, Any] = json.loads(row["data"])
                if d.get("api_key"):
                    d["api_key_masked"] = _mask(decrypt(d["api_key"]))
                    del d["api_key"]
                result.append(d)
            return result
        finally:
            close_db(conn)
