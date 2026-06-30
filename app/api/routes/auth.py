"""Auth routes — shared dependencies and session endpoints."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from app.auth.auth import (
    admin_set_password,
    admin_update_user,
    cancel_user_deletion,
    consume_reset_token,
    create_password_reset_token,
    create_token,
    decode_token,
    decode_workspace_token,
    delete_user,
    get_owned_workspaces,
    get_user_by_email,
    get_user_by_username,
    get_user_role,
    list_users,
    register_user_email,
    schedule_user_deletion,
    send_account_status_email,
    send_reset_email,
    send_verification_email,
    set_own_password,
    verify_email_token,
    verify_password_async,
)
from app.config.data import AGENTS_DIR as _AGENTS_DIR
from app.config.data import DB_FILE as _DB_FILE
from app.storage.db import IS_PG, open_db
from app.storage.storage import AgentStorage as _AgentStorage
from app.utils.net import client_ip as _client_ip
from app.config.session import (
    EMAIL_VERIFY_ENABLED,
    REGISTER_MAX,
    REGISTER_WINDOW,
    REGISTRATION_MODE,
    SECURE_COOKIES,
)
from app.middleware.ratelimit import RateLimiter
from app.storage.groups import GroupStorage as _GroupStorage
from app.storage.workspaces import WorkspaceStorage as _WorkspaceStorage

_groups = _GroupStorage(_DB_FILE)
_workspaces = _WorkspaceStorage(_DB_FILE)
_agents = _AgentStorage(_AGENTS_DIR)
_register_limiter = RateLimiter(
    calls=REGISTER_MAX, window=REGISTER_WINDOW, key_func=_client_ip
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class WorkspaceContext:
    """Contexto de request con usuario y workspace activo."""

    __slots__ = ("user", "workspace_id")

    def __init__(self, user: str, workspace_id: str) -> None:
        self.user = user
        self.workspace_id = workspace_id


def require_auth(ga_token: Optional[str] = Cookie(default=None)) -> str:
    """Dependency: validates session token and returns username."""
    if not ga_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    username = decode_token(ga_token)
    if not username:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    return username


async def require_workspace(
    ga_token: Optional[str] = Cookie(default=None),
) -> WorkspaceContext:
    """Dependency: validates session token and returns WorkspaceContext(user, workspace_id).

    Defense-in-depth: even with a valid JWT, verify that the user still belongs to the
    workspace in the token (handles removed members and any token-creation bugs).
    """
    if not ga_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    username, workspace_id = decode_workspace_token(ga_token)
    if not username:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    # Personal workspace: id == username, always accessible
    if workspace_id != username and not await _workspaces.can_access(
        workspace_id, username
    ):
        # Membership revoked or token tampered — fall back to personal workspace
        workspace_id = username
    return WorkspaceContext(user=username, workspace_id=workspace_id)


async def require_admin(username: str = Depends(require_auth)) -> str:
    if await get_user_role(username) != "admin":
        raise HTTPException(status_code=403, detail="Acceso restringido")
    return username


@router.post("/register")
async def register(request: Request, response: Response) -> Dict[str, Any]:
    if REGISTRATION_MODE == "closed":
        raise HTTPException(status_code=403, detail="El registro está desactivado.")
    if REGISTRATION_MODE == "invite":
        raise HTTPException(
            status_code=403,
            detail="El registro requiere invitación de un administrador.",
        )
    await _register_limiter(request)
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    birth_date = str(body.get("birth_date") or "").strip() or None
    gender = str(body.get("gender") or "").strip() or None
    country = str(body.get("country") or "").strip() or None
    phone = str(body.get("phone") or "").strip() or None

    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=400, detail="Email inválido")
    if len(password) < 8:
        raise HTTPException(
            status_code=400, detail="La contraseña debe tener al menos 8 caracteres"
        )

    try:
        username, verify_token = await register_user_email(
            email,
            password,
            birth_date=birth_date,
            gender=gender,
            country=country,
            phone=phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if EMAIL_VERIFY_ENABLED and verify_token:
        base_url = str(request.base_url).rstrip("/")
        send_verification_email(email, verify_token, base_url)
        return {"ok": True, "email": email, "pending_verification": True}

    token = create_token(username)
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "email": email, "pending_verification": False}


@router.post("/login")
async def login(request: Request, response: Response) -> Dict[str, Any]:
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")
    user = await get_user_by_email(email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not await verify_password_async(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user.get("is_active", 1):
        raise HTTPException(status_code=403, detail="Cuenta desactivada")
    if EMAIL_VERIFY_ENABLED and not user.get("is_verified", 1):
        raise HTTPException(
            status_code=403,
            detail="Cuenta pendiente de verificación. Revisa tu correo.",
        )
    token = create_token(user["username"])
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "username": user["username"]}


@router.get("/verify")
async def verify_email(token: str, response: Response) -> Dict[str, Any]:
    username = await verify_email_token(token)
    if not username:
        raise HTTPException(
            status_code=400, detail="Enlace de verificación inválido o expirado"
        )
    auth_token = create_token(username)
    response.set_cookie(
        "ga_token",
        auth_token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "username": username}


@router.post("/guest")
async def guest_login(response: Response) -> Dict[str, Any]:
    from app.storage.guest import new_guest_id

    guest_id = new_guest_id()
    token = create_token(guest_id)
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "username": guest_id}


@router.post("/logout")
async def logout(response: Response) -> Dict[str, Any]:
    response.delete_cookie("ga_token")
    return {"ok": True}


@router.get("/me")
async def me(ctx: WorkspaceContext = Depends(require_workspace)) -> Dict[str, Any]:
    from app.storage.guest import is_guest
    from app.config.session import WEBMAIL_URL

    username = ctx.user
    workspace_id = ctx.workspace_id

    role = await get_user_role(username)
    ws_name: Optional[str] = None
    if is_guest(username):
        auth_method = "guest"
        user_row: Dict[str, Any] = {}
    else:
        user_row = await get_user_by_username(username) or {}
        auth_method = user_row.get("provider") or "internal"
        if workspace_id != username:
            ws = await _workspaces.get(workspace_id)
            ws_name = ws["name"] if ws else workspace_id
        else:
            ws_name = user_row.get("display_name") or username

    payload: Dict[str, Any] = {
        "username": username,
        "role": role,
        "auth_method": auth_method,
        "workspace_id": workspace_id,
        "workspace_personal": workspace_id == username,
    }
    if role == "admin" and WEBMAIL_URL:
        payload["webmail_url"] = WEBMAIL_URL
    if ws_name is not None:
        payload["workspace_name"] = ws_name
    return payload


@router.post("/forgot-password")
async def forgot_password(request: Request) -> Dict[str, Any]:
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=400, detail="Email inválido")
    token = await create_password_reset_token(email)
    if token:
        base_url = str(request.base_url).rstrip("/")
        send_reset_email(email, token, base_url)
    # Respuesta siempre igual para no revelar si el email existe
    return {"ok": True}


@router.post("/reset-password")
async def reset_password(request: Request) -> Dict[str, Any]:
    body = await request.json()
    token = str(body.get("token") or "").strip()
    new_password = str(body.get("password") or "").strip()
    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token y contraseña requeridos")
    if len(new_password) < 8:
        raise HTTPException(
            status_code=400, detail="La contraseña debe tener al menos 8 caracteres"
        )
    if not await consume_reset_token(token, new_password):
        raise HTTPException(status_code=400, detail="Enlace inválido o expirado")
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    request: Request, username: str = Depends(require_auth)
) -> Dict[str, Any]:
    body = await request.json()
    current = str(body.get("current_password") or "")
    new_pw = str(body.get("new_password") or "").strip()
    if not current or not new_pw:
        raise HTTPException(status_code=400, detail="Completa todos los campos")
    if len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="La nueva contraseña es muy corta")

    user = await get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not await verify_password_async(current, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")
    await set_own_password(username, new_pw)
    return {"ok": True}


# ── GDPR ──────────────────────────────────────────────────────────────────────


@router.get("/me/deletion-status")
async def get_deletion_status(username: str = Depends(require_auth)) -> Dict[str, Any]:
    user = await get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {
        "scheduled": user.get("deletion_requested_at") is not None,
        "deletion_date": user.get("deletion_requested_at"),
    }


@router.post("/me/request-deletion")
async def request_account_deletion(
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    owned = await get_owned_workspaces(username)
    if owned:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Transfiere o elimina tus workspaces antes de borrar la cuenta",
                "workspaces": owned,
            },
        )
    await schedule_user_deletion(username)
    return {"ok": True, "message": "Cuenta programada para eliminación en 30 días"}


@router.post("/me/cancel-deletion")
async def cancel_account_deletion(request: Request) -> Dict[str, Any]:
    body = await request.json()
    token = str(body.get("token", "")).strip()
    if not token or not await cancel_user_deletion(token):
        raise HTTPException(status_code=400, detail="Token inválido o expirado")
    return {"ok": True}


@router.get("/me/export")
async def export_my_data(username: str = Depends(require_auth)):
    from datetime import datetime, timezone
    from fastapi.responses import StreamingResponse
    from app.services.gdpr import export_user_data

    buf = await export_user_data(username)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_name = username.split("@")[0].replace(" ", "_")
    filename = f"export_{safe_name}_{date_str}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Social profile ────────────────────────────────────────────────────────────

_ALLOWED_LANGUAGES = {"es", "en", "fr", "de", "pt", "it", "zh", "ja", "ar"}
_MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB
_ALLOWED_AVATAR_EXT = {".jpg", ".jpeg", ".png", ".webp"}


async def _get_social_fields(username: str) -> Dict[str, Any]:
    import json

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT avatar, bio, languages, email_public, github, cv, created_at "
            "FROM users WHERE username = ?",
            (username,),
        )
        if not row:
            return {}
        followers_count = (
            await conn.fetchval(
                "SELECT COUNT(*) FROM user_follows WHERE following = ?",
                (username,),
            )
            or 0
        )
        following_count = (
            await conn.fetchval(
                "SELECT COUNT(*) FROM user_follows WHERE follower = ?",
                (username,),
            )
            or 0
        )
    try:
        langs = json.loads(row[2] or "[]")
    except Exception:
        langs = []
    return {
        "avatar_url": f"/api/users/{username}/avatar" if row[0] else None,
        "bio": row[1],
        "languages": langs,
        "email_public": row[3],
        "github": row[4],
        "cv": row[5],
        "joined_at": row[6],
        "followers_count": followers_count,
        "following_count": following_count,
    }


@router.put("/me/profile")
async def update_profile(
    request: Request,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    import json

    body = await request.json()
    bio = str(body.get("bio") or "").strip()[:500] or None
    raw_langs = body.get("languages") or []
    languages = json.dumps([lang for lang in raw_langs if lang in _ALLOWED_LANGUAGES])
    email_public = str(body.get("email_public") or "").strip()[:200] or None
    github = str(body.get("github") or "").strip()[:100] or None
    cv = str(body.get("cv") or "").strip()[:20000] or None

    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET bio=?, languages=?, email_public=?, github=?, cv=? WHERE username=?",
            (bio, languages, email_public, github, cv, username),
        )
        await conn.commit()
    return {"ok": True}


@router.post("/me/avatar")
async def upload_avatar(
    request: Request,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    import base64
    from pathlib import Path as _Path
    from fastapi import UploadFile
    from fastapi.datastructures import FormData

    form: FormData = await request.form()
    file: UploadFile = form.get("avatar")  # type: ignore[assignment]
    if not file:
        raise HTTPException(status_code=400, detail="Campo 'avatar' requerido")

    ext = _Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_AVATAR_EXT:
        raise HTTPException(
            status_code=400, detail="Formato no permitido. Usa jpg, png o webp."
        )

    data = await file.read()
    if len(data) > _MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="El avatar no puede superar 2 MB.")

    encoded = base64.b64encode(data).decode("ascii")
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET avatar=? WHERE username=?",
            (encoded, username),
        )
        await conn.commit()
    return {"ok": True, "avatar_url": f"/api/users/{username}/avatar"}


users_router = APIRouter(prefix="/api/users", tags=["users"])


@users_router.get("")
async def search_users(
    q: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    limit = min(limit, 50)
    async with open_db() as conn:
        if q:
            pattern = f"%{q}%"
            rows = await conn.fetchall(
                "SELECT u.username, u.avatar, "
                "(SELECT COUNT(*) FROM user_follows WHERE following = u.username) AS followers_count, "
                "(SELECT COUNT(*) FROM resource_social WHERE owner = u.username AND is_public = 1) AS public_resources_count "
                "FROM users u "
                "WHERE u.username != ? AND LOWER(u.username) LIKE LOWER(?) "
                "ORDER BY u.username LIMIT ? OFFSET ?",
                (username, pattern, limit, offset),
            )
        else:
            rows = await conn.fetchall(
                "SELECT u.username, u.avatar, "
                "(SELECT COUNT(*) FROM user_follows WHERE following = u.username) AS followers_count, "
                "(SELECT COUNT(*) FROM resource_social WHERE owner = u.username AND is_public = 1) AS public_resources_count "
                "FROM users u "
                "WHERE u.username != ? "
                "ORDER BY u.username LIMIT ? OFFSET ?",
                (username, limit, offset),
            )
    return [
        {
            "username": row[0],
            "avatar_url": f"/api/users/{row[0]}/avatar" if row[1] else None,
            "followers_count": row[2],
            "public_resources_count": row[3],
        }
        for row in rows
    ]


@users_router.get("/{username}/avatar")
async def get_avatar(username: str, _: str = Depends(require_auth)):
    import base64
    from fastapi.responses import Response

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT avatar FROM users WHERE username=?", (username,)
        )

    if not row or not row[0]:
        return Response(status_code=204)

    try:
        data = base64.b64decode(row[0])
    except Exception:
        return Response(status_code=204)

    # Canvas always exports PNG; detect jpeg by magic bytes as fallback
    mime = "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
    return Response(content=data, media_type=mime)


@users_router.get("/{username}")
async def get_public_profile(
    username: str,
    _: str = Depends(require_auth),
) -> Dict[str, Any]:
    user = await get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    fields = await _get_social_fields(username)
    return {"username": username, **fields}


# ── Admin ─────────────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.get("/stats")
async def admin_stats(_: str = Depends(require_admin)) -> Dict[str, Any]:
    import datetime as _dt

    async with open_db() as conn:
        u = await conn.fetchone(
            "SELECT COUNT(*), SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN is_verified=1 THEN 1 ELSE 0 END) FROM users"
        )
        users_total, users_active, users_verified = (u[0] or 0, u[1] or 0, u[2] or 0)

        c = await conn.fetchone(
            "SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0) FROM connections"
        )
        conns_total, tokens_in, tokens_out = (c[0] or 0, c[1] or 0, c[2] or 0)

        knowledge_total = (
            await conn.fetchval("SELECT COUNT(*) FROM knowledge_items")
        ) or 0
        conversations_total = (
            await conn.fetchval("SELECT COUNT(*) FROM conversations")
        ) or 0

        cutoff = (_dt.date.today() - _dt.timedelta(days=13)).isoformat()
        today = _dt.date.today().isoformat()
        try:
            daily_rows = await conn.fetchall(
                "SELECT day, SUM(tokens) FROM token_daily WHERE day >= ? GROUP BY day ORDER BY day ASC",
                (cutoff,),
            )
            tokens_daily = [{"day": r[0], "tokens": r[1]} for r in daily_rows]
            # First-run backfill: seed today from cumulative connection totals
            if not tokens_daily and (tokens_in + tokens_out) > 0:
                if IS_PG:
                    await conn.execute(
                        "INSERT INTO token_daily (day, owner_id, tokens) "
                        "SELECT ?, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE tokens_in + tokens_out > 0 ON CONFLICT (day, owner_id) DO NOTHING",
                        (today,),
                    )
                else:
                    await conn.execute(
                        "INSERT OR IGNORE INTO token_daily (day, owner_id, tokens) "
                        "SELECT ?, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE tokens_in + tokens_out > 0",
                        (today,),
                    )
                await conn.commit()
                daily_rows = await conn.fetchall(
                    "SELECT day, SUM(tokens) FROM token_daily WHERE day >= ? GROUP BY day ORDER BY day ASC",
                    (cutoff,),
                )
                tokens_daily = [{"day": r[0], "tokens": r[1]} for r in daily_rows]
        except Exception:
            tokens_daily = []

    agents_public = (
        len(list(_AGENTS_DIR.glob("public/*/config.json")))
        if _AGENTS_DIR.exists()
        else 0
    )
    agents_private = (
        len(list(_AGENTS_DIR.glob("private/*/config.json")))
        if _AGENTS_DIR.exists()
        else 0
    )

    from app.config.session import WEBMAIL_URL

    return {
        "users_total": users_total,
        "users_active": users_active,
        "users_verified": users_verified,
        "connections_total": conns_total,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "knowledge_total": knowledge_total,
        "conversations_total": conversations_total,
        "agents_public": agents_public,
        "agents_private": agents_private,
        "webmail_url": WEBMAIL_URL,
        "tokens_daily": tokens_daily,
    }


@admin_router.get("/users")
async def admin_list_users(
    q: Optional[str] = None,
    role: Optional[str] = None,
    active: Optional[str] = None,
    verified: Optional[str] = None,
    _: str = Depends(require_admin),
) -> List[Dict[str, Any]]:
    users = await list_users()
    async with open_db() as conn:
        token_rows = await conn.fetchall(
            "SELECT owner_id, COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
    token_map = {r[0]: {"tokens_in": r[1], "tokens_out": r[2]} for r in token_rows}
    for u in users:
        uname = u.get("username")
        tokens = token_map.get(uname, {"tokens_in": 0, "tokens_out": 0})
        u["tokens_in"] = tokens["tokens_in"]
        u["tokens_out"] = tokens["tokens_out"]
    if q:
        q_low = q.lower()
        users = [
            u
            for u in users
            if q_low in (u.get("username") or "").lower()
            or q_low in (u.get("email") or "").lower()
        ]
    if role:
        users = [u for u in users if u.get("role") == role]
    if active is not None:
        want = active.lower() in ("1", "true", "yes")
        users = [u for u in users if bool(u.get("is_active", 1)) == want]
    if verified is not None:
        want = verified.lower() in ("1", "true", "yes")
        users = [u for u in users if bool(u.get("is_verified", 1)) == want]
    return users


@admin_router.patch("/users/{username}")
async def admin_patch_user(
    username: str,
    request: Request,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    if username == admin:
        raise HTTPException(
            status_code=400, detail="No puedes modificar tu propia cuenta"
        )
    body = await request.json()
    updates: Dict[str, Any] = {}
    if "is_active" in body:
        updates["is_active"] = 1 if body["is_active"] else 0
    if "role" in body:
        if body["role"] not in ("admin", "gestor", "standard"):
            raise HTTPException(status_code=400, detail="Rol inválido")
        updates["role"] = body["role"]
    new_pw = str(body.get("password") or "").strip()
    if new_pw and len(new_pw) < 4:
        raise HTTPException(
            status_code=400, detail="La contraseña debe tener al menos 4 caracteres"
        )
    if not updates and not new_pw:
        raise HTTPException(status_code=400, detail="Sin cambios")
    if updates and not await admin_update_user(username, **updates):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if new_pw and not await admin_set_password(username, new_pw):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if "is_active" in updates:
        user = await get_user_by_username(username)
        email = user.get("email") if user else None
        if email:
            base_url = str(request.base_url).rstrip("/")
            send_account_status_email(email, bool(updates["is_active"]), base_url)
    return {"ok": True}


@admin_router.delete("/users/{username}")
async def admin_delete_user(
    username: str, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    if username == admin:
        raise HTTPException(
            status_code=400, detail="No puedes eliminar tu propia cuenta"
        )
    if not await delete_user(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"ok": True}


@admin_router.get("/connections")
async def admin_list_connections(
    _: str = Depends(require_admin),
) -> List[Dict[str, Any]]:
    import json as _json

    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT id, owner_id, data, tokens_in, tokens_out, created_at "
            "FROM connections ORDER BY created_at DESC"
        )
        email_rows = await conn.fetchall("SELECT username, email FROM users")
    email_map = {r[0]: r[1] for r in email_rows}
    result = []
    for row in rows:
        d = (
            dict(row)
            if isinstance(row, dict)
            else {
                "id": row[0],
                "owner_id": row[1],
                "data": row[2],
                "tokens_in": row[3],
                "tokens_out": row[4],
                "created_at": row[5],
            }
        )
        try:
            data = _json.loads(d.get("data") or "{}")
        except Exception:
            data = {}
        result.append(
            {
                "id": d["id"],
                "owner_id": d["owner_id"],
                "owner_email": email_map.get(d["owner_id"], d["owner_id"]),
                "name": data.get("name", d["id"]),
                "type": data.get("type", ""),
                "tokens_in": d["tokens_in"],
                "tokens_out": d["tokens_out"],
                "created_at": d["created_at"],
            }
        )
    return result


@admin_router.delete("/connections/{conn_id}")
async def admin_delete_connection(
    conn_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage

    if not await ConnectionStorage(DB_FILE).delete(conn_id, owner_id=None):
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    return {"ok": True}


@admin_router.get("/agents")
async def admin_list_agents(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    agents = await _agents.list(scope="all")
    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT username, email FROM users")
        conn_rows = await conn.fetchall(
            "SELECT id, owner_id, tokens_in, tokens_out FROM connections"
        )
    email_map = {r[0]: r[1] for r in user_rows}
    conn_data = {
        r[0]: {"owner_id": r[1], "tokens_in": r[2], "tokens_out": r[3]}
        for r in conn_rows
    }
    for a in agents:
        conn_id = a.get("connection_id")
        owner = a.get("owner_id")
        if not owner and conn_id and conn_id in conn_data:
            owner = conn_data[conn_id]["owner_id"]
        a["owner_email"] = email_map.get(owner, owner) if owner else None
        # Agregar tokens de la conexión asociada
        if conn_id and conn_id in conn_data:
            a["tokens_in"] = conn_data[conn_id]["tokens_in"]
            a["tokens_out"] = conn_data[conn_id]["tokens_out"]
        else:
            a["tokens_in"] = 0
            a["tokens_out"] = 0
    return agents


@admin_router.put("/agents/{agent_id}")
async def admin_update_agent(
    agent_id: str,
    request: Request,
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    agent = await _agents.get(agent_id, scope="private")
    if not agent:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    payload = await request.json()
    protected = {"id", "owner_id", "created_at", "scope"}
    updated = {**agent, **{k: v for k, v in payload.items() if k not in protected}}
    new_name = str(updated.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    new_id = re.sub(r"[^a-z0-9_\-]", "-", new_name.lower()).strip("-")
    if new_id != agent_id:
        await _agents.delete(agent_id, scope="private")
    return await _agents.save(updated, "private", owner_id=agent.get("owner_id"))


@admin_router.delete("/agents/{agent_id}")
async def admin_delete_agent(
    agent_id: str,
    scope: str = "private",
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    if scope not in ("public", "private"):
        raise HTTPException(
            status_code=400, detail="scope debe ser 'public' o 'private'"
        )
    deleted = await _agents.delete(agent_id, scope=scope)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    return {"ok": True}


@admin_router.get("/knowledge")
async def admin_list_knowledge(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage

    async with open_db() as conn:
        user_rows = await conn.fetchall("SELECT username, email FROM users")
    email_map = {r[0]: r[1] for r in user_rows}
    items = await KnowledgeStorage(DB_FILE).list(owner_id=None)
    for item in items:
        item["owner_email"] = email_map.get(
            item.get("owner_id", ""), item.get("owner_id", "")
        )
        item.pop("content", None)
    return items


@admin_router.delete("/knowledge/{item_id}")
async def admin_delete_knowledge(
    item_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage

    if not await KnowledgeStorage(DB_FILE).delete(item_id, owner_id=None):
        raise HTTPException(status_code=404, detail="Elemento no encontrado")
    return {"ok": True}


@admin_router.get("/groups")
async def admin_list_groups(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    async with open_db() as conn:
        rows = await conn.fetchall(
            "SELECT wg.id, wg.workspace_id, wg.name, wg.created_by, wg.created_at, "
            "COUNT(DISTINCT wgm.username) AS member_count, "
            "COUNT(DISTINCT rg.resource_id || '|' || rg.resource_type) AS resource_count "
            "FROM workspace_groups wg "
            "LEFT JOIN workspace_group_members wgm ON wg.id = wgm.group_id "
            "LEFT JOIN resource_groups rg ON wg.id = rg.group_id "
            "GROUP BY wg.id ORDER BY wg.created_at DESC"
        )
    return [
        {
            "id": r[0],
            "workspace_id": r[1],
            "name": r[2],
            "created_by": r[3],
            "created_at": r[4],
            "member_count": r[5],
            "resource_count": r[6],
        }
        for r in rows
    ]


@admin_router.delete("/groups/{group_id}")
async def admin_delete_group(
    group_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    if not await _groups.get(group_id):
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    await _groups.delete(group_id)
    return {"ok": True}


@admin_router.get("/workspaces")
async def admin_list_workspaces(
    _: str = Depends(require_admin),
) -> List[Dict[str, Any]]:
    import json as _json
    from app.config.data import AGENTS_DIR

    async with open_db() as conn:
        ws_rows = await conn.fetchall(
            "SELECT id, name, created_by, created_at FROM workspaces ORDER BY created_at DESC"
        )
        workspaces = [
            {"id": r[0], "name": r[1], "created_by": r[2], "created_at": r[3]}
            for r in ws_rows
        ]
        mc_rows = await conn.fetchall(
            "SELECT workspace_id, COUNT(*) FROM workspace_members GROUP BY workspace_id"
        )
        member_counts = {r[0]: r[1] for r in mc_rows}
        cs_rows = await conn.fetchall(
            "SELECT owner_id, COUNT(*), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
        conn_stats = {
            r[0]: {"count": r[1], "tokens_in": r[2], "tokens_out": r[3]}
            for r in cs_rows
        }
        kc_rows = await conn.fetchall(
            "SELECT owner_id, COUNT(*) FROM knowledge_items GROUP BY owner_id"
        )
        know_counts = {r[0]: r[1] for r in kc_rows}

    agent_counts: Dict[str, int] = {}
    if AGENTS_DIR.exists():
        for cfg_path in AGENTS_DIR.glob("private/*/config.json"):
            try:
                data = _json.loads(cfg_path.read_text())
                owner = data.get("owner_id")
                if owner:
                    agent_counts[owner] = agent_counts.get(owner, 0) + 1
            except Exception:
                pass

    result = []
    for ws in workspaces:
        ws_id = ws["id"]
        stats = conn_stats.get(ws_id, {"count": 0, "tokens_in": 0, "tokens_out": 0})
        result.append(
            {
                **ws,
                "member_count": member_counts.get(ws_id, 0),
                "connections_count": stats["count"],
                "tokens_in": stats["tokens_in"],
                "tokens_out": stats["tokens_out"],
                "knowledge_count": know_counts.get(ws_id, 0),
                "agents_count": agent_counts.get(ws_id, 0),
            }
        )
    return result


@admin_router.delete("/workspaces/{workspace_id}")
async def admin_delete_workspace(
    workspace_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    if not await _workspaces.get(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    await _workspaces.delete(workspace_id)
    return {"ok": True}


@admin_router.put("/resources/{resource_type}/{resource_id}/verify")
async def admin_verify_resource(
    resource_type: str,
    resource_id: str,
    request: Request,
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    _valid_types = ("agent", "skill", "knowledge")
    if resource_type not in _valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"resource_type debe ser uno de {_valid_types}",
        )
    body = await request.json()
    verified_val = bool(body.get("verified", False))
    db_val = verified_val if IS_PG else (1 if verified_val else 0)

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT 1 FROM resource_social WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        )
        if not row:
            raise HTTPException(
                status_code=404, detail="Recurso no encontrado en el catálogo social"
            )
        await conn.execute(
            "UPDATE resource_social SET verified=? WHERE resource_type=? AND resource_id=?",
            (db_val, resource_type, resource_id),
        )
        await conn.commit()
    return {"ok": True}


@admin_router.post("/impersonate/{username}")
async def admin_impersonate(
    username: str,
    response: Response,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    if username == admin:
        raise HTTPException(status_code=400, detail="Ya eres este usuario")
    if not await get_user_by_username(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    token = create_token(username)
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "username": username}
