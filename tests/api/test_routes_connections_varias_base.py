"""Varias conexiones Ollama base: todas tienen que salir del listado.

La versión anterior de este listado tomaba `base_conns[0]` y del resto no se
volvía a hablar. Dos Ollama sin modelo son un estado legítimo —la tabla no impone
unicidad por dueño ni por tipo— y es la configuración obvia de quien tiene uno en
el portátil y otro en la máquina con GPU. El dato quedaba bien guardado y `/raw`
seguía devolviéndolo, así que el usuario lo veía en una pantalla y no en la otra.

El listado se rehizo con paginación por cursor y la expansión pasó a
`_model_variants`, que trabaja conexión a conexión, así que el defecto ya no
puede darse por construcción. Estas pruebas se quedan como la red que lo fija:
son la propiedad, no la implementación.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.storage import crypto


def _crear_ollama(client, etiqueta: str, host: str) -> dict:
    r = client.post(
        "/api/connections",
        json={"type": "ollama", "label": etiqueta, "host": host, "api_key": "k"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _listar(client) -> list[dict]:
    r = client.get("/api/v2/connections", params={"include_models": "true"})
    assert r.status_code == 200, r.text
    return r.json()["items"]


@pytest.fixture()
def rotar_clave(monkeypatch):
    """El secreto de cifrado cambiado DESPUÉS de guardar, como en la mejora #04."""

    def _rotar() -> None:
        monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))

    return _rotar


def test_las_dos_conexiones_base_se_expanden(admin_client):
    portatil = _crear_ollama(admin_client, "Ollama portátil", "http://localhost:11434")
    gpu = _crear_ollama(admin_client, "Ollama GPU", "http://gpu.local:11434")

    catalogos = {portatil["host"]: ["llama3"], gpu["host"]: ["mixtral"]}
    with patch(
        "app.connections.ollama.OllamaProvider.fetch_models",
        side_effect=lambda conn: catalogos[conn["host"]],
    ):
        items = _listar(admin_client)

    modelos = {
        variante["model"]
        for item in items
        for variante in item.get("model_variants", [])
    }
    assert "llama3" in modelos
    assert "mixtral" in modelos, "la segunda conexión base se quedaba sin expandir"


def test_una_credencial_rota_no_se_lleva_por_delante_a_las_demas(
    admin_client, rotar_clave
):
    """Una credencial ilegible solo puede cancelar su propia expansión."""
    _crear_ollama(admin_client, "Ollama rota", "http://rota.local:11434")
    rotar_clave()
    _crear_ollama(admin_client, "Ollama sana", "http://sana.local:11434")

    with patch(
        "app.connections.ollama.OllamaProvider.fetch_models",
        side_effect=lambda conn: ["gemma"],
    ):
        items = _listar(admin_client)

    # La rota sigue visible con su distintivo, para que se pueda reparar.
    assert any(item.get("credentials_unreadable") for item in items)
    modelos = {
        variante["model"]
        for item in items
        for variante in item.get("model_variants", [])
    }
    assert "gemma" in modelos, (
        "una credencial ilegible cancelaba la expansión de las demás"
    )
