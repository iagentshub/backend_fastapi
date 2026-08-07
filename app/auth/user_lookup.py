"""Resolución de usuarios por id/username/email — sin dependencias de escritura.

Módulo hoja a propósito: tanto ``app.auth.auth`` (CRUD, admin bootstrap) como
``app.auth.gdpr`` (borrado RGPD) necesitan resolver un usuario, y si uno de
los dos reexportara del otro se cerraría un ciclo de import
(``app.auth.auth <-> app.auth.gdpr``, ver ``tests/test_ciclos_de_import.py``).
"""

from __future__ import annotations

from typing import Optional

from app.storage.db import open_db
from app.utils.validation import normalize_username

_ALLOWED_USER_FIELDS = frozenset({"id", "email", "username"})


async def _get_user_by(field: str, value: str) -> Optional[dict]:
    if field not in _ALLOWED_USER_FIELDS:
        raise ValueError(f"Campo no permitido para búsqueda de usuario: {field!r}")
    async with open_db() as conn:
        row = await conn.fetchone(f"SELECT * FROM users WHERE {field} = ?", (value,))
        return dict(row) if row else None


async def get_user_by_email(email: str) -> Optional[dict]:
    return await _get_user_by("email", email.strip().lower())


async def get_user_by_username(username: str) -> Optional[dict]:
    return await _get_user_by("username", normalize_username(username))


async def get_user_by_id(user_id: str) -> Optional[dict]:
    return await _get_user_by("id", user_id)


async def get_user_by_identity(identity: str) -> Optional[dict]:
    """Resolve an internal user id or a public username."""
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT * FROM users WHERE id = ? OR username = ?",
            (identity, normalize_username(identity)),
        )
        return dict(row) if row else None


async def get_user_by_login(identifier: str) -> Optional[dict]:
    """Resolve a login identifier without exposing which field matched."""
    normalized = identifier.strip().lower()
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (normalized, normalized),
        )
        return dict(row) if row else None
