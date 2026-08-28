"""Tests de /api/notifications — la campana y el correo que la acompaña.

Cubre el camino entero del único evento accionable del producto: A invita a B,
B recibe el aviso y el correo, y al marcarlo leído el contador baja.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _auth(client: TestClient, username: str, password: str = "pass1234") -> TestClient:
    """Registra un usuario y establece su cookie en el client."""
    import asyncio

    from app.auth.auth import create_token, register_user

    try:
        asyncio.run(register_user(username, password, email=f"{username}@test.com"))
    except ValueError:
        pass
    client.cookies.set("ga_token", create_token(username))
    return client


def _crear_grupo_e_invitar(client: TestClient, dueno: str, invitado: str, nombre: str) -> str:
    """Deja al invitado con una invitación pendiente. Devuelve el group_id."""
    _auth(client, invitado)
    _auth(client, dueno)
    group_id = client.post("/api/groups", json={"name": nombre}).json()["id"]
    r = client.post(f"/api/groups/{group_id}/invitations", json={"username": invitado})
    assert r.status_code == 200, r.text
    return group_id


@pytest.fixture()
def correos(monkeypatch):
    """Intercepta el envío SMTP y devuelve la lista de (destino, asunto, html)."""
    import app.services.email as email_mod

    enviados: list[tuple[str, str, str]] = []
    monkeypatch.setattr(email_mod, "_smtp_available", lambda: True)
    monkeypatch.setattr(
        email_mod,
        "_send_smtp",
        lambda to, subject, html: enviados.append((to, subject, html)),
    )
    return enviados


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_listar_requiere_sesion(client):
    assert client.get("/api/notifications").status_code == 401


# ── El camino completo de la invitación ───────────────────────────────────────

def test_invitar_genera_notificacion_para_el_invitado(client):
    _crear_grupo_e_invitar(client, "notif_owner", "notif_guest", "Marketing")

    _auth(client, "notif_guest")
    r = client.get("/api/notifications")
    assert r.status_code == 200
    data = r.json()

    assert data["unread"] == 1
    aviso = data["items"][0]
    assert aviso["kind"] == "group_invite"
    assert aviso["read"] is False
    assert aviso["data"]["actor"] == "notif_owner"
    assert aviso["data"]["group"] == "Marketing"
    # El id de la invitación viaja en el aviso: es lo que deja aceptar o
    # rechazar desde la propia campana, sin ir a buscarla a otra pantalla.
    assert aviso["data"]["invitation_id"]


def test_el_que_invita_no_se_notifica_a_si_mismo(client):
    _crear_grupo_e_invitar(client, "notif_solo_owner", "notif_solo_guest", "Ventas")

    _auth(client, "notif_solo_owner")
    assert client.get("/api/notifications").json()["unread"] == 0


def test_marcar_una_leida_baja_el_contador(client):
    _crear_grupo_e_invitar(client, "notif_read_owner", "notif_read_guest", "Soporte")

    _auth(client, "notif_read_guest")
    aviso_id = client.get("/api/notifications").json()["items"][0]["id"]

    r = client.post("/api/notifications/read", json={"id": aviso_id})
    assert r.status_code == 200
    assert r.json()["unread"] == 0
    assert client.get("/api/notifications").json()["items"][0]["read"] is True


def test_marcar_todas_sin_id(client):
    _crear_grupo_e_invitar(client, "notif_all_owner", "notif_all_guest", "Diseno")

    _auth(client, "notif_all_guest")
    r = client.post("/api/notifications/read", json={})
    assert r.status_code == 200
    assert r.json()["unread"] == 0


def test_no_se_puede_marcar_la_notificacion_de_otro(client):
    _crear_grupo_e_invitar(client, "notif_otro_owner", "notif_otro_guest", "Legal")

    _auth(client, "notif_otro_guest")
    aviso_id = client.get("/api/notifications").json()["items"][0]["id"]

    _auth(client, "notif_otro_owner")
    client.post("/api/notifications/read", json={"id": aviso_id})

    _auth(client, "notif_otro_guest")
    assert client.get("/api/notifications").json()["unread"] == 1


# ── Otros productores ─────────────────────────────────────────────────────────

def test_sacar_a_un_miembro_le_avisa(client):
    group_id = _crear_grupo_e_invitar(client, "notif_out_owner", "notif_out_guest", "Obra")
    _auth(client, "notif_out_guest")
    inv = client.get("/api/notifications").json()["items"][0]["data"]["invitation_id"]
    client.post(f"/api/groups/invitations/{inv}/accept")
    client.post("/api/notifications/read", json={})

    _auth(client, "notif_out_owner")
    r = client.delete(f"/api/groups/{group_id}/members/notif_out_guest")
    assert r.status_code == 200, r.text

    _auth(client, "notif_out_guest")
    data = client.get("/api/notifications").json()
    assert data["unread"] == 1
    assert data["items"][0]["kind"] == "group_member_removed"


def test_salirse_uno_mismo_no_genera_aviso(client):
    """Avisarte de algo que acabas de hacer tú es ruido."""
    group_id = _crear_grupo_e_invitar(client, "notif_self_owner", "notif_self_guest", "Taller")
    _auth(client, "notif_self_guest")
    inv = client.get("/api/notifications").json()["items"][0]["data"]["invitation_id"]
    client.post(f"/api/groups/invitations/{inv}/accept")
    client.post("/api/notifications/read", json={})

    r = client.delete(f"/api/groups/{group_id}/members/notif_self_guest")
    assert r.status_code == 200, r.text
    assert client.get("/api/notifications").json()["unread"] == 0


# ── El correo ─────────────────────────────────────────────────────────────────

def test_invitar_encola_un_correo_al_invitado(client, correos):
    _crear_grupo_e_invitar(client, "notif_mail_owner", "notif_mail_guest", "Prensa")

    assert len(correos) == 1
    destino, asunto, html = correos[0]
    assert destino == "notif_mail_guest@test.com"
    assert "notif_mail_owner" in asunto and "Prensa" in asunto
    # El botón tiene que llevar a la app, no a la portada pública, y a una
    # pantalla donde la campana esté a la vista con su contador.
    assert "/app/dashboard" in html


def test_el_correo_escapa_lo_que_escribe_el_usuario(client, correos):
    """Un nombre de grupo con marcado no puede entrar crudo en el buzón ajeno."""
    _crear_grupo_e_invitar(
        client, "notif_xss_owner", "notif_xss_guest", "<script>alert(1)</script>"
    )

    _, asunto, html = correos[0]
    assert "<script>" not in html
    assert "<script>" not in asunto
    assert "&lt;script&gt;" in html


def test_sin_smtp_la_notificacion_sigue_llegando(client, monkeypatch):
    """El correo es un canal adicional, nunca el único: es el caso por defecto."""
    import app.services.email as email_mod

    monkeypatch.setattr(email_mod, "_smtp_available", lambda: False)
    _crear_grupo_e_invitar(client, "notif_nosmtp_owner", "notif_nosmtp_guest", "Radio")

    _auth(client, "notif_nosmtp_guest")
    assert client.get("/api/notifications").json()["unread"] == 1


# ── Purga por antigüedad ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_la_purga_respeta_dos_ventanas(client):
    """Una leída caduca antes que una que nadie ha visto."""
    from app.auth.auth import get_user_by_username, register_user
    from app.storage.db import open_db
    from app.storage.notifications import (
        count_unread,
        insert_notification,
        list_notifications,
        mark_read,
        purge_old,
    )

    try:
        await register_user("purga_user", "pass1234", email="purga@test.com")
    except ValueError:
        pass
    user = await get_user_by_username("purga_user")
    uid = user["id"]

    vieja_leida = await insert_notification(user_id=uid, kind="a", data={})
    vieja_sin_leer = await insert_notification(user_id=uid, kind="b", data={})
    reciente = await insert_notification(user_id=uid, kind="c", data={})
    await mark_read(uid, vieja_leida)

    # 100 días: pasada la ventana corta (90) pero dentro de la larga (365). Es
    # justo el hueco donde las dos políticas discrepan, que es lo que se prueba.
    from datetime import datetime, timedelta, timezone

    hace_100 = (datetime.now(timezone.utc) - timedelta(days=100)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    async with open_db() as c:
        for ident in (vieja_leida, vieja_sin_leer):
            await c.execute(
                "UPDATE notifications SET created_at=? WHERE id=?",
                (hace_100, ident),
            )
        await c.commit()

    borradas = await purge_old(dias_leidas=90, dias_sin_leer=365)

    assert borradas == 1, "solo la vieja LEÍDA entra en la ventana corta"
    quedan = {n["id"] for n in await list_notifications(uid)}
    assert vieja_leida not in quedan
    assert vieja_sin_leer in quedan, "una sin leer sobrevive a la ventana corta"
    assert reciente in quedan
    assert await count_unread(uid) == 2


@pytest.mark.asyncio
async def test_la_ventana_larga_acaba_barriendo_las_sin_leer(client):
    from app.auth.auth import get_user_by_username, register_user
    from app.storage.db import open_db
    from app.storage.notifications import insert_notification, purge_old

    try:
        await register_user("purga_larga", "pass1234", email="purgal@test.com")
    except ValueError:
        pass
    user = await get_user_by_username("purga_larga")
    ident = await insert_notification(user_id=user["id"], kind="a", data={})
    async with open_db() as c:
        await c.execute(
            "UPDATE notifications SET created_at=? WHERE id=?",
            ("2020-01-01T00:00:00.000Z", ident),
        )
        await c.commit()

    assert await purge_old(dias_leidas=90, dias_sin_leer=365) == 1


@pytest.mark.asyncio
async def test_la_purga_no_toca_lo_reciente(client):
    from app.auth.auth import get_user_by_username, register_user
    from app.storage.notifications import count_unread, insert_notification, purge_old

    try:
        await register_user("purga_reciente", "pass1234", email="purgar@test.com")
    except ValueError:
        pass
    user = await get_user_by_username("purga_reciente")
    await insert_notification(user_id=user["id"], kind="a", data={})

    assert await purge_old(dias_leidas=1, dias_sin_leer=1) == 0
    assert await count_unread(user["id"]) == 1


# ── Preferencias por categoría ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apagar_una_categoria_no_apaga_las_demas(client, monkeypatch):
    """Es el objetivo entero: silenciar los grupos sin perder la facturación."""
    import app.services.email as email_mod

    enviados: list[str] = []
    monkeypatch.setattr(email_mod, "_smtp_available", lambda: True)
    monkeypatch.setattr(email_mod, "_send_smtp", lambda to, s, h: enviados.append(s))

    from app.auth.auth import create_token, get_user_by_username, register_user

    try:
        await register_user("cat_user", "pass1234", email="cat@test.com")
    except ValueError:
        pass
    client.cookies.set("ga_token", create_token("cat_user"))

    r = client.put(
        "/api/settings",
        json={"notification_categories": {"groups": {"email": False}}},
    )
    assert r.status_code == 200
    assert r.json()["notification_categories"]["groups"]["email"] is False
    assert r.json()["notification_categories"]["billing"]["email"] is True

    from app.services.notifications import notify

    user = await get_user_by_username("cat_user")
    await notify(user_id=user["id"], kind="group_invite", actor="a", group="M")
    await notify(user_id=user["id"], kind="license_assigned", actor="a")

    assert len(enviados) == 1, "solo debe salir el de facturación"
    assert "licencia" in enviados[0].lower()


@pytest.mark.asyncio
async def test_el_interruptor_general_manda_sobre_la_categoria(client, monkeypatch):
    """Apagar el correo entero no puede dejar pasar una categoría encendida."""
    import app.services.email as email_mod

    enviados: list[str] = []
    monkeypatch.setattr(email_mod, "_smtp_available", lambda: True)
    monkeypatch.setattr(email_mod, "_send_smtp", lambda to, s, h: enviados.append(s))

    from app.auth.auth import create_token, get_user_by_username, register_user

    try:
        await register_user("cat_maestro", "pass1234", email="catm@test.com")
    except ValueError:
        pass
    client.cookies.set("ga_token", create_token("cat_maestro"))
    client.put(
        "/api/settings",
        json={
            "notify_email": False,
            "notification_categories": {"groups": {"email": True}},
        },
    )

    from app.services.notifications import notify

    user = await get_user_by_username("cat_maestro")
    await notify(user_id=user["id"], kind="group_invite", actor="a", group="M")

    assert enviados == []


def test_los_ajustes_publican_el_catalogo(client):
    """El cliente pinta lo que reciba; una copia suya se desincronizaría."""
    _auth(client, "cat_catalogo")
    datos = client.get("/api/settings").json()["notification_categories"]

    from app.models.notification_kinds import categorias_publicas

    assert set(datos) == set(categorias_publicas())
    assert all({"email", "push"} == set(v) for v in datos.values())


def test_una_categoria_inventada_se_descarta(client):
    """Nadie engorda `users.preferences` mandando claves que no existen."""
    _auth(client, "cat_invento")
    r = client.put(
        "/api/settings",
        json={"notification_categories": {"inventada": {"email": False}}},
    )
    assert r.status_code == 200
    assert "inventada" not in r.json()["notification_categories"]


def test_tocar_un_interruptor_no_borra_los_otros(client):
    """La pantalla manda solo lo que se movió: el resto debe sobrevivir."""
    _auth(client, "cat_fusion")
    client.put(
        "/api/settings", json={"notification_categories": {"groups": {"push": False}}}
    )
    r = client.put(
        "/api/settings", json={"notification_categories": {"billing": {"email": False}}}
    )
    categorias = r.json()["notification_categories"]
    assert categorias["groups"]["push"] is False, "se perdió el ajuste anterior"
    assert categorias["billing"]["email"] is False
