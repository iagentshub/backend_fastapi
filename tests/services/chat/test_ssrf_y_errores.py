"""SSRF en la URL de la conexión y saneado de los errores que van al cliente.

Cubre las mejoras #01 y #17 de la revisión: una URL de proveedor la escribe el
usuario, así que puede apuntar a la red interna del despliegue; y el bloque de
excepciones de `stream_chat` reenviaba al navegador el cuerpo de esa respuesta
interna, el host del fallo de red y `str(exc)` sin filtrar.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from app.services.chat import stream_chat
from tests.services.chat._helpers import _make_agent, _make_conn, _skill_storage


async def _recoger(agent, conn) -> list[dict]:
    """Ejecuta stream_chat y devuelve los eventos SSE ya parseados."""
    eventos = []
    async for frame in stream_chat(agent, conn, [{"role": "user", "content": "hola"}], _skill_storage()):
        for linea in frame.splitlines():
            if linea.startswith("data: "):
                eventos.append(json.loads(linea[6:]))
    return eventos


# ── #01 · SSRF ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",  # metadata de cloud
        "http://127.0.0.1:8765/v1",                 # el propio backend
        "http://10.0.0.5/v1",                       # red privada
        "http://[::ffff:127.0.0.1]/v1",             # IPv4-mapped IPv6
    ],
)
@pytest.mark.asyncio
async def test_openai_compat_rechaza_url_hacia_red_interna(url):
    conn = {**_make_conn("openai"), "url": url}

    with patch("urllib.request.urlopen") as urlopen:
        eventos = await _recoger(_make_agent("openai"), conn)

    assert urlopen.call_count == 0, "no debe llegar a hacer la petición"
    assert eventos[-1]["type"] == "error"
    assert eventos[-1]["code"] == "unsafe_url"


@pytest.mark.asyncio
async def test_claude_rechaza_url_hacia_red_interna():
    conn = {**_make_conn("claude"), "url": "http://169.254.169.254/latest/meta-data"}

    with patch("urllib.request.urlopen") as urlopen:
        eventos = await _recoger(_make_agent("claude"), conn)

    assert urlopen.call_count == 0
    assert eventos[-1]["code"] == "unsafe_url"


@pytest.mark.asyncio
async def test_ollama_sigue_permitiendo_loopback():
    """La excepción deliberada: un Ollama en localhost es el caso normal."""
    conn = {"type": "ollama", "host": "http://localhost:11434", "model": "llama3"}
    respuesta = json.dumps({"message": {"content": "Hola"}, "done": True}).encode()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter([respuesta])

    with patch("urllib.request.urlopen", return_value=_Resp()):
        eventos = await _recoger(_make_agent("ollama", "llama3"), conn)

    assert eventos[-1]["type"] == "done"


# ── #17 · lo que se le cuenta al cliente ────────────────────────────────────

def _http_error(cuerpo: str, code: int = 403):
    return urllib.error.HTTPError(
        "https://example.com/v1/chat/completions",
        code,
        "Forbidden",
        {},
        BytesIO(cuerpo.encode()),
    )


@pytest.mark.asyncio
async def test_cuerpo_no_json_no_llega_al_cliente():
    """Antes viajaban 500 bytes crudos del host que respondiese."""
    secreto = "<html>token interno: s3cr3t-de-red-interna</html>"

    with patch("urllib.request.urlopen", side_effect=_http_error(secreto)):
        eventos = await _recoger(_make_agent("openai"), _make_conn("openai"))

    error = eventos[-1]
    assert error["code"] == "provider_http_error"
    assert "s3cr3t" not in error["message"]


@pytest.mark.asyncio
async def test_mensaje_de_negocio_del_proveedor_si_llega():
    """Distinguir el error de negocio del proveedor del volcado de red."""
    cuerpo = json.dumps({"error": {"message": "You exceeded your current quota"}})

    with patch("urllib.request.urlopen", side_effect=_http_error(cuerpo, 429)):
        eventos = await _recoger(_make_agent("openai"), _make_conn("openai"))

    assert "exceeded your current quota" in eventos[-1]["message"]


@pytest.mark.asyncio
async def test_urlerror_no_revela_el_host():
    fallo = urllib.error.URLError("Connection refused to 10.0.0.7:5432")

    with patch("urllib.request.urlopen", side_effect=fallo):
        eventos = await _recoger(_make_agent("openai"), _make_conn("openai"))

    error = eventos[-1]
    assert error["code"] == "provider_unreachable"
    assert "10.0.0.7" not in error["message"]


@pytest.mark.asyncio
async def test_excepcion_inesperada_no_revela_str_exc():
    fallo = RuntimeError("no such column: users.secreto — /srv/app/storage/db.py")

    with patch("urllib.request.urlopen", side_effect=fallo):
        eventos = await _recoger(_make_agent("openai"), _make_conn("openai"))

    error = eventos[-1]
    assert error["code"] == "internal_error"
    assert "secreto" not in error["message"]
    assert "/srv/app" not in error["message"]
