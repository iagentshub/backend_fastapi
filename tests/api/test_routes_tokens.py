"""Tests de los personal access tokens (PAT) y de la paridad cookie ↔ Bearer.

El test que justifica todo el diseño es `test_paridad_cookie_bearer_*`: un PAT
debe abrir exactamente las mismas puertas que la cookie de sesión, porque
require_auth/require_workspace son las dos únicas funciones que leen la
credencial y de ellas cuelgan los ~164 Depends() del resto de routers.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def _register(client: TestClient, username: str) -> TestClient:
    """Registra un usuario y deja su cookie de sesión puesta en el client."""
    from app.auth.auth import create_token, register_user

    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass
    client.cookies.set("ga_token", create_token(username))
    return client


def _make_token(client: TestClient, name: str = "vscode", **body) -> str:
    r = client.post("/api/auth/tokens", json={"name": name, **body})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _clear_auth_cache() -> None:
    """_get_user_auth_state cachea (is_active, password_changed_at) durante 60 s.

    Sin limpiarlo, un test que cambia la contraseña seguiría leyendo el valor
    viejo y verificaría el caché en lugar de la regla de invalidación.
    """
    from app.api.routes.auth import _active_cache

    _active_cache.clear()


# ── Creación ──────────────────────────────────────────────────────────────────


def test_crear_token_devuelve_el_secreto_una_vez(client):
    _register(client, "pat_create")
    r = client.post("/api/auth/tokens", json={"name": "mi portátil"})
    assert r.status_code == 200
    data = r.json()

    assert data["token"].startswith("iah_")
    assert data["name"] == "mi portátil"
    assert data["status"] == "active"
    assert data["prefix"] == data["token"][:12]
    assert data["expires_at"] is not None  # default 90 días


def test_listar_nunca_devuelve_el_secreto(client):
    _register(client, "pat_list")
    token = _make_token(client)

    r = client.get("/api/auth/tokens")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1

    serialized = repr(items)
    assert token not in serialized
    assert "token_hash" not in serialized
    assert "token" not in items[0]
    assert items[0]["prefix"] == token[:12]


def test_el_secreto_no_se_persiste_en_bd(client):
    """Quien lea la tabla no debe poder autenticarse con lo que encuentre."""
    _register(client, "pat_nostore")
    token = _make_token(client)

    from app.storage.db import open_db
    from app.storage.tokens import hash_token

    async def _fetch():
        async with open_db() as conn:
            return await conn.fetchone(
                "SELECT * FROM personal_access_tokens WHERE username = ?",
                ("pat_nostore",),
            )

    row = dict(asyncio.run(_fetch()))
    assert token not in repr(row)
    assert row["token_hash"] == hash_token(token)


def test_sin_caducidad_cuando_expires_in_days_es_null(client):
    _register(client, "pat_noexp")
    r = client.post(
        "/api/auth/tokens", json={"name": "eterno", "expires_in_days": None}
    )
    assert r.status_code == 200
    assert r.json()["expires_at"] is None


def test_expires_in_days_invalido(client):
    _register(client, "pat_badexp")
    r = client.post("/api/auth/tokens", json={"name": "x", "expires_in_days": 7})
    assert r.status_code == 400


def test_nombre_obligatorio(client):
    _register(client, "pat_noname")
    assert client.post("/api/auth/tokens", json={"name": "  "}).status_code == 400


def test_crear_token_requiere_sesion(client):
    client.cookies.clear()
    assert client.post("/api/auth/tokens", json={"name": "x"}).status_code == 401


# ── Paridad cookie ↔ Bearer ───────────────────────────────────────────────────


def test_paridad_cookie_bearer_en_me(client):
    _register(client, "pat_parity")
    token = _make_token(client)

    client.cookies.clear()  # sin cookie: solo el Bearer puede autenticar
    r = client.get("/api/auth/me", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["username"] == "pat_parity"


def test_paridad_cookie_bearer_en_require_workspace(client):
    """/api/agents cuelga de require_workspace, no de require_auth."""
    _register(client, "pat_ws")
    token = _make_token(client)

    con_cookie = client.get("/api/agents")
    assert con_cookie.status_code == 200

    client.cookies.clear()
    con_bearer = client.get("/api/agents", headers=_bearer(token))
    assert con_bearer.status_code == 200
    assert con_bearer.json() == con_cookie.json()


def test_bearer_tiene_prioridad_sobre_la_cookie(client):
    """Si el cliente manda un Bearer explícito, es la credencial que quiere usar."""
    _register(client, "pat_prio_a")
    token_a = _make_token(client)

    _register(client, "pat_prio_b")  # la cookie pasa a ser de B
    r = client.get("/api/auth/me", headers=_bearer(token_a))
    assert r.status_code == 200
    assert r.json()["username"] == "pat_prio_a"


def test_sin_credencial_es_401(client):
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_bearer_basura_es_401(client):
    client.cookies.clear()
    r = client.get("/api/auth/me", headers=_bearer("iah_noexiste"))
    assert r.status_code == 401


def test_bearer_sin_el_prefijo_es_401(client):
    client.cookies.clear()
    r = client.get("/api/auth/me", headers=_bearer("token-de-otro-formato"))
    assert r.status_code == 401


# ── Revocación y caducidad ────────────────────────────────────────────────────


def test_revocar_invalida_el_token(client):
    _register(client, "pat_revoke")
    token = _make_token(client)
    token_id = client.get("/api/auth/tokens").json()[0]["id"]

    assert client.delete(f"/api/auth/tokens/{token_id}").status_code == 200
    assert client.get("/api/auth/tokens").json()[0]["status"] == "revoked"

    client.cookies.clear()
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 401


def test_no_se_puede_revocar_un_token_ajeno(client):
    _register(client, "pat_victima")
    _make_token(client)
    token_id = client.get("/api/auth/tokens").json()[0]["id"]

    _register(client, "pat_atacante")
    assert client.delete(f"/api/auth/tokens/{token_id}").status_code == 404

    # El token de la víctima sigue vivo
    _register(client, "pat_victima")
    assert client.get("/api/auth/tokens").json()[0]["status"] == "active"


def test_token_caducado_es_401(client):
    _register(client, "pat_exp")
    token = _make_token(client)

    from app.storage.db import open_db

    ayer = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    async def _expire():
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE personal_access_tokens SET expires_at = ? WHERE username = ?",
                    (ayer, "pat_exp"),
                )

    asyncio.run(_expire())

    client.cookies.clear()
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 401
    _register(client, "pat_exp")
    assert client.get("/api/auth/tokens").json()[0]["status"] == "expired"


def test_last_used_at_se_registra(client):
    _register(client, "pat_lastused")
    token = _make_token(client)
    assert client.get("/api/auth/tokens").json()[0]["last_used_at"] is None

    client.cookies.clear()
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 200

    _register(client, "pat_lastused")
    assert client.get("/api/auth/tokens").json()[0]["last_used_at"] is not None


def test_cambiar_la_contrasena_invalida_los_tokens_anteriores(client):
    """Un PAT es una credencial de largo recorrido: si te roban la cuenta y
    cambias la contraseña, los tokens que dejó el atacante deben morir."""
    _register(client, "pat_pwd")
    token = _make_token(client)

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "pass1234", "new_password": "nuevapass5678"},
    )
    assert r.status_code == 200
    _clear_auth_cache()

    client.cookies.clear()
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 401


def test_cuenta_desactivada_invalida_el_token(client):
    _register(client, "pat_inactive")
    token = _make_token(client)

    from app.storage.db import open_db

    async def _deactivate():
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE users SET is_active = 0 WHERE username = ?", ("pat_inactive",)
                )

    asyncio.run(_deactivate())
    _clear_auth_cache()

    client.cookies.clear()
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 403


# ── Cabecera X-iAgents-Workspace ──────────────────────────────────────────────


def test_workspace_ajeno_cae_al_personal(client):
    """El test de seguridad de la cabecera: pedir un workspace del que no eres
    miembro NO debe darte acceso a él."""
    _register(client, "pat_owner")
    ws_id = client.post("/api/workspaces", json={"name": "Equipo privado"}).json()["id"]

    _register(client, "pat_intruso")
    token = _make_token(client)
    client.cookies.clear()

    r = client.get(
        "/api/agents",
        headers={**_bearer(token), "X-iAgents-Workspace": ws_id},
    )
    # No es miembro → fallback al workspace personal, nunca 200 sobre el ajeno
    assert r.status_code == 200

    from app.api.routes.auth import require_workspace

    async def _ctx():
        return await require_workspace(
            ga_token=None,
            authorization=f"Bearer {token}",
            x_iagents_workspace=ws_id,
        )

    ctx = asyncio.run(_ctx())
    assert ctx.workspace_id == "pat_intruso"  # el suyo, no el ajeno


def test_workspace_inexistente_cae_al_personal(client):
    _register(client, "pat_wsghost")
    token = _make_token(client)

    from app.api.routes.auth import require_workspace

    async def _ctx():
        return await require_workspace(
            ga_token=None,
            authorization=f"Bearer {token}",
            x_iagents_workspace="no-existe",
        )

    ctx = asyncio.run(_ctx())
    assert ctx.workspace_id == "pat_wsghost"


def test_workspace_propio_se_respeta(client):
    _register(client, "pat_wsok")
    ws_id = client.post("/api/workspaces", json={"name": "Mi equipo"}).json()["id"]
    token = _make_token(client)

    from app.api.routes.auth import require_workspace

    async def _ctx():
        return await require_workspace(
            ga_token=None,
            authorization=f"Bearer {token}",
            x_iagents_workspace=ws_id,
        )

    ctx = asyncio.run(_ctx())
    assert ctx.workspace_id == ws_id


def test_sin_cabecera_usa_el_workspace_personal(client):
    _register(client, "pat_wsdefault")
    token = _make_token(client)

    from app.api.routes.auth import require_workspace

    async def _ctx():
        return await require_workspace(
            ga_token=None, authorization=f"Bearer {token}", x_iagents_workspace=None
        )

    ctx = asyncio.run(_ctx())
    assert ctx.workspace_id == "pat_wsdefault"


def test_invitado_no_puede_crear_tokens(client):
    """Las sesiones de invitado viven en memoria y son efímeras: un PAT
    permanente colgando de una de ellas no tendría a quién pertenecer."""
    client.cookies.clear()
    assert client.post("/api/auth/guest").status_code == 200

    r = client.post("/api/auth/tokens", json={"name": "de invitado"})
    assert r.status_code == 403
