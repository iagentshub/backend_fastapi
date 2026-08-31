"""Las etiquetas son un catálogo cerrado en los cuatro recursos, no en tres.

El agente se quedó sin comprobación: solo miraba si alguien se autoproclamaba
`official` —lo importante, y eso sí estaba defendido— mientras su mensaje de
error describía una validación contra el catálogo cuya rama era inalcanzable.
El valor llegaba intacto al índice transversal `resource_labels`, que es de
donde lo leen consumidores que sí dan por hecho la pertenencia al catálogo.

Esta prueba recorre los cuatro a la vez a propósito: el defecto no fue escribir
mal la comprobación, fue que un recurso nuevo no la tuviera. Un quinto que
llegue sin ella también debe aparecer aquí.
"""

from __future__ import annotations

import pytest

# Los tres recursos de catálogo cuelgan del scope; el agente no.
_RECURSOS = {
    "/api/agents": {"name": "Agente etiquetas", "system_prompt": "x", "model": "gpt-4o"},
    "/api/skills/private": {"name": "Skill etiquetas", "description": "d", "content": "c"},
    "/api/prompts/private": {"name": "Prompt etiquetas", "description": "d", "content": "c", "alias": "alias-etiquetas"},
    "/api/tools/private": {"name": "Tool etiquetas", "description": "d", "language": "python", "content": "print(1)"},
}


@pytest.mark.parametrize("ruta,payload", list(_RECURSOS.items()))
def test_una_label_fuera_del_catalogo_se_rechaza(admin_client, ruta, payload):
    r = admin_client.post(ruta, json={**payload, "labels": ["no-existe-en-el-catalogo"]})
    assert r.status_code == 422, f"{ruta} aceptó una label inventada: {r.text}"
    assert r.json()["detail"]["code"] == "invalid_field"


@pytest.mark.parametrize("ruta,payload", list(_RECURSOS.items()))
def test_una_label_del_catalogo_se_acepta(admin_client, ruta, payload):
    r = admin_client.post(ruta, json={**payload, "labels": ["draft"]})
    assert r.status_code == 200, r.text
    assert "draft" in r.json()["labels"]


@pytest.mark.parametrize("ruta,payload", list(_RECURSOS.items()))
def test_labels_mutuamente_excluyentes_se_rechazan(admin_client, ruta, payload):
    r = admin_client.post(ruta, json={**payload, "labels": ["private", "public"]})
    assert r.status_code == 422, f"{ruta} aceptó dos visibilidades: {r.text}"


@pytest.mark.parametrize("ruta,payload", list(_RECURSOS.items()))
def test_la_lista_de_labels_tiene_cota(admin_client, ruta, payload):
    """`sync_labels` hace un INSERT por etiqueta distinta y no había techo.

    El de cuerpo que lo frenaría, `max_request_bytes`, vale 0 por defecto.
    """
    r = admin_client.post(ruta, json={**payload, "labels": [f"l{i}" for i in range(5000)]})
    assert r.status_code == 422, f"{ruta} aceptó 5000 etiquetas: {r.status_code}"
