"""Resolución de usuarios locales ligados a una identidad de GitHub OAuth.

Recibe la identidad que ya trajo ``app.auth.github_device_flow`` y la traduce a
una fila de `users`. No habla con GitHub en ningún momento.
"""

from __future__ import annotations

from app.auth.auth import get_user_by_id
from app.storage.db import open_db
from app.utils import flog
from app.utils.generators import generate_date, generate_id
from app.utils.validation import is_valid_username, normalize_username


async def get_or_create_github_user(
    github_id: str, login: str, email: str, name: str
) -> dict:
    """Resuelve el usuario local ligado a una identidad de GitHub (columnas
    `provider`/`provider_sub`), creándolo si es la primera vez que inicia
    sesión así. Sin contraseña local (`password_hash=NULL`) — el login por
    usuario/contraseña ya trata eso como "usuario no encontrado"."""
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT * FROM users WHERE provider = ? AND provider_sub = ?",
            ("github", github_id),
        )
        if row:
            return dict(row)

        base_username = normalize_username(login)
        if not is_valid_username(base_username):
            base_username = normalize_username(f"gh-{github_id}")
        username = base_username
        suffix = 1
        while await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (username,)):
            suffix += 1
            username = f"{base_username}{suffix}"[:32]

        account_email = (email or "").strip().lower()
        if not account_email or await conn.fetchone(
            "SELECT 1 FROM users WHERE email = ?", (account_email,)
        ):
            account_email = f"{username}@users.noreply.github.com"

        now = generate_date()
        user_id = generate_id(32)
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO users "
                "(id, username, email, password_hash, display_name, provider, "
                "provider_sub, role, is_active, is_verified, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    user_id,
                    username,
                    account_email,
                    None,
                    name or username,
                    "github",
                    github_id,
                    "standard",
                    1,
                    1,
                    now,
                ),
            )
    flog.ok(f"Nuevo usuario vía GitHub: {username}")
    return await get_user_by_id(user_id)  # type: ignore[return-value]
