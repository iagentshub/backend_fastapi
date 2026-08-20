"""Tests para app.utils.flog — logger centralizado (SQLite backend)."""

from __future__ import annotations

import logging
import sqlite3
import time
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


def test_evento_de_negocio_hereda_actor_y_origen_de_la_peticion(captured):
    from app.utils import flog

    token = flog.set_request_context(ip="10.0.0.7", username="alice")
    try:
        info("agente creado")
    finally:
        flog.reset_request_context(token)

    line = _last_line(captured)
    assert "[10.0.0.7] [alice]" in line


def test_contexto_de_peticion_no_se_filtra_al_trabajo_de_fondo(captured):
    from app.utils import flog

    token = flog.set_request_context(ip="10.0.0.7", username="alice")
    flog.reset_request_context(token)
    info("mantenimiento")

    assert "[-] [-]" in _last_line(captured)


def test_actor_y_origen_explicitos_mandan_sobre_el_contexto(captured):
    from app.utils import flog

    token = flog.set_request_context(ip="10.0.0.7", username="alice")
    try:
        info("alta invitado", ip="10.0.0.8", username="guest:123")
    finally:
        flog.reset_request_context(token)

    assert "[10.0.0.8] [guest:123]" in _last_line(captured)


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


def test_el_hilo_de_volcado_revive_si_alguien_cierra_el_handler(tmp_path):
    """El fallo: uvicorn deja el logger sin quien vuelque el buffer.

    `Config.configure_logging()` llama a `dictConfig`, que empieza cerrando
    todos los handlers ya registrados (`_clearExistingHandlers`). Nuestro
    `close()` corta el hilo `flog-flush`, y el handler sigue vivo pero sin nadie
    que escriba: a partir de ahí solo llegaban a `app_logs` los lotes completos
    de 50 y los ERROR. En una instalación tranquila eso son las últimas hasta 49
    líneas sin aparecer en el visor — y en un invitado, cuyo rastro en el log es
    lo único que le sobrevive, justo las que interesan.
    """
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=50, flush_interval=0.05)
    assert h._flusher is not None and h._flusher.is_alive()

    h.close()  # lo que hace dictConfig por debajo
    assert not h._flusher.is_alive()

    h.emit(_make_record(logging.INFO, "después del cierre", ip="1.1.1.1", username="x"))
    assert h._flusher.is_alive(), "el buffer se quedó sin quien lo vuelque"

    # Y vuelca solo, sin llenar el lote y sin que nadie llame a flush().
    esperar = time.monotonic() + 5
    filas: list = []
    while time.monotonic() < esperar and not filas:
        conn = sqlite3.connect(str(db))
        filas = conn.execute("SELECT summary FROM app_logs").fetchall()
        conn.close()
        if not filas:
            time.sleep(0.05)
    assert filas and filas[0][0] == "después del cierre"


def test_db_handler_crea_fichero(tmp_path):
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    h.emit(_make_record(logging.INFO, "hola", ip="-", username="-", source="BE"))
    assert db.exists()


def test_db_handler_amplia_un_app_logs_antiguo_antes_de_crear_indices(tmp_path):
    db = tmp_path / "logs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE app_logs (id INTEGER PRIMARY KEY, ts REAL NOT NULL, "
        "date TEXT NOT NULL, time TEXT NOT NULL, ip TEXT NOT NULL, "
        "username TEXT NOT NULL, level TEXT NOT NULL, source TEXT NOT NULL, "
        "summary TEXT NOT NULL)"
    )
    conn.close()

    h = _DBHandler(db, batch_size=100, flush_interval=0)
    h.emit(
        _make_record(
            logging.INFO,
            "evento",
            category="AUDIT",
            action="sharing.created",
            outcome="SUCCESS",
        )
    )

    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT category, action, outcome FROM app_logs").fetchone()
    indexes = {item[1] for item in conn.execute("PRAGMA index_list(app_logs)")}
    conn.close()
    assert row == ("AUDIT", "sharing.created", "SUCCESS")
    assert {"idx_al_category_ts", "idx_al_action_ts"} <= indexes


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
    h.flush()  # la escritura es por lotes: sin esto sigue en el buffer
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
    h.flush()
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
    h.flush()
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
    h.flush()
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT source FROM app_logs").fetchone()
    conn.close()
    assert row[0] == "BE"


def test_db_handler_reutiliza_la_conexion(tmp_path):
    """Una sola conexión para todos los registros, no una por emit."""
    h = _DBHandler(tmp_path / "logs.sqlite3")
    h.emit(_make_record(logging.INFO, "uno"))
    h.flush()
    primera = h._conn
    h.emit(_make_record(logging.INFO, "dos"))
    h.flush()
    assert h._conn is primera is not None


def test_db_handler_reconecta_si_la_conexion_muere(tmp_path):
    """Una conexión rota no deja el logging muerto para siempre."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    h.emit(_make_record(logging.INFO, "antes"))
    h.flush()

    h._conn.close()  # simula PG caído o fichero retirado bajo los pies
    h.emit(_make_record(logging.INFO, "durante"))
    h.flush()  # falla, reconecta y reintenta el lote
    h.emit(_make_record(logging.INFO, "despues"))
    h.flush()

    conn = sqlite3.connect(str(db))
    resumenes = [r[0] for r in conn.execute("SELECT summary FROM app_logs")]
    conn.close()
    assert "antes" in resumenes and "despues" in resumenes


def test_db_handler_no_ejecuta_ddl_por_registro(tmp_path):
    """El esquema se crea en _init_schema, no en cada emit (era 4 DDL por request)."""
    h = _DBHandler(tmp_path / "logs.sqlite3")
    h.emit(_make_record(logging.INFO, "primero"))
    h.flush()

    ejecutadas: list[str] = []
    h._conn.set_trace_callback(ejecutadas.append)
    h.emit(_make_record(logging.INFO, "segundo"))
    h.flush()
    h._conn.set_trace_callback(None)

    assert ejecutadas, "el trace no capturó nada: el test no está probando nada"
    assert all("CREATE" not in sql.upper() for sql in ejecutadas)


def test_el_camino_completo_cola_a_bd_conserva_los_campos(tmp_path):
    """flog.info() -> QueueHandler -> hilo listener -> INSERT, con ip y usuario.

    Es el camino que corre en producción. Los tests de _DBHandler llaman a
    emit() directamente y se lo saltan entero: sin esto, que la cola perdiera
    los campos extra o no vaciara nunca no lo detectaría nadie.
    """
    import logging.handlers
    import queue as _queue

    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db)
    q: _queue.Queue = _queue.Queue(-1)
    listener = logging.handlers.QueueListener(q, h, respect_handler_level=True)
    listener.start()

    log = logging.getLogger("flog_test_cola")
    log.setLevel(logging.DEBUG)
    log.addHandler(logging.handlers.QueueHandler(q))
    log.propagate = False
    try:
        log.warning(
            "usuario %s hizo algo",
            "andres",
            extra={"ip": "9.9.9.9", "username": "andres", "source": "FE"},
        )
    finally:
        listener.stop()  # vacía la cola antes de devolver el control
        h.close()  # y close() vuelca el buffer del handler

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT summary, ip, username, source, level FROM app_logs"
    ).fetchone()
    conn.close()
    assert row is not None, "el registro nunca llegó a la BD"
    assert row[0] == "usuario andres hizo algo"  # los args se sustituyeron
    assert row[1] == "9.9.9.9"
    assert row[2] == "andres"
    assert row[3] == "FE"
    assert row[4] == "WARNING"


# ── Escritura por lotes ────────────────────────────────────────────────────────


def _filas(db) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        return [r[0] for r in conn.execute("SELECT summary FROM app_logs ORDER BY ts")]
    finally:
        conn.close()


def test_lote_no_escribe_hasta_completarse(tmp_path):
    """El punto entero de la mejora: N registros no son N transacciones."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=3, flush_interval=0)
    h.emit(_make_record(logging.INFO, "uno"))
    h.emit(_make_record(logging.INFO, "dos"))
    assert _filas(db) == [], "escribió antes de completar el lote"


def test_lote_escribe_al_completarse(tmp_path):
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=3, flush_interval=0)
    for i in range(3):
        h.emit(_make_record(logging.INFO, f"n{i}"))
    assert _filas(db) == ["n0", "n1", "n2"]


def test_lote_conserva_orden_y_contenido(tmp_path):
    """executemany no debe reordenar ni mezclar campos entre filas."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=10, flush_interval=0)
    for i in range(10):
        h.emit(
            _make_record(logging.INFO, f"linea {i}", ip=f"10.0.0.{i}", username=f"u{i}")
        )
    conn = sqlite3.connect(str(db))
    filas = conn.execute(
        "SELECT summary, ip, username FROM app_logs ORDER BY ts"
    ).fetchall()
    conn.close()
    assert len(filas) == 10
    for i, (resumen, ip, usuario) in enumerate(filas):
        assert resumen == f"linea {i}"
        assert ip == f"10.0.0.{i}"
        assert usuario == f"u{i}"


def test_un_solo_commit_por_lote(tmp_path):
    """Cinco registros, un COMMIT. Antes eran cinco."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=5, flush_interval=0)
    h.emit(_make_record(logging.INFO, "calienta"))  # abre la conexión
    h.flush()

    ejecutadas: list[str] = []
    h._conn.set_trace_callback(ejecutadas.append)
    for i in range(5):
        h.emit(_make_record(logging.INFO, f"x{i}"))
    h._conn.set_trace_callback(None)

    commits = [sql for sql in ejecutadas if "COMMIT" in sql.upper()]
    assert len(commits) == 1, f"esperaba 1 commit para 5 registros, hubo {len(commits)}"


def test_error_no_espera_al_lote(tmp_path):
    """Un ERROR es lo que alguien va a buscar ya, y puede preceder a una caída."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=100, flush_interval=0)
    h.emit(_make_record(logging.INFO, "rutina"))
    assert _filas(db) == [], "el INFO no debería haber forzado la escritura"
    h.emit(_make_record(logging.ERROR, "algo explotó"))
    assert _filas(db) == ["rutina", "algo explotó"]


def test_audit_es_estructurado_y_no_espera_al_lote(tmp_path):
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=100, flush_interval=0)
    h.emit(
        _make_record(
            logging.INFO,
            "impersonación iniciada",
            ip="1.2.3.4",
            username="admin",
            category="AUDIT",
            action="admin.impersonation.started",
            resource_type="user",
            resource_id="alice",
            outcome="SUCCESS",
            details_json='{"reason":"support"}',
        )
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT category, action, resource_type, resource_id, outcome, details_json "
        "FROM app_logs"
    ).fetchone()
    conn.close()
    assert row == (
        "AUDIT",
        "admin.impersonation.started",
        "user",
        "alice",
        "SUCCESS",
        '{"reason":"support"}',
    )


def test_audit_rechaza_secretos_en_details():
    with pytest.raises(ValueError, match="Campo sensible"):
        flog_mod.audit("auth.login.succeeded", details={"access_token_hash": "no"})


def test_close_vuelca_lo_pendiente(tmp_path):
    """Al salir del proceso, el buffer suele tener el traceback que lo explica."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=100, flush_interval=0)
    h.emit(_make_record(logging.INFO, "ultimo aliento"))
    h.close()
    assert _filas(db) == ["ultimo aliento"]


def test_flush_periodico_escribe_sin_intervencion(tmp_path):
    """Un registro suelto no puede quedarse en memoria esperando tráfico."""
    import time as _time

    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=100, flush_interval=0.05)
    try:
        h.emit(_make_record(logging.INFO, "solitario"))
        limite = _time.monotonic() + 3
        while not _filas(db) and _time.monotonic() < limite:
            _time.sleep(0.02)
        assert _filas(db) == ["solitario"], "el hilo de volcado no escribió"
    finally:
        h.close()


def test_buffer_no_crece_sin_limite(tmp_path, monkeypatch):
    """Con la BD caída, el logger no se come la memoria del proceso."""
    import app.config.logging as cfg

    monkeypatch.setattr(cfg, "LOG_MAX_BUFFER", 10)
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=1000, flush_interval=0)
    for i in range(50):
        h.emit(_make_record(logging.INFO, f"r{i}"))
    assert len(h._buffer) <= 10
    assert h._descartados == 40


def test_batch_size_1_escribe_inmediato(tmp_path):
    """La vía de escape para quien quiera durabilidad estricta."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=1, flush_interval=0)
    h.emit(_make_record(logging.INFO, "al momento"))
    assert _filas(db) == ["al momento"]


def test_batch_size_1_no_arranca_hilo(tmp_path):
    """Sin lotes no hay nada que volcar periódicamente: el hilo sobra."""
    h = _DBHandler(tmp_path / "logs.sqlite3", batch_size=1, flush_interval=1.0)
    assert h._flusher is None


def test_registro_malformado_no_tumba_el_lote(tmp_path):
    """Un mensaje con args mal emparejados no puede perder los demás."""
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=10, flush_interval=0)
    h.emit(_make_record(logging.INFO, "bueno 1"))
    malo = logging.LogRecord("flog", logging.INFO, "", 0, "%d y %d", (1,), None)
    h.handleError = lambda record: None  # silencia el aviso a stderr
    h.emit(malo)
    h.emit(_make_record(logging.INFO, "bueno 2"))
    h.flush()
    assert _filas(db) == ["bueno 1", "bueno 2"]


# ── Resolución de niveles (app/config/logging.py) ──────────────────────────────


def test_nivel_ok_unico_en_config_y_flog():
    """Un solo sitio define el 25: la config. flog solo le pone nombre."""
    import app.config.logging as cfg

    assert flog_mod._OK == cfg.LOG_LEVEL_OK == 25
    assert logging.getLevelName(cfg.LOG_LEVEL_OK) == "OK"


def test_resuelve_el_nivel_ok_propio_del_proyecto():
    """`logging.getLevelName("OK")` no serviría: flog registra el nombre
    DESPUÉS de importar la config. El mapa propio no depende de ese orden."""
    import app.config.logging as cfg

    assert cfg.LOG_LEVEL_NAMES["OK"] == 25


@pytest.mark.parametrize(
    "nombre,esperado",
    [
        ("ERROR", logging.ERROR),
        ("error", logging.ERROR),
        ("  Warning  ", logging.WARNING),
        ("WARN", logging.WARNING),
        ("CRITICAL", logging.CRITICAL),
        ("DEBUG", logging.DEBUG),
    ],
)
def test_nombres_de_nivel_admitidos(nombre, esperado):
    import app.config.logging as cfg

    assert cfg.LOG_LEVEL_NAMES.get(nombre.strip().upper(), logging.ERROR) == esperado


def test_nivel_mal_escrito_cae_a_error():
    """Un typo en la variable no puede dejar los errores esperando en el buffer."""
    import app.config.logging as cfg

    assert cfg.LOG_LEVEL_NAMES.get("ERORR", logging.ERROR) == logging.ERROR


def test_nivel_inmediato_configurable(tmp_path, monkeypatch):
    """Bajarlo a WARNING hace que los avisos tampoco esperen al lote."""
    import app.config.logging as cfg

    monkeypatch.setattr(cfg, "LOG_IMMEDIATE_LEVEL", logging.WARNING)
    db = tmp_path / "logs.sqlite3"
    h = _DBHandler(db, batch_size=100, flush_interval=0)
    h.emit(_make_record(logging.INFO, "rutina"))
    assert _filas(db) == []
    h.emit(_make_record(logging.WARNING, "aviso"))
    assert _filas(db) == ["rutina", "aviso"]


# ── log_db_path ────────────────────────────────────────────────────────────────


def test_log_db_path_sin_env_usa_la_ruta_del_backend(monkeypatch):
    """Sin GAIA_DATA_DIR ya no se queda sin BD de logs.

    Devolvía None y `_build` interpretaba eso como «no hay handler de BD», así
    que el backend arrancaba con normalidad —config/data.py sí tiene un valor
    por defecto para la misma variable— pero /api/admin/logs salía siempre
    vacío sin un solo mensaje que lo explicara.
    """
    import app.config.data as cfg

    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    assert flog_mod.log_db_path() == cfg.DB_FILE


def test_log_db_path_con_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    p = flog_mod.log_db_path()
    assert p == tmp_path / "hub.db"
