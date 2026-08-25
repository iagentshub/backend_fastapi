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

# Las dos columnas grandes de la fila, y las únicas que estas funciones NO
# traen. El avatar se guarda en base64 y `POST /api/auth/me/avatar` admite
# hasta 10 MB de fichero, unos 13,3 MB por fila; `cv` llega a 20.000
# caracteres. Estas funciones están en el camino crítico de toda petición
# autenticada (`_resolve_principal`, `_get_user_auth_state`, `get_user_role`),
# así que un `SELECT *` transportaba esos megabytes en cada una para
# descartarlos acto seguido: el avatar solo lo necesita
# `GET /api/users/{u}/avatar`, que lo pide aparte.
#
# `tests/auth/test_user_lookup_columnas.py` compara esta lista con las
# columnas reales de la tabla: una columna nueva rompe ese test en vez de
# desaparecer en silencio de todas las respuestas.
_EXCLUIDAS = ("avatar", "cv")

_USER_COLS = (
    "id, username, email, password_hash, display_name, birth_date, gender, "
    "country, phone, provider, provider_sub, role, is_active, is_verified, "
    "verification_token, reset_token, reset_token_expires, preferences, "
    "deletion_requested_at, deletion_token, stripe_customer_id, bio, "
    "languages, is_email_public, github, password_changed_at, created_at"
)


async def _get_user_by(field: str, value: str) -> Optional[dict]:
    if field not in _ALLOWED_USER_FIELDS:
        raise ValueError(f"Campo no permitido para búsqueda de usuario: {field!r}")
    # `username` e `email` se comparan sin distinguir mayúsculas: los dos llegan
    # tecleados —de la URL de un perfil, del formulario de acceso— y la columna
    # guarda siempre la forma normalizada. Normalizar solo el parámetro dejaba
    # fuera las filas que un `UPDATE` a mano hubiera dejado con mayúsculas.
    # `id` no: es un hexadecimal que genera el servidor y su índice sí se usa.
    comparacion = f"{field} = ?" if field == "id" else f"LOWER({field}) = LOWER(?)"
    async with open_db() as conn:
        row = await conn.fetchone(
            f"SELECT {_USER_COLS} FROM users WHERE {comparacion}", (value,)
        )
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
            f"SELECT {_USER_COLS} FROM users "
            f"WHERE id = ? OR LOWER(username) = LOWER(?)",
            (identity, normalize_username(identity)),
        )
        return dict(row) if row else None


async def get_user_by_login(identifier: str) -> Optional[dict]:
    """Resolve a login identifier without exposing which field matched."""
    normalized = identifier.strip().lower()
    async with open_db() as conn:
        row = await conn.fetchone(
            f"SELECT {_USER_COLS} FROM users WHERE username = ? OR email = ?",
            (normalized, normalized),
        )
        return dict(row) if row else None
