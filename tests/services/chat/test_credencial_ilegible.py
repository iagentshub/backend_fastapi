"""El chat no manda al proveedor una credencial que no se pudo descifrar.

Antes, `decrypt()` devolvía el ciphertext y `enc:gAAAAA…` viajaba en la
cabecera `Authorization`: el usuario recibía un 401 del proveedor y creía que
su clave había caducado. Ahora el stream corta antes de la petición con un
código propio.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.services.chat import stream_chat
from app.storage.crypto import UNREADABLE_FIELDS, UNREADABLE_FLAG
from tests.services.chat._helpers import _make_agent, _make_conn, _skill_storage


async def _recoger(agent, conn) -> list[dict]:
    eventos = []
    async for frame in stream_chat(
        agent, conn, [{"role": "user", "content": "hola"}], _skill_storage()
    ):
        for linea in frame.splitlines():
            if linea.startswith("data: "):
                eventos.append(json.loads(linea[6:]))
    return eventos


@pytest.mark.asyncio
async def test_no_llama_al_proveedor_con_la_credencial_ilegible():
    conn = {
        **_make_conn("openai"),
        "api_key": "",
        UNREADABLE_FLAG: True,
        UNREADABLE_FIELDS: ["api_key"],
    }

    with patch("app.services.chat.safe_urlopen") as urlopen:
        eventos = await _recoger(_make_agent("openai"), conn)

    assert urlopen.call_count == 0, "no debe llegar a hacer la petición"
    assert eventos[-1]["type"] == "error"
    assert eventos[-1]["code"] == "credential_unreadable"


@pytest.mark.asyncio
async def test_una_conexion_legible_sigue_funcionando():
    """La guarda solo actúa sobre la marca: sin ella, el flujo es el de siempre."""
    from tests.services.chat._helpers import _sse_done_response

    with patch(
        "app.services.chat.safe_urlopen", return_value=_sse_done_response("Hola")
    ):
        eventos = await _recoger(_make_agent("openai"), _make_conn("openai"))

    assert eventos[-1]["type"] == "done"
