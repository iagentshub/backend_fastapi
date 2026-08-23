"""Contratos ASGI que una prueba de ruta o de ``dispatch`` no puede cubrir."""

from __future__ import annotations

import asyncio

import pytest
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.middleware.csrf import CsrfMiddleware
from app.middleware.licenses import LicenseGateMiddleware
from app.middleware.locale import LocaleMiddleware, get_locale
from app.middleware.request_logging import RequestLoggerMiddleware
from app.middleware.security import SecurityHeadersMiddleware

CUSTOM_MIDDLEWARE = (
    SecurityHeadersMiddleware,
    LocaleMiddleware,
    LicenseGateMiddleware,
    CsrfMiddleware,
    RequestLoggerMiddleware,
)


def _scope(*, path: str = "/stream", headers=()):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": ("10.0.0.1", 1234),
        "server": ("testserver", 80),
    }


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def test_los_middlewares_propios_no_usan_base_http_middleware():
    assert all(not issubclass(item, BaseHTTPMiddleware) for item in CUSTOM_MIDDLEWARE)


@pytest.mark.asyncio
@pytest.mark.parametrize("middleware_cls", CUSTOM_MIDDLEWARE)
@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_protocolos_no_http_pasan_sin_interferencia(middleware_cls, scope_type):
    received = []

    async def app(scope, receive, send):
        received.append(scope["type"])

    async def receive():
        return {"type": f"{scope_type}.connect"}

    async def send(message):
        raise AssertionError(f"el middleware emitió un mensaje inesperado: {message}")

    await middleware_cls(app)({"type": scope_type}, receive, send)
    assert received == [scope_type]


@pytest.mark.asyncio
async def test_security_modifica_el_unico_response_start_y_respeta_valores_previos():
    sent = []

    async def app(scope, receive, send):
        await Response(
            "ok", status_code=403, headers={"X-Frame-Options": "SAMEORIGIN"}
        )(scope, receive, send)

    async def send(message):
        sent.append(message)

    await SecurityHeadersMiddleware(app)(_scope(), _receive, send)

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    headers = Headers(raw=starts[0]["headers"])
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["content-security-policy"].startswith("default-src 'self'")


@pytest.mark.asyncio
async def test_locale_esta_aislado_entre_peticiones_concurrentes():
    ready = 0
    both_ready = asyncio.Event()
    observed = []

    async def app(scope, receive, send):
        nonlocal ready
        ready += 1
        if ready == 2:
            both_ready.set()
        await both_ready.wait()
        observed.append((Headers(scope=scope)["accept-language"], get_locale()))
        await Response("ok")(scope, receive, send)

    async def run(language):
        async def send(message):
            return None

        scope = _scope(headers=[(b"accept-language", language.encode())])
        await LocaleMiddleware(app)(scope, _receive, send)

    await asyncio.gather(run("es"), run("en"))
    assert sorted(observed) == [("en", "en"), ("es", "es")]
    assert get_locale() == "es"


@pytest.mark.asyncio
async def test_logger_observa_todo_el_stream_sin_amortiguarlo(monkeypatch):
    logged = []
    client_messages = []

    monkeypatch.setattr(
        "app.middleware.request_logging.flog.info",
        lambda message, **context: logged.append((message, context)),
    )

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"uno", "more_body": True})
        assert not logged, "el logger cerró la petición antes de acabar el stream"
        await send({"type": "http.response.body", "body": b"dos"})

    async def send(message):
        client_messages.append(message)

    await RequestLoggerMiddleware(app)(_scope(), _receive, send)

    assert [item["type"] for item in client_messages] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]
    assert len(logged) == 1
    assert "→ 200" in logged[0][0]
    assert "; bytes=6)" in logged[0][0]


@pytest.mark.asyncio
async def test_logger_registra_cancelacion_y_la_propaga(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "app.middleware.request_logging.flog.warning",
        lambda message, **context: warnings.append(message),
    )

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"parcial", "more_body": True})
        raise asyncio.CancelledError

    async def send(message):
        return None

    with pytest.raises(asyncio.CancelledError):
        await RequestLoggerMiddleware(app)(_scope(), _receive, send)

    assert len(warnings) == 1
    assert "→ cancelled" in warnings[0]
    assert "; bytes=7)" in warnings[0]


@pytest.mark.asyncio
async def test_logger_distingue_fallo_despues_de_empezar_respuesta(monkeypatch):
    errors = []
    monkeypatch.setattr(
        "app.middleware.request_logging.flog.error",
        lambda message, **context: errors.append((message, context)),
    )

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("stream roto")

    async def send(message):
        return None

    with pytest.raises(RuntimeError, match="stream roto"):
        await RequestLoggerMiddleware(app)(_scope(), _receive, send)

    assert len(errors) == 1
    assert "→ failed" in errors[0][0]
    assert errors[0][1]["exc_info"] is True
