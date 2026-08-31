"""Cuántas copias se conservan del historial de cada recurso.

Guardar un agente, una skill, un prompt o una orquestación archiva además el
recurso **entero** en `resource_versions` — no es un diff, es una copia—, y nada
lo borraba nunca. El disparador no es un evento externo sino el botón de
guardar: afinar el prompt de un agente son decenas de guardados en una tarde, y
la tabla crecía en proporción a lo bien que trabajase el usuario, que es el
incentivo exactamente equivocado.

El tope se aplica en el mismo INSERT que archiva, así que no hay bucle de fondo,
ni cadencia que ajustar, ni índice por fecha que añadir: el trabajo se hace donde
se genera el dato.

ponytail: solo tope por número, no retención por antigüedad. Cincuenta versiones
son historial de sobra para revertir un error, que es para lo que se usa. Si
alguna vez hace falta también por fecha, la forma ya está decidida por el resto
del sistema —cuánto se guarda a configuración del admin, cada cuánto se barre a
`maintenance.py` con su suelo— y cuelga del barrido de logs, que ya pasa cada
24 h.
"""

from __future__ import annotations

import os


def _tope(nombre: str, defecto: int, *, minimo: int) -> int:
    """Un tope por debajo del mínimo dejaría un historial que no sirve."""
    try:
        return max(minimo, int(os.getenv(nombre, str(defecto))))
    except ValueError:
        return defecto


MAX_VERSIONS_PER_RESOURCE = _tope(
    "GAIA_MAX_RESOURCE_VERSIONS", 50, minimo=5
)
