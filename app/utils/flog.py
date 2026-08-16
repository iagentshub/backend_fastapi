"""flog — logger central de iAgents Hub."""

from __future__ import annotations

import asyncio
import atexit
import logging
import logging.handlers
import os
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# La configuración de la escritura por lotes vive con el resto de la del
# backend. `app/config/logging.py` no importa nada del proyecto justamente para
# que flog —que se construye al importarse— pueda depender de él sin arrastrar
# medio grafo de imports.
#
# Se importa el módulo, no los valores: así `emit()` lee la configuración
# vigente en cada llamada y los tests pueden cambiarla con monkeypatch.
from app.config import logging as _cfg

# Referencias al handler de BD y su cola, para que `flush()` pueda forzar el
# volcado. El handler cuelga del QueueListener, no del logger, así que no se
# puede alcanzar recorriendo `_L.handlers`.
_DB_HANDLER: "_DBHandler | None" = None
_LOG_QUEUE: "queue.Queue | None" = None

# El número vive en la configuración (ver LOG_LEVEL_OK); aquí solo se registra
# su nombre en logging. Se mantiene expuesto como `_OK` porque lo importan tests
# y otros módulos.
_OK = _cfg.LOG_LEVEL_OK
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

    def __init__(
        self,
        db_path: Path | None,
        *,
        batch_size: int = -1,
        flush_interval: float = -1.0,
    ) -> None:
        super().__init__()
        # -1 = «usa la configuración»; permite pasar 0 explícitamente para
        # desactivar el hilo de volcado sin que el default lo pise.
        if batch_size < 0:
            batch_size = _cfg.LOG_BATCH_SIZE
        if flush_interval < 0:
            flush_interval = _cfg.LOG_FLUSH_INTERVAL
        self._db = str(db_path) if db_path else None
        self._conn: object | None = None
        self._batch_size = max(1, batch_size)
        self._buffer: list[tuple] = []
        # Reentrante: _flush_locked() puede acabar llamando a _drop_conn(), y el
        # camino de error vuelve a tocar el mismo estado.
        self._lock = threading.RLock()
        self._last_record: logging.LogRecord | None = None
        self._descartados = 0
        self._stop = threading.Event()
        self._flusher: threading.Thread | None = None
        # Solo para PostgreSQL: loop y hilo propios del logger (ver _pg_loop).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        if db_path:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()
        # Con lote de 1 la escritura ya es inmediata: un hilo que despierta cada
        # segundo para no encontrar nada solo gasta.
        if self._batch_size > 1 and flush_interval > 0:
            self._flusher = threading.Thread(
                target=self._flush_loop,
                args=(flush_interval,),
                name="flog-flush",
                daemon=True,
            )
            self._flusher.start()

    def _flush_loop(self, interval: float) -> None:
        """Vuelca lo pendiente cada `interval` segundos.

        Sin esto, un registro que no completa lote se quedaría en memoria hasta
        que llegara tráfico suficiente: en un sistema en reposo, justo el aviso
        que explica por qué está en reposo.
        """
        while not self._stop.wait(interval):
            try:
                self.flush()
            except Exception:  # noqa: S110, BLE001  # pragma: no cover
                # Nunca dejar morir el hilo de volcado: si esta ronda falla, la
                # siguiente reintenta. flush() ya reporta por handleError.
                pass

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

    def _pg_loop(self) -> asyncio.AbstractEventLoop:
        """Event loop propio del logger, en su hilo, para hablar con asyncpg.

        asyncpg es asíncrono y este handler es síncrono: corre en el hilo del
        QueueListener y en el del volcado periódico. La salida NO es usar el
        loop principal —el logger se construye al importar el módulo, mucho
        antes de que ese loop exista, y sigue escribiendo durante el apagado,
        cuando ya no está— sino tener uno propio aquí.

        Así se elimina la conexión psycopg2 que vivía fuera del pool sin que
        nadie la contabilizara, sin acoplar el logger al ciclo de vida de la
        aplicación: si el loop principal se para, este sigue escribiendo.
        """
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever, name="flog-pg", daemon=True
            )
            self._loop_thread.start()
        return self._loop

    def _connect(self):
        """Conexión perezosa. Se reconstruye sola si una escritura la invalidó."""
        if self._conn is None:
            if self._db is None:
                import asyncpg  # type: ignore[import]

                async def _open():
                    return await asyncpg.connect(os.environ.get("DATABASE_URL", ""))

                self._conn = self._run_pg(_open())
            else:
                conn = sqlite3.connect(self._db, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                self._conn = conn
        return self._conn

    def _drop_conn(self) -> None:
        try:
            if self._conn is None:
                pass
            elif self._db:
                self._conn.close()  # type: ignore[attr-defined]
            else:
                # asyncpg: cerrar es una corrutina y hay que ejecutarla en el
                # loop del logger, no en el hilo que llama.
                self._run_pg(self._conn.close(), timeout=5)  # type: ignore[attr-defined]
        except Exception:  # noqa: S110, BLE001
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

    def _run_pg(self, coro, timeout: float = 10.0):
        """Ejecuta una corrutina en el loop del logger y espera el resultado."""
        futuro = asyncio.run_coroutine_threadsafe(coro, self._pg_loop())
        return futuro.result(timeout=timeout)

    def _sql(self) -> str:
        if self._db:
            marcadores = ", ".join(["?"] * 8)
        else:
            # asyncpg usa $1..$N posicionales, no %s.
            marcadores = ", ".join(f"${i}" for i in range(1, 9))
        return (
            "INSERT INTO app_logs (ts, date, time, ip, username, level, source, summary) "
            f"VALUES ({marcadores})"
        )

    def _write(self, filas: list[tuple]) -> None:
        """Un executemany + un commit para todo el lote."""
        conn = self._connect()
        if self._db:
            conn.executemany(self._sql(), filas)  # type: ignore[attr-defined]
            conn.commit()  # type: ignore[attr-defined]
        else:
            # asyncpg confirma cada sentencia por su cuenta; executemany va
            # dentro de una transacción explícita para que el lote entero sea
            # atómico, igual que el commit único de SQLite.
            async def _insertar():
                async with conn.transaction():  # type: ignore[attr-defined]
                    await conn.executemany(self._sql(), filas)  # type: ignore[attr-defined]

            self._run_pg(_insertar())

    def _flush_locked(self) -> None:
        """Vuelca el buffer. El llamante ya tiene el lock."""
        if not self._buffer:
            return
        filas, self._buffer = self._buffer, []
        try:
            self._write(filas)
        except Exception:  # noqa: BLE001
            # Conexión rota (fichero borrado, PG caído): tirarla y reintentar
            # una vez sobre una nueva. Antes cada registro se perdía en el
            # primer fallo; ahora el lote entero sobrevive a una reconexión,
            # que es el caso habitual (el proceso de BD se reinició).
            self._drop_conn()
            try:
                self._write(filas)
            except Exception:  # noqa: BLE001
                self._drop_conn()
                if self._last_record is not None:
                    self.handleError(self._last_record)

    def flush(self) -> None:
        """Fuerza la escritura de lo pendiente. Seguro desde cualquier hilo."""
        with self._lock:
            self._flush_locked()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            fila = self._row(record)
        except Exception:  # noqa: BLE001
            # Formatear el mensaje puede fallar (args mal emparejados). Que un
            # registro malformado no se lleve por delante el lote entero.
            self.handleError(record)
            return
        with self._lock:
            self._buffer.append(fila)
            self._last_record = record
            if len(self._buffer) > _cfg.LOG_MAX_BUFFER:
                sobran = len(self._buffer) - _cfg.LOG_MAX_BUFFER
                del self._buffer[:sobran]
                self._descartados += sobran
            if (
                len(self._buffer) >= self._batch_size
                or record.levelno >= _cfg.LOG_IMMEDIATE_LEVEL
            ):
                self._flush_locked()

    def close(self) -> None:
        # Volcar ANTES de cerrar la conexión: al salir del proceso, lo que queda
        # en el buffer suele ser el traceback que explica la salida.
        self._stop.set()
        if self._flusher is not None and self._flusher.is_alive():
            self._flusher.join(timeout=2)
        try:
            self.flush()
        finally:
            self._drop_conn()
            self._stop_loop()
            super().close()

    def _stop_loop(self) -> None:
        """Para el loop de asyncpg, si se llegó a crear."""
        loop, self._loop = self._loop, None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
            if self._loop_thread is not None:
                self._loop_thread.join(timeout=2)
            loop.close()
        except Exception:  # noqa: S110, BLE001
            # Cerrando el proceso: que el loop del logger no se resista no
            # puede impedir la salida.
            pass


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
    except Exception:  # noqa: BLE001
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
            global _DB_HANDLER, _LOG_QUEUE
            _DB_HANDLER = db
            q: queue.Queue = queue.Queue(-1)
            _LOG_QUEUE = q
            log.addHandler(logging.handlers.QueueHandler(q))
            listener = logging.handlers.QueueListener(q, db, respect_handler_level=True)
            listener.start()
            # Vacía la cola al salir: sin esto se pierden los últimos registros
            # (incluido el traceback que provocó el cierre).
            #
            # atexit ejecuta en orden INVERSO al registro, así que esto corre
            # `listener.stop` primero —vacía la cola hacia el buffer del
            # handler— y `db.close` después, que es quien escribe ese buffer.
            # Al revés, los últimos registros se quedarían en memoria.
            atexit.register(db.close)
            atexit.register(listener.stop)
    log.propagate = False
    return log


_L = _build()


def flush(timeout: float = 2.0) -> None:
    """Fuerza la escritura de los logs pendientes en BD.

    Con la escritura por lotes, un registro puede tardar hasta
    GAIA_LOG_FLUSH_INTERVAL en aparecer en `app_logs`. Quien necesite leerlos
    inmediatamente después de escribirlos —un test, o un endpoint que muestre
    lo que acaba de pasar— llama aquí primero.

    Espera a que la cola se drene antes de volcar: un registro recién emitido
    puede seguir en tránsito hacia el hilo del listener, y volcar sin esperarlo
    escribiría todo menos justo el que se acaba de escribir.
    """
    if _DB_HANDLER is None:
        return
    if _LOG_QUEUE is not None:
        limite = time.monotonic() + max(0.0, timeout)
        while not _LOG_QUEUE.empty() and time.monotonic() < limite:
            time.sleep(0.001)
        # `empty()` pasa a True cuando el listener SACA el registro, no cuando
        # termina de procesarlo. Ese margen es lo que separa la cola del buffer.
        time.sleep(0.005)
    _DB_HANDLER.flush()


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
