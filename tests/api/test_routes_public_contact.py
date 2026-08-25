"""Formulario de contacto público — POST /api/public/contact.

Es el único endpoint que escribe sin credencial de ningún tipo, así que lo que
se comprueba aquí no es solo que funcione: es que siga abierto al anónimo (que
era el fallo original, apuntaba a /api/admin/ y expulsaba al visitante al
login), que valide lo que guarda, y que la fila quede aunque el correo no salga
—el caso por defecto, porque SMTP puede no estar configurado—.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _payload(**cambios) -> dict:
    cuerpo = {
        "type": "plan_ent",
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "message": "Queremos el plan Legión para 40 personas.",
    }
    cuerpo.update(cambios)
    return cuerpo


def _guardadas() -> list:
    from app.storage.contact import list_contact_requests

    return asyncio.run(list_contact_requests())


# ── El camino que estaba roto ────────────────────────────────────────────────


def test_el_anonimo_puede_enviar_sin_sesion(client):
    r = client.post("/api/public/contact", json=_payload())

    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    filas = _guardadas()
    assert len(filas) == 1
    assert filas[0]["type"] == "plan_ent"
    assert filas[0]["email"] == "ada@example.com"
    assert filas[0]["name"] == "Ada Lovelace"


def test_la_peticion_se_guarda_aunque_no_haya_smtp(client):
    """Sin SMTP el aviso no sale, y aun así el lead no se pierde."""
    r = client.post("/api/public/contact", json=_payload())

    assert r.status_code == 200, r.text
    # `notified` es falso porque los tests no configuran SMTP: es exactamente
    # el estado de una instalación recién instalada.
    assert r.json()["notified"] is False
    assert len(_guardadas()) == 1


def test_avisa_por_correo_cuando_hay_smtp(client):
    with patch("app.api.routes.public.send_contact_notification", return_value=True) as aviso:
        r = client.post("/api/public/contact", json=_payload())

    assert r.status_code == 200, r.text
    assert r.json()["notified"] is True
    assert aviso.call_args.kwargs["kind"] == "plan_ent"
    assert aviso.call_args.kwargs["email"] == "ada@example.com"


# ── Validación ───────────────────────────────────────────────────────────────


def test_rechaza_un_email_invalido(client):
    r = client.post("/api/public/contact", json=_payload(email="ada@"))

    assert r.status_code == 400
    assert r.json()["detail"]["field"] == "email"
    assert _guardadas() == []


def test_rechaza_un_tipo_desconocido(client):
    """El tipo acaba en el asunto del correo del operador: no vale cualquiera."""
    r = client.post("/api/public/contact", json=_payload(type="lo-que-sea"))

    assert r.status_code == 400
    assert r.json()["detail"]["field"] == "type"
    assert _guardadas() == []


def test_rechaza_un_nombre_vacio(client):
    r = client.post("/api/public/contact", json=_payload(name="   "))

    assert r.status_code == 400
    assert r.json()["detail"]["field"] == "name"
    assert _guardadas() == []


def test_corta_los_campos_desmesurados(client):
    r = client.post("/api/public/contact", json=_payload(message="x" * 4001))

    assert r.status_code == 422
    assert _guardadas() == []


# ── Antispam ─────────────────────────────────────────────────────────────────


def test_el_campo_trampa_descarta_en_silencio(client):
    """Responder 400 le enseñaría al bot cómo evitarlo la próxima vez."""
    r = client.post("/api/public/contact", json=_payload(website="https://spam.example"))

    assert r.status_code == 200, r.text
    assert _guardadas() == []


def test_el_cupo_por_ip_corta_la_rafaga(client):
    from app.config.session import RATE_CONTACT_CALLS

    codigos = [
        client.post("/api/public/contact", json=_payload()).status_code
        for _ in range(RATE_CONTACT_CALLS + 3)
    ]

    assert 429 in codigos, codigos
    assert len(_guardadas()) <= RATE_CONTACT_CALLS


# ── Lectura ──────────────────────────────────────────────────────────────────


def test_el_admin_lee_las_peticiones(client, admin_client):
    client.post("/api/public/contact", json=_payload())

    r = admin_client.get("/api/admin/contact-requests")

    assert r.status_code == 200, r.text
    assert [p["email"] for p in r.json()] == ["ada@example.com"]


def test_el_anonimo_no_puede_leerlas(client):
    """Escribir es público; leer, nunca: ahí están el email y la IP de terceros."""
    r = client.get("/api/admin/contact-requests")

    assert r.status_code == 401
