"""Varias conexiones Ollama base: todas tienen que salir del listado.

`_expand_model_connections` tomaba `base_conns[0]` y del resto no se volvía a
hablar. Dos Ollama sin modelo son un estado legítimo —la tabla no impone
unicidad por dueño ni por tipo— y es la configuración obvia de quien tiene uno
en el portátil y otro en la máquina con GPU. El dato quedaba bien guardado y
`/raw` seguía devolviéndolo, así que el usuario lo veía en una pantalla y no en
la otra, sin ningún error en medio.
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


@pytest.fixture()
def rotar_clave(monkeypatch):
    """El secreto de cifrado cambiado DESPUÉS de guardar, como en la mejora #04."""

    def _rotar() -> None:
        monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))

    return _rotar


def test_las_dos_conexiones_base_se_expanden(admin_client):
    portatil = _crear_ollama(admin_client, "Ollama portátil", "http://localhost:11434")
    gpu = _crear_ollama(admin_client, "Ollama GPU", "http://gpu.local:11434")

    catalogos = {
        portatil["host"]: ["llama3"],
        gpu["host"]: ["mixtral"],
    }
    with patch(
        "app.connections.ollama.OllamaProvider.fetch_models",
        side_effect=lambda conn: catalogos[conn["host"]],
    ):
        conns = admin_client.get("/api/connections").json()

    nombres = {c["name"] for c in conns}
    assert "llama3" in nombres
    assert "mixtral" in nombres, "la segunda conexión base desaparecía del listado"


def test_una_credencial_rota_no_se_lleva_por_delante_a_las_demas(
    admin_client, rotar_clave
):
    """El `return` anticipado dejaba fuera también a las base sanas."""
    _crear_ollama(admin_client, "Ollama rota", "http://rota.local:11434")
    rotar_clave()
    sana = _crear_ollama(admin_client, "Ollama sana", "http://sana.local:11434")

    with patch(
        "app.connections.ollama.OllamaProvider.fetch_models",
        side_effect=lambda conn: ["gemma"],
    ):
        conns = admin_client.get("/api/connections").json()

    # La rota sigue visible con su distintivo, para que se pueda reparar.
    assert any(c.get("credentials_unreadable") for c in conns)
    # Y la sana se expande igual, que es lo que antes no ocurría.
    assert "gemma" in {c["name"] for c in conns}, (
        "una credencial ilegible cancelaba la expansión de todas las demás"
    )
    assert sana["id"] not in {c["id"] for c in conns if c.get("model") is None}
