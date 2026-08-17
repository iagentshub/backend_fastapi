"""Storage for linked provider accounts — DB-backed con owner_id.

Varias cuentas pueden compartir el mismo `provider` (ej. dos API keys de
OpenAI distintas): la clave de unicidad es `(id, owner_id)`, `provider` es
solo un campo más, no forma parte de la clave.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.sql import sql
from app.storage.crypto import (
    UNREADABLE_FIELDS,
    UNREADABLE_FLAG,
    decrypt_fields,
    encrypt,
)
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
            count = await conn.fetchval(sql("queries/accounts:count_all"))
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
                except Exception as exc:  # noqa: BLE001
                    # Migración fichero a fichero: uno corrupto no para el resto.
                    flog.warning(f"[accounts] Migración de {p.name} fallida: {exc}")

    async def _upsert_with_conn(
        self, conn: Any, owner_id: str, data: Dict[str, Any]
    ) -> None:
        if IS_PG:
            await conn.execute(
                sql("queries/accounts:upsert_pg"),
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
                sql("queries/accounts:upsert_sqlite"),
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
                sql("queries/accounts:data_of"),
                (owner_id, account_id),
            )
            if not row:
                return None
            d = json.loads(row["data"])
            decrypt_fields(d, ("api_key",))
            return d

    async def _stored_api_key(self, account_id: str, owner_id: str) -> str:
        """api_key tal como está en la BD, sin descifrar."""
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/accounts:data_of"),
                (owner_id, account_id),
            )
        if not row:
            return ""
        return str(json.loads(row["data"]).get("api_key") or "")

    async def save(self, data: Dict[str, Any], owner_id: str = "admin") -> Dict[str, Any]:
        """Crea una cuenta nueva o actualiza una existente.

        Con `data["id"]` presente y ya existente, actualiza esa cuenta
        (preservando `api_key`/`provider` si no se mandan de nuevo). Sin
        `id` (o con un `id` que no existe todavía), crea una cuenta nueva —
        así se permiten varias cuentas del mismo `provider` para el mismo
        owner, cada una con su propio id.
        """
        account_id = str(data.get("id") or "").strip()
        # Marcas de lectura: las pone el propio storage al descifrar.
        data.pop(UNREADABLE_FIELDS, None)
        data.pop(UNREADABLE_FLAG, None)
        existing = await self.get(account_id, owner_id) if account_id else None
        if existing is None:
            account_id = generate_id()
        data["id"] = account_id
        data.setdefault("provider", (existing or {}).get("provider"))
        data.setdefault("linked_at", (existing or {}).get("linked_at") or _now())
        if not data.get("api_key") and (existing or {}).get("api_key"):
            data["api_key"] = existing["api_key"]
        # Clave ilegible que esta edición no reemplaza: se conserva cifrada tal
        # cual, porque vuelve a ser válida en cuanto se restaura el secreto.
        keep_encrypted = not data.get("api_key") and bool(
            (existing or {}).get(UNREADABLE_FLAG)
        )
        stored = dict(data)
        if stored.get("api_key"):
            stored["api_key"] = encrypt(stored["api_key"])
        elif keep_encrypted:
            stored["api_key"] = await self._stored_api_key(account_id, owner_id)
            data[UNREADABLE_FIELDS] = ["api_key"]
            data[UNREADABLE_FLAG] = True
        async with open_db() as conn:
            await self._upsert_with_conn(conn, owner_id, stored)
        return data

    async def delete(self, account_id: str, owner_id: str = "admin") -> bool:
        async with open_db() as conn:
            exists = await conn.fetchone(
                sql("queries/accounts:exists"),
                (owner_id, account_id),
            )
            if not exists:
                return False
            await conn.execute(
                sql("queries/accounts:delete"),
                (owner_id, account_id),
            )
            await conn.commit()
            return True

    async def list(self, owner_id: str = "admin") -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/accounts:list_data_by_owner"),
                (owner_id,),
            )
        result = []
        for row in rows:
            d: Dict[str, Any] = json.loads(row["data"])
            if d.get("api_key"):
                decrypt_fields(d, ("api_key",))
                # Ilegible → api_key vacía: se marca en vez de enmascarar un
                # valor que no existe, y el cliente lo pinta como «requiere
                # atención» antes de que el usuario intente usarla.
                d["api_key_masked"] = _mask(d["api_key"]) if d["api_key"] else ""
                del d["api_key"]
            result.append(d)
        return result
