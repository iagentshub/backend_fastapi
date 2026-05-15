"""Auth: JWT, password hashing y gestión de usuarios (DB-backed)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.config.data import DB_FILE, SETTINGS_FILE
from app.config.session import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET_ENV, JWT_UNSAFE_SECRETS
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


def _gen_username(email: str, conn) -> str:
    """Generate a unique username from an email address."""
    base = re.sub(r"[^a-z0-9]+", "_", email.split("@")[0].lower()).strip("_") or "user"
    candidate = base
    suffix = 2
    while True:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM users WHERE username = {PH}", (candidate,))
        if not cur.fetchone():
            return candidate
        candidate = f"{base}_{suffix}"
        suffix += 1


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
) -> str:
    """Register user by email, auto-generating a unique username. Returns the username."""
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM users WHERE email = {PH}", (email,))
        if cur.fetchone():
            raise ValueError("El correo electrónico ya está registrado")
        username = _gen_username(email, conn)
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            "INSERT INTO users "
            "(username, email, password_hash, display_name, birth_date, gender, "
            f"country, phone, role, is_active, created_at) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
            (
                username,
                email,
                hash_password(password),
                display_name,
                birth_date,
                gender,
                country,
                phone,
                "standard",
                1,
                now,
            ),
        )
        conn.commit()
        return username
    finally:
        close_db(conn)


def get_or_create_oauth_user(provider: str, sub: str, email: str, name: str) -> str:
    """Look up an OAuth user by (provider, sub). Create if absent. Returns username."""
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        # psycopg2 doesn't support "provider" as column name trick — use explicit columns
        cur.execute(
            f"SELECT username FROM users WHERE provider = {PH} AND provider_sub = {PH}",
            (provider, sub),
        )
        row = cur.fetchone()
        if row:
            return row["username"] if isinstance(row, dict) else row[0]

        # Try to link by email
        cur.execute(f"SELECT username FROM users WHERE email = {PH}", (email,))
        row = cur.fetchone()
        if row:
            username = row["username"] if isinstance(row, dict) else row[0]
            cur.execute(
                f"UPDATE users SET provider = {PH}, provider_sub = {PH} WHERE username = {PH}",
                (provider, sub, username),
            )
            conn.commit()
            return username

        # Create new OAuth user
        username = email
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            "INSERT INTO users "
            f"(username, email, display_name, role, is_active, created_at) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
            (username, email, name, "standard", 1, now),
        )
        # Store provider info — columns added via migration if missing
        try:
            cur.execute(
                f"UPDATE users SET provider = {PH}, provider_sub = {PH} WHERE username = {PH}",
                (provider, sub, username),
            )
        except Exception:
            pass
        conn.commit()
        return username
    finally:
        close_db(conn)


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


# ── Compatibility shim ─────────────────────────────────────────────────────────


def _load_users() -> list:  # noqa: F811
    raise RuntimeError("Migrado a DB — usa list_users() en su lugar")


def _save_users(_users: list) -> None:  # noqa: F811
    raise RuntimeError("Migrado a DB — usa las funciones DB en su lugar")
