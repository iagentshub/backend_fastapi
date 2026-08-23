"""Contrato estructural y de transporte de los proveedores configurables."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config.providers import OLLAMA_ALLOWED_INTERNAL_ORIGINS
from app.connections.ollama import OllamaProvider
from app.utils.safe_http import assert_url_allowed, safe_urlopen


def test_ollama_allowlist_is_exact_by_origin_and_port():
    assert_url_allowed(
        "http://localhost:11434/api/tags",
        allowed_internal_origins=OLLAMA_ALLOWED_INTERNAL_ORIGINS,
    )
    with pytest.raises(ValueError):
        assert_url_allowed(
            "http://localhost:5432/api/tags",
            allowed_internal_origins=OLLAMA_ALLOWED_INTERNAL_ORIGINS,
        )


@pytest.mark.parametrize(
    "host",
    [
        "https://ollama.com",
        "https://ollama.example.com:8443",
    ],
)
def test_ollama_accepts_official_and_custom_public_urls(host):
    """La allowlist es solo la excepción interna, no una lista de todo Ollama."""
    OllamaProvider.validate_config({"host": host}, purpose="save")


def test_allowed_internal_origin_is_resolved_once_and_pinned():
    response = MagicMock(status=200, reason="OK", headers={})
    connection = MagicMock()
    connection.getresponse.return_value = response
    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 11434))
    ]
    with (
        patch("app.utils.safe_http.socket.getaddrinfo", return_value=addresses) as dns,
        patch(
            "app.utils.safe_http._PinnedHTTPConnection", return_value=connection
        ) as connection_class,
    ):
        with safe_urlopen(
            "http://localhost:11434/api/tags",
            allowed_internal_origins=OLLAMA_ALLOWED_INTERNAL_ORIGINS,
        ):
            pass

    dns.assert_called_once_with("localhost", 11434, type=socket.SOCK_STREAM)
    connection_class.assert_called_once_with("localhost", "127.0.0.1", 11434, 20)


def test_public_hostname_resolving_private_is_blocked_before_connect():
    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 80))
    ]
    with (
        patch("app.config.security.socket.getaddrinfo", return_value=addresses),
        patch("app.utils.safe_http._PinnedHTTPConnection") as connection_class,
        pytest.raises(ValueError, match="red privada"),
    ):
        safe_urlopen("http://attacker.example/api/tags")
    connection_class.assert_not_called()


@pytest.mark.parametrize(
    "host",
    [
        "http://169.254.169.254",
        "http://172.18.0.2:11434",
        "http://localhost:5432",
        "file:///etc/passwd",
    ],
)
def test_ollama_rejects_internal_destinations_outside_exact_allowlist(host):
    with pytest.raises(ValueError):
        OllamaProvider.validate_config({"host": host}, purpose="test")


def test_provider_consumers_do_not_reimplement_provider_selection_or_raw_urlopen():
    app_dir = Path(__file__).parents[2] / "app"
    chat_source = (app_dir / "services/chat/__init__.py").read_text(encoding="utf-8")
    models_source = (app_dir / "services/provider_models.py").read_text(
        encoding="utf-8"
    )
    assert "conn_type ==" not in chat_source
    assert "provider ==" not in models_source
    offenders = []
    for path in app_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "urllib.request.urlopen" in source:
            offenders.append(str(path.relative_to(app_dir)))
    assert offenders == []
