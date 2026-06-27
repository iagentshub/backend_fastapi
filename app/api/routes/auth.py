"""Auth routes — shared dependencies and session endpoints."""
from __future__ import annotations

import re
import time
from collections import defaultdict
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
    hash_password,
    list_users,
    register_user_email,
    schedule_user_deletion,
    send_account_status_email,
    send_reset_email,
    send_verification_email,
    verify_email_token,
    verify_password,
)
from app.config.data import DB_FILE as _DB_FILE
from app.config.session import (
    EMAIL_VERIFY_ENABLED,
    REGISTER_MAX,
    REGISTER_WINDOW,
    REGISTRATION_MODE,
    SECURE_COOKIES,
)
from app.storage.groups import GroupStorage as _GroupStorage
from app.storage.workspaces import WorkspaceStorage as _WorkspaceStorage

_groups = _GroupStorage(_DB_FILE)
_workspaces = _WorkspaceStorage(_DB_FILE)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_rate_data: Dict[str, list] = defaultdict(list)


class WorkspaceContext:
    """Contexto de request con usuario y workspace activo."""
    __slots__ = ("user", "workspace_id")

    def __init__(self, user: str, workspace_id: str) -> None:
        self.user = user
        self.workspace_id = workspace_id


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


def _check_register_rate(ip: str) -> None:
    now = time.monotonic()
    events = [t for t in _rate_data[f"reg:{ip}"] if now - t < REGISTER_WINDOW]
    _rate_data[f"reg:{ip}"] = events
    if len(events) >= REGISTER_MAX:
        raise HTTPException(status_code=429, detail="Demasiados registros desde esta dirección. Espera un rato.")


def _record_register(ip: str) -> None:
    _rate_data[f"reg:{ip}"].append(time.monotonic())


def require_auth(ga_token: Optional[str] = Cookie(default=None)) -> str:
    """Dependency: validates session token and returns username."""
    if not ga_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    username = decode_token(ga_token)
    if not username:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    return username


def require_workspace(ga_token: Optional[str] = Cookie(default=None)) -> WorkspaceContext:
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
    if workspace_id != username and not _workspaces.can_access(workspace_id, username):
        # Membership revoked or token tampered — fall back to personal workspace
        workspace_id = username
    return WorkspaceContext(user=username, workspace_id=workspace_id)


def require_admin(username: str = Depends(require_auth)) -> str:
    if get_user_role(username) != "admin":
        raise HTTPException(status_code=403, detail="Acceso restringido")
    return username


@router.post("/register")
async def register(request: Request, response: Response) -> Dict[str, Any]:
    if REGISTRATION_MODE == "closed":
        raise HTTPException(status_code=403, detail="El registro está desactivado.")
    if REGISTRATION_MODE == "invite":
        raise HTTPException(status_code=403, detail="El registro requiere invitación de un administrador.")
    _check_register_rate(_client_ip(request))
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
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres")

    try:
        username, verify_token = register_user_email(
            email,
            password,
            birth_date=birth_date,
            gender=gender,
            country=country,
            phone=phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    _record_register(_client_ip(request))

    if EMAIL_VERIFY_ENABLED and verify_token:
        base_url = str(request.base_url).rstrip("/")
        send_verification_email(email, verify_token, base_url)
        return {"ok": True, "email": email, "pending_verification": True}

    token = create_token(username)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
    return {"ok": True, "email": email, "pending_verification": False}


@router.post("/login")
async def login(request: Request, response: Response) -> Dict[str, Any]:
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")
    user = get_user_by_email(email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user.get("is_active", 1):
        raise HTTPException(status_code=403, detail="Cuenta desactivada")
    if EMAIL_VERIFY_ENABLED and not user.get("is_verified", 1):
        raise HTTPException(status_code=403, detail="Cuenta pendiente de verificación. Revisa tu correo.")
    token = create_token(user["username"])
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
    return {"ok": True, "username": user["username"]}


@router.get("/verify")
async def verify_email(token: str, response: Response) -> Dict[str, Any]:
    username = verify_email_token(token)
    if not username:
        raise HTTPException(status_code=400, detail="Enlace de verificación inválido o expirado")
    auth_token = create_token(username)
    response.set_cookie("ga_token", auth_token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
    return {"ok": True, "username": username}


@router.post("/guest")
async def guest_login(response: Response) -> Dict[str, Any]:
    from app.storage.guest import new_guest_id
    guest_id = new_guest_id()
    token = create_token(guest_id)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
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
    role = get_user_role(username)
    if is_guest(username):
        auth_method = "guest"
    else:
        user = get_user_by_username(username) or {}
        auth_method = user.get("provider") or "internal"
    payload: Dict[str, Any] = {
        "username": username,
        "role": role,
        "auth_method": auth_method,
        "workspace_id": workspace_id,
        "workspace_personal": workspace_id == username,
    }
    if role == "admin" and WEBMAIL_URL:
        payload["webmail_url"] = WEBMAIL_URL
    if not is_guest(username):
        if workspace_id != username:
            ws = _workspaces.get(workspace_id)
            payload["workspace_name"] = ws["name"] if ws else workspace_id
        else:
            user = get_user_by_username(username) or {}
            payload["workspace_name"] = user.get("display_name") or username
    return payload


@router.post("/forgot-password")
async def forgot_password(request: Request) -> Dict[str, Any]:
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=400, detail="Email inválido")
    token = create_password_reset_token(email)
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
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres")
    if not consume_reset_token(token, new_password):
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

    user = get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not verify_password(current, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")

    from app.storage.db import PH, close_db, open_db
    from app.config.data import DB_FILE

    conn = open_db(DB_FILE)
    try:
        conn.cursor().execute(
            f"UPDATE users SET password_hash = {PH} WHERE username = {PH}",
            (hash_password(new_pw), username),
        )
        conn.commit()
    finally:
        close_db(conn)
    return {"ok": True}


# ── GDPR ──────────────────────────────────────────────────────────────────────


@router.get("/me/deletion-status")
async def get_deletion_status(username: str = Depends(require_auth)) -> Dict[str, Any]:
    user = get_user_by_username(username)
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
    owned = get_owned_workspaces(username)
    if owned:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Transfiere o elimina tus workspaces antes de borrar la cuenta",
                "workspaces": owned,
            },
        )
    schedule_user_deletion(username)
    return {"ok": True, "message": "Cuenta programada para eliminación en 30 días"}


@router.post("/me/cancel-deletion")
async def cancel_account_deletion(request: Request) -> Dict[str, Any]:
    body = await request.json()
    token = str(body.get("token", "")).strip()
    if not token or not cancel_user_deletion(token):
        raise HTTPException(status_code=400, detail="Token inválido o expirado")
    return {"ok": True}


@router.get("/me/export")
async def export_my_data(username: str = Depends(require_auth)):
    from fastapi.responses import StreamingResponse
    from app.services.gdpr import export_user_data
    from datetime import datetime, timezone

    buf = export_user_data(username)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_name = username.split("@")[0].replace(" ", "_")
    filename = f"export_{safe_name}_{date_str}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Admin ─────────────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.get("/stats")
async def admin_stats(_: str = Depends(require_admin)) -> Dict[str, Any]:
    import datetime as _dt
    from app.config.data import AGENTS_DIR, DB_FILE
    from app.storage.db import close_db, open_db

    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*), SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END), SUM(CASE WHEN is_verified=1 THEN 1 ELSE 0 END) FROM users")
        u = cur.fetchone()
        users_total, users_active, users_verified = (u[0] or 0, u[1] or 0, u[2] or 0)

        cur.execute("SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0) FROM connections")
        c = cur.fetchone()
        conns_total, tokens_in, tokens_out = (c[0] or 0, c[1] or 0, c[2] or 0)

        cur.execute("SELECT COUNT(*) FROM knowledge_items")
        knowledge_total = (cur.fetchone()[0] or 0)

        cur.execute("SELECT COUNT(*) FROM conversations")
        conversations_total = (cur.fetchone()[0] or 0)

        from app.storage.db import IS_PG
        _PH = "%s" if IS_PG else "?"
        cutoff = (_dt.date.today() - _dt.timedelta(days=13)).isoformat()
        today = _dt.date.today().isoformat()
        try:
            cur.execute(
                f"SELECT day, SUM(tokens) FROM token_daily WHERE day >= {_PH} GROUP BY day ORDER BY day ASC",
                (cutoff,),
            )
            tokens_daily = [{"day": r[0], "tokens": r[1]} for r in cur.fetchall()]
            # First-run backfill: seed today from cumulative connection totals
            if not tokens_daily and (tokens_in + tokens_out) > 0:
                if IS_PG:
                    cur.execute(
                        "INSERT INTO token_daily (day, owner_id, tokens) "
                        "SELECT %s, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE tokens_in + tokens_out > 0 ON CONFLICT (day, owner_id) DO NOTHING",
                        (today,),
                    )
                else:
                    cur.execute(
                        "INSERT OR IGNORE INTO token_daily (day, owner_id, tokens) "
                        "SELECT ?, owner_id, tokens_in + tokens_out FROM connections "
                        "WHERE tokens_in + tokens_out > 0",
                        (today,),
                    )
                conn.commit()
                cur.execute(
                    f"SELECT day, SUM(tokens) FROM token_daily WHERE day >= {_PH} GROUP BY day ORDER BY day ASC",
                    (cutoff,),
                )
                tokens_daily = [{"day": r[0], "tokens": r[1]} for r in cur.fetchall()]
        except Exception:
            tokens_daily = []
    finally:
        close_db(conn)

    agents_public = len(list(AGENTS_DIR.glob("public/*/config.json"))) if AGENTS_DIR.exists() else 0
    agents_private = len(list(AGENTS_DIR.glob("private/*/config.json"))) if AGENTS_DIR.exists() else 0

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
    from app.config.data import DB_FILE
    from app.storage.db import close_db, open_db
    users = list_users()
    # Agregar tokens consumidos por usuario
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT owner_id, COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
        token_map = {r[0]: {"tokens_in": r[1], "tokens_out": r[2]} for r in cur.fetchall()}
    finally:
        close_db(conn)
    for u in users:
        username = u.get("username")
        tokens = token_map.get(username, {"tokens_in": 0, "tokens_out": 0})
        u["tokens_in"] = tokens["tokens_in"]
        u["tokens_out"] = tokens["tokens_out"]
    if q:
        q_low = q.lower()
        users = [u for u in users if q_low in (u.get("username") or "").lower() or q_low in (u.get("email") or "").lower()]
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
        raise HTTPException(status_code=400, detail="No puedes modificar tu propia cuenta")
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
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 4 caracteres")
    if not updates and not new_pw:
        raise HTTPException(status_code=400, detail="Sin cambios")
    if updates and not admin_update_user(username, **updates):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if new_pw and not admin_set_password(username, new_pw):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if "is_active" in updates:
        user = get_user_by_username(username)
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
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propia cuenta")
    if not delete_user(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"ok": True}


@admin_router.get("/connections")
async def admin_list_connections(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    from app.config.data import DB_FILE
    from app.storage.db import close_db, open_db
    import json as _json

    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, owner_id, data, tokens_in, tokens_out, created_at FROM connections ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.execute("SELECT username, email FROM users")
        email_map = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        close_db(conn)

    result = []
    for row in rows:
        d = dict(row) if isinstance(row, dict) else {
            "id": row[0], "owner_id": row[1], "data": row[2],
            "tokens_in": row[3], "tokens_out": row[4], "created_at": row[5],
        }
        try:
            data = _json.loads(d.get("data") or "{}")
        except Exception:
            data = {}
        result.append({
            "id": d["id"],
            "owner_id": d["owner_id"],
            "owner_email": email_map.get(d["owner_id"], d["owner_id"]),
            "name": data.get("name", d["id"]),
            "type": data.get("type", ""),
            "tokens_in": d["tokens_in"],
            "tokens_out": d["tokens_out"],
            "created_at": d["created_at"],
        })
    return result


@admin_router.delete("/connections/{conn_id}")
async def admin_delete_connection(
    conn_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage
    if not ConnectionStorage(DB_FILE).delete(conn_id, owner_id=None):
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    return {"ok": True}


@admin_router.get("/agents")
async def admin_list_agents(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    from app.config.data import AGENTS_DIR, DB_FILE
    from app.storage.storage import AgentStorage
    from app.storage.db import close_db, open_db
    agents = AgentStorage(AGENTS_DIR).list(scope="all")
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, email FROM users")
        email_map = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT id, owner_id, tokens_in, tokens_out FROM connections")
        conn_data = {r[0]: {"owner_id": r[1], "tokens_in": r[2], "tokens_out": r[3]} for r in cur.fetchall()}
    finally:
        close_db(conn)
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
    import re as _re
    import shutil
    from app.config.data import AGENTS_DIR
    from app.storage.storage import AgentStorage
    store = AgentStorage(AGENTS_DIR)
    agent = store.get(agent_id, scope="private")
    if not agent:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    payload = await request.json()
    protected = {"id", "owner_id", "created_at", "scope"}
    updated = {**agent, **{k: v for k, v in payload.items() if k not in protected}}
    new_name = str(updated.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    new_id = _re.sub(r"[^a-z0-9_\-]", "-", new_name.lower()).strip("-")
    if new_id != agent_id:
        old_dir = AGENTS_DIR / "private" / _re.sub(r"[^a-z0-9_\-]", "-", agent_id.lower()).strip("-")
        if old_dir.exists():
            shutil.rmtree(old_dir)
    return store.save(updated, "private", owner_id=agent.get("owner_id"))


@admin_router.delete("/agents/{agent_id}")
async def admin_delete_agent(
    agent_id: str,
    scope: str = "private",
    _: str = Depends(require_admin),
) -> Dict[str, Any]:
    import re as _re
    import shutil
    from app.config.data import AGENTS_DIR
    if scope not in ("public", "private"):
        raise HTTPException(status_code=400, detail="scope debe ser 'public' o 'private'")
    safe_id = _re.sub(r"[^a-z0-9_\-]", "-", agent_id.lower()).strip("-")
    agent_dir = AGENTS_DIR / scope / safe_id
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    shutil.rmtree(agent_dir)
    return {"ok": True}


@admin_router.get("/knowledge")
async def admin_list_knowledge(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    from app.config.data import DB_FILE
    from app.storage.db import close_db, open_db
    from app.storage.knowledge import KnowledgeStorage

    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, email FROM users")
        email_map = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        close_db(conn)

    items = KnowledgeStorage(DB_FILE).list(owner_id=None)
    for item in items:
        item["owner_email"] = email_map.get(item.get("owner_id", ""), item.get("owner_id", ""))
        item.pop("content", None)
    return items


@admin_router.delete("/knowledge/{item_id}")
async def admin_delete_knowledge(
    item_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage
    if not KnowledgeStorage(DB_FILE).delete(item_id, owner_id=None):
        raise HTTPException(status_code=404, detail="Elemento no encontrado")
    return {"ok": True}


@admin_router.get("/groups")
async def admin_list_groups(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    from app.config.data import DB_FILE
    from app.storage.db import close_db, open_db
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT wg.id, wg.workspace_id, wg.name, wg.created_by, wg.created_at, "
            "COUNT(DISTINCT wgm.username) AS member_count, "
            "COUNT(DISTINCT rg.resource_id || '|' || rg.resource_type) AS resource_count "
            "FROM workspace_groups wg "
            "LEFT JOIN workspace_group_members wgm ON wg.id = wgm.group_id "
            "LEFT JOIN resource_groups rg ON wg.id = rg.group_id "
            "GROUP BY wg.id ORDER BY wg.created_at DESC"
        )
        rows = cur.fetchall()
    finally:
        close_db(conn)
    return [
        {
            "id": r[0], "workspace_id": r[1], "name": r[2],
            "created_by": r[3], "created_at": r[4],
            "member_count": r[5], "resource_count": r[6],
        }
        for r in rows
    ]


@admin_router.delete("/groups/{group_id}")
async def admin_delete_group(
    group_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    if not _groups.get(group_id):
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    _groups.delete(group_id)
    return {"ok": True}


@admin_router.get("/workspaces")
async def admin_list_workspaces(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    import json as _json
    from app.config.data import AGENTS_DIR, DB_FILE
    from app.storage.db import close_db, open_db

    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, created_by, created_at FROM workspaces ORDER BY created_at DESC")
        workspaces = [
            {"id": r[0], "name": r[1], "created_by": r[2], "created_at": r[3]}
            for r in cur.fetchall()
        ]
        cur.execute("SELECT workspace_id, COUNT(*) FROM workspace_members GROUP BY workspace_id")
        member_counts = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            "SELECT owner_id, COUNT(*), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
        conn_stats = {r[0]: {"count": r[1], "tokens_in": r[2], "tokens_out": r[3]} for r in cur.fetchall()}
        cur.execute("SELECT owner_id, COUNT(*) FROM knowledge_items GROUP BY owner_id")
        know_counts = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        close_db(conn)

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
        result.append({
            **ws,
            "member_count": member_counts.get(ws_id, 0),
            "connections_count": stats["count"],
            "tokens_in": stats["tokens_in"],
            "tokens_out": stats["tokens_out"],
            "knowledge_count": know_counts.get(ws_id, 0),
            "agents_count": agent_counts.get(ws_id, 0),
        })
    return result


@admin_router.delete("/workspaces/{workspace_id}")
async def admin_delete_workspace(
    workspace_id: str, _: str = Depends(require_admin)
) -> Dict[str, Any]:
    if not _workspaces.get(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace no encontrado")
    _workspaces.delete(workspace_id)
    return {"ok": True}


@admin_router.post("/impersonate/{username}")
async def admin_impersonate(
    username: str,
    response: Response,
    admin: str = Depends(require_admin),
) -> Dict[str, Any]:
    if username == admin:
        raise HTTPException(status_code=400, detail="Ya eres este usuario")
    if not get_user_by_username(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    token = create_token(username)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
    return {"ok": True, "username": username}
