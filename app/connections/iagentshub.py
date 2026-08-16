"""iAgents Hub provider — conecta con otra instancia de iAgents Hub."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from typing import Any, Dict

from app.utils.safe_http import safe_urlopen

from .base import BaseProvider, FieldDef, TestResult, register


def _login(url: str, username: str, password: str) -> str:
    """Autentica contra el hub remoto y devuelve el ga_token. Lanza excepción si falla."""
    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{url}/api/auth/login",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with safe_urlopen(req, timeout=10) as r:
        r.read()
        raw_cookies = r.headers.get_all("Set-Cookie") or []
    cookies = SimpleCookie()
    for raw_cookie in raw_cookies:
        cookies.load(raw_cookie)
    token = cookies["ga_token"].value if "ga_token" in cookies else None
    if not token:
        raise ValueError("Login correcto pero no se recibió token de sesión")
    return token


@register
class IAgentsHubProvider(BaseProvider):
    type_id = "iagentshub"
    label = "iAgents Hub"
    icon = ""
    fields = [
        FieldDef(
            "url", "URL del hub", "text", "https://hub.example.com", required=True
        ),
        FieldDef("username", "Usuario", "text", "", required=True),
        FieldDef("api_key", "Contraseña", "password", "", required=True),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        url = (config.get("url") or "").strip().rstrip("/")
        username = (config.get("username") or "").strip()
        password = (config.get("api_key") or "").strip()

        if not url:
            return TestResult(False, "Falta la URL del hub")
        if not username:
            return TestResult(False, "Falta el usuario")
        if not password:
            return TestResult(False, "Falta la contraseña")

        try:
            token = _login(url, username, password)
            # Verificar acceso real
            req = urllib.request.Request(
                f"{url}/api/auth/me",
                headers={"Cookie": f"ga_token={token}"},
            )
            with safe_urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            display = data.get("username") or username
            return TestResult(True, f"OK — conectado como {display}")

        except urllib.error.HTTPError as e:
            if e.code == 401:
                return TestResult(False, "Usuario o contraseña incorrectos")
            body = e.read().decode("utf-8", errors="replace")[:200]
            return TestResult(False, f"HTTP {e.code}", body)
        except ValueError as e:
            return TestResult(False, str(e))
        except (OSError, ValueError) as e:
            # OSError cubre URLError, timeouts y fallos de socket/DNS;
            # ValueError, el JSONDecodeError de una respuesta que no es JSON.
            # El mensaje viaja al usuario en TestResult.detail.
            return TestResult(False, "Error de conexión", str(e))
