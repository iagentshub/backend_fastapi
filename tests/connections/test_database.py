"""Tests de los providers de base de datos — test() sin conexiones reales."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from app.connections.database import MySQLProvider, OracleProvider, PostgreSQLProvider


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_socket():
    sock = MagicMock()
    sock.__enter__ = lambda s: s
    sock.__exit__ = MagicMock(return_value=False)
    return sock


def _field(provider, key):
    return next((f for f in provider.fields if f.key == key), None)


# ══════════════════════════════════════════════════════════════════════════════
# PostgreSQL
# ══════════════════════════════════════════════════════════════════════════════


class TestPostgreSQL:
    def test_category(self):
        assert PostgreSQLProvider.category == "database"

    def test_type_id(self):
        assert PostgreSQLProvider.type_id == "db-postgres"

    def test_has_required_fields(self):
        keys = [f.key for f in PostgreSQLProvider.fields]
        for k in ("host", "port", "dbname", "username", "password", "readonly", "ssl"):
            assert k in keys, f"Campo faltante: {k}"

    def test_port_default(self):
        assert _field(PostgreSQLProvider, "port").default == "5432"

    def test_host_required(self):
        assert _field(PostgreSQLProvider, "host").required is True

    def test_readonly_is_checkbox(self):
        assert _field(PostgreSQLProvider, "readonly").type == "checkbox"

    def test_ssl_is_checkbox(self):
        assert _field(PostgreSQLProvider, "ssl").type == "checkbox"

    def test_test_empty_host_fails(self):
        result = PostgreSQLProvider.test({"host": "", "port": 5432, "dbname": "mydb"})
        assert result.ok is False

    def test_test_success(self):
        with patch("socket.create_connection", return_value=_mock_socket()):
            result = PostgreSQLProvider.test(
                {"host": "db.local", "port": 5432, "dbname": "mydb"}
            )
        assert result.ok is True
        assert "db.local" in result.message

    def test_test_includes_dbname_in_message(self):
        with patch("socket.create_connection", return_value=_mock_socket()):
            result = PostgreSQLProvider.test(
                {"host": "db.local", "port": 5432, "dbname": "prod"}
            )
        assert "prod" in result.message

    def test_test_timeout(self):
        with patch("socket.create_connection", side_effect=socket.timeout()):
            result = PostgreSQLProvider.test(
                {"host": "db.local", "port": 5432, "dbname": "x"}
            )
        assert result.ok is False
        assert "timeout" in result.message.lower()

    def test_test_connection_error(self):
        with patch(
            "socket.create_connection", side_effect=ConnectionRefusedError("refused")
        ):
            result = PostgreSQLProvider.test(
                {"host": "db.local", "port": 5432, "dbname": "x"}
            )
        assert result.ok is False

    def test_test_uses_default_port_when_missing(self):
        with patch(
            "socket.create_connection", return_value=_mock_socket()
        ) as mock_conn:
            PostgreSQLProvider.test({"host": "db.local", "dbname": "x"})
        mock_conn.assert_called_once_with(("db.local", 5432), timeout=8)


# ══════════════════════════════════════════════════════════════════════════════
# MySQL / MariaDB
# ══════════════════════════════════════════════════════════════════════════════


class TestMySQL:
    def test_category(self):
        assert MySQLProvider.category == "database"

    def test_type_id(self):
        assert MySQLProvider.type_id == "db-mysql"

    def test_has_required_fields(self):
        keys = [f.key for f in MySQLProvider.fields]
        for k in ("host", "port", "dbname", "username", "password", "readonly"):
            assert k in keys, f"Campo faltante: {k}"

    def test_port_default(self):
        assert _field(MySQLProvider, "port").default == "3306"

    def test_readonly_is_checkbox(self):
        assert _field(MySQLProvider, "readonly").type == "checkbox"

    def test_test_empty_host_fails(self):
        result = MySQLProvider.test({"host": "", "port": 3306, "dbname": "mydb"})
        assert result.ok is False

    def test_test_success(self):
        with patch("socket.create_connection", return_value=_mock_socket()):
            result = MySQLProvider.test(
                {"host": "mysql.local", "port": 3306, "dbname": "app"}
            )
        assert result.ok is True
        assert "mysql.local" in result.message

    def test_test_timeout(self):
        with patch("socket.create_connection", side_effect=socket.timeout()):
            result = MySQLProvider.test(
                {"host": "mysql.local", "port": 3306, "dbname": "x"}
            )
        assert result.ok is False

    def test_test_uses_default_port_when_missing(self):
        with patch(
            "socket.create_connection", return_value=_mock_socket()
        ) as mock_conn:
            MySQLProvider.test({"host": "mysql.local", "dbname": "x"})
        mock_conn.assert_called_once_with(("mysql.local", 3306), timeout=8)


# ══════════════════════════════════════════════════════════════════════════════
# Oracle
# ══════════════════════════════════════════════════════════════════════════════


class TestOracle:
    def test_category(self):
        assert OracleProvider.category == "database"

    def test_type_id(self):
        assert OracleProvider.type_id == "db-oracle"

    def test_has_required_fields(self):
        keys = [f.key for f in OracleProvider.fields]
        for k in ("host", "port", "service_name", "username", "password", "readonly"):
            assert k in keys, f"Campo faltante: {k}"

    def test_no_dbname_field(self):
        """Oracle usa service_name, no dbname."""
        keys = [f.key for f in OracleProvider.fields]
        assert "dbname" not in keys

    def test_port_default(self):
        assert _field(OracleProvider, "port").default == "1521"

    def test_service_name_required(self):
        assert _field(OracleProvider, "service_name").required is True

    def test_readonly_is_checkbox(self):
        assert _field(OracleProvider, "readonly").type == "checkbox"

    def test_test_empty_host_fails(self):
        result = OracleProvider.test({"host": "", "port": 1521, "service_name": "ORCL"})
        assert result.ok is False

    def test_test_success(self):
        with patch("socket.create_connection", return_value=_mock_socket()):
            result = OracleProvider.test(
                {"host": "oracle.local", "port": 1521, "service_name": "ORCL"}
            )
        assert result.ok is True
        assert "oracle.local" in result.message
        assert "ORCL" in result.message

    def test_test_timeout(self):
        with patch("socket.create_connection", side_effect=socket.timeout()):
            result = OracleProvider.test(
                {"host": "oracle.local", "port": 1521, "service_name": "X"}
            )
        assert result.ok is False

    def test_test_connection_error(self):
        with patch(
            "socket.create_connection", side_effect=ConnectionRefusedError("refused")
        ):
            result = OracleProvider.test(
                {"host": "oracle.local", "port": 1521, "service_name": "X"}
            )
        assert result.ok is False

    def test_test_uses_default_port_when_missing(self):
        with patch(
            "socket.create_connection", return_value=_mock_socket()
        ) as mock_conn:
            OracleProvider.test({"host": "oracle.local", "service_name": "ORCL"})
        mock_conn.assert_called_once_with(("oracle.local", 1521), timeout=8)
