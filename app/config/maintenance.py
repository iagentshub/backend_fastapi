"""Cadencia de los bucles de fondo que arranca `_lifespan`.

Cada uno de estos números decide **cada cuánto se pasa la escoba**, nunca qué
se barre: la política de cada purga vive donde le corresponde —la retención de
logs en las preferencias del admin, la ventana de cada limiter en
`session.py`— y no cambia porque el barrido pase antes o después. Subir un
intervalo deja más basura entre pasadas; no altera lo que ve un usuario.

Los cinco tienen **suelo**. Un `while True` con `sleep(0)` no es «purgar
constantemente»: es un proceso quemando una CPU entera sin avanzar, y con
`GAIA_WORKERS=4` son cuatro. Un valor por debajo del suelo, o que no es un
número, se corrige al arrancar y queda anotado en `ANOMALIAS` para que la
auditoría de configuración lo diga en vez de aplicarse en silencio.
"""

from __future__ import annotations

import os

# Correcciones aplicadas al leer el entorno. Las lee
# `startup_checks._check_maintenance_intervals()`; se guardan nombres de
# variable, nunca valores, como el resto del informe de arranque.
ANOMALIAS: list[str] = []

_HORA = 3600


def _intervalo(var: str, defecto: int, *, factor: int = 1, suelo: int = 60) -> int:
    """Segundos entre pasadas de un bucle, saneados.

    `factor` convierte la unidad en la que se declara la variable (horas, casi
    siempre) a los segundos que espera `asyncio.sleep`.
    """
    crudo = os.getenv(var, "")
    if not crudo:
        return defecto * factor
    try:
        valor = int(crudo) * factor
    except ValueError:
        ANOMALIAS.append(var)
        return defecto * factor
    if valor < suelo:
        ANOMALIAS.append(var)
        return suelo
    return valor


# Purga de cuentas con el período de gracia del RGPD ya expirado.
GDPR_PURGE_SECONDS = _intervalo("GAIA_GDPR_PURGE_HOURS", 6, factor=_HORA)

# Purga de entradas de log más antiguas que la retención configurada por el
# admin. La retención es la política y se toca en el panel; esto solo es cada
# cuánto se comprueba.
LOG_PURGE_SECONDS = _intervalo("GAIA_LOG_PURGE_HOURS", 24, factor=_HORA)

# Purga de avisos vencidos. La retención —dos ventanas, leídos y sin leer— es
# la política y la fija el admin; esto solo es cada cuánto se comprueba.
NOTIFICATION_PURGE_SECONDS = _intervalo("GAIA_NOTIFICATION_PURGE_HOURS", 24, factor=_HORA)

# Purga de ventanas de rate limit ya vencidas. No afecta a ninguna cuota: una
# ventana caducada deja de contar al caducar, no al borrarse.
# Ver docs/adr/009-cuota-compartida-y-por-principal.md
RATELIMIT_PURGE_SECONDS = _intervalo("GAIA_RATELIMIT_PURGE_HOURS", 6, factor=_HORA)

# Reconciliación de ejecuciones de workflow huérfanas (el worker que las tenía
# murió). Es el único bucle que corre en segundos: mientras no pase, esas
# ejecuciones se ven «en curso» en la interfaz sin que nadie las esté moviendo.
WORKFLOW_TICK_SECONDS = _intervalo("GAIA_WORKFLOW_TICK_SECONDS", 30, suelo=5)

# Retención de ejecuciones de workflow. Corre montada sobre el tick anterior,
# así que el bucle la traduce a un número de ticks.
WORKFLOW_PURGE_SECONDS = _intervalo("GAIA_WORKFLOW_PURGE_HOURS", 1, factor=_HORA)
