"""Storage de prompts reutilizables, invocables desde el chat vía "@alias"."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# db se importa DOS veces a propósito: ver app/storage/_storage_helpers.py.
from app.storage import db as _db
from app.storage._storage_helpers import _PUBLIC_OWNER
from app.storage.db import AsyncConn, open_db
from app.storage.db_migrations import _compact_resource_data
from app.storage.resource_base import ResourceStorage

# Catálogo de labels compartido; vive en skill_storage (ver comentario allí).
from app.storage.skill_storage import SKILL_LABELS, ensure_origin_label
from app.utils import now_iso as _now
from app.utils.generators import generate_id

PROMPT_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$")


class PromptStorage(ResourceStorage):
    """Async DB-backed prompt storage (SQLite / PostgreSQL)."""

    table = "prompts"
    resource_type = "prompt"

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _upsert(
        self, conn: Any, prompt_id: str, owner_id: str, scope: str, data: Dict[str, Any]
    ) -> None:

        name = str(data.get("name") or "").strip()
        content = str(data.get("content") or "")
        alias = str(data.get("alias") or "").strip().lower()
        now = _now()
        created_at = str(data.get("created_at") or now)
        updated_at = str(data.get("updated_at") or now)
        is_active = 1 if data.get("is_active", True) else 0
        deactivated_at = data.get("deactivated_at")
        # alias y content tienen columna propia — no duplicar en el JSON de meta.
        meta = {k: v for k, v in data.items() if k not in ("content", "alias")}
        meta_json = _compact_resource_data(meta)
        if _db.IS_PG:
            await conn.execute(
                "INSERT INTO prompts (id, owner_id, name, alias, scope, data, content, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET name=EXCLUDED.name, alias=EXCLUDED.alias, scope=EXCLUDED.scope, data=EXCLUDED.data, "
                "content=EXCLUDED.content, is_active=EXCLUDED.is_active, "
                "deactivated_at=EXCLUDED.deactivated_at, updated_at=EXCLUDED.updated_at",
                (
                    prompt_id,
                    owner_id,
                    name,
                    alias,
                    scope,
                    meta_json,
                    content,
                    is_active,
                    deactivated_at,
                    created_at,
                    updated_at,
                ),
            )
        else:
            # Ver skill_storage._upsert: upsert explícito para no perder las
            # columnas que este INSERT no nombra (las de fuente oficial).
            await conn.execute(
                "INSERT INTO prompts "
                "(id, owner_id, name, alias, scope, data, content, "
                "is_active, deactivated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id, owner_id) DO UPDATE SET name=excluded.name, "
                "alias=excluded.alias, scope=excluded.scope, data=excluded.data, "
                "content=excluded.content, is_active=excluded.is_active, "
                "deactivated_at=excluded.deactivated_at, updated_at=excluded.updated_at",
                (
                    prompt_id,
                    owner_id,
                    name,
                    alias,
                    scope,
                    meta_json,
                    content,
                    is_active,
                    deactivated_at,
                    created_at,
                    updated_at,
                ),
            )

    def _row_to_dict(self, row: Any, include_content: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = json.loads(row["data"])
        d["alias"] = row["alias"]
        if include_content:
            d["content"] = row["content"]
        d.update(
            {
                "id": row["id"],
                "name": row["name"],
                "resource_type": "prompt",
                "scope": row["scope"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        d["is_active"] = bool(row["is_active"])
        d["deactivated_at"] = row["deactivated_at"]
        owner = row["owner_id"]
        d["owner_id"] = None if owner == _PUBLIC_OWNER else owner
        return d

    # ── public API ───────────────────────────────────────────────────────────

    async def list(
        self, scope: str = "all", owner_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            if scope == "public":
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at "
                    "FROM prompts WHERE scope='public' ORDER BY created_at ASC"
                )
            elif scope == "private":
                if owner_id:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                        "deactivated_at, created_at, updated_at "
                        "FROM prompts WHERE scope='private' AND owner_id=? ORDER BY created_at ASC",
                        (owner_id,),
                    )
                else:
                    rows = await conn.fetchall(
                        "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                        "deactivated_at, created_at, updated_at "
                        "FROM prompts WHERE scope='private' ORDER BY created_at ASC"
                    )
            else:  # all
                rows = await conn.fetchall(
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at "
                    "FROM prompts ORDER BY created_at ASC"
                )
        return [self._row_to_dict(r, include_content=False) for r in rows]

    async def get(
        self, scope: str, prompt_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_migrated()

        async with open_db() as conn:
            owner_filter = " AND owner_id=?" if owner_id is not None else ""
            params: tuple[Any, ...] = (
                (prompt_id, scope, owner_id)
                if owner_id is not None
                else (prompt_id, scope)
            )
            row = await conn.fetchone(
                "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                "deactivated_at, created_at, updated_at "
                f"FROM prompts WHERE id=? AND scope=?{owner_filter} LIMIT 1",
                params,
            )
        return self._row_to_dict(row) if row else None

    async def get_any(
        self, prompt_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch a prompt from any scope — public first, then private."""
        for scope in ("public", "private"):
            result = await self.get(scope, prompt_id, owner_id=owner_id)
            if result:
                return result
        return None

    async def find_by_alias(
        self, alias: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Busca un prompt accesible por su alias exacto: primero uno propio
        del owner, si no un público. Evita traer todo el listado solo para
        resolver una mención "@alias" en el chat."""
        await self._ensure_migrated()
        alias = alias.strip().lower()
        async with open_db() as conn:
            row = None
            if owner_id:
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at FROM prompts "
                    "WHERE alias=? AND owner_id=? AND is_active=1 LIMIT 1",
                    (alias, owner_id),
                )
            if row is None:
                row = await conn.fetchone(
                    "SELECT id, owner_id, name, alias, scope, data, content, is_active, "
                    "deactivated_at, created_at, updated_at FROM prompts "
                    "WHERE alias=? AND scope='public' AND is_active=1 LIMIT 1",
                    (alias,),
                )
        return self._row_to_dict(row) if row else None

    async def save(
        self,
        scope: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
        *,
        conn: Optional[AsyncConn] = None,
    ) -> Dict[str, Any]:
        if scope not in ("private", "public"):
            raise ValueError("scope must be private or public")
        if scope == "public" and not owner_id:
            raise ValueError("Los prompts públicos de sistema son de solo lectura")
        await self._ensure_migrated()

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        alias = str(payload.get("alias") or "").strip().lower()
        if not PROMPT_ALIAS_RE.match(alias):
            raise ValueError(
                "Alias inválido: usa minúsculas, números, guion o guion bajo, 3-30 caracteres"
            )
        prompt_id = payload.get("id") or generate_id()
        actual_owner = owner_id or "admin"
        now = _now()
        existing = await self.get_any(prompt_id, owner_id=actual_owner)
        if "labels" in payload:
            labels = [str(label) for label in (payload.get("labels") or []) if label]
        elif existing:
            labels = [str(label) for label in (existing.get("labels") or []) if label]
            if existing.get("scope") != scope:
                labels = [
                    label for label in labels if label not in ("private", "public")
                ]
                labels.append(scope)
        else:
            labels = [scope]
        invalid_labels = [label for label in labels if label not in SKILL_LABELS]
        if invalid_labels:
            raise ValueError("invalid prompt labels")
        labels = ensure_origin_label(labels)
        data: Dict[str, Any] = {
            "id": prompt_id,
            "name": name,
            "resource_type": "prompt",
            "description": str(payload.get("description") or "").strip(),
            "icon": str(payload.get("icon") or "📝").strip(),
            "alias": alias,
            "content": str(payload.get("content") or "").strip(),
            "labels": labels,
            "scope": scope,
            "owner_id": actual_owner,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
            # Conservar el borrado suave a través de las ediciones.
            "is_active": existing.get("is_active", True) if existing else True,
            "deactivated_at": existing.get("deactivated_at") if existing else None,
        }
        if conn is not None:
            dup = await conn.fetchone(
                "SELECT 1 FROM prompts WHERE owner_id=? AND alias=? AND id != ?",
                (actual_owner, alias, prompt_id),
            )
            if dup:
                raise ValueError("Ya tienes un prompt con ese alias")
            await self._upsert(conn, prompt_id, actual_owner, scope, data)
            await self.sync_labels(
                prompt_id, actual_owner, data.get("labels") or [], conn=conn
            )
        else:
            async with open_db() as own_conn:
                async with own_conn.transaction():
                    dup = await own_conn.fetchone(
                        "SELECT 1 FROM prompts WHERE owner_id=? AND alias=? AND id != ?",
                        (actual_owner, alias, prompt_id),
                    )
                    if dup:
                        raise ValueError("Ya tienes un prompt con ese alias")
                    await self._upsert(own_conn, prompt_id, actual_owner, scope, data)
            await self.sync_labels(prompt_id, actual_owner, data.get("labels") or [])
        return data

    async def delete(
        self,
        scope: str,
        prompt_id: str,
        owner_id: Optional[str] = None,
        allow_public: bool = False,
    ) -> bool:
        if scope == "public" and owner_id is None and not allow_public:
            raise ValueError("Los prompts públicos de sistema son de solo lectura")
        await self._ensure_migrated()

        async with open_db() as conn:
            if owner_id is not None:
                row = await conn.fetchone(
                    "SELECT id FROM prompts WHERE id=? AND scope=? AND owner_id=? LIMIT 1",
                    (prompt_id, scope, owner_id),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM prompts WHERE id=? AND scope=? AND owner_id=?",
                    (prompt_id, scope, owner_id),
                )
            else:
                row = await conn.fetchone(
                    "SELECT id FROM prompts WHERE id=? AND scope=? LIMIT 1",
                    (prompt_id, scope),
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM prompts WHERE id=? AND scope=?", (prompt_id, scope)
                )
            await conn.commit()
        await self.clear_labels(prompt_id)
        return True

    async def unique_alias(
        self, owner_id: str, alias: str, exclude_id: str = ""
    ) -> str:
        """Devuelve ``alias`` si está libre para ``owner_id``, o un sufijo
        incremental (``alias-2``, ``alias-3``…) si ya existe. Usado por los
        flujos de clonado/enlace, donde el alias del prompt de origen puede
        colisionar con uno ya existente del destino — la copia nunca debe
        fallar por esto ni tocar la fila del propietario original."""
        await self._ensure_migrated()

        base = alias
        candidate = base
        n = 2
        async with open_db() as conn:
            while True:
                row = await conn.fetchone(
                    "SELECT 1 FROM prompts WHERE owner_id=? AND alias=? AND id != ?",
                    (owner_id, candidate, exclude_id),
                )
                if not row:
                    return candidate
                suffix = f"-{n}"
                trimmed = base[: 30 - len(suffix)].rstrip("-_") or base[:1]
                candidate = f"{trimmed}{suffix}"
                n += 1
