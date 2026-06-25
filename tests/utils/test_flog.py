"""Tests para app.utils.flog — logger centralizado."""
from __future__ import annotations

import logging
import re
from io import StringIO

import pytest

import app.utils.flog as flog_mod
from app.utils.flog import _DateFileHandler, _Fmt, _OK, debug, error, info, ok, warning


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_record(level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="flog", level=level, pathname="", lineno=0,
        msg=msg, args=(), exc_info=None,
    )


@pytest.fixture()
def captured():
    """Añade un handler StringIO temporal al logger de flog y lo retira al salir."""
    buf = StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(_Fmt())
    flog_mod._L.addHandler(h)
    yield buf
    flog_mod._L.removeHandler(h)
    h.close()


def _last_line(buf: StringIO) -> str:
    return buf.getvalue().rstrip("\n").split("\n")[-1]


# ── Formatter: estructura ──────────────────────────────────────────────────────

def test_formato_tiene_timestamp():
    line = _Fmt().format(_make_record(logging.INFO, "msg"))
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line)


def test_timestamp_longitud():
    line = _Fmt().format(_make_record(logging.INFO, "msg"))
    ts = line.split(" - ", 1)[0]
    assert len(ts) == 19


def test_formato_tres_partes():
    line = _Fmt().format(_make_record(logging.INFO, "hola"))
    parts = line.split(" - ", 2)
    assert len(parts) == 3


def test_mensaje_al_final():
    line = _Fmt().format(_make_record(logging.INFO, "texto final"))
    assert line.endswith("texto final")


# ── Formatter: nivel padded ────────────────────────────────────────────────────

@pytest.mark.parametrize("level,name", [
    (logging.DEBUG,   "DEBUG  "),
    (logging.INFO,    "INFO   "),
    (_OK,             "OK     "),
    (logging.WARNING, "WARNING"),
    (logging.ERROR,   "ERROR  "),
])
def test_nivel_padded_a_7(level, name):
    line = _Fmt().format(_make_record(level, "x"))
    assert f" - {name} - " in line


# ── Constante OK ───────────────────────────────────────────────────────────────

def test_ok_vale_25():
    assert _OK == 25


def test_ok_entre_info_y_warning():
    assert logging.INFO < _OK < logging.WARNING


def test_ok_nombre_registrado():
    assert logging.getLevelName(_OK) == "OK"


def test_ok_nombre_inverso():
    assert logging.getLevelName("OK") == _OK


# ── Configuración del logger ────────────────────────────────────────────────────

def test_sin_propagacion():
    assert flog_mod._L.propagate is False


def test_un_solo_handler():
    # El logger tiene exactamente un handler propio (el de stdout).
    # pytest inyecta sus propios handlers en todos los loggers; los excluimos
    # comparando el tipo exacto, ya que los handlers de pytest son subclases.
    real = [h for h in flog_mod._L.handlers if type(h) is logging.StreamHandler]
    assert len(real) == 1


def test_nivel_debug():
    assert flog_mod._L.level == logging.DEBUG


# ── Salida por nivel ───────────────────────────────────────────────────────────

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


# ── Sustitución de args ────────────────────────────────────────────────────────

def test_args_se_sustituyen(captured):
    info("usuario: %s, tokens: %d", "andres", 42)
    assert "usuario: andres, tokens: 42" in _last_line(captured)


def test_multiples_llamadas_acumulan(captured):
    info("linea 1")
    info("linea 2")
    lines = [ln for ln in captured.getvalue().strip().split("\n") if ln]
    assert len(lines) == 2
    assert "linea 1" in lines[0]
    assert "linea 2" in lines[1]


# ── Idempotencia del singleton ─────────────────────────────────────────────────

def test_build_no_duplica_handlers():
    """Llamar a _build() un número arbitrario de veces no añade handlers extra."""
    before = len(flog_mod._L.handlers)
    flog_mod._build()
    flog_mod._build()
    assert len(flog_mod._L.handlers) == before


# ── _DateFileHandler ───────────────────────────────────────────────────────────

def test_date_handler_crea_fichero(tmp_path):
    h = _DateFileHandler(tmp_path)
    h.setFormatter(_Fmt())
    record = logging.LogRecord("flog", logging.INFO, "", 0, "hola", (), None)
    h.emit(record)
    h.close()
    assert len(list(tmp_path.glob("*.log"))) == 1


def test_date_handler_formato_correcto(tmp_path):
    h = _DateFileHandler(tmp_path)
    h.setFormatter(_Fmt())
    record = logging.LogRecord("flog", logging.INFO, "", 0, "test msg", (), None)
    h.emit(record)
    h.close()
    content = next(tmp_path.glob("*.log")).read_text()
    assert "INFO" in content
    assert "test msg" in content


def test_date_handler_nombre_fecha(tmp_path):
    from datetime import date
    h = _DateFileHandler(tmp_path)
    h.close()
    expected = date.today().strftime("%Y%m%d") + ".log"
    assert (tmp_path / expected).exists()


def test_date_handler_acumula_lineas(tmp_path):
    h = _DateFileHandler(tmp_path)
    h.setFormatter(_Fmt())
    for msg in ("a", "b", "c"):
        record = logging.LogRecord("flog", logging.INFO, "", 0, msg, (), None)
        h.emit(record)
    h.close()
    lines = next(tmp_path.glob("*.log")).read_text().strip().split("\n")
    assert len(lines) == 3
