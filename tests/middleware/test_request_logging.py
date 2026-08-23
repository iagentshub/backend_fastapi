"""Tests de RequestLoggerMiddleware: identidad verificada y ruido silenciado."""

from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.auth.passwords import create_token
from app.middleware.csrf import CsrfMiddleware
from app.middleware.request_logging import RequestLoggerMiddleware


def _request(cookies: dict[str, str] | None = None, path: str = "/api/agents"):
    galletas = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    cabeceras = [(b"cookie", galletas.encode())] if galletas else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": cabeceras,
            "client": ("10.0.0.1", 1234),
        }
    )


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _run(middleware, scope):
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _receive, send)
    return sent


def test_logger_envuelve_los_middlewares_que_pueden_rechazar_acciones():
    from app.api.app import create_app

    middlewares = [item.cls for item in create_app().user_middleware]
    assert middlewares.index(RequestLoggerMiddleware) < middlewares.index(CsrfMiddleware)


# ── Identidad en el log: firma verificada ──────────────────────────────────────


def test_token_valido_registra_el_usuario():
    token = create_token("andres")
    assert RequestLoggerMiddleware._username_for_log(_request({"ga_token": token})) == (
        "andres"
    )


def test_token_falsificado_no_se_cree():
    """El fallo que corrige esto: sembrar el audit log con la identidad ajena.

    Se fabrica un JWT con {"sub": "admin"} y firma basura. `require_auth` lo
    rechaza igual —no hay escalada— pero antes el registro decía «admin».
    """
    import base64
    import json

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    falso = f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': 'admin'})}.firmabasura"
    resultado = RequestLoggerMiddleware._username_for_log(_request({"ga_token": falso}))
    assert resultado != "admin", "el log se creyó un token sin verificar"
    assert resultado == "?invalid"


def test_token_firmado_con_otro_secreto_no_cuela():
    """Firma bien formada pero de otra clave: sigue sin ser de fiar.

    Es el caso realista de un atacante que sabe cómo se construye el token: la
    estructura es impecable, lo único que no tiene es el secreto.
    """
    from datetime import datetime, timedelta, timezone

    import jwt

    ahora = datetime.now(timezone.utc)
    ajeno = jwt.encode(
        {
            "sub": "admin",
            "gid": "admin",
            "iat": ahora,
            "exp": ahora + timedelta(hours=1),
        },
        "un-secreto-completamente-otro-y-suficientemente-largo",
        algorithm="HS256",
    )
    assert RequestLoggerMiddleware._username_for_log(_request({"ga_token": ajeno})) == (
        "?invalid"
    )


def test_token_invalido_se_distingue_de_anonimo():
    """Un token que no verifica no puede parecer una visita anónima normal."""
    anonimo = RequestLoggerMiddleware._username_for_log(_request())
    invalido = RequestLoggerMiddleware._username_for_log(
        _request({"ga_token": "esto-no-es-un-jwt"})
    )
    assert anonimo == "-"
    assert invalido == "?invalid"


def test_el_invitado_se_registra_con_su_id():
    """El invitado no tiene cookie propia: viaja en `ga_token` como todos.

    Este test congelaba una cookie `ga_guest` que ningún emisor puso nunca, y de
    paso daba por bueno registrar a todos los invitados como "guest". Su id los
    distingue, que es lo que se necesita para seguir a uno por el log.
    """
    token = create_token("guest:ab12cd34ef56")
    assert RequestLoggerMiddleware._username_for_log(_request({"ga_token": token})) == (
        "guest:ab12cd34ef56"
    )


def test_sin_cookies_es_anonimo():
    assert RequestLoggerMiddleware._username_for_log(_request()) == "-"


@pytest.mark.asyncio
async def test_eventos_de_la_ruta_heredan_ip_y_usuario(monkeypatch):
    from app.utils import flog

    vistos: list[dict[str, str]] = []
    original_extra = flog._extra

    def capturar(ip, username, source):
        extra = original_extra(ip, username, source)
        vistos.append(extra)
        return extra

    monkeypatch.setattr(flog, "_extra", capturar)
    token = create_token("andres")
    request = _request({"ga_token": token})

    async def ruta(scope, receive, send):
        flog.ok("agente creado")
        await Response(status_code=201)(scope, receive, send)

    sent = await _run(RequestLoggerMiddleware(ruta), request.scope)

    assert sent[0]["status"] == 201
    assert vistos[0]["ip"] == "10.0.0.1"
    assert vistos[0]["username"] == "andres"


@pytest.mark.asyncio
async def test_peticion_anonima_atribuye_eventos_a_su_ip(monkeypatch):
    from app.utils import flog

    vistos: list[dict[str, str]] = []
    original_extra = flog._extra

    def capturar(ip, username, source):
        extra = original_extra(ip, username, source)
        vistos.append(extra)
        return extra

    monkeypatch.setattr(flog, "_extra", capturar)

    async def ruta(scope, receive, send):
        flog.info("acción pública")
        await Response(status_code=200)(scope, receive, send)

    await _run(RequestLoggerMiddleware(ruta), _request().scope)

    assert vistos[0]["ip"] == "10.0.0.1"
    assert vistos[0]["username"] == "-"


@pytest.mark.asyncio
async def test_excepcion_se_registra_y_limpia_el_contexto(monkeypatch):
    from app.utils import flog

    vistos: list[dict[str, str]] = []
    original_extra = flog._extra

    def capturar(ip, username, source):
        extra = original_extra(ip, username, source)
        vistos.append(extra)
        return extra

    monkeypatch.setattr(flog, "_extra", capturar)

    async def ruta(scope, receive, send):
        raise RuntimeError("fallo de prueba")

    with pytest.raises(RuntimeError, match="fallo de prueba"):
        await _run(RequestLoggerMiddleware(ruta), _request().scope)

    assert vistos[-1]["ip"] == "10.0.0.1"
    assert vistos[-1]["username"] == "-"
    assert original_extra(None, None, "BE") == {
        "ip": "-",
        "username": "-",
        "source": "BE",
    }


# ── Silenciado de sondas de vida ───────────────────────────────────────────────


@pytest.mark.parametrize("codigo", [200, 204, 304])
def test_health_ok_no_se_registra(codigo):
    peticion = _request(path="/api/health")
    assert RequestLoggerMiddleware._silenciar(peticion, codigo)


@pytest.mark.parametrize("codigo", [500, 503])
def test_health_caido_si_se_registra(codigo):
    """Un health check que falla es justo lo que hay que ver en el log."""
    peticion = _request(path="/api/health")
    assert not RequestLoggerMiddleware._silenciar(peticion, codigo)


def test_las_rutas_normales_no_se_silencian():
    assert not RequestLoggerMiddleware._silenciar(_request(path="/api/agents"), 200)


def test_la_escotilla_devuelve_el_comportamiento_anterior(monkeypatch):
    import app.config.logging as cfg

    monkeypatch.setattr(cfg, "LOG_HEALTH", True)
    assert not RequestLoggerMiddleware._silenciar(_request(path="/api/health"), 200)
