"""Tests del provider SSH — test() sin conexiones reales."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from app.connections.ssh import SSHProvider


# ── Metadatos del provider ─────────────────────────────────────────────────────


def test_category_is_machine():
    assert SSHProvider.category == "machine"


def test_type_id():
    assert SSHProvider.type_id == "ssh"


def test_fields_present():
    keys = [f.key for f in SSHProvider.fields]
    assert "host" in keys
    assert "port" in keys
    assert "os" in keys
    assert "username" in keys
    assert "password" in keys
    assert "ssh_key" in keys
    assert "readonly" in keys


def test_ssh_key_field_is_textarea():
    field = next(f for f in SSHProvider.fields if f.key == "ssh_key")
    assert field.type == "textarea"


def test_readonly_field_is_checkbox():
    field = next(f for f in SSHProvider.fields if f.key == "readonly")
    assert field.type == "checkbox"


def test_os_field_has_options():
    field = next(f for f in SSHProvider.fields if f.key == "os")
    assert field.type == "select"
    values = [o["value"] for o in field.options]
    assert "linux" in values
    assert "macos" in values
    assert "windows" in values


def test_port_default_is_22():
    field = next(f for f in SSHProvider.fields if f.key == "port")
    assert field.default == "22"


def test_host_is_required():
    field = next(f for f in SSHProvider.fields if f.key == "host")
    assert field.required is True


# ── Lógica de test() ───────────────────────────────────────────────────────────


def test_test_empty_host_fails():
    result = SSHProvider.test({"host": "", "port": 22})
    assert result.ok is False
    assert "host" in result.message.lower()


def test_test_missing_host_fails():
    result = SSHProvider.test({"port": 22})
    assert result.ok is False


def _mock_socket_with_banner(banner: bytes = b"SSH-2.0-OpenSSH_8.9\r\n"):
    """Devuelve un mock de socket que envía un banner SSH al recv()."""
    sock = MagicMock()
    sock.recv.return_value = banner
    sock.__enter__ = lambda s: s
    sock.__exit__ = MagicMock(return_value=False)
    return sock


def test_test_success_reads_banner():
    sock = _mock_socket_with_banner(b"SSH-2.0-OpenSSH_8.9\r\n")
    with patch("socket.create_connection", return_value=sock):
        result = SSHProvider.test({"host": "192.168.1.10", "port": 22})
    assert result.ok is True
    assert "SSH-2.0" in result.detail


def test_test_success_empty_banner():
    sock = _mock_socket_with_banner(b"")
    with patch("socket.create_connection", return_value=sock):
        result = SSHProvider.test({"host": "192.168.1.10", "port": 22})
    assert result.ok is True
    assert result.detail == ""


def test_test_timeout():
    with patch("socket.create_connection", side_effect=socket.timeout()):
        result = SSHProvider.test({"host": "10.0.0.1", "port": 22})
    assert result.ok is False
    assert "timeout" in result.message.lower()


def test_test_connection_refused():
    with patch(
        "socket.create_connection", side_effect=ConnectionRefusedError("refused")
    ):
        result = SSHProvider.test({"host": "10.0.0.1", "port": 22})
    assert result.ok is False
    assert result.ok is False


def test_test_uses_custom_port():
    sock = _mock_socket_with_banner()
    with patch("socket.create_connection", return_value=sock) as mock_conn:
        SSHProvider.test({"host": "10.0.0.1", "port": 2222})
    mock_conn.assert_called_once_with(("10.0.0.1", 2222), timeout=8)


def test_test_default_port_when_missing():
    """Si no se pasa port, debe usar 22."""
    sock = _mock_socket_with_banner()
    with patch("socket.create_connection", return_value=sock) as mock_conn:
        SSHProvider.test({"host": "10.0.0.1"})
    mock_conn.assert_called_once_with(("10.0.0.1", 22), timeout=8)
