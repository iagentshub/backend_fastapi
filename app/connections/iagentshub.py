"""iAgents Hub provider — conecta con otra instancia de iAgents Hub."""
from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request
from typing import Any, Dict

from app.config.security import assert_safe_url

from .base import BaseProvider, FieldDef, TestResult, register


def _login(url: str, username: str, password: str) -> str:
    """Autentica contra el hub remoto y devuelve el ga_token. Lanza excepción si falla."""
    assert_safe_url(url)  # C2: prevenir SSRF a redes privadas
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{url}/api/auth/login",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=10) as r:
        r.read()
    token = next((c.value for c in jar if c.name == "ga_token"), None)
    if not token:
        raise ValueError("Login correcto pero no se recibió token de sesión")
    return token


@register
class IAgentsHubProvider(BaseProvider):
    type_id = "iagentshub"
    label = "iAgents Hub"
    icon = ""
    fields = [
        FieldDef("url", "URL del hub", "text", "https://hub.example.com", required=True),
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
            with urllib.request.urlopen(req, timeout=10) as r:
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
        except Exception as e:
            return TestResult(False, "Error de conexión", str(e))
