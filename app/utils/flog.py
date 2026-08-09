"""flog — logger central de iAgents Hub."""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import queue
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_OK = 25
logging.addLevelName(_OK, "OK")

def _log_schema() -> str:
    """El DDL de ``app_logs``, extraído del esquema real. Aquí no vive una copia.

    Había una segunda definición de la tabla en este módulo, con dos índices
    frente a los seis de ``schema.py``. No rompía nada —los ``CREATE INDEX IF
    NOT EXISTS`` del esquema principal se ejecutan después y completan lo que
    falta—, pero era la copia de flog la que creaba la tabla en un arranque
    limpio (el handler se construye al importar, antes de ``init_db``), y los
    cuatro índices que le faltaban son exactamente los que el visor de logs usa
    para filtrar por nivel, usuario, IP y fuente.

    flog sigue creando la tabla él mismo, porque sigue arrancando antes que
    ``init_db``; lo que ya no hace es tener su propia idea de cómo es.
    """
    from app.storage.schema import SCHEMA_SQLITE

    trozos = [
        sentencia.strip() + ";"
        for sentencia in SCHEMA_SQLITE.split(";")
        if "app_logs" in sentencia
    ]
    return "\n".join(trozos)


class _StdoutFmt(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(7)
        ip = getattr(record, "ip", "-")
        username = getattr(record, "username", "-")
        return f"{ts} - {level} - [{ip}] [{username}] {record.getMessage()}"


class _DBHandler(logging.Handler):
    """Escribe cada entrada de log en hub.db (SQLite) o PostgreSQL.

    Mantiene UNA conexión abierta y reutilizada. Antes se abría una conexión
    nueva —con su PRAGMA y su executescript del esquema entero— por cada
    registro; como RequestLoggerMiddleware loguea toda petición, eso era un
    CREATE TABLE IF NOT EXISTS x2 + CREATE INDEX IF NOT EXISTS x2 síncronos por
    request HTTP. El DDL vive ahora solo en _init_schema().

    Va detrás de un QueueListener (ver _build), así que emit() corre siempre en
    el mismo hilo dedicado: una única conexión es suficiente y no hay carrera.
    """

    def __init__(self, db_path: Path | None) -> None:
        super().__init__()
        self._db = str(db_path) if db_path else None
        self._conn: object | None = None
        if db_path:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def _init_schema(self) -> None:
        """Crea app_logs si hub.db aún no tiene la tabla (arranque previo a init_db)."""
        try:
            conn = sqlite3.connect(self._db, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_log_schema())
            conn.close()
        except Exception:
            # Sin tabla de logs el proceso arranca igual: el handler de stdout
            # sigue funcionando y cada emit reintentará la conexión.
            logging.getLogger(__name__).warning(
                "flog: no se pudo inicializar app_logs en %s", self._db, exc_info=True
            )

    def _connect(self):
        """Conexión perezosa. Se reconstruye sola si una escritura la invalidó."""
        if self._conn is None:
            if self._db is None:
                import psycopg2  # type: ignore[import]

                self._conn = psycopg2.connect(os.environ.get("DATABASE_URL", ""))
            else:
                conn = sqlite3.connect(self._db, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                self._conn = conn
        return self._conn

    def _drop_conn(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()  # type: ignore[attr-defined]
        except Exception:
            # ponytail: silencio deliberado. Aquí se llega porque la conexión ya
            # falló; que cerrarla también falle no aporta nada, y logear desde el
            # handler de logs es la forma de montar una recursión. El finally
            # deja _conn a None, que es lo único que importa: _connect() la
            # reconstruye en el siguiente emit.
            pass
        finally:
            self._conn = None

    def _row(self, record: logging.LogRecord) -> tuple:
        dt = datetime.fromtimestamp(record.created)
        return (
            record.created,
            dt.strftime("%Y-%m-%d"),
            dt.strftime("%H:%M:%S"),
            getattr(record, "ip", "-") or "-",
            getattr(record, "username", "-") or "-",
            record.levelname,
            getattr(record, "source", "BE") or "BE",
            record.getMessage(),
        )

    def emit(self, record: logging.LogRecord) -> None:
        placeholder = "?" if self._db else "%s"
        sql = (
            "INSERT INTO app_logs (ts, date, time, ip, username, level, source, summary) "
            f"VALUES ({', '.join([placeholder] * 8)})"
        )
        try:
            conn = self._connect()
            if self._db:
                conn.execute(sql, self._row(record))  # type: ignore[attr-defined]
            else:
                conn.cursor().execute(sql, self._row(record))  # type: ignore[attr-defined]
            conn.commit()  # type: ignore[attr-defined]
        except Exception:
            # Conexión rota (fichero borrado, PG caído): tirarla para que el
            # siguiente registro reconecte en vez de fallar para siempre.
            self._drop_conn()
            self.handleError(record)

    def close(self) -> None:
        self._drop_conn()
        super().close()


def log_db_path() -> Path | None:
    """Ruta de la BD de logs: la MISMA que usa el resto del backend.

    Resolvía ``GAIA_DATA_DIR`` por su cuenta y devolvía ``None`` si no estaba
    definida, mientras ``app/config/data.py`` sí tiene un valor por defecto para
    la misma variable. El resultado eran dos formas de resolver la misma ruta en
    el mismo proceso: sin ``GAIA_DATA_DIR`` el backend arrancaba y funcionaba con
    normalidad, pero el panel ``/api/admin/logs`` salía siempre vacío y ningún
    mensaje de error lo explicaba.

    El import es diferido a propósito: ``flog`` se construye al importarse
    (``_L = _build()``) y lo importa medio backend, así que traer
    ``app.config.data`` al nivel superior lo metería en el grafo de imports de
    todo el mundo — y ``tests/test_ciclos_de_import.py`` vigila justo eso.
    """
    try:
        from app.config.data import DB_FILE
    except Exception:
        # Si la configuración aún no se puede importar, el handler de stdout
        # sigue funcionando: quedarse sin logs en BD es mejor que no arrancar.
        return None
    return DB_FILE


def _build() -> logging.Logger:
    log = logging.getLogger("flog")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        # La consola de Windows es cp1252: un '→' en el mensaje reventaba el
        # handler y se tragaba el log (y el traceback que iba detrás).
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_StdoutFmt())
        log.addHandler(h)

        # La escritura a BD sale del hilo que loguea. Sin esto, cada llamada a
        # flog.info() desde un handler async ejecutaba un INSERT síncrono
        # dentro del event loop.
        db: logging.Handler | None = None
        if os.environ.get("DATABASE_URL", "").strip():
            db = _DBHandler(None)  # PostgreSQL: usa DATABASE_URL
        else:
            p = log_db_path()
            if p is not None:
                db = _DBHandler(p)
        if db is not None:
            q: queue.Queue = queue.Queue(-1)
            log.addHandler(logging.handlers.QueueHandler(q))
            listener = logging.handlers.QueueListener(q, db, respect_handler_level=True)
            listener.start()
            # Vacía la cola al salir: sin esto se pierden los últimos registros
            # (incluido el traceback que provocó el cierre).
            atexit.register(listener.stop)
    log.propagate = False
    return log


_L = _build()


def debug(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.debug(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def info(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.info(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def ok(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.log(
        _OK,
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def warning(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.warning(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )


def error(
    msg: str, *a, ip: str = "-", username: str = "-", source: str = "BE", **kw
) -> None:
    _L.error(
        msg,
        *a,
        extra={"ip": ip or "-", "username": username or "-", "source": source},
        **kw,
    )
