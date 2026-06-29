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

from app.config.data import AGENTS_DIR, SETTINGS_FILE, SKILLS_DIR
from app.config.session import (
    EMAIL_VERIFY_ENABLED,
    JWT_ALGORITHM,
    JWT_EXPIRE_HOURS,
    JWT_SECRET_ENV,
    JWT_UNSAFE_SECRETS,
)
from app.storage.db import open_db

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


async def _get_user_by(field: str, value: str) -> Optional[dict]:
    async with open_db() as conn:
        row = await conn.fetchone(f"SELECT * FROM users WHERE {field} = ?", (value,))
        return dict(row) if row else None


# ── Public user API ────────────────────────────────────────────────────────────


async def get_user_by_email(email: str) -> Optional[dict]:
    return await _get_user_by("email", email)


async def get_user_by_username(username: str) -> Optional[dict]:
    return await _get_user_by("username", username)


async def _gen_username(email: str) -> str:
    """Username is the full email address."""
    return email


async def register_user(username: str, password: str, email: str = "") -> None:
    """Create a new local user. Raises ValueError if username or email already taken."""
    if not email:
        email = f"{username}@local"
    async with open_db() as conn:
        async with conn.transaction():
            if await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (username,)):
                raise ValueError("El nombre de usuario ya está en uso")
            if await conn.fetchone("SELECT 1 FROM users WHERE email = ?", (email,)):
                raise ValueError("El correo electrónico ya está registrado")
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, email, hash_password(password), "standard", 1, now),
            )
    flog.ok(f"Nuevo usuario: {email}")


async def register_user_email(
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
    token: Optional[str] = None
    async with open_db() as conn:
        async with conn.transaction():
            if await conn.fetchone("SELECT 1 FROM users WHERE email = ?", (email,)):
                raise ValueError("El correo electrónico ya está registrado")
            now = datetime.now(timezone.utc).isoformat()
            is_verified = 1
            if EMAIL_VERIFY_ENABLED:
                token = secrets.token_urlsafe(32)
                is_verified = 0
            await conn.execute(
                "INSERT INTO users "
                "(username, email, password_hash, display_name, birth_date, gender, "
                "country, phone, role, is_active, is_verified, verification_token, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    username, email, hash_password(password), display_name,
                    birth_date, gender, country, phone,
                    "standard", 1, is_verified, token, now,
                ),
            )
    flog.ok(f"Nuevo usuario: {email}")
    return username, token


async def verify_email_token(token: str) -> Optional[str]:
    """Mark user as verified by their token. Returns the username on success, None if invalid."""
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT username FROM users WHERE verification_token = ? AND is_verified = 0",
            (token,),
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


def _build_email_html(title: str, heading: str, body_html: str, cta_url: str = "", cta_label: str = "") -> str:
    cta_block = ""
    if cta_url and cta_label:
        cta_block = (
            f'<a href="{cta_url}" style="display:inline-block;background:#dc2626;color:#fff;text-decoration:none;'
            f'padding:12px 28px;border-radius:8px;font-size:14px;font-weight:600">{cta_label}</a>'
            f'<p style="margin:28px 0 0;font-size:11px;color:#555">O copia este enlace en tu navegador:<br>'
            f'<span style="color:#888;word-break:break-all">{cta_url}</span></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f0f10;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0f10;padding:40px 16px">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#1a1a1b;border-radius:12px;padding:40px 36px;max-width:480px">
        <tr><td>
          <p style="margin:0 0 4px;font-size:20px;font-weight:700;color:#fff">iAgents<span style="color:#dc2626">Hub</span></p>
          <p style="margin:0 0 32px;font-size:13px;color:#666">{title}</p>
          <h1 style="margin:0 0 12px;font-size:22px;font-weight:600;color:#e8e8e8">{heading}</h1>
          <div style="font-size:14px;color:#aaa;line-height:1.6;margin-bottom:28px">{body_html}</div>
          {cta_block}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _send_smtp(to: str, subject: str, html: str) -> None:
    """Send an HTML email via the configured SMTP server. Runs synchronously."""
    import smtplib
    import threading
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from app.config.session import SMTP_FROM, SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_TLS, SMTP_USER

    if not SMTP_HOST:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM or SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    def _send() -> None:
        try:
            if SMTP_TLS == "ssl":
                server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
            else:
                server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
                if SMTP_TLS == "starttls":
                    server.starttls()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(msg["From"], [to], msg.as_string())
            server.quit()
            flog.ok(f"[email] Enviado a {to}: {subject}")
        except Exception as exc:
            flog.warning(f"[email] Error al enviar a {to}: {exc}")

    threading.Thread(target=_send, daemon=True).start()


def send_verification_email(email: str, token: str, base_url: str = "") -> None:
    from app.config.session import SMTP_HOST

    verify_url = f"{base_url}/verify/?token={token}"

    if not SMTP_HOST:
        flog.info(f"[email] SMTP no configurado — enlace de verificación: {verify_url}")
        return

    html = _build_email_html(
        title="Verifica tu cuenta",
        heading="Confirma tu dirección de email",
        body_html="Haz clic en el botón para activar tu cuenta en iAgents Hub.<br>El enlace expira en <strong>24 horas</strong>.",
        cta_url=verify_url,
        cta_label="Verificar cuenta",
    )
    _send_smtp(email, "Verifica tu cuenta en iAgents Hub", html)


def send_reset_email(email: str, token: str, base_url: str = "") -> None:
    from app.config.session import SMTP_HOST

    reset_url = f"{base_url}/reset-password/?token={token}"

    if not SMTP_HOST:
        flog.info(f"[email] SMTP no configurado — enlace de recuperación: {reset_url}")
        return

    html = _build_email_html(
        title="Recuperar contraseña",
        heading="Restablecer contraseña",
        body_html="Recibimos una solicitud para restablecer la contraseña de tu cuenta.<br>El enlace expira en <strong>1 hora</strong>. Si no fuiste tú, ignora este mensaje.",
        cta_url=reset_url,
        cta_label="Restablecer contraseña",
    )
    _send_smtp(email, "Recupera el acceso a iAgents Hub", html)


def send_deletion_scheduled_email(email: str, cancel_token: str, deletion_at: str, base_url: str = "") -> None:
    from app.config.session import SMTP_HOST

    cancel_url = f"{base_url}/profile/?deletion_token={cancel_token}"
    try:
        date_str = datetime.fromisoformat(deletion_at).strftime("%d/%m/%Y")
    except Exception:
        date_str = deletion_at[:10]

    if not SMTP_HOST:
        flog.info(f"[email] SMTP no configurado — enlace cancelación: {cancel_url}")
        return

    html = _build_email_html(
        title="Eliminación de cuenta programada",
        heading="Tu cuenta será eliminada el " + date_str,
        body_html=(
            "Hemos recibido una solicitud para eliminar tu cuenta de iAgents Hub.<br>"
            f"Todos tus datos se borrarán permanentemente el <strong>{date_str}</strong>.<br><br>"
            "Si cambiaste de opinión, cancela la eliminación antes de esa fecha."
        ),
        cta_url=cancel_url,
        cta_label="Cancelar eliminación",
    )
    _send_smtp(email, "Eliminación de tu cuenta en iAgents Hub programada", html)


def send_account_status_email(email: str, is_active: bool, base_url: str = "") -> None:
    from app.config.session import SMTP_HOST

    if not SMTP_HOST:
        status = "reactivada" if is_active else "suspendida"
        flog.info(f"[email] SMTP no configurado — cuenta {status}: {email}")
        return

    if is_active:
        html = _build_email_html(
            title="Estado de tu cuenta",
            heading="Tu cuenta ha sido reactivada",
            body_html="Un administrador ha reactivado tu acceso a iAgents Hub.<br>Ya puedes iniciar sesión con normalidad.",
            cta_url=f"{base_url}/login/",
            cta_label="Entrar",
        )
        _send_smtp(email, "Tu cuenta en iAgents Hub ha sido reactivada", html)
    else:
        html = _build_email_html(
            title="Estado de tu cuenta",
            heading="Tu cuenta ha sido suspendida",
            body_html="Un administrador ha suspendido temporalmente tu acceso a iAgents Hub.<br>Si crees que es un error, contacta con el soporte.",
        )
        _send_smtp(email, "Tu cuenta en iAgents Hub ha sido suspendida", html)


async def create_password_reset_token(email: str) -> Optional[str]:
    """Generate a reset token for the given email. Returns None if email not found."""
    from app.config.session import PASSWORD_RESET_EXPIRE_HOURS

    async with open_db() as conn:
        if not await conn.fetchone("SELECT username FROM users WHERE email = ? AND is_active = 1", (email,)):
            return None
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=PASSWORD_RESET_EXPIRE_HOURS)).isoformat()
        await conn.execute(
            "UPDATE users SET reset_token = ?, reset_token_expires = ? WHERE email = ?",
            (token, expires, email),
        )
        await conn.commit()
        return token


async def consume_reset_token(token: str, new_password: str) -> bool:
    """Verify token, apply new password, and invalidate token. Returns False if invalid/expired."""
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT email, reset_token_expires FROM users WHERE reset_token = ?",
            (token,),
        )
        if not row:
            return False
        email = row[0]
        expires = row[1] or ""
        if not expires or datetime.fromisoformat(expires) < datetime.now(timezone.utc):
            return False
        await conn.execute(
            "UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expires = NULL "
            "WHERE email = ?",
            (hash_password(new_password), email),
        )
        await conn.commit()
        flog.ok(f"[auth] Contraseña reseteada para {email}")
        return True


async def get_user_role(username: str) -> str:
    if username == "guest" or username.startswith("guest_"):
        return "guest"
    user = await _get_user_by("username", username)
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
    async with open_db() as conn:
        if not await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (username,)):
            return False
        await conn.execute("DELETE FROM users WHERE username = ?", (username,))
        await conn.commit()
        return True


# ── GDPR ──────────────────────────────────────────────────────────────────────


async def get_owned_workspaces(username: str) -> list:
    """Return workspaces where the user is owner (created_by)."""
    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT id, name FROM workspaces WHERE created_by = ?",
            (username,),
        )
        return [dict(r) for r in rows]


async def schedule_user_deletion(username: str) -> str:
    """Schedule account deletion 30 days from now. Returns cancellation token."""
    token = secrets.token_urlsafe(32)
    deletion_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET deletion_requested_at = ?, deletion_token = ? WHERE username = ?",
            (deletion_at, token, username),
        )
        await conn.commit()
    user = await get_user_by_username(username)
    if user:
        send_deletion_scheduled_email(user["email"], token, deletion_at)
    flog.info(f"[gdpr] Borrado programado para {username} el {deletion_at}")
    return token


async def cancel_user_deletion(token: str) -> bool:
    """Cancel a scheduled deletion via token. Returns True if found and cancelled."""
    async with open_db() as conn:
        if not await conn.fetchone("SELECT 1 FROM users WHERE deletion_token = ?", (token,)):
            return False
        await conn.execute(
            "UPDATE users SET deletion_requested_at = NULL, deletion_token = NULL WHERE deletion_token = ?",
            (token,),
        )
        await conn.commit()
        return True


async def purge_user_data(username: str) -> None:
    """Hard-delete all user data from DB (cascade) and filesystem."""
    import shutil as _shutil
    import json as _json

    try:
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)",
                    (username,),
                )
                await conn.execute("DELETE FROM conversations WHERE user_id = ?", (username,))
                await conn.execute("DELETE FROM knowledge_items WHERE owner_id = ?", (username,))
                await conn.execute("DELETE FROM knowledge_folders WHERE owner_id = ?", (username,))
                await conn.execute("DELETE FROM connections WHERE owner_id = ?", (username,))
                await conn.execute("DELETE FROM token_daily WHERE owner_id = ?", (username,))
                await conn.execute("DELETE FROM accounts WHERE owner_id = ?", (username,))
                await conn.execute("DELETE FROM resource_groups WHERE shared_by = ?", (username,))
                await conn.execute("DELETE FROM workspace_group_members WHERE username = ?", (username,))
                await conn.execute("DELETE FROM workspace_invitations WHERE username = ?", (username,))
                await conn.execute("DELETE FROM workspace_members WHERE username = ?", (username,))
                await conn.execute("DELETE FROM workspaces WHERE created_by = ?", (username,))
                await conn.execute("DELETE FROM users WHERE username = ?", (username,))
        flog.ok(f"[gdpr] BD purgada para {username}")
    except Exception as exc:
        flog.error(f"[gdpr] Error purgando BD de {username}: {exc}")
        raise

    for base_dir in (AGENTS_DIR, SKILLS_DIR):
        for scope_dir in (base_dir / "private", base_dir / "public"):
            if not scope_dir.exists():
                continue
            for item_dir in scope_dir.iterdir():
                cfg = item_dir / "config.json"
                if not cfg.exists():
                    continue
                try:
                    if _json.loads(cfg.read_text()).get("owner_id") == username:
                        _shutil.rmtree(item_dir, ignore_errors=True)
                except Exception:
                    pass

    flog.ok(f"[gdpr] Purga completa de {username}")


async def purge_expired_deletions() -> int:
    """Hard-delete accounts whose 30-day grace period has passed. Returns count."""
    now = datetime.now(timezone.utc).isoformat()
    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT username FROM users WHERE deletion_requested_at IS NOT NULL AND deletion_requested_at <= ?",
            (now,),
        )
    usernames = [r[0] for r in rows]

    for username in usernames:
        try:
            await purge_user_data(username)
        except Exception as exc:
            flog.error(f"[gdpr] No se pudo purgar {username}: {exc}")

    return len(usernames)


async def update_user_profile(username: str, **fields) -> None:
    """Update allowed profile fields for a user."""
    allowed = {"birth_date", "gender", "country", "phone", "display_name"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    async with open_db() as conn:
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [username]
        await conn.execute(f"UPDATE users SET {set_clause} WHERE username = ?", values)
        await conn.commit()


async def admin_update_user(username: str, **fields) -> bool:
    """Admin-only: update is_active or role. Returns False if user not found."""
    allowed = {"is_active", "role"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return True
    async with open_db() as conn:
        if not await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (username,)):
            return False
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [username]
        await conn.execute(f"UPDATE users SET {set_clause} WHERE username = ?", values)
        await conn.commit()
        return True


async def admin_set_password(username: str, new_password: str) -> bool:
    """Admin-only: set a new password for another user. Returns False if not found."""
    async with open_db() as conn:
        if not await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (username,)):
            return False
        await conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(new_password), username),
        )
        await conn.commit()
        return True


# ── Gestor role helpers ────────────────────────────────────────────────────────


async def promote_to_gestor(username: str) -> bool:
    """Promote a standard user to gestor. Returns False if user not found or not standard."""
    async with open_db() as conn:
        row = await conn.fetchone("SELECT role FROM users WHERE username = ?", (username,))
        if not row:
            return False
        role = row[0]
        if role not in ("standard",):
            return False
        await conn.execute("UPDATE users SET role = 'gestor' WHERE username = ?", (username,))
        await conn.commit()
        return True


async def demote_if_no_teams(username: str) -> bool:
    """Demote gestor back to standard if they manage no teams. Returns True if demoted."""
    from app.config.data import DB_FILE as _DB
    from app.storage.teams import TeamStorage

    ts = TeamStorage(_DB)
    managed = ts.list_managed_teams(username)
    if managed:
        return False
    async with open_db() as conn:
        row = await conn.fetchone("SELECT role FROM users WHERE username = ?", (username,))
        if not row:
            return False
        role = row[0]
        if role != "gestor":
            return False
        await conn.execute("UPDATE users SET role = 'standard' WHERE username = ?", (username,))
        await conn.commit()
        return True


def send_team_invitation_email(
    email: str,
    team_name: str,
    invited_by: str,
    token: str,
    base_url: str = "",
) -> None:
    from app.config.session import SMTP_HOST

    accept_url = f"{base_url}/profile/?tab=teams&token={token}"
    if not SMTP_HOST:
        flog.info(f"[email] SMTP no configurado — invitación equipo '{team_name}': {accept_url}")
        return

    html = _build_email_html(
        title="Invitación a equipo",
        heading=f"Te han invitado al equipo «{team_name}»",
        body_html=(
            f"<strong>{invited_by}</strong> te ha invitado a unirte al equipo "
            f"<strong>{team_name}</strong> en iAgents Hub.<br><br>"
            "Acepta la invitación desde tu perfil o haz clic en el botón. "
            "La invitación expira en <strong>48 horas</strong>."
        ),
        cta_url=accept_url,
        cta_label="Ver invitación",
    )
    _send_smtp(email, f"Te invitaron al equipo «{team_name}» en iAgents Hub", html)


# ── JWT ────────────────────────────────────────────────────────────────────────


def create_token(username: str, workspace_id: Optional[str] = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "wid": workspace_id or username,  # workspace personal = username
        "exp": expire,
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    """Return the username or None if the token is invalid/expired."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        return data.get("sub")
    except JWTError:
        return None


def decode_workspace_token(token: str) -> tuple[Optional[str], Optional[str]]:
    """Return (username, workspace_id). workspace_id defaults to username if not present."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        username = data.get("sub")
        workspace_id = data.get("wid") or username
        return username, workspace_id
    except JWTError:
        return None, None


# ── First-boot admin bootstrap ────────────────────────────────────────────────


async def ensure_admin_user() -> None:
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

    password: Optional[str] = None
    action: Optional[str] = None

    async with open_db() as conn:
        row = await conn.fetchone("SELECT username, role FROM users WHERE email = ?", (target_email,))
        target = dict(row) if row else None

        if target:
            if target["role"] != "admin":
                await conn.execute("UPDATE users SET role = ? WHERE email = ?", ("admin", target_email))
                await conn.commit()
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
            await conn.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                (hash_password(password), target_email),
            )
            await conn.commit()
            action = "contraseña reseteada"
        else:
            # No hay cuenta con ese email
            existing = await conn.fetchone("SELECT username FROM users WHERE role = ? LIMIT 1", ("admin",))
            if existing and not reset_mode:
                return

            password = secrets.token_urlsafe(12)
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                "INSERT INTO users "
                "(username, email, password_hash, role, is_active, is_verified, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (target_email, target_email, hash_password(password), "admin", 1, 1, now),
            )
            await conn.commit()
            action = "cuenta creada"

    if password is None or action is None:
        return

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
