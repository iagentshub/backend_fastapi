"""Tests de cobertura para app/api/routes/auth.py — líneas no cubiertas."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

# ── Helpers ───────────────────────────────────────────────────────────────────

def _direct_register(username: str, password: str = "pass1234", email: str = "") -> str:
    """Registra usuario directamente (sin HTTP) y devuelve su token."""
    from app.auth.auth import create_token, register_user
    asyncio.run(register_user(username, password, email=email or f"{username}@test.com"))
    return create_token(username)


def test_public_base_url_ignores_untrusted_host_header():
    from starlette.requests import Request

    from app.api.routes.auth import _public_base_url

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/forgot-password",
            "headers": [(b"host", b"attacker.example")],
            "server": ("attacker.example", 443),
            "scheme": "https",
        }
    )
    with patch.dict("os.environ", {"GAIA_FRONTEND_URL": ""}):
        assert _public_base_url(request) == "http://localhost:8007"


def test_public_base_url_prefers_configured_frontend():
    from starlette.requests import Request

    from app.api.routes.auth import _public_base_url

    request = Request({"type": "http", "headers": []})
    with patch.dict(
        "os.environ",
        {"GAIA_FRONTEND_URL": "https://app.iagents.example/"},
    ):
        assert _public_base_url(request) == "https://app.iagents.example"


# ── require_auth — token presente pero inválido (línea 73) ───────────────────

def test_require_auth_invalid_token(client):
    """Token malformado en require_auth → 401."""
    client.cookies.set("ga_token", "invalid.token.here")
    r = client.get("/api/auth/me/deletion-status")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_token"


# ── require_group — token inválido (línea 87) ────────────────────────────

def test_require_group_invalid_token(client):
    """Ruta que usa require_group con token malformado → 401."""
    client.cookies.set("ga_token", "bad.token.xyz")
    r = client.get("/api/agents")
    assert r.status_code == 401


# ── register — REGISTRATION_MODE closed (línea 104) ──────────────────────────

def test_register_mode_closed(client, reset_rate_limiter):
    with patch("app.api.routes.auth.REGISTRATION_MODE", "closed"):
        r = client.post("/api/auth/register", json={"email": "closed@test.com", "password": "pass1234"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "registration_disabled"


# ── register — REGISTRATION_MODE invite (línea 106) ──────────────────────────

def test_register_mode_invite(client, reset_rate_limiter):
    with patch("app.api.routes.auth.REGISTRATION_MODE", "invite"):
        r = client.post("/api/auth/register", json={"email": "invite@test.com", "password": "pass1234"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "registration_invite_only"


# ── register — EMAIL_VERIFY_ENABLED (líneas 134-136) ─────────────────────────

def test_register_email_verify_enabled_returns_pending(client, reset_rate_limiter):
    """Con EMAIL_VERIFY_ENABLED=True el registro devuelve pending_verification=True."""
    # Hay que parchear en ambos módulos: el de rutas (para el if de la respuesta)
    # y el de auth (para que register_user_email genere el verify_token).
    with patch("app.api.routes.auth.EMAIL_VERIFY_ENABLED", True), \
         patch("app.auth.auth.EMAIL_VERIFY_ENABLED", True), \
         patch("app.api.routes.auth.send_verification_email"):
        r = client.post("/api/auth/register", json={
            "email": "pendingverify@test.com", "password": "pass1234"
        })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data.get("pending_verification") is True


# ── login — cuenta desactivada (línea 156) ───────────────────────────────────

def test_login_inactive_account(client):
    from app.auth.auth import register_user
    from app.storage.db import open_db

    async def _setup():
        await register_user("inactive_user", "pass1234", email="inactive@test.com")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET is_active = 0 WHERE username = ?",
                ("inactive_user",),
            )
            await conn.commit()

    asyncio.run(_setup())
    r = client.post("/api/auth/login", json={"email": "inactive@test.com", "password": "pass1234"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "account_disabled"


# ── login — cuenta sin verificar con EMAIL_VERIFY_ENABLED (línea 158) ────────

def test_login_unverified_account(client):
    from app.auth.auth import register_user
    from app.storage.db import open_db

    async def _setup():
        await register_user("unverified_user", "pass1234", email="unverified@test.com")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET is_verified = 0 WHERE username = ?",
                ("unverified_user",),
            )
            await conn.commit()

    asyncio.run(_setup())
    with patch("app.api.routes.auth.EMAIL_VERIFY_ENABLED", True):
        r = client.post("/api/auth/login", json={
            "email": "unverified@test.com", "password": "pass1234"
        })
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "email_not_verified"


# ── logout (líneas 185-186) ───────────────────────────────────────────────────

def test_logout_clears_cookie(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "logout@test.com", "password": "pass1234"})
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── me — usuario guest (líneas 199-200) ──────────────────────────────────────

def test_me_guest_auth_method(client):
    r_guest = client.post("/api/auth/guest")
    assert r_guest.status_code == 200
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["auth_method"] == "guest"


# ── me — admin con WEBMAIL_URL (línea 218) ────────────────────────────────────

def test_me_admin_with_webmail_url(admin_client):
    """Cuando WEBMAIL_URL está configurado y el rol es admin, aparece en la respuesta.

    WEBMAIL_URL se importa localmente dentro de me(), así que hay que parchear
    el módulo de configuración, no el de rutas.
    """
    with patch("app.config.session.WEBMAIL_URL", "https://mail.example.com"):
        r = admin_client.get("/api/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["role"] == "admin"
    assert data.get("webmail_url") == "https://mail.example.com"


# ── change_password — campos vacíos (línea 260) ──────────────────────────────

def test_change_password_empty_fields(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "emptypw@test.com", "password": "pass1234"})
    r = client.post("/api/auth/change-password", json={"current_password": "", "new_password": ""})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "all_fields_required"


# ── admin list users — filtro por query (líneas 605-606) ─────────────────────

def test_admin_list_users_filter_q(admin_client):
    _direct_register("qalice_filter")
    _direct_register("qbob_filter")
    r = admin_client.get("/api/admin/users?q=qalice")
    assert r.status_code == 200
    users = r.json()
    usernames = [u["username"] for u in users]
    assert "qalice_filter" in usernames
    assert "qbob_filter" not in usernames


# ── admin list users — filtro por role (línea 608) ───────────────────────────

def test_admin_list_users_filter_role(admin_client):
    _direct_register("role_std_user")
    r = admin_client.get("/api/admin/users?role=standard")
    assert r.status_code == 200
    users = r.json()
    assert all(u.get("role") == "standard" for u in users)
    assert any(u["username"] == "role_std_user" for u in users)


# ── admin list users — filtro por active (líneas 610-611) ─────────────────────

def test_admin_list_users_filter_active_false(admin_client):
    async def _setup():
        from app.auth.auth import register_user
        from app.storage.db import open_db
        await register_user("inactive_filter", "pass1234", email="inact_filt@test.com")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET is_active = 0 WHERE username = ?",
                ("inactive_filter",),
            )
            await conn.commit()

    asyncio.run(_setup())
    r = admin_client.get("/api/admin/users?active=false")
    assert r.status_code == 200
    users = r.json()
    assert any(u["username"] == "inactive_filter" for u in users)
    assert all(u.get("is_active") == 0 for u in users)


# ── admin list users — filtro por verified (líneas 613-614) ──────────────────

def test_admin_list_users_filter_verified_false(admin_client):
    async def _setup():
        from app.auth.auth import register_user
        from app.storage.db import open_db
        await register_user("unverif_filter", "pass1234", email="uvfilt@test.com")
        async with open_db() as conn:
            await conn.execute(
                "UPDATE users SET is_verified = 0 WHERE username = ?",
                ("unverif_filter",),
            )
            await conn.commit()

    asyncio.run(_setup())
    r = admin_client.get("/api/admin/users?verified=false")
    assert r.status_code == 200
    users = r.json()
    assert any(u["username"] == "unverif_filter" for u in users)
    assert all(u.get("is_verified") == 0 for u in users)


# ── admin_patch_user — modificar propia cuenta rechazado (línea 625) ─────────

def test_admin_patch_user_self_rejected(admin_client):
    r = admin_client.patch("/api/admin/users/testadmin", json={"is_active": 0})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "cannot_modify_own_account"


# ── admin_patch_user — cambiar is_active (líneas 629, 644-648) ───────────────

def test_admin_patch_user_is_active_sends_notification(admin_client):
    """Cambiar is_active dispara send_account_status_email si el usuario tiene email."""
    _direct_register("patch_active_tgt")
    with patch("app.api.routes.admin.send_account_status_email") as mock_email:
        r = admin_client.patch("/api/admin/users/patch_active_tgt", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    mock_email.assert_called_once()


# ── admin_patch_user — rol inválido (líneas 631-632) ─────────────────────────

def test_admin_patch_user_invalid_role(admin_client):
    _direct_register("patch_role_tgt")
    r = admin_client.patch("/api/admin/users/patch_role_tgt", json={"role": "superuser"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_field"
    assert detail["field"] == "role"


# ── admin_patch_user — rol válido (línea 633) ────────────────────────────────

def test_admin_patch_user_valid_role(admin_client):
    _direct_register("patch_valid_role")
    r = admin_client.patch("/api/admin/users/patch_valid_role", json={"role": "standard"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── admin_patch_user — usuario no encontrado (línea 640) ─────────────────────

def test_admin_patch_user_not_found(admin_client):
    r = admin_client.patch("/api/admin/users/ghost_patch_user", json={"is_active": 1})
    assert r.status_code == 404


# ── admin_update_agent — agente no encontrado (línea 742) ────────────────────

def test_admin_update_agent_not_found(admin_client):
    r = admin_client.put("/api/admin/agents/nonexistent-agent", json={"name": "Updated"})
    assert r.status_code == 404


# ── admin_update_agent — nombre vacío (línea 748) ────────────────────────────

def test_admin_update_agent_empty_name(admin_client):
    created = admin_client.post("/api/agents", json={"name": "AgentToUpdate"}).json()
    r = admin_client.put(f"/api/admin/agents/{created['id']}", json={"name": ""})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "agent_name_required"


# ── admin_update_agent — renombrar (líneas 749-752) ──────────────────────────

def test_admin_update_agent_rename(admin_client):
    created = admin_client.post("/api/agents", json={"name": "OldAgentName"}).json()
    r = admin_client.put(f"/api/admin/agents/{created['id']}", json={"name": "New Agent Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Agent Name"


# ── admin_delete_agent — scope inválido (línea 762) ──────────────────────────

def test_admin_delete_agent_invalid_scope(admin_client):
    r = admin_client.delete("/api/admin/agents/some-agent?scope=all")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_field"
    assert detail["field"] == "scope"


# ── admin_list_knowledge — con ítems (líneas 779-780) ────────────────────────

def test_admin_list_knowledge_with_items(admin_client):
    """Con ítems en la BD se ejercita la asignación de owner_email y pop de content."""
    admin_client.post("/api/knowledge/text", json={
        "title": "Test Knowledge Item", "content": "Contenido de prueba"
    })
    r = admin_client.get("/api/admin/knowledge")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    for item in items:
        assert "content" not in item
        assert "owner_email" in item


# ── admin_delete_knowledge — no encontrado (línea 791) ───────────────────────

def test_admin_delete_knowledge_not_found(admin_client):
    r = admin_client.delete("/api/admin/knowledge/nonexistent-knowledge-id")
    assert r.status_code == 404


# ── admin_delete_knowledge — éxito (línea 792) ───────────────────────────────

def test_admin_delete_knowledge_ok(admin_client):
    created = admin_client.post("/api/knowledge/text", json={
        "title": "To Delete", "content": "Contenido para borrar"
    }).json()
    r = admin_client.delete(f"/api/admin/knowledge/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── admin_list_groups (líneas 829-878) ───────────────────────────────────

def test_admin_list_groups(admin_client):
    r = admin_client.get("/api/admin/groups")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_list_groups_has_expected_fields(admin_client):
    admin_client.post("/api/groups", json={"name": "AdminWS Fields"})
    r = admin_client.get("/api/admin/groups")
    assert r.status_code == 200
    groups = r.json()
    assert len(groups) >= 1
    group = groups[0]
    for field in ("id", "name", "created_by", "member_count", "connections_count", "tokens_in", "tokens_out"):
        assert field in group, f"Campo '{field}' ausente en group"


# ── admin_delete_group — no encontrado (línea 886) ───────────────────────

def test_admin_delete_group_not_found(admin_client):
    r = admin_client.delete("/api/admin/groups/nonexistent-group-id")
    assert r.status_code == 404


# ── admin_delete_group — éxito (líneas 887-888) ──────────────────────────

def test_admin_delete_group_ok(admin_client):
    group_r = admin_client.post("/api/groups", json={"name": "Grupo To Delete"})
    assert group_r.status_code == 200
    group_id = group_r.json()["id"]
    r = admin_client.delete(f"/api/admin/groups/{group_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── admin_impersonate — impersonar propia cuenta (línea 930) ─────────────────

def test_admin_impersonate_self_rejected(admin_client):
    r = admin_client.post("/api/admin/impersonate/testadmin")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "already_own_user"


# ── admin_impersonate — usuario no encontrado (línea 932) ────────────────────

def test_admin_impersonate_not_found(admin_client):
    r = admin_client.post("/api/admin/impersonate/ghost_impersonate_user")
    assert r.status_code == 404


# ── admin_impersonate — éxito (líneas 933-935) ───────────────────────────────

def test_admin_impersonate_ok(admin_client):
    _direct_register("impersonate_target")
    r = admin_client.post("/api/admin/impersonate/impersonate_target")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["username"] == "impersonate_target"
    assert "ga_token" in r.cookies


# ── admin_stats — backfill token_daily (líneas 540-561) ──────────────────────

def test_admin_stats_with_token_backfill(admin_client):
    """Si hay conexiones con tokens pero no hay token_daily, se hace backfill."""
    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage

    asyncio.run(ConnectionStorage(DB_FILE).save(
        {"type": "openai", "label": "backfill-conn", "api_key": "sk-x", "model": "gpt-4o"},
        owner_id="testadmin",
    ))

    async def _add_tokens():
        from app.storage.db import open_db
        async with open_db() as conn:
            await conn.execute(
                "UPDATE connections SET tokens_in = 200, tokens_out = 100 WHERE owner_id = ?",
                ("testadmin",),
            )
            await conn.commit()

    asyncio.run(_add_tokens())
    r = admin_client.get("/api/admin/stats")
    assert r.status_code == 200
    stats = r.json()
    assert "tokens_daily" in stats
    assert isinstance(stats["tokens_daily"], list)


# ── admin_list_agents — agente con conexión (líneas 726-727) ─────────────────

def test_admin_list_agents_with_connection_tokens(admin_client):
    """Agente con connection_id → tokens_in / tokens_out se rellenan desde la conexión."""
    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage

    conn_data = asyncio.run(ConnectionStorage(DB_FILE).save(
        {"type": "openai", "label": "agent-conn", "api_key": "sk-z", "model": "gpt-4o"},
        owner_id="testadmin",
    ))
    conn_id = conn_data["id"]

    admin_client.post("/api/agents", json={"name": "Agent With Connection", "connection_id": conn_id})
    r = admin_client.get("/api/admin/agents")
    assert r.status_code == 200
    agents = r.json()
    connected = [a for a in agents if a.get("connection_id") == conn_id]
    assert connected, "No se encontró el agente con connection_id"
    assert "tokens_in" in connected[0]
    assert "tokens_out" in connected[0]
