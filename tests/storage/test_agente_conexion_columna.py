"""`connection_id` es columna, no un campo dentro del blob JSON.

La pregunta «¿qué agentes usan esta conexión?» se resolvía trayendo todos los
agentes de la instalación y filtrándolos en Python, mientras la pregunta
equivalente sobre `user_agent_preferences` ya era un `COUNT(*)` en la misma
función. Sin columna no había índice que consultar; ahora sí.
"""

from __future__ import annotations

import asyncio

from app.config.data import AGENTS_DIR
from app.storage.agent_storage import AgentStorage


def _guardar(storage: AgentStorage, agent_id: str, connection_id: str | None):
    payload = {"id": agent_id, "name": agent_id, "connection_id": connection_id}
    return asyncio.run(storage.save(payload, "private", owner_id="duenyo"))


def test_la_columna_se_rellena_al_guardar(tmp_data_dir):
    storage = AgentStorage(AGENTS_DIR)
    _guardar(storage, "uno", "conn-a")
    _guardar(storage, "dos", "conn-a")
    _guardar(storage, "tres", "conn-b")
    _guardar(storage, "cuatro", None)

    assert asyncio.run(storage.count_by_connection("conn-a")) == 2
    assert asyncio.run(storage.count_by_connection("conn-b")) == 1
    assert asyncio.run(storage.count_by_connection("conn-inexistente")) == 0


def test_la_columna_sigue_al_blob_cuando_el_agente_cambia_de_conexion(tmp_data_dir):
    """El JSON es la fuente y la columna su espejo: si se separan, el COUNT
    empieza a responder por un estado que ya no existe."""
    storage = AgentStorage(AGENTS_DIR)
    _guardar(storage, "movil", "conn-vieja")
    assert asyncio.run(storage.count_by_connection("conn-vieja")) == 1

    _guardar(storage, "movil", "conn-nueva")
    assert asyncio.run(storage.count_by_connection("conn-vieja")) == 0
    assert asyncio.run(storage.count_by_connection("conn-nueva")) == 1

    # Y al quitarla del agente, la columna queda vacía en vez de conservar
    # la anterior.
    _guardar(storage, "movil", None)
    assert asyncio.run(storage.count_by_connection("conn-nueva")) == 0


def test_el_agente_sigue_exponiendo_connection_id(tmp_data_dir):
    """La columna es interna; el recurso que ve el cliente no cambia."""
    storage = AgentStorage(AGENTS_DIR)
    _guardar(storage, "visible", "conn-a")
    agente = asyncio.run(storage.get("visible", owner_id="duenyo"))
    assert agente is not None
    assert agente["connection_id"] == "conn-a"
