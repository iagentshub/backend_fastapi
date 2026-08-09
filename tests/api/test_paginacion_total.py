"""X-Total-Count en los listados paginados (mejora #15).

Diez listados aceptaban `limit`/`offset` y devolvían una lista pelada: el
cliente podía pedir la página 3 pero no sabía cuántas hay. El total viaja ahora
en cabecera porque es aditivo — cambiar el cuerpo a `{items: [...]}` rompería a
la vez a todos los clientes, y el real es la app Flutter.
"""

from __future__ import annotations

from uuid import uuid4

from app.api.pagination import TOTAL_HEADER


def _login(client) -> str:
    user = f"pag{uuid4().hex[:8]}"
    r = client.post(
        "/api/auth/register",
        json={"username": user, "email": f"{user}@pag.test", "password": "pass1234"},
    )
    assert r.status_code == 200, r.text
    return user


def _crear_agentes(client, cuantos: int) -> None:
    for i in range(cuantos):
        r = client.post("/api/agents", json={"name": f"Agente {i}", "description": "d"})
        assert r.status_code == 200


def test_el_total_no_depende_de_la_pagina(client):
    _login(client)
    _crear_agentes(client, 5)

    completa = client.get("/api/agents")
    assert completa.headers[TOTAL_HEADER] == "5"

    pagina = client.get("/api/agents", params={"limit": 2, "offset": 2})
    assert len(pagina.json()) == 2
    # El total es el de antes de recortar: es lo que necesita el paginador.
    assert pagina.headers[TOTAL_HEADER] == "5"


def test_el_cuerpo_sigue_siendo_una_lista(client):
    _login(client)
    _crear_agentes(client, 2)

    r = client.get("/api/agents", params={"limit": 1})
    assert isinstance(r.json(), list), "el cambio tenía que ser aditivo"


def test_listados_con_el_total_en_cabecera(client):
    _login(client)
    for ruta in ("/api/agents", "/api/skills", "/api/prompts", "/api/tools",
                 "/api/knowledge", "/api/connections", "/api/users"):
        r = client.get(ruta, params={"limit": 5})
        assert r.status_code == 200, f"{ruta}: {r.text}"
        assert TOTAL_HEADER in r.headers, f"{ruta} no publica el total"
        assert r.headers[TOTAL_HEADER].isdigit()


def test_explore_cuenta_con_su_propio_count(client):
    """explore pagina en SQL, así que el total sale de un COUNT aparte."""
    _login(client)

    r = client.get("/api/explore", params={"limit": 5})
    assert r.status_code == 200
    assert r.headers[TOTAL_HEADER].isdigit()


def test_la_cabecera_se_expone_a_cors(client):
    """Sin Access-Control-Expose-Headers el navegador no deja leerla."""
    _login(client)
    r = client.get(
        "/api/agents",
        params={"limit": 1},
        headers={"Origin": "http://localhost:5173"},
    )
    expuestas = r.headers.get("access-control-expose-headers", "")
    assert TOTAL_HEADER.lower() in expuestas.lower(), expuestas
