"""Tests de RequestLoggerMiddleware: identidad verificada y ruido silenciado."""

from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.auth.passwords import create_token
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


def test_invitado_sin_token():
    assert RequestLoggerMiddleware._username_for_log(_request({"ga_guest": "1"})) == (
        "guest"
    )


def test_sin_cookies_es_anonimo():
    assert RequestLoggerMiddleware._username_for_log(_request()) == "-"


# ── Silenciado de sondas de vida ───────────────────────────────────────────────


@pytest.mark.parametrize("codigo", [200, 204, 304])
def test_health_ok_no_se_registra(codigo):
    peticion = _request(path="/api/health")
    assert RequestLoggerMiddleware._silenciar(peticion, Response(status_code=codigo))


@pytest.mark.parametrize("codigo", [500, 503])
def test_health_caido_si_se_registra(codigo):
    """Un health check que falla es justo lo que hay que ver en el log."""
    peticion = _request(path="/api/health")
    assert not RequestLoggerMiddleware._silenciar(
        peticion, Response(status_code=codigo)
    )


def test_las_rutas_normales_no_se_silencian():
    assert not RequestLoggerMiddleware._silenciar(
        _request(path="/api/agents"), Response(status_code=200)
    )


def test_la_escotilla_devuelve_el_comportamiento_anterior(monkeypatch):
    import app.config.logging as cfg

    monkeypatch.setattr(cfg, "LOG_HEALTH", True)
    assert not RequestLoggerMiddleware._silenciar(
        _request(path="/api/health"), Response(status_code=200)
    )
