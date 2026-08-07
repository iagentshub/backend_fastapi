"""Tests de los personal access tokens (PAT) y de la paridad cookie ↔ Bearer.

El test que justifica todo el diseño es `test_paridad_cookie_bearer_*`: un PAT
debe abrir exactamente las mismas puertas que la cookie de sesión, porque
require_auth/require_group son las dos únicas funciones que leen la
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


def _user_id(username: str) -> str:
    from app.auth.auth import get_user_by_username
    user = asyncio.run(get_user_by_username(username))
    assert user is not None
    return str(user["id"])


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
    from app.api.routes.auth.dependencies import _active_cache

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
    user_id = _user_id("pat_nostore")

    async def _fetch():
        async with open_db() as conn:
            return await conn.fetchone(
                "SELECT * FROM personal_access_tokens WHERE username = ?",
                (user_id,),
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


def test_paridad_cookie_bearer_en_require_group(client):
    """/api/agents cuelga de require_group, no de require_auth."""
    _register(client, "pat_groups")
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
    user_id = _user_id("pat_exp")

    async def _expire():
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE personal_access_tokens SET expires_at = ? WHERE username = ?",
                    (ayer, user_id),
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


# ── Cabecera X-iAgents-Group ──────────────────────────────────────────────


def test_group_ajeno_cae_al_personal(client):
    """El test de seguridad de la cabecera: pedir un group del que no eres
    miembro NO debe darte acceso a él."""
    _register(client, "pat_owner")
    group_id = client.post("/api/groups", json={"name": "Equipo privado"}).json()["id"]

    _register(client, "pat_intruso")
    token = _make_token(client)
    client.cookies.clear()

    r = client.get(
        "/api/agents",
        headers={**_bearer(token), "X-iAgents-Group": group_id},
    )
    # No es miembro → fallback al group personal, nunca 200 sobre el ajeno
    assert r.status_code == 200

    from app.api.routes.auth import require_group

    async def _ctx():
        return await require_group(
            ga_token=None,
            authorization=f"Bearer {token}",
            x_iagents_group=group_id,
        )

    ctx = asyncio.run(_ctx())
    assert ctx.group_id == _user_id("pat_intruso")  # el suyo, no el ajeno


def test_group_inexistente_cae_al_personal(client):
    _register(client, "pat_groupghost")
    token = _make_token(client)

    from app.api.routes.auth import require_group

    async def _ctx():
        return await require_group(
            ga_token=None,
            authorization=f"Bearer {token}",
            x_iagents_group="no-existe",
        )

    ctx = asyncio.run(_ctx())
    assert ctx.group_id == _user_id("pat_groupghost")


def test_group_propio_se_respeta(client):
    _register(client, "pat_groupok")
    group_id = client.post("/api/groups", json={"name": "Mi equipo"}).json()["id"]
    token = _make_token(client)

    from app.api.routes.auth import require_group

    async def _ctx():
        return await require_group(
            ga_token=None,
            authorization=f"Bearer {token}",
            x_iagents_group=group_id,
        )

    ctx = asyncio.run(_ctx())
    assert ctx.group_id == group_id


def test_sin_cabecera_usa_el_group_personal(client):
    _register(client, "pat_groupdefault")
    token = _make_token(client)

    from app.api.routes.auth import require_group

    async def _ctx():
        return await require_group(
            ga_token=None, authorization=f"Bearer {token}", x_iagents_group=None
        )

    ctx = asyncio.run(_ctx())
    assert ctx.group_id == _user_id("pat_groupdefault")


def test_invitado_no_puede_crear_tokens(client):
    """Las sesiones de invitado viven en memoria y son efímeras: un PAT
    permanente colgando de una de ellas no tendría a quién pertenecer."""
    client.cookies.clear()
    assert client.post("/api/auth/guest").status_code == 200

    r = client.post("/api/auth/tokens", json={"name": "de invitado"})
    assert r.status_code == 403


# ── Login de VS Code ──────────────────────────────────────────────────────────

_STATE = "estado-de-prueba-123"
_CALLBACK = "vscode://iagentshub.iagentshub/auth"


def _authorize(client: TestClient, state: str = _STATE) -> str:
    r = client.post("/api/auth/vscode/authorize", json={"state": state})
    assert r.status_code == 200, r.text
    return r.json()["code"]


def test_vscode_flujo_completo(client):
    _register(client, "vsc_ok")
    code = _authorize(client)

    client.cookies.clear()  # la extensión no tiene cookie: solo código + state
    r = client.post("/api/auth/vscode/exchange", json={"code": code, "state": _STATE})
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["token"].startswith("iah_")
    assert data["username"] == "vsc_ok"

    # El PAT emitido abre las mismas puertas que cualquier otro
    me = client.get("/api/auth/me", headers=_bearer(data["token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "vsc_ok"

    # …y aparece en Perfil → Tokens, revocable como los demás
    _register(client, "vsc_ok")
    tokens = client.get("/api/auth/tokens").json()
    assert [t["name"] for t in tokens] == ["VS Code"]
    assert tokens[0]["id"] == data["token_id"]


def test_vscode_el_codigo_es_de_un_solo_uso(client):
    """Un código canjeado dos veces sería un token robable con solo repetir la
    petición que ya viajó por el manejador de URIs del sistema."""
    _register(client, "vsc_reuse")
    code = _authorize(client)
    client.cookies.clear()

    body = {"code": code, "state": _STATE}
    assert client.post("/api/auth/vscode/exchange", json=body).status_code == 200
    assert client.post("/api/auth/vscode/exchange", json=body).status_code == 400


def test_vscode_state_que_no_casa_no_canjea(client):
    """El state solo existe en la memoria de la extensión que abrió el navegador:
    sin él, tener el código no sirve de nada."""
    _register(client, "vsc_state")
    code = _authorize(client)
    client.cookies.clear()

    r = client.post(
        "/api/auth/vscode/exchange", json={"code": code, "state": "otro-state-distinto"}
    )
    assert r.status_code == 400

    # Y el intento fallido quema el código: no hay segunda oportunidad.
    r = client.post("/api/auth/vscode/exchange", json={"code": code, "state": _STATE})
    assert r.status_code == 400


def test_vscode_codigo_caducado(client):
    _register(client, "vsc_exp")
    code = _authorize(client)

    from app.storage.db import open_db

    ayer = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    user_id = _user_id("vsc_exp")

    async def _expire():
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE vscode_auth_codes SET expires_at = ? WHERE username = ?",
                    (ayer, user_id),
                )

    asyncio.run(_expire())

    client.cookies.clear()
    r = client.post("/api/auth/vscode/exchange", json={"code": code, "state": _STATE})
    assert r.status_code == 400


def test_vscode_codigo_inexistente(client):
    client.cookies.clear()
    r = client.post(
        "/api/auth/vscode/exchange", json={"code": "no-existe", "state": _STATE}
    )
    assert r.status_code == 400


def test_vscode_authorize_requiere_sesion(client):
    client.cookies.clear()
    r = client.post("/api/auth/vscode/authorize", json={"state": _STATE})
    assert r.status_code == 401


def test_vscode_invitado_no_puede_conectar(client):
    client.cookies.clear()
    assert client.post("/api/auth/guest").status_code == 200
    r = client.post("/api/auth/vscode/authorize", json={"state": _STATE})
    assert r.status_code == 403


def test_vscode_start_redirige_a_la_pantalla_de_autorizacion(client):
    r = client.get(
        "/api/auth/vscode/start",
        params={"state": _STATE, "callback": _CALLBACK},
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert "/vscode-auth/" in location
    assert _STATE in location


def test_vscode_start_rechaza_callbacks_ajenos(client):
    """Sin lista blanca, /vscode/start sería un redirector abierto: bastaría con
    colar un esquema propio para llevarse al usuario y sus parámetros."""
    for callback in (
        "https://malo.example.com/robar",
        "vscode://otra.extension/auth",
        "javascript:alert(1)",
        "vscode-fake://iagentshub.iagentshub/auth",
    ):
        r = client.get(
            "/api/auth/vscode/start",
            params={"state": _STATE, "callback": callback},
            follow_redirects=False,
        )
        assert r.status_code == 400, callback
