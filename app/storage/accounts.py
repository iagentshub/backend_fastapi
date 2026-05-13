"""Storage para cuentas de proveedor vinculadas — SQLite-backed, con owner_id."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:6] + "..." + key[-4:]


class AccountStorage:
    """SQLite-backed account storage. Acepta la ruta del fichero DB."""

    def __init__(self, db_path: Path) -> None:
        from app.storage.db import open_db
        self._db = open_db(Path(db_path))
        self._migrate_files()

    def _migrate_files(self) -> None:
        """Importación one-time desde ficheros JSON por proveedor."""
        if self._db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
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
                self._upsert("admin", provider, d)
                p.rename(p.with_suffix(".migrated"))
            except Exception:
                pass

    def _upsert(self, owner_id: str, provider: str, data: Dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO accounts (owner_id, provider, data, linked_at) "
            "VALUES (?, ?, ?, ?)",
            (
                owner_id,
                provider,
                json.dumps(data, ensure_ascii=False),
                str(data.get("linked_at") or _now()),
            ),
        )
        self._db.commit()

    def get(self, provider: str, owner_id: str = "admin") -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT data FROM accounts WHERE owner_id = ? AND provider = ?",
            (owner_id, provider),
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def save(self, provider: str, data: Dict[str, Any], owner_id: str = "admin") -> Dict[str, Any]:
        existing = self.get(provider, owner_id) or {}
        data["provider"] = provider
        data.setdefault("linked_at", existing.get("linked_at") or _now())
        if not data.get("api_key") and existing.get("api_key"):
            data["api_key"] = existing["api_key"]
        self._upsert(owner_id, provider, data)
        return data

    def delete(self, provider: str, owner_id: str = "admin") -> bool:
        cur = self._db.execute(
            "DELETE FROM accounts WHERE owner_id = ? AND provider = ?",
            (owner_id, provider),
        )
        self._db.commit()
        return cur.rowcount > 0

    def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT data FROM accounts WHERE owner_id = ? ORDER BY provider",
            (owner_id,),
        ).fetchall()
        result = []
        for row in rows:
            d: Dict[str, Any] = json.loads(row["data"])
            if d.get("api_key"):
                d["api_key_masked"] = _mask(d["api_key"])
                del d["api_key"]
            result.append(d)
        return result
