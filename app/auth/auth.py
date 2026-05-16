"""Auth: JWT, password hashing y gestión de usuarios (DB-backed)."""
from __future__ import annotations

import json
from pathlib import Path

from app.utils import flog
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import secrets

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.config.data import DB_FILE, SETTINGS_FILE
from app.config.session import (
    EMAIL_VERIFY_ENABLED,
    JWT_ALGORITHM,
    JWT_EXPIRE_HOURS,
    JWT_SECRET_ENV,
    JWT_UNSAFE_SECRETS,
)
from app.storage.db import IS_PG, PH, close_db, open_db

# ── Settings ───────────────────────────────────────────────────────────────────


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _secret() -> str:
    env_val = os.environ.get(JWT_SECRET_ENV)
    secret = env_val or _load_settings().get("jwt_secret", "")
    if secret in JWT_UNSAFE_SECRETS:
        raise RuntimeError(
            f"JWT secret no configurado. "
            f"Define la variable de entorno {JWT_SECRET_ENV} o establece "
            f"'jwt_secret' en data/settings.json antes de arrancar."
        )
    return secret


# ── Password helpers ───────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Internal DB helpers ────────────────────────────────────────────────────────


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    # sqlite3.Row
    return dict(row)


def _get_user_by(field: str, value: str) -> Optional[dict]:
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM users WHERE {field} = {PH}", (value,))
        row = cur.fetchone()
        return _row_to_dict(row)
    finally:
        close_db(conn)


# ── Public user API ────────────────────────────────────────────────────────────


def get_user_by_email(email: str) -> Optional[dict]:
    return _get_user_by("email", email)


def get_user_by_username(username: str) -> Optional[dict]:
    return _get_user_by("username", username)


def _gen_username(email: str, _conn=None) -> str:
    """Username is the full email address."""
    return email


def register_user(username: str, password: str, email: str = "") -> None:
    """Create a new local user. Raises ValueError if username or email already taken."""
    if not email:
        email = f"{username}@local"
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM users WHERE username = {PH}", (username,))
        if cur.fetchone():
            raise ValueError("El nombre de usuario ya está en uso")
        cur.execute(f"SELECT 1 FROM users WHERE email = {PH}", (email,))
        if cur.fetchone():
            raise ValueError("El correo electrónico ya está registrado")
        now = datetime.now(timezone.utc).isoformat()
        if IS_PG:
            cur.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                (username, email, hash_password(password), "standard", 1, now),
            )
        else:
            conn.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                (username, email, hash_password(password), "standard", 1, now),
            )
        conn.commit()
        flog.ok(f"Nuevo usuario: {email}")
    finally:
        close_db(conn)


def register_user_email(
    email: str,
    password: str,
    *,
    birth_date: Optional[str] = None,
    gender: Optional[str] = None,
    country: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Register user by email. Username = full email. Returns (username, verification_token).

    verification_token is None when EMAIL_VERIFY_ENABLED is False (user auto-verified).
    """
    username = email  # username IS the full email address
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM users WHERE email = {PH}", (email,))
        if cur.fetchone():
            raise ValueError("El correo electrónico ya está registrado")
        now = datetime.now(timezone.utc).isoformat()
        token: Optional[str] = None
        is_verified = 1
        if EMAIL_VERIFY_ENABLED:
            token = secrets.token_urlsafe(32)
            is_verified = 0
        cur.execute(
            "INSERT INTO users "
            "(username, email, password_hash, display_name, birth_date, gender, "
            f"country, phone, role, is_active, is_verified, verification_token, created_at) "
            f"VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})",
            (
                username, email, hash_password(password), display_name,
                birth_date, gender, country, phone,
                "standard", 1, is_verified, token, now,
            ),
        )
        conn.commit()
        flog.ok(f"Nuevo usuario: {email}")
        return username, token
    finally:
        close_db(conn)


def verify_email_token(token: str) -> Optional[str]:
    """Mark user as verified by their token. Returns the username on success, None if invalid."""
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT username FROM users WHERE verification_token = {PH} AND is_verified = 0",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        username = row["username"] if isinstance(row, dict) else row[0]
        cur.execute(
            f"UPDATE users SET is_verified = 1, verification_token = NULL WHERE username = {PH}",
            (username,),
        )
        conn.commit()
        return username
    finally:
        close_db(conn)


def send_verification_email(email: str, token: str, base_url: str = "") -> None:
    """Send a verification email. Currently a no-op — implement when SMTP is configured."""
    # TODO: configure SMTP via GAIA_SMTP_* env vars and implement here
    pass




def get_user_role(username: str) -> str:
    if username == "guest" or username.startswith("guest_"):
        return "guest"
    user = _get_user_by("username", username)
    return user.get("role", "standard") if user else "standard"


def list_users() -> list:
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users ORDER BY created_at ASC")
        rows = cur.fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            if d:
                d.pop("password_hash", None)
                d.pop("provider_sub", None)
                result.append(d)
        return result
    finally:
        close_db(conn)


def delete_user(username: str) -> bool:
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM users WHERE username = {PH}", (username,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        close_db(conn)


def update_user_profile(username: str, **fields) -> None:
    """Update allowed profile fields for a user."""
    allowed = {"birth_date", "gender", "country", "phone", "display_name"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = open_db(DB_FILE)
    try:
        set_clause = ", ".join(f"{col} = {PH}" for col in updates)
        values = list(updates.values()) + [username]
        conn.cursor().execute(
            f"UPDATE users SET {set_clause} WHERE username = {PH}", values
        )
        conn.commit()
    finally:
        close_db(conn)


def admin_update_user(username: str, **fields) -> bool:
    """Admin-only: update is_active or role. Returns False if user not found."""
    allowed = {"is_active", "role"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return True
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM users WHERE username = {PH}", (username,))
        if not cur.fetchone():
            return False
        set_clause = ", ".join(f"{col} = {PH}" for col in updates)
        values = list(updates.values()) + [username]
        cur.execute(f"UPDATE users SET {set_clause} WHERE username = {PH}", values)
        conn.commit()
        return True
    finally:
        close_db(conn)


# ── JWT ────────────────────────────────────────────────────────────────────────


def create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    """Return the username or None if the token is invalid/expired."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        return data.get("sub")
    except JWTError:
        return None


# ── First-boot admin bootstrap ────────────────────────────────────────────────


def ensure_admin_user() -> None:
    """Garantiza que existe al menos un admin con el email de GAIA_ADMIN_EMAIL.

    Lógica:
    1. Si GAIA_ADMIN_EMAIL ya tiene cuenta → promoverla a admin si no lo es.
       Con GAIA_ADMIN_RESET=true también resetea su contraseña.
    2. Si GAIA_ADMIN_EMAIL no tiene cuenta → crearla como admin.
    3. Si no se puede hacer nada con GAIA_ADMIN_EMAIL y ya hay otro admin
       sin reset_mode → no tocar nada.
    """
    reset_mode = os.environ.get("GAIA_ADMIN_RESET", "").lower() in ("1", "true", "yes")
    target_email = os.environ.get("GAIA_ADMIN_EMAIL", "admin@localhost")

    # If .admin_pass doesn't exist yet, force a one-time reset so gaia.sh can always display it
    data_dir = os.environ.get("GAIA_DATA_DIR", "").strip()
    _pass_file = Path(data_dir) / ".admin_pass" if data_dir else None
    if not reset_mode and _pass_file and not _pass_file.exists():
        reset_mode = True

    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()

        cur.execute(f"SELECT username, role FROM users WHERE email = {PH}", (target_email,))
        row = cur.fetchone()
        target = dict(row) if row else None

        if target:
            if target["role"] != "admin":
                cur.execute(f"UPDATE users SET role = {PH} WHERE email = {PH}", ("admin", target_email))
                conn.commit()
                if not reset_mode:
                    sep = "=" * 60
                    flog.warning(sep)
                    flog.warning(f" iAgents Hub — {target_email} promovido a administrador")
                    flog.warning(sep)
                    return

            if not reset_mode:
                return

            # reset_mode: cambiar contraseña de la cuenta con target_email
            password = secrets.token_urlsafe(12)
            cur.execute(
                f"UPDATE users SET password_hash = {PH} WHERE email = {PH}",
                (hash_password(password), target_email),
            )
            conn.commit()
            action = "contraseña reseteada"
        else:
            # No hay cuenta con ese email
            cur.execute(f"SELECT username FROM users WHERE role = {PH} LIMIT 1", ("admin",))
            existing = cur.fetchone()
            if existing and not reset_mode:
                return

            password = secrets.token_urlsafe(12)
            now = datetime.now(timezone.utc).isoformat()
            cur.execute(
                "INSERT INTO users "
                "(username, email, password_hash, role, is_active, is_verified, created_at) "
                f"VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH})",
                (target_email, target_email, hash_password(password), "admin", 1, 1, now),
            )
            conn.commit()
            action = "cuenta creada"

    finally:
        close_db(conn)

    # Persist plaintext password so gaia.sh can always display it
    data_dir = os.environ.get("GAIA_DATA_DIR", "").strip()
    if data_dir:
        try:
            p = Path(data_dir) / ".admin_pass"
            p.write_text(password, encoding="utf-8")
            p.chmod(0o600)
        except OSError:
            pass

    sep = "=" * 60
    flog.warning(sep)
    flog.warning(f" iAgents Hub — Administrador ({action})")
    flog.warning(f" Email:      {target_email}")
    flog.warning(f" Contraseña: {password}")
    flog.warning(" Accede a /login/ y cambia la contraseña desde /profile/")
    flog.warning(sep)


# ── Compatibility shim ─────────────────────────────────────────────────────────


def _load_users() -> list:  # noqa: F811
    raise RuntimeError("Migrado a DB — usa list_users() en su lugar")


def _save_users(_users: list) -> None:  # noqa: F811
    raise RuntimeError("Migrado a DB — usa las funciones DB en su lugar")
