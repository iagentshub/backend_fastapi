"""Frontera del invitado — BE-01.

Hasta el cierre, `require_auth` y `require_group` estaban aliasadas al rango
`guest`, así que cualquiera que llamase a POST /api/auth/guest pasaba la puerta
de los ~140 endpoints que las usan, incluidos billing, settings, social,
sharing, users, accounts y workflows, que nunca miraron si quien llamaba era un
invitado.

El allowlist no se decidió a ojo: los endpoints con rama `is_guest(...)` son los
conscientes del invitado por diseño y siguen abiertos con `require_session`.
Este fichero fija esa frontera en ambas direcciones para que no se pueda mover
sin que un test lo diga.
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
# Un endpoint representativo por cada router que nunca contempló al invitado.

CERRADOS = [
    ("GET", "/api/accounts"),
    ("GET", "/api/billing/subscription"),
    ("GET", "/api/feed"),
    ("GET", "/api/groups"),
    ("GET", "/api/labels"),
    ("GET", "/api/settings"),
    ("GET", "/api/social/feed"),
    ("GET", "/api/users"),
    ("GET", "/api/workflows"),
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
    if r.status_code == 403:
        assert r.json()["detail"]["code"] == "guest_forbidden"


def test_el_anonimo_sin_credencial_tampoco(client):
    """Sin sesión de ningún tipo la respuesta es 401, no 403."""
    r = client.get("/api/settings")
    assert r.status_code == 401


# ── Lo que el invitado SÍ debe poder hacer: el demo ───────────────────────────

ABIERTOS = [
    ("GET", "/api/auth/me"),
    ("GET", "/api/agents"),
    ("GET", "/api/connections"),
    ("GET", "/api/skills"),
    ("GET", "/api/knowledge"),
    ("GET", "/api/memory"),
    ("GET", "/api/chats/recent"),
    # Catálogo público: solo filas is_public. Es la vitrina del demo.
    ("GET", "/api/explore"),
    # Prompts llegó a main mientras corrían las fases y se cerró al
    # integrar, pese a tener ramas is_guest y su propio campo en
    # GuestSession. Lo cazó la suite; aquí queda fijado.
    ("GET", "/api/prompts"),
]


@pytest.mark.parametrize("metodo,ruta", ABIERTOS)
def test_el_demo_del_invitado_sigue_abierto(guest, metodo, ruta):
    r = guest.request(metodo, ruta)
    assert r.status_code == 200, f"{metodo} {ruta} rompió el demo: {r.status_code} {r.text}"


def test_el_invitado_completa_el_camino_del_demo(guest):
    """Crear conexión y agente en la sesión efímera, que es para lo que existe."""
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

    assert any(a["name"] == "agente demo" for a in guest.get("/api/agents").json())


# ── Prompts: guest-aware por diseño, pero no entero ───────────────────────────
# Los 4 handlers con rama is_guest() están abiertos; activate y deactivate no la
# tienen —operan sobre el estado del grupo en BD— y siguen cerrados.

PROMPTS_CERRADOS = [
    ("POST", "/api/prompts/x/activate"),
    ("POST", "/api/prompts/x/deactivate"),
]


@pytest.mark.parametrize("metodo,ruta", PROMPTS_CERRADOS)
def test_el_invitado_no_activa_prompts_del_grupo(guest, metodo, ruta):
    r = guest.request(metodo, ruta)
    assert r.status_code == 403, f"{metodo} {ruta} devolvió {r.status_code}"
    assert r.json()["detail"]["code"] == "guest_forbidden"


def test_el_invitado_guarda_y_borra_su_prompt_privado(guest):
    """El prompt vive en la sesión efímera, no en la BD."""
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
