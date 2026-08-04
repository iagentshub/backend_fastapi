"""El cuerpo JSON tiene que ser un objeto (BE-09).

43 handlers hacían `body = await request.json()` y justo después `body.get(...)`.
Mandar un array bastaba para reventarlos con AttributeError y un 500, en
endpoints públicos como el registro: entrada inválida devuelta como fallo del
servidor.
"""

from __future__ import annotations

import pytest

CUERPOS_QUE_NO_SON_OBJETO = ["[]", '"hola"', "123", "true", "null"]


@pytest.mark.parametrize("cuerpo", CUERPOS_QUE_NO_SON_OBJETO)
def test_registro_rechaza_cuerpos_que_no_son_objeto(client, cuerpo):
    r = client.post(
        "/api/auth/register",
        content=cuerpo,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_json"


def test_registro_rechaza_json_corrupto(client):
    r = client.post(
        "/api/auth/register",
        content="{no-es-json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_json"


def test_quote_rechaza_cuerpos_que_no_son_objeto(client):
    r = client.post(
        "/api/billing/quote",
        content="[]",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_json"


def test_un_objeto_valido_sigue_pasando(client):
    """Un objeto llega al handler y falla por su propia validación, no por json_body."""
    r = client.post("/api/auth/register", json={"username": "x"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] != "invalid_json"


def test_ningun_handler_se_saltó_el_helper():
    """Guardia: request.json() crudo vuelve a abrir el agujero."""
    import pathlib

    rutas = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "routes"
    culpables = [
        str(p.relative_to(rutas))
        for p in rutas.rglob("*.py")
        if "await request.json()" in p.read_text(encoding="utf-8")
    ]
    assert culpables == [], f"usa json_body(request) en: {culpables}"
