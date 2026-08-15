"""Configuración del registro de actividad (app_logs).

Este módulo no importa nada del proyecto a propósito: `app/utils/flog.py` lo
consume y flog se construye al importarse, antes que casi todo lo demás. Un
import hacia dentro de la aplicación lo metería en el grafo de medio backend,
que es justo lo que vigila `tests/test_ciclos_de_import.py`.
"""

from __future__ import annotations

import logging
import os

# ── Escritura por lotes ────────────────────────────────────────────────────────
# Cada registro era un INSERT + COMMIT propio. Como RequestLoggerMiddleware
# registra toda petición, el volumen de transacciones era proporcional al
# tráfico: en SQLite cada commit compite por el mismo WAL que atiende las
# peticiones, y en PostgreSQL era un round-trip de red por línea de log.
#
# Ahora se acumulan en memoria y se vuelcan con un solo executemany + commit
# cuando se llena el lote o cuando vence el intervalo, lo que ocurra antes.

# Registros por transacción. 1 restaura la escritura inmediata de antes, que es
# lo que usan los tests que consultan la BD justo después de emitir.
LOG_BATCH_SIZE: int = max(1, int(os.getenv("GAIA_LOG_BATCH_SIZE", "50")))

# Segundos máximos que un registro puede esperar en memoria. Sin este techo, un
# registro suelto se quedaría sin escribir hasta que llegara tráfico suficiente.
LOG_FLUSH_INTERVAL: float = max(0.0, float(os.getenv("GAIA_LOG_FLUSH_INTERVAL", "1.0")))

# Nivel propio del proyecto, entre INFO y WARNING. Vive aquí y no en flog para
# que este módulo pueda resolver nombres de nivel sin importar flog (que es
# quien lo importa a él).
LOG_LEVEL_OK: int = 25

# Mapa explícito nombre → número. NO se usa `logging.getLevelName()` para esto:
# su forma str → int está documentada como un error de diseño y desaconsejada,
# y además no serviría aquí, porque "OK" solo entra en el registro global de
# logging cuando flog ejecuta `addLevelName` — es decir, DESPUÉS de importar
# este módulo. Un mapa propio no depende del orden de import.
LOG_LEVEL_NAMES: dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "OK": LOG_LEVEL_OK,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

# A partir de este nivel no se espera al lote: es el registro que alguien va a
# buscar de inmediato, y el que más probablemente preceda a una caída. Un nombre
# mal escrito cae a ERROR en lugar de dejar los errores esperando en el buffer.
LOG_IMMEDIATE_LEVEL: int = LOG_LEVEL_NAMES.get(
    os.getenv("GAIA_LOG_IMMEDIATE_LEVEL", "ERROR").strip().upper(), logging.ERROR
)

# Tope del buffer. Si la BD lleva un rato caída, el logger no puede comerse la
# memoria del proceso: a partir de aquí se descartan los más antiguos, que es
# preferible a tumbar el backend por guardar sus propios logs.
LOG_MAX_BUFFER: int = max(1, int(os.getenv("GAIA_LOG_MAX_BUFFER", "10000")))

# ── Ruido de alto volumen ──────────────────────────────────────────────────────
# Sondas de vida: el HEALTHCHECK del contenedor las dispara cada 30 s y, con
# varios workers, llenaban la tabla de líneas idénticas que solo sirven para
# esconder las interesantes. Solo se silencian cuando responden bien: un health
# check que devuelve 503 es justo lo que hay que ver.
LOG_SILENT_PATHS: frozenset[str] = frozenset(
    p.strip()
    for p in os.getenv("GAIA_LOG_SILENT_PATHS", "/api/health").split(",")
    if p.strip()
)

# Escotilla para volver al comportamiento anterior sin tocar código.
LOG_HEALTH: bool = os.getenv("GAIA_LOG_HEALTH", "").lower() in ("1", "true", "yes")
