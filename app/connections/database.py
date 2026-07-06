"""Database connection providers — PostgreSQL, MySQL, Oracle."""

from __future__ import annotations

import socket
from typing import Any, Dict

from .base import BaseProvider, FieldDef, TestResult, register

# ── Campos comunes a todos los motores ────────────────────────────────────────

def _common_auth(default_port: str) -> list:
    """Devuelve los campos estándar con el puerto por defecto del motor."""
    return [
        FieldDef("host", "Host", "text", "localhost", required=True),
        FieldDef("port", "Puerto", "number", default=default_port, required=True),
        FieldDef("dbname", "Base de datos", "text", "", required=True),
        FieldDef("username", "Usuario", "text", "", required=True),
        FieldDef("password", "Contraseña", "password", ""),
        FieldDef(
            "readonly", "Solo lectura (prohibir INSERT/UPDATE/DELETE/DDL)", "checkbox"
        ),
    ]


def _tcp_test(config: Dict[str, Any], default_port: int) -> TestResult:
    host = (config.get("host") or "").strip()
    port = int(config.get("port") or default_port)
    if not host:
        return TestResult(False, "Falta el host")
    dbname = (config.get("dbname") or "").strip()
    try:
        with socket.create_connection((host, port), timeout=8):
            pass
        label = f"{host}:{port}/{dbname}" if dbname else f"{host}:{port}"
        return TestResult(True, f"OK — Puerto accesible en {label}")
    except socket.timeout:
        return TestResult(False, f"Timeout conectando a {host}:{port}")
    except Exception as e:
        return TestResult(False, "No se puede conectar", str(e))


# ── PostgreSQL ─────────────────────────────────────────────────────────────────


@register
class PostgreSQLProvider(BaseProvider):
    type_id = "db-postgres"
    category = "database"
    label = "PostgreSQL"
    icon = "🐘"
    fields = _common_auth("5432") + [
        FieldDef("ssl", "Conexión SSL", "checkbox"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        return _tcp_test(config, 5432)


# ── MySQL / MariaDB ────────────────────────────────────────────────────────────


@register
class MySQLProvider(BaseProvider):
    type_id = "db-mysql"
    category = "database"
    label = "MySQL / MariaDB"
    icon = "🐬"
    fields = _common_auth("3306")  # sin SSL por defecto, igual que Postgres pero sin ese campo

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        return _tcp_test(config, 3306)


# ── Oracle ─────────────────────────────────────────────────────────────────────


@register
class OracleProvider(BaseProvider):
    type_id = "db-oracle"
    category = "database"
    label = "Oracle"
    icon = "🔴"
    fields = [
        FieldDef("host", "Host", "text", "localhost", required=True),
        FieldDef("port", "Puerto", "number", default="1521", required=True),
        FieldDef("service_name", "Service Name / SID", "text", "ORCL", required=True),
        FieldDef("username", "Usuario", "text", "system", required=True),
        FieldDef("password", "Contraseña", "password", ""),
        FieldDef(
            "readonly", "Solo lectura (prohibir INSERT/UPDATE/DELETE/DDL)", "checkbox"
        ),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        host = (config.get("host") or "").strip()
        port = int(config.get("port") or 1521)
        if not host:
            return TestResult(False, "Falta el host")
        svc = (config.get("service_name") or "").strip()
        try:
            with socket.create_connection((host, port), timeout=8):
                pass
            label = f"{host}:{port}/{svc}" if svc else f"{host}:{port}"
            return TestResult(True, f"OK — Puerto accesible en {label}")
        except socket.timeout:
            return TestResult(False, f"Timeout conectando a {host}:{port}")
        except Exception as e:
            return TestResult(False, "No se puede conectar", str(e))
