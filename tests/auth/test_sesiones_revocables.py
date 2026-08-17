"""Las sesiones se pueden cerrar de verdad — punto 06 de la revisión.

Antes de esto el JWT vivía 12 h y no había forma de invalidarlo salvo cambiar la
contraseña: cerrar sesión borraba las cookies y devolvía un token perfectamente
válido al atacante que ya lo tuviera. Lo que fija este fichero es que la
credencial deja de servir en el instante en que la sesión se cierra, por
cualquiera de los caminos que la cierran.

Ver docs/adr/008-sesiones-revocables.md.
"""

from __future__ import annotations

import asyncio

import pytest

from app.auth.passwords import decode_claims
from app.config.session import REFRESH_COOKIE, REFRESH_COOKIE_PATH


def _registrar(client, email: str, password: str = "pass1234") -> str:
    username = email.split("@", 1)[0]
    r = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return username


def _login(client, email: str, password: str = "pass1234"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


def _sid(client) -> str:
    claims = decode_claims(client.cookies.get("ga_token"), allow_expired=True)
    assert claims is not None
    return claims.session_id


# ── El token lleva sesión y la sesión se comprueba ────────────────────────────


def test_el_login_emite_un_token_con_sesion(client, reset_rate_limiter):
    _registrar(client, "sesion@example.com")
    _login(client, "sesion@example.com")
    assert _sid(client), "el access no lleva claim `sid`: la sesión no es revocable"
    assert client.cookies.get(REFRESH_COOKIE), "no se emitió la cookie de refresh"


def test_logout_invalida_el_token_que_el_cliente_conserva(client, reset_rate_limiter):
    """El caso que da nombre al punto: cerrar sesión y reusar el token robado."""
    _registrar(client, "robado@example.com")
    _login(client, "robado@example.com")
    token = client.cookies.get("ga_token")
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/logout").status_code == 200

    # El cliente vuelve a presentar el MISMO token, como haría quien lo copió.
    client.cookies.set("ga_token", token)
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "session_revoked"


def test_logout_borra_tambien_la_cookie_de_refresh(client, reset_rate_limiter):
    """Con el refresh vivo, cerrar sesión no cerraría nada: se renueva y vuelve."""
    _registrar(client, "refrescado@example.com")
    _login(client, "refrescado@example.com")
    client.post("/api/auth/logout")
    assert not client.cookies.get(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


# ── Renovación ────────────────────────────────────────────────────────────────


def test_el_refresh_renueva_el_access_y_rota_el_refresh(client, reset_rate_limiter):
    _registrar(client, "rotado@example.com")
    _login(client, "rotado@example.com")
    refresh_viejo = client.cookies.get(REFRESH_COOKIE)
    sid = _sid(client)

    r = client.post("/api/auth/refresh")
    assert r.status_code == 200, r.text
    assert client.cookies.get(REFRESH_COOKIE) != refresh_viejo, "no rotó"
    assert _sid(client) == sid, "renovar no debe abrir una sesión nueva"
    assert client.get("/api/auth/me").status_code == 200


def test_reusar_un_refresh_ya_rotado_tumba_la_sesion(client, reset_rate_limiter):
    """Dos clientes con el mismo refresh: uno lo robó y no se sabe cuál."""
    _registrar(client, "reusado@example.com")
    _login(client, "reusado@example.com")
    refresh_viejo = client.cookies.get(REFRESH_COOKIE)
    client.post("/api/auth/refresh")

    client.cookies.set(REFRESH_COOKIE, refresh_viejo, path=REFRESH_COOKIE_PATH)
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "session_revoked"


def test_el_refresh_conserva_el_grupo_activo(client, reset_rate_limiter):
    """Sin esto, renovar devolvería al usuario a su espacio personal a media sesión."""
    username = _registrar(client, "congrupo@example.com")
    _login(client, "congrupo@example.com")
    uid = decode_claims(client.cookies.get("ga_token")).username

    r = client.post("/api/groups", json={"name": "Equipo"})
    assert r.status_code in (200, 201), r.text
    group_id = r.json().get("id") or r.json().get("group_id")
    assert client.post(f"/api/groups/switch/{group_id}").status_code == 200
    assert decode_claims(client.cookies.get("ga_token")).group_id == group_id

    client.post("/api/auth/refresh")
    claims = decode_claims(client.cookies.get("ga_token"))
    assert claims.group_id == group_id, "la renovación perdió el grupo activo"
    assert claims.username == uid
    assert username  # el registro se hizo con el nombre esperado


def test_cambiar_de_grupo_no_abre_una_sesion_nueva(client, reset_rate_limiter):
    """Si abriera una, el perfil acumularía una fila por cada cambio de grupo."""
    _registrar(client, "mismogrupo@example.com")
    sid = _sid(client)
    uid = decode_claims(client.cookies.get("ga_token")).username
    antes = len(client.get("/api/auth/sessions").json()["sessions"])

    client.post(f"/api/groups/switch/{uid}")
    assert _sid(client) == sid
    assert len(client.get("/api/auth/sessions").json()["sessions"]) == antes


# ── Listado y cierre selectivo ────────────────────────────────────────────────


@pytest.fixture()
def dos_sesiones(client, reset_rate_limiter):
    """Dos sesiones del mismo usuario, en dos clientes distintos."""
    from fastapi.testclient import TestClient

    _registrar(client, "dosses@example.com")
    client.post("/api/auth/logout")  # el registro ya abrió una: sobra para contar
    _login(client, "dosses@example.com")
    otro = TestClient(client.app)
    _login(otro, "dosses@example.com")
    return client, otro


def test_el_listado_marca_la_sesion_actual(dos_sesiones):
    client, _otro = dos_sesiones
    sesiones = client.get("/api/auth/sessions").json()["sessions"]
    assert len(sesiones) == 2
    actuales = [s for s in sesiones if s["current"]]
    assert len(actuales) == 1
    assert actuales[0]["id"] == _sid(client)


def test_el_listado_no_filtra_el_refresh(dos_sesiones):
    """La credencial que renueva la sesión no puede salir por la API."""
    client, _otro = dos_sesiones
    for s in client.get("/api/auth/sessions").json()["sessions"]:
        assert "refresh_hash" not in s
        assert "prev_refresh_hash" not in s


def test_cerrar_las_demas_conserva_la_propia(dos_sesiones):
    client, otro = dos_sesiones
    assert client.delete("/api/auth/sessions").status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert otro.get("/api/auth/me").status_code == 401


def test_cerrar_una_sesion_concreta(dos_sesiones):
    client, otro = dos_sesiones
    ajena = _sid(otro)
    assert client.delete(f"/api/auth/sessions/{ajena}").status_code == 200
    assert otro.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200


def test_nadie_cierra_la_sesion_de_otro(client, reset_rate_limiter):
    from fastapi.testclient import TestClient

    _registrar(client, "victima@example.com")
    _login(client, "victima@example.com")
    victima_sid = _sid(client)

    atacante = TestClient(client.app)
    _registrar(atacante, "atacante@example.com")
    _login(atacante, "atacante@example.com")

    r = atacante.delete(f"/api/auth/sessions/{victima_sid}")
    assert r.status_code == 404
    assert client.get("/api/auth/me").status_code == 200


# ── Revocación desde los otros caminos ────────────────────────────────────────


def test_cambiar_la_contrasena_cierra_las_sesiones(dos_sesiones):
    """El `iat` ya invalidaba el access; el refresh no pasaba por ahí."""
    client, otro = dos_sesiones
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "pass1234", "new_password": "otraclave9"},
    )
    assert r.status_code == 200, r.text

    assert otro.get("/api/auth/me").status_code == 401
    assert otro.post("/api/auth/refresh").status_code == 401


def test_desactivar_la_cuenta_cierra_las_sesiones_ya(admin_client):
    """No en ≤60 s cuando caduque la caché de rol: ahora, y sin dejar refresh."""
    from fastapi.testclient import TestClient

    # Cliente aparte: `admin_client` es el MISMO TestClient que `client`, y un
    # login aquí pisaría la cookie del admin.
    usuario = TestClient(admin_client.app)
    _registrar(usuario, "suspendido@example.com")
    _login(usuario, "suspendido@example.com")

    r = admin_client.patch(
        "/api/admin/users/suspendido", json={"is_active": False}
    )
    assert r.status_code == 200, r.text

    r = usuario.post("/api/auth/refresh")
    assert r.status_code == 401, "el refresh sobrevivió a la desactivación"


def test_el_borrado_rgpd_se_lleva_las_sesiones(client, reset_rate_limiter):
    _registrar(client, "borrado@example.com")
    _login(client, "borrado@example.com")
    uid = decode_claims(client.cookies.get("ga_token")).username

    from app.auth.gdpr import purge_user_data
    from app.storage.db import open_db

    asyncio.run(purge_user_data(uid))

    async def _quedan() -> int:
        async with open_db() as conn:
            filas = await conn.fetchall(
                "SELECT id FROM sessions WHERE user_id = ?", (uid,)
            )
        return len(filas)

    assert asyncio.run(_quedan()) == 0


# ── Compatibilidad ────────────────────────────────────────────────────────────


def test_un_token_sin_sesion_sigue_valiendo(client):
    """Los tokens emitidos antes de que existiera la tabla no se rechazan.

    Rechazarlos habría echado de golpe a todos los usuarios con sesión abierta
    en el despliegue. La ventana se cierra sola cuando caducan; el día que se
    quiera cerrar a mano, este test es el que hay que cambiar.
    """
    from app.auth.auth import register_user
    from app.auth.passwords import create_token, derive_csrf_token

    asyncio.run(register_user("sinsid", "pass1234", email="sinsesion@example.com"))
    token = create_token("sinsid")
    assert decode_claims(token).session_id is None
    client.cookies.set("ga_token", token)
    client.cookies.set("ga_csrf", derive_csrf_token(token))

    assert client.get("/api/auth/me").status_code == 200


def test_el_invitado_tambien_tiene_sesion_revocable(client, reset_rate_limiter):
    """El demo abre sesión como todos: si no, su logout no cerraría nada."""
    assert client.post("/api/auth/guest").status_code == 200
    token = client.cookies.get("ga_token")
    assert _sid(client)

    client.post("/api/auth/logout")
    client.cookies.set("ga_token", token)
    assert client.get("/api/auth/me").status_code == 401
