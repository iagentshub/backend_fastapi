"""El invitado se borra entero al cerrar sesión, y no se le ve mientras existe.

Las dos mitades del contrato de la demo. La primera es la que el modelo
anterior no cumplía: el logout revocaba la sesión pero dejaba la GuestSession
viva en el proceso hasta agotar su TTL. La segunda es nueva por construcción —
antes el invitado no tenía fila en `users`, así que no había dónde asomarse.
"""

from __future__ import annotations

from app.auth.user_lookup import get_user_by_identity


def _abrir_invitado(client) -> str:
    r = client.post("/api/auth/guest")
    assert r.status_code == 200, r.text
    return r.json()["username"]


def test_el_logout_borra_al_invitado_y_lo_suyo(client):
    guest_id = _abrir_invitado(client)

    r = client.post(
        "/api/agents",
        json={"name": "efímero", "system_prompt": "x", "model": "gpt-4o"},
    )
    assert r.status_code in (200, 201), r.text
    assert any(
        a["name"] == "efímero"
        for a in client.get("/api/v2/agents").json()["items"]
    )

    assert client.post("/api/auth/logout").status_code == 200

    # Ni el usuario ni su agente sobreviven.
    import asyncio

    from app.config.data import AGENTS_DIR
    from app.storage.agent_storage import AgentStorage

    async def _comprobar():
        assert await get_user_by_identity(guest_id) is None
        assert await AgentStorage(AGENTS_DIR).list("private", owner_id=guest_id) == []

    asyncio.run(_comprobar())


def test_el_trabajo_del_invitado_sobrevive_entre_peticiones(client):
    """El fallo del punto 22: con varios workers el agente recién creado
    'desaparecía' en la siguiente petición. Ahora está en la BD, que los
    workers comparten."""
    _abrir_invitado(client)
    r = client.post(
        "/api/agents",
        json={"name": "persistente", "system_prompt": "x", "model": "gpt-4o"},
    )
    agent_id = r.json()["id"]

    for _ in range(3):
        assert client.get(f"/api/agents/{agent_id}").status_code == 200


def test_dos_invitados_no_se_ven_entre_si(client):
    _abrir_invitado(client)
    client.post(
        "/api/agents",
        json={"name": "del primero", "system_prompt": "x", "model": "gpt-4o"},
    )
    client.post("/api/auth/logout")

    _abrir_invitado(client)
    nombres = [a["name"] for a in client.get("/api/v2/agents").json()["items"]]
    assert "del primero" not in nombres


def test_el_invitado_purgado_no_puede_seguir_operando(client):
    """La cookie sigue en el navegador; el invitado ya no existe.

    Lo corta la sesión: `purge_user_data` borra también su fila de `sessions` y
    `_assert_session_live` responde 401 antes de mirar nada más. Es el camino
    normal —purga y sesión se van juntas—; el de abajo cubre el otro.
    """
    import asyncio

    from app.auth.gdpr import purge_user_data

    guest_id = _abrir_invitado(client)
    assert client.get("/api/v2/agents").status_code == 200

    asyncio.run(purge_user_data(guest_id))

    r = client.get("/api/v2/agents")
    assert r.status_code == 401, r.text


def test_un_invitado_sin_fila_no_pasa_aunque_su_sesion_siga_viva(client):
    """El atajo que se retiró de `_resolve_principal`.

    Devolvía la identidad del invitado sin leer su fila, así que con el invitado
    en la BD dejaba operar a uno ya borrado. Aquí se borra **solo** la fila de
    `users` para que la sesión no sea la que corta: si el atajo vuelve, la
    petición responde 200 y este test lo dice.
    """
    import asyncio

    from app.storage.db import PH, open_db

    guest_id = _abrir_invitado(client)
    assert client.get("/api/v2/agents").status_code == 200

    async def _borrar_solo_el_usuario() -> None:
        async with open_db() as conn:
            await conn.execute(f"DELETE FROM users WHERE id={PH}", (guest_id,))
            await conn.commit()

    asyncio.run(_borrar_solo_el_usuario())

    r = client.get("/api/v2/agents")
    assert r.status_code == 401, r.text


def test_el_invitado_no_aparece_como_usuario(admin_client):
    """El alta va por la BD y no por la ruta: abrirla desde este cliente
    pisaría la cookie del admin, que es la credencial que el test necesita."""
    import asyncio

    from app.storage.guest import create_guest_user

    guest_id = asyncio.run(create_guest_user())

    filas = admin_client.get("/api/v2/admin/explore?type=user&limit=100").json()["items"]
    assert all(u.get("username") != guest_id for u in filas)

    # Tampoco tiene perfil público ni sale en el buscador de personas.
    assert admin_client.get(f"/api/users/{guest_id}").status_code == 404


def test_el_invitado_no_sale_en_el_buscador_ni_en_las_estadisticas(admin_client):
    """Las dos consultas que cuentan usuarios sin pasar por el listado."""
    import asyncio

    from app.storage.guest import create_guest_user

    antes = admin_client.get("/api/admin/stats").json()["users_total"]
    guest_id = asyncio.run(create_guest_user())

    encontrados = admin_client.get("/api/v2/users", params={"q": "guest"}).json()["items"]
    filas = encontrados["items"] if isinstance(encontrados, dict) else encontrados
    assert all(u.get("username") != guest_id for u in filas)

    despues = admin_client.get("/api/admin/stats").json()["users_total"]
    assert despues == antes, "el invitado infla el total de usuarios del panel"


def test_sin_cupo_de_invitados_el_alta_responde_503(client, monkeypatch):
    """GAIA_MAX_GUEST_SESSIONS=0 es el interruptor de la demo."""
    import app.storage.guest as guest_mod

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 0)
    r = client.post("/api/auth/guest")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "server_busy"


def test_el_logout_de_una_cuenta_normal_no_borra_nada(client, reset_rate_limiter):
    """La contrapartida del borrado del invitado, y el peor fallo posible.

    El logout llama a `purge_user_data` cuando quien sale es un invitado. Si esa
    condición se ensanchara —o `is_guest` cambiara de criterio—, cerrar sesión
    borraría la cuenta de un usuario real y todo lo suyo, sin que nada lo pida.
    """
    r = client.post(
        "/api/auth/register",
        json={
            "username": "quedaviva",
            "email": "quedaviva@example.com",
            "password": "pass1234",
        },
    )
    assert r.status_code in (200, 201), r.text
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "quedaviva@example.com", "password": "pass1234"},
        ).status_code
        == 200
    )

    creado = client.post(
        "/api/agents",
        json={"name": "sobrevive", "system_prompt": "x", "model": "gpt-4o"},
    )
    assert creado.status_code in (200, 201), creado.text

    assert client.post("/api/auth/logout").status_code == 200

    # La cuenta sigue ahí y su agente también: se vuelve a entrar y está.
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "quedaviva@example.com", "password": "pass1234"},
        ).status_code
        == 200
    )
    nombres = [a["name"] for a in client.get("/api/v2/agents").json()["items"]]
    assert "sobrevive" in nombres


def test_pulsar_dos_veces_el_boton_no_acumula_invitados(client):
    """La fuga de cupo: cada pulsación dejaba un invitado que nadie purga.

    El barrido se lleva a los que no tienen sesión viva, y estos la tienen —solo
    que ya no la usa nadie—, así que ocupaban sitio hasta que caducaba. Medido
    antes del arreglo: tres pulsaciones, tres filas, cero purgadas.
    """
    import asyncio

    from app.storage.db import open_db

    ids = []
    for _ in range(3):
        r = client.post("/api/auth/guest")
        assert r.status_code == 200, r.text
        ids.append(r.json()["username"])
    assert len(set(ids)) == 3, "cada alta debe emitir una identidad nueva"

    async def _cuenta() -> tuple[int, int]:
        async with open_db() as conn:
            invitados = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE role='guest'"
            )
            vivas = await conn.fetchval(
                "SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL"
            )
            return int(invitados or 0), int(vivas or 0)

    invitados, sesiones_vivas = asyncio.run(_cuenta())
    assert invitados == 1, f"quedaron {invitados} invitados: el cupo gotea"
    assert sesiones_vivas == 1, f"quedaron {sesiones_vivas} sesiones vivas"

    # Y el que sobrevive es el último, que es con el que se queda el navegador.
    assert client.get("/api/auth/me").json()["username"] == ids[-1]


def test_una_cuenta_registrada_no_pierde_su_sesion_por_el_boton_de_invitado(
    client, reset_rate_limiter
):
    """El reverso: `_cerrar_invitado_previo` solo puede tocar a invitados."""
    client.post(
        "/api/auth/register",
        json={
            "username": "nopierde",
            "email": "nopierde@example.com",
            "password": "pass1234",
        },
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "nopierde@example.com", "password": "pass1234"},
        ).status_code
        == 200
    )
    token_antes = client.cookies.get("ga_token")

    assert client.post("/api/auth/guest").status_code == 200

    # La sesión de la cuenta sigue viva: se vuelve a presentar su token.
    client.cookies.set("ga_token", token_antes)
    r = client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "nopierde"


def test_el_panel_de_admin_ve_cuantos_invitados_hay(admin_client, monkeypatch):
    """Los invitados no suman a `users_total` pero el admin tiene que verlos.

    Son quienes consumen el cupo del demo, y cuando se llena el alta responde
    503 sin que nada más lo explique.
    """
    import asyncio

    import app.storage.guest as guest_mod
    from app.storage.guest import create_guest_user

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 7)
    antes = admin_client.get("/api/admin/stats").json()
    asyncio.run(create_guest_user())
    despues = admin_client.get("/api/admin/stats").json()

    assert despues["guests_active"] == antes["guests_active"] + 1
    assert despues["guests_max"] == 7
    assert despues["users_total"] == antes["users_total"], (
        "el invitado no debe sumar al total de usuarios"
    )
