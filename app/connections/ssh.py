"""SSH / SFTP connection provider."""

from __future__ import annotations

import socket
from typing import Any, Dict

from .base import BaseProvider, FieldDef, TestResult, register


@register
class SSHProvider(BaseProvider):
    type_id = "ssh"
    category = "machine"
    label = "SSH / SFTP"
    icon = "🖥️"
    fields = [
        FieldDef("host", "Host", "text", "192.168.1.100", required=True),
        FieldDef("port", "Puerto", "number", default="22", required=True),
        FieldDef(
            "os",
            "Sistema operativo",
            "select",
            options=[
                {"value": "linux", "label": "Linux"},
                {"value": "macos", "label": "macOS (Apple)"},
                {"value": "windows", "label": "Windows"},
            ],
            required=True,
        ),
        FieldDef("username", "Usuario", "text", "ubuntu", required=True),
        FieldDef("password", "Contraseña", "password", ""),
        FieldDef(
            "ssh_key",
            "Clave SSH privada (PEM)",
            "textarea",
            "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
        ),
        FieldDef("readonly", "Solo lectura (prohibir escritura/borrado)", "checkbox"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        host = (config.get("host") or "").strip()
        port = int(config.get("port") or 22)
        if not host:
            return TestResult(False, "Falta el host")
        try:
            with socket.create_connection((host, port), timeout=8) as s:
                # SSH servers send a banner on connect
                banner = s.recv(64).decode("utf-8", errors="replace").strip()
            detail = f"Banner: {banner}" if banner else ""
            return TestResult(True, f"OK — Puerto {port} accesible en {host}", detail)
        except socket.timeout:
            return TestResult(False, f"Timeout conectando a {host}:{port}")
        except Exception as e:
            return TestResult(False, "No se puede conectar", str(e))
