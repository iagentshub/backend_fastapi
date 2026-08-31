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


def _modulos_de_rutas():
    import pathlib

    rutas = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "routes"
    return rutas, sorted(rutas.rglob("*.py"))


def test_ningun_handler_se_saltó_el_helper():
    """Guardia: request.json() crudo vuelve a abrir el agujero."""
    rutas, modulos = _modulos_de_rutas()
    culpables = [
        str(p.relative_to(rutas))
        for p in modulos
        if "await request.json()" in p.read_text(encoding="utf-8")
    ]
    assert culpables == [], f"usa json_body(request) en: {culpables}"


def test_ningun_cuerpo_llega_a_openapi_como_objeto_libre():
    """La otra puerta, que esta guarda no miraba.

    Un parámetro anotado `Dict[str, Any]` no es «parsear a mano» —FastAPI ya
    rechaza un cuerpo que no sea objeto, así que el 500 ante un `[]` aquí no se
    da— pero produce lo mismo que la migración venía a quitar: la validación se
    reescribe campo por campo, los límites acaban viviendo dentro del handler, y
    el cuerpo desaparece del único contrato que se genera solo. Cinco endpoints
    de `groups/` se quedaron así, y la guarda de arriba solo vigilaba la forma
    literal del problema en vez de la propiedad que se quería.

    Se comprueba sobre el esquema y no sobre el texto de los ficheros: lo que
    importa es que el cuerpo esté descrito, y un ayudante interno que reciba un
    dict ya parseado es legítimo. Un modelo sale como `$ref`; un diccionario sin
    tipar, como un objeto con `additionalProperties`.
    """
    from app.api.app import create_app

    esquema = create_app().openapi()
    culpables = []
    for ruta, operaciones in esquema["paths"].items():
        for metodo, operacion in operaciones.items():
            contenido = (operacion.get("requestBody") or {}).get("content") or {}
            cuerpo = (contenido.get("application/json") or {}).get("schema") or {}
            if cuerpo.get("additionalProperties") is True and "$ref" not in cuerpo:
                culpables.append(f"{metodo.upper()} {ruta}")

    assert culpables == [], (
        f"declara un modelo en app/models/request_bodies.py para: {sorted(culpables)}"
    )
