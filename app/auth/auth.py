"""Auth: gestión de usuarios (DB-backed) y bootstrap del admin inicial.

El hashing de contraseñas y el JWT viven en ``app.auth.passwords``; la
identidad OAuth de GitHub en ``app.auth.github_user_link``; el borrado RGPD en
``app.auth.gdpr``; el vínculo con Stripe en ``app.services.billing_link``.
Este módulo re-exporta lo de ``passwords`` para no romper a los ~70 callers
existentes que hacían ``from app.auth.auth import hash_password, ...`` — los
demás (GDPR, GitHub, billing) tienen pocos callers y se apuntan directamente
al módulo nuevo para evitar un import circular (esos módulos importan de
aquí).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.auth.passwords import (  # noqa: F401 - re-exportadas para compat
    TokenClaims,
    _hash_token,
    _secret,
    create_token,
    decode_claims,
    decode_token,
    hash_password,
    hash_password_async,
    verify_password,
    verify_password_async,
)
from app.auth.user_lookup import (  # noqa: F401 - re-exportadas para compat
    _ALLOWED_USER_FIELDS,
    _get_user_by,
    get_user_by_email,
    get_user_by_id,
    get_user_by_identity,
    get_user_by_login,
    get_user_by_username,
)
from app.config.data import DATA_DIR
from app.config.session import EMAIL_VERIFY_ENABLED
from app.storage.db import IS_PG, open_db
from app.storage.guest import is_guest
from app.utils import flog
from app.utils.generators import generate_date, generate_id
from app.utils.validation import is_valid_username, normalize_username


async def register_user(username: str, password: str, email: str = "") -> None:
    """Create a new local user. Raises ValueError if username or email already taken."""
    username = normalize_username(username)
    email = email.strip().lower()
    if not is_valid_username(username):
        raise ValueError("El usuario debe tener entre 5 y 32 caracteres: a-z, 0-9, punto, guion o guion bajo")
    if not email:
        email = f"{username}@localhost.com"
    password_hash = await hash_password_async(password)
    async with open_db() as conn:
        async with conn.transaction():
            if await conn.fetchone(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ):
                raise ValueError("El nombre de usuario ya está en uso")
            if await conn.fetchone("SELECT 1 FROM users WHERE email = ?", (email,)):
                raise ValueError("El correo electrónico ya está registrado")
            now = generate_date()
            await conn.execute(
                "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (generate_id(32), username, email, password_hash, "standard", 1, now),
            )
    flog.ok(f"Nuevo usuario: {username}", username=username)


async def register_user_email(
    username: str,
    email: str,
    password: str,
    *,
    birth_date: Optional[str] = None,
    gender: Optional[str] = None,
    country: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Register a user with separate public username and private account email.

    verification_token is None when EMAIL_VERIFY_ENABLED is False (user auto-verified).
    """
    username = normalize_username(username)
    email = email.strip().lower()
    if not is_valid_username(username):
        raise ValueError("El usuario debe tener entre 5 y 32 caracteres: a-z, 0-9, punto, guion o guion bajo")
    token: Optional[str] = None
    password_hash = await hash_password_async(password)
    async with open_db() as conn:
        async with conn.transaction():
            if await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (username,)):
                raise ValueError("El nombre de usuario ya está en uso")
            if await conn.fetchone("SELECT 1 FROM users WHERE email = ?", (email,)):
                raise ValueError("El correo electrónico ya está registrado")
            now = generate_date()
            is_verified = 1
            token_hash: Optional[str] = None
            if EMAIL_VERIFY_ENABLED:
                token = secrets.token_urlsafe(32)
                token_hash = _hash_token(token)
                is_verified = 0
            await conn.execute(
                "INSERT INTO users "
                "(id, username, email, password_hash, display_name, birth_date, gender, "
                "country, phone, role, is_active, is_verified, verification_token, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    generate_id(32),
                    username,
                    email,
                    password_hash,
                    display_name,
                    birth_date,
                    gender,
                    country,
                    phone,
                    "standard",
                    1,
                    is_verified,
                    token_hash,
                    now,
                ),
            )
    flog.ok(f"Nuevo usuario: {username}", username=username)
    return username, token


async def verify_email_token(token: str) -> Optional[str]:
    """Mark user as verified by their token. Returns the username on success, None if invalid."""
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT username FROM users WHERE verification_token = ? AND is_verified = 0",
            (_hash_token(token),),
        )
        if not row:
            return None
        username = row[0]
        await conn.execute(
            "UPDATE users SET is_verified = 1, verification_token = NULL WHERE username = ?",
            (username,),
        )
        await conn.commit()
        return username


async def create_password_reset_token(email: str) -> Optional[str]:
    """Generate a reset token for the given email. Returns None if email not found."""
    from app.config.session import PASSWORD_RESET_EXPIRE_HOURS

    async with open_db() as conn:
        if not await conn.fetchone(
            "SELECT username FROM users WHERE email = ? AND is_active = 1", (email,)
        ):
            return None
        token = secrets.token_urlsafe(32)
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=PASSWORD_RESET_EXPIRE_HOURS)
        ).isoformat()
        await conn.execute(
            "UPDATE users SET reset_token = ?, reset_token_expires = ? WHERE email = ?",
            (_hash_token(token), expires, email),
        )
        await conn.commit()
        return token


async def consume_reset_token(token: str, new_password: str) -> bool:
    """Verify token, apply new password, and invalidate token. Returns False if invalid/expired."""
    token_hash = _hash_token(token)
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT reset_token_expires FROM users WHERE reset_token = ?",
            (token_hash,),
        )
        if not row:
            return False
        expires = row[0] or ""
        if not expires or datetime.fromisoformat(expires) < datetime.now(timezone.utc):
            return False

    password_hash = await hash_password_async(new_password)
    now = generate_date()
    async with open_db() as conn, conn.transaction():
        updated = await conn.fetchone(
            "UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expires = NULL "
            "WHERE reset_token = ? AND reset_token_expires > ? "
            "RETURNING username, email",
            (password_hash, token_hash, now),
        )
        if not updated:
            return False
        await _touch_password_changed_at(conn, updated[0])

    flog.ok(f"[auth] Contraseña reseteada para {updated[1]}")
    return True


async def _touch_password_changed_at(conn: Any, username: str) -> None:
    """Marca el instante de cambio de contraseña para invalidar tokens anteriores (A2)."""
    now = generate_date()
    await conn.execute(
        "UPDATE users SET password_changed_at = ? WHERE id = ? OR username = ?",
        (now, username, normalize_username(username)),
    )
    await _clear_temp_admin_pass(conn, username)


async def _clear_temp_admin_pass(conn: Any, username: str) -> None:
    """Vacía .admin_pass en cuanto el admin inicial elige su propia contraseña.

    La contraseña temporal se escribe en claro para que el instalador pueda
    mostrarla; a partir del primer cambio ya no sirve para nada y no debe
    seguir en disco.

    Se VACÍA en vez de borrarse a propósito: los instaladores esperan a que el
    fichero exista como señal de arranque, y ensure_admin_user() lee su
    contenido —vacío significa "ya consumida", que es justo lo que queremos.

    Cuelga de _touch_password_changed_at porque es el único punto por el que
    pasan los tres caminos que cambian una contraseña (perfil, token de
    recuperación y reseteo por admin).
    """
    import contextlib
    import os

    target = os.environ.get("GAIA_ADMIN_EMAIL", "admin@localhost.com").strip().lower()
    row = await conn.fetchone(
        "SELECT 1 FROM users WHERE (id = ? OR username = ?) AND lower(email) = ?",
        (username, normalize_username(username), target),
    )
    if not row:
        return
    with contextlib.suppress(OSError):
        (DATA_DIR / ".admin_pass").write_text("", encoding="utf-8")


async def set_own_password(username: str, new_password: str) -> None:
    """Actualiza el hash de contraseña de un usuario existente."""
    password_hash = await hash_password_async(new_password)
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ? OR username = ?",
            (password_hash, username, normalize_username(username)),
        )
        await _touch_password_changed_at(conn, username)
        await conn.commit()


async def get_user_role(username: str) -> str:
    """Rol del principal: "guest", "standard", "admin" (o el que tenga en BD).

    El invitado se reconoce con ``is_guest`` de ``storage.guest`` — la misma y
    única definición que usa el resto del backend. Antes esta función tenía su
    propia comprobación (``"guest"`` / prefijo ``guest_``) que no encajaba con
    los ids que emite ``new_guest_id()`` (prefijo ``guest:``), así que todo
    invitado caía al default y se clasificaba como ``standard``.
    """
    if is_guest(username):
        return "guest"
    user = await get_user_by_identity(username)
    return user.get("role", "standard") if user else "standard"


async def list_users() -> list:
    async with open_db() as conn:
        rows = await conn.fetchall("SELECT * FROM users ORDER BY created_at ASC")
        result = []
        for row in rows:
            d = dict(row)
            d.pop("password_hash", None)
            d.pop("provider_sub", None)
            result.append(d)
        return result


async def delete_user(username: str) -> bool:
    if not await get_user_by_username(username):
        return False
    # Import diferido: app.auth.gdpr importa get_user_by_identity de este
    # módulo, así que un import a nivel de módulo aquí crearía un ciclo.
    from app.auth.gdpr import purge_user_data

    await purge_user_data(username)
    return True


_ADMIN_SQL: dict = {
    "is_active": "is_active = ?",
    "role": "role = ?",
}


async def admin_update_user(username: str, **fields) -> bool:
    """Admin-only: update is_active or role. Returns False if user not found."""
    clauses = [(sql, fields[col]) for col, sql in _ADMIN_SQL.items() if col in fields]
    if not clauses:
        return True
    async with open_db() as conn:
        if not await conn.fetchone(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ):
            return False
        await conn.execute(
            "UPDATE users SET "
            + ", ".join(c[0] for c in clauses)
            + " WHERE username = ?",
            [c[1] for c in clauses] + [username],
        )
        await conn.commit()
        return True


async def admin_set_password(username: str, new_password: str) -> bool:
    """Admin-only: set a new password for another user. Returns False if not found."""
    password_hash = await hash_password_async(new_password)
    async with open_db() as conn:
        if not await conn.fetchone(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ):
            return False
        await conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (password_hash, username),
        )
        await _touch_password_changed_at(conn, username)  # A2
        await conn.commit()
        return True


# ── First-boot admin bootstrap ────────────────────────────────────────────────


def _unique_violation_errors() -> tuple[type[Exception], ...]:
    """Excepción de violación de restricción UNIQUE según el backend activo."""
    if IS_PG:
        import asyncpg  # type: ignore[import]

        return (asyncpg.UniqueViolationError,)
    import sqlite3

    return (sqlite3.IntegrityError,)


async def ensure_admin_user() -> None:
    """Garantiza que existe al menos un admin con el email de GAIA_ADMIN_EMAIL.

    Lógica:
    1. Si GAIA_ADMIN_EMAIL ya tiene cuenta → promoverla a admin si no lo es.
       Con GAIA_ADMIN_RESET=true también resetea su contraseña.
    2. Si .admin_pass existe pero no coincide con el hash de la DB → resetear
       automáticamente para que la contraseña mostrada por gaia.py sea siempre válida.
    3. Si GAIA_ADMIN_EMAIL no tiene cuenta → crearla como admin.
    4. Si no se puede hacer nada con GAIA_ADMIN_EMAIL y ya hay otro admin
       sin reset_mode → no tocar nada.
    """
    import os

    reset_mode = os.environ.get("GAIA_ADMIN_RESET", "").lower() in ("1", "true", "yes")
    target_email = os.environ.get("GAIA_ADMIN_EMAIL", "admin@localhost.com").strip().lower()
    target_username = normalize_username(os.environ.get("GAIA_ADMIN_USERNAME", "admin"))
    if not is_valid_username(target_username):
        raise RuntimeError("GAIA_ADMIN_USERNAME no es un nombre de usuario válido")

    # If .admin_pass doesn't exist yet, force a one-time reset so gaia.py can always display it
    # DATA_DIR, no GAIA_DATA_DIR: sin la env var la contraseña se generaba y se
    # perdía, dejando la cuenta admin inaccesible sin avisar.
    #
    # OJO: "fichero ausente" significa instalación nueva, no "contraseña ya
    # usada". Cuando el admin cambia su contraseña el fichero se VACÍA, no se
    # borra (ver _clear_temp_admin_pass); borrarlo a mano hace que el siguiente
    # arranque regenere la contraseña y tire la que eligió el usuario.
    import bcrypt as _bcrypt

    _pass_file = DATA_DIR / ".admin_pass"
    if not reset_mode and not _pass_file.exists():
        reset_mode = True

    # Verify .admin_pass against the DB hash — reset if they don't match.
    # This handles cases where the DB password was changed externally without
    # updating .admin_pass, which would cause gaia.py to display a stale password.
    if not reset_mode and _pass_file.exists():
        try:
            stored_pass = _pass_file.read_text(encoding="utf-8").strip()
            if stored_pass:
                async with open_db() as _chk:
                    _row = await _chk.fetchone(
                        "SELECT password_hash FROM users WHERE email = ?",
                        (target_email,),
                    )
                if _row and _row["password_hash"]:
                    if not _bcrypt.checkpw(
                        stored_pass.encode(), _row["password_hash"].encode()
                    ):
                        reset_mode = True  # hash mismatch → regenerate
        except Exception as exc:  # noqa: BLE001
            # No se escala: si la comprobación falla se deja reset_mode como
            # está y el arranque continúa con la contraseña que ya hubiera.
            # Pero se registra — un bcrypt que revienta aquí es la diferencia
            # entre "la contraseña del fichero sirve" y "gaia.py enseña una
            # contraseña obsoleta", y así no había forma de saber cuál era.
            flog.error(f"[admin] No se pudo verificar .admin_pass: {exc}")

    password: Optional[str] = None
    action: Optional[str] = None

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT username, role FROM users WHERE email = ?", (target_email,)
        )
        target = dict(row) if row else None

        if target:
            if target["role"] != "admin":
                await conn.execute(
                    "UPDATE users SET role = ? WHERE email = ?", ("admin", target_email)
                )
                await conn.commit()
                if not reset_mode:
                    sep = "=" * 60
                    flog.warning(sep)
                    flog.warning(
                        f" iAgents Hub — {target_email} promovido a administrador"
                    )
                    flog.warning(sep)
                    return

            if not reset_mode:
                return

            # reset_mode: cambiar contraseña de la cuenta con target_email
            password = secrets.token_urlsafe(12)
            password_hash = await hash_password_async(password)
            await conn.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                (password_hash, target_email),
            )
            await conn.commit()
            action = "contraseña reseteada"
        else:
            # No hay cuenta con ese email
            existing = await conn.fetchone(
                "SELECT username FROM users WHERE role = ? LIMIT 1", ("admin",)
            )
            if existing and not reset_mode:
                return

            password = secrets.token_urlsafe(12)
            password_hash = await hash_password_async(password)
            now = generate_date()
            try:
                await conn.execute(
                    "INSERT INTO users "
                    "(id, username, email, password_hash, role, is_active, is_verified, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        generate_id(32),
                        target_username,
                        target_email,
                        password_hash,
                        "admin",
                        1,
                        1,
                        now,
                    ),
                )
                await conn.commit()
                action = "cuenta creada"
            except _unique_violation_errors():
                # Con GAIA_WORKERS > 1, varios procesos arrancan a la vez contra
                # una DB recién creada: el SELECT de "existing" de arriba puede
                # pasar en todos antes de que ninguno haga commit del INSERT.
                # Otro worker ya ganó la carrera y creó el admin — no hay nada
                # que hacer aquí (password/action se descartan para no
                # sobrescribir .admin_pass con una contraseña que no coincide
                # con la que realmente quedó en la DB).
                return

    if password is None or action is None:
        return

    # Persist plaintext password so gaia.py can always display it
    saved = False
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _pass_file.write_text(password, encoding="utf-8")
        _pass_file.chmod(0o600)
        saved = True
    except OSError as exc:
        flog.error(f"[admin] No se pudo persistir .admin_pass: {exc}")

    sep = "=" * 60
    flog.warning(sep)
    flog.warning(f" iAgents Hub — Administrador ({action})")
    flog.warning(f" Email:      {target_email}")
    flog.warning(
        f" Contraseña: [ver {_pass_file} — se borra sola al cambiarla; no la borres a mano]"
        if saved
        else f" Contraseña: {password}  [no se pudo escribir {_pass_file}: apúntala AHORA]"
    )
    flog.warning(" Accede a /login/ y cambia la contraseña desde /profile/")
    flog.warning(sep)


# ── Compatibility shim ─────────────────────────────────────────────────────────


def _load_users() -> list:  # noqa: F811
    raise RuntimeError("Migrado a DB — usa list_users() en su lugar")


def _save_users(_users: list) -> None:  # noqa: F811
    raise RuntimeError("Migrado a DB — usa las funciones DB en su lugar")
