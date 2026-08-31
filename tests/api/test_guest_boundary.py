"""Frontera del invitado.

Hasta el cierre de BE-01, `require_auth` y `require_group` estaban aliasadas al
rango `guest`, así que cualquiera que llamase a POST /api/auth/guest pasaba la
puerta de los ~140 endpoints que las usan, incluidos billing, settings, social,
sharing, users y accounts, que nunca miraron si quien llamaba era un invitado.

Aquel allowlist se derivaba del código: «endpoint con rama `is_guest(...)`» era
exactamente el conjunto que sabía trabajar contra la GuestSession en memoria.
Esa regla ya no existe —el invitado es un usuario efímero en la BD y usa el
mismo almacenamiento que todos—, así que la frontera es ahora una decisión de
producto: **todo su espacio personal, nada de lo que no es suyo**. Este fichero
es donde está escrita, en las dos direcciones.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def guest(client):
    """Client con una sesión de invitado activa."""
    r = client.post("/api/auth/guest")
    assert r.status_code == 200, r.text
    return client


# ── Lo que el invitado NO debe poder tocar ────────────────────────────────────
# Un endpoint representativo por cada router que no le pertenece: lo que es de
# otros (users, social, groups, sharing), lo que no tiene sentido sin cuenta
# (billing, cuentas OAuth, PATs) y lo que es de la instalación (admin).

CERRADOS = [
    ("GET", "/api/accounts"),
    ("GET", "/api/billing/subscription"),
    ("GET", "/api/v2/feed"),
    ("GET", "/api/groups"),
    ("GET", "/api/notifications"),
    ("GET", "/api/notifications/push/key"),
    ("GET", "/api/social/feed"),
    ("GET", "/api/v2/users"),
    ("POST", "/api/auth/tokens"),
]


@pytest.mark.parametrize("metodo,ruta", CERRADOS)
def test_el_invitado_no_entra(guest, metodo, ruta):
    r = guest.request(metodo, ruta)
    assert r.status_code != 200, (
        f"{metodo} {ruta} sigue abierto al invitado (BE-01 reabierto)"
    )
    # 403 es la respuesta correcta: credencial válida, rango insuficiente.
    # 404 también vale — significa que la ruta ni siquiera existe ya.
    assert r.status_code in (403, 404), f"{metodo} {ruta} devolvió {r.status_code}"


def test_el_anonimo_sin_credencial_tampoco(client):
    """Sin sesión de ningún tipo la respuesta es 401, no 403."""
    r = client.get("/api/v2/users")
    assert r.status_code == 401


def test_el_invitado_no_administra(guest):
    r = guest.get("/api/v2/admin/explore?type=user&limit=100")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "forbidden"


# ── Lo que el invitado SÍ debe poder hacer: su espacio personal ───────────────

ABIERTOS = [
    ("GET", "/api/auth/me"),
    ("GET", "/api/v2/agents"),
    ("GET", "/api/v2/connections"),
    ("GET", "/api/v2/skills"),
    ("GET", "/api/v2/prompts"),
    ("GET", "/api/v2/tools"),
    ("GET", "/api/v2/knowledge"),
    ("GET", "/api/v2/knowledge-packs"),
    ("GET", "/api/memory"),
    ("GET", "/api/chats/recent"),
    ("GET", "/api/workflows"),
    ("GET", "/api/settings"),
    ("GET", "/api/labels/private"),
    ("GET", "/api/llm-orchestrations"),
    # Catálogo público: solo filas is_public. Es la vitrina del demo.
    ("GET", "/api/v2/explore"),
]


@pytest.mark.parametrize("metodo,ruta", ABIERTOS)
def test_el_demo_del_invitado_sigue_abierto(guest, metodo, ruta):
    r = guest.request(metodo, ruta)
    assert r.status_code == 200, (
        f"{metodo} {ruta} rompió el demo: {r.status_code} {r.text}"
    )


def test_el_invitado_completa_el_camino_del_demo(guest):
    """Crear conexión y agente, que es para lo que existe la demo."""
    r = guest.post(
        "/api/connections",
        json={
            "type": "openai",
            "label": "demo",
            "api_key": "sk-demo",
            "model": "gpt-4o",
        },
    )
    assert r.status_code in (200, 201), r.text
    conn_id = r.json().get("id")
    assert conn_id

    r = guest.post(
        "/api/agents",
        json={
            "name": "agente demo",
            "system_prompt": "eres un demo",
            "model": "gpt-4o",
            "connection_id": conn_id,
        },
    )
    assert r.status_code in (200, 201), r.text

    assert any(a["name"] == "agente demo" for a in guest.get("/api/v2/agents").json()["items"])


def test_el_invitado_activa_y_desactiva_lo_suyo(guest):
    """Con la sesión en memoria esto respondía 403: no había dónde guardarlo."""
    r = guest.post(
        "/api/agents",
        json={"name": "para desactivar", "system_prompt": "x", "model": "gpt-4o"},
    )
    assert r.status_code in (200, 201), r.text
    agent_id = r.json()["id"]

    assert guest.post(f"/api/agents/{agent_id}/deactivate").status_code == 200
    assert guest.post(f"/api/agents/{agent_id}/activate").status_code == 200


def test_el_invitado_guarda_su_conversacion(guest):
    """Antes esto era un 403 explícito: «los invitados no pueden guardar
    conversaciones». Ahora puede, y se borra con él."""
    r = guest.post("/api/chats/agente-x", json={"title": "una charla"})
    assert r.status_code == 200, r.text
    assert guest.get("/api/chats/agente-x").status_code == 200


def test_el_invitado_guarda_y_borra_su_prompt_privado(guest):
    r = guest.post(
        "/api/prompts/private",
        json={
            "name": "Demo",
            "description": "del invitado",
            "content": "contenido",
            "alias": "demo-invitado",
        },
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    assert guest.get(f"/api/prompts/private/{pid}").status_code == 200
    assert guest.delete(f"/api/prompts/private/{pid}").status_code == 200
    assert guest.get(f"/api/prompts/private/{pid}").status_code == 404


PUBLICABLES = [
    ("POST", "/api/skills/public"),
    ("POST", "/api/prompts/public"),
    ("POST", "/api/tools/public"),
]


@pytest.mark.parametrize("metodo,ruta", PUBLICABLES)
def test_el_invitado_no_publica_en_la_vitrina(guest, metodo, ruta):
    """Lo único cerrado dentro de su propio espacio: lo que publicase se
    desvanecería del catálogo al expirar su sesión, dejando enlaces rotos en
    quien lo hubiera enlazado."""
    r = guest.request(
        metodo,
        ruta,
        json={"name": "publico", "description": "d", "content": "c", "alias": "pub-x"},
    )
    assert r.status_code == 403, f"{metodo} {ruta} devolvió {r.status_code}"
    assert r.json()["detail"]["code"] == "guest_cannot_publish"


def test_el_invitado_tampoco_publica_un_agente(guest):
    """El agente no pasa por `scope` en la URL sino en el cuerpo, así que su
    guarda está en otro sitio del handler y se comprueba aparte."""
    r = guest.post(
        "/api/agents",
        json={
            "name": "agente publico",
            "system_prompt": "x",
            "model": "gpt-4o",
            "scope": "public",
        },
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "guest_cannot_publish"


def test_el_invitado_no_crea_knowledge_publico(guest):
    r = guest.post(
        "/api/knowledge/text",
        json={
            "title": "Knowledge público",
            "content": "contenido",
            "labels": ["public"],
        },
    )

    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "guest_cannot_publish"
    assert guest.get("/api/v2/knowledge").json()["items"] == []


def test_el_invitado_no_convierte_su_knowledge_privado_en_publico(guest):
    created = guest.post(
        "/api/knowledge/text",
        json={"title": "Knowledge privado", "content": "contenido"},
    )
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]

    published = guest.put(
        f"/api/knowledge/{item_id}/labels",
        json={"labels": ["public"]},
    )

    assert published.status_code == 403, published.text
    assert published.json()["detail"]["code"] == "guest_cannot_publish"
    stored = next(
        item for item in guest.get("/api/v2/knowledge").json()["items"] if item["id"] == item_id
    )
    assert "private" in stored["labels"]
    assert "public" not in stored["labels"]


def test_el_invitado_no_inicia_un_pack_publico(guest):
    r = guest.post(
        "/api/knowledge/packs/upload-sessions",
        json={
            "name": "Pack público",
            "labels": ["public"],
            "total_files": 1,
        },
    )

    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "guest_cannot_publish"
    assert guest.get("/api/v2/knowledge-packs").json()["items"] == []
