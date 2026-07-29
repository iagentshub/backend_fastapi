"""Tests para app.utils.flog — logger centralizado (SQLite backend)."""

from __future__ import annotations

import logging
import sqlite3
from io import StringIO

import pytest

import app.utils.flog as flog_mod
from app.utils.flog import _OK, _DBHandler, _StdoutFmt, debug, error, info, ok, warning

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_record(level: int, msg: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="flog",
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


@pytest.fixture()
def captured():
    """Añade un handler StringIO temporal al logger de flog y lo retira al salir."""
    buf = StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(_StdoutFmt())
    flog_mod._L.addHandler(h)
    yield buf
    flog_mod._L.removeHandler(h)
    h.close()


def _last_line(buf: StringIO) -> str:
    return buf.getvalue().rstrip("\n").split("\n")[-1]


# ── Constante OK ───────────────────────────────────────────────────────────────


def test_ok_vale_25():
    assert _OK == 25


def test_ok_entre_info_y_warning():
    assert logging.INFO < _OK < logging.WARNING


def test_ok_nombre_registrado():
    assert logging.getLevelName(_OK) == "OK"


def test_ok_nombre_inverso():
    assert logging.getLevelName("OK") == _OK


# ── _StdoutFmt ─────────────────────────────────────────────────────────────────


def test_formato_tiene_timestamp():
    import re

    line = _StdoutFmt().format(_make_record(logging.INFO, "msg", ip="-", username="-"))
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line)


def test_formato_incluye_ip():
    line = _StdoutFmt().format(
        _make_record(logging.INFO, "msg", ip="1.2.3.4", username="-")
    )
    assert "[1.2.3.4]" in line


def test_formato_incluye_username():
    line = _StdoutFmt().format(
        _make_record(logging.INFO, "msg", ip="-", username="alice")
    )
    assert "[alice]" in line


def test_formato_mensaje_al_final():
    line = _StdoutFmt().format(
        _make_record(logging.INFO, "texto final", ip="-", username="-")
    )
    assert line.endswith("texto final")


def test_formato_nivel_info():
    line = _StdoutFmt().format(_make_record(logging.INFO, "x", ip="-", username="-"))
    assert "INFO" in line


def test_formato_nivel_warning():
    line = _StdoutFmt().format(_make_record(logging.WARNING, "x", ip="-", username="-"))
    assert "WARNING" in line


def test_formato_nivel_error():
    line = _StdoutFmt().format(_make_record(logging.ERROR, "x", ip="-", username="-"))
    assert "ERROR" in line


def test_formato_sin_ip_usa_guion():
    """Si no se pasa extra.ip el formatter usa '-' como fallback."""
    record = logging.LogRecord("flog", logging.INFO, "", 0, "msg", (), None)
    line = _StdoutFmt().format(record)
    assert "[-]" in line


# ── Configuración del logger ────────────────────────────────────────────────────


def test_sin_propagacion():
    assert flog_mod._L.propagate is False


def test_nivel_debug():
    assert flog_mod._L.level == logging.DEBUG


def test_build_no_duplica_handlers():
    """Llamar a _build() múltiples veces no añade handlers extra."""
    before = len(flog_mod._L.handlers)
    flog_mod._build()
    flog_mod._build()
    assert len(flog_mod._L.handlers) == before


# ── Salida por nivel (stdout) ──────────────────────────────────────────────────


def test_debug_sale(captured):
    debug("traza de depuración")
    line = _last_line(captured)
    assert "DEBUG" in line
    assert "traza de depuración" in line


def test_info_sale(captured):
    info("mensaje informativo")
    line = _last_line(captured)
    assert "INFO" in line
    assert "mensaje informativo" in line


def test_ok_sale(captured):
    ok("operación exitosa")
    line = _last_line(captured)
    assert "OK" in line
    assert "operación exitosa" in line


def test_warning_sale(captured):
    warning("aviso importante")
    line = _last_line(captured)
    assert "WARNING" in line
    assert "aviso importante" in line


def test_error_sale(captured):
    error("error crítico")
    line = _last_line(captured)
    assert "ERROR" in line
    assert "error crítico" in line


def test_ip_en_salida(captured):
    info("con ip", ip="10.0.0.1")
    assert "[10.0.0.1]" in _last_line(captured)


def test_username_en_salida(captured):
    info("con usuario", username="bob")
    assert "[bob]" in _last_line(captured)


def test_defaults_ip_username_son_guion(captured):
    info("sin extras")
    assert "[-]" in _last_line(captured)


def test_multiples_llamadas_acumulan(captured):
    info("linea 1")
    info("linea 2")
    lines = [ln for ln in captured.getvalue().strip().split("\n") if ln]
    assert len(lines) == 2
    assert "linea 1" in lines[0]
    assert "linea 2" in lines[1]


def test_args_se_sustituyen(captured):
    info("usuario: %s, tokens: %d", "andres", 42)
    assert "usuario: andres, tokens: 42" in _last_line(captured)


# ── _DBHandler ─────────────────────────────────────────────────────────────────


def test_db_handler_crea_fichero(tmp_path):
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    h.emit(_make_record(logging.INFO, "hola", ip="-", username="-", source="BE"))
    assert db.exists()


def test_db_handler_inserta_fila(tmp_path):
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    h.emit(
        _make_record(
            logging.WARNING,
            "mensaje de prueba",
            ip="2.2.2.2",
            username="admin",
            source="BE",
        )
    )
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT level, summary, ip, username FROM app_logs").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "WARNING"
    assert rows[0][1] == "mensaje de prueba"
    assert rows[0][2] == "2.2.2.2"
    assert rows[0][3] == "admin"


def test_db_handler_fecha_y_hora(tmp_path):
    import re

    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    h.emit(_make_record(logging.INFO, "ts test", ip="-", username="-"))
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT date, time, ts FROM app_logs").fetchone()
    conn.close()
    assert re.match(r"\d{4}-\d{2}-\d{2}", row[0])
    assert re.match(r"\d{2}:\d{2}:\d{2}", row[1])
    assert row[2] > 0


def test_db_handler_multiples_entradas(tmp_path):
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    for i in range(5):
        h.emit(_make_record(logging.INFO, f"entrada {i}", ip="-", username="-"))
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM app_logs").fetchone()[0]
    conn.close()
    assert count == 5


def test_db_handler_fuente_fe(tmp_path):
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    h.emit(
        _make_record(
            logging.ERROR, "fe error", ip="3.3.3.3", username="admin", source="FE"
        )
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT source FROM app_logs").fetchone()
    conn.close()
    assert row[0] == "FE"


def test_db_handler_directorio_no_existente(tmp_path):
    """El handler crea el directorio padre si no existe."""
    db = tmp_path / "subdir" / "logs.sqlite3"
    _DBHandler(db)
    assert db.parent.exists()


def test_db_handler_source_por_defecto_be(tmp_path):
    """Si no se indica source, se guarda 'BE'."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    record = logging.LogRecord("flog", logging.INFO, "", 0, "msg", (), None)
    h.emit(record)
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT source FROM app_logs").fetchone()
    conn.close()
    assert row[0] == "BE"


# ── log_db_path ────────────────────────────────────────────────────────────────


def test_log_db_path_sin_env(monkeypatch):
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    assert flog_mod.log_db_path() is None


def test_log_db_path_con_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    p = flog_mod.log_db_path()
    assert p == tmp_path / "hub.db"
