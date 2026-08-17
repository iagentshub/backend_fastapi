"""Catálogo de SQL en ficheros, resuelto por identificador.

La SQL estática del backend vive aquí, en `.sql`, y el código la pide por su
identificador en vez de llevarla incrustada en un literal de Python. El motivo
es poder leer todo el SQL junto: estaba repartido en más de cuarenta módulos,
mezclado con la lógica que decodifica las filas, y no había forma de revisar
"todas las consultas sobre `agents`" sin abrir media docena de ficheros.

Dos formas de identificador:

    sql("schema/agents")           -> app/sql/schema/agents.sql, entero
    sql("queries/agents:get_any")  -> la sección `-- name: get_any` de
                                      app/sql/queries/agents.sql

El esquema va a fichero por tabla porque hay un consumidor que necesita una
sola —`flog` crea `app_logs` él mismo antes de `init_db` y hasta ahora la
extraía filtrando el DDL completo por substring—. Las consultas van agrupadas
por módulo con secciones, porque un fichero por sentencia serían seiscientos.

El contenido se cachea al primer acceso: `flog` pide SQL en cada arranque de
proceso y las rutas en cada petición, así que leer del disco cada vez sería
pagar E/S por algo que no cambia en caliente.

Ver docs/adr/007-sql-en-ficheros.md.

Lo que NO está aquí: las consultas que se construyen en tiempo de ejecución
—filtros opcionales, listas IN de longitud variable, la tabla como parámetro—.
Un fichero estático no las puede representar sin inventar un lenguaje de
plantillas, que es exactamente la complejidad que este módulo evita. Siguen en
su módulo, y `tests/storage/test_sql_en_ficheros.py` las conoce una por una.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent

# `-- name: identificador` abre una sección y la anterior termina donde empieza
# la siguiente. El resto de comentarios del fichero son texto normal y viajan
# con la sentencia que acompañan.
_MARCA = "-- name:"

# `-- engine: pg` o `-- engine: sqlite` declara que esa consulta solo vale en un
# motor. Se declara en vez de deducirse del nombre porque el sufijo `_pg` es una
# convención, y la convención es lo que se olvida; el test compara lo declarado
# con la sintaxis real y con la rama de `IS_PG` desde la que se usa.
_MARCA_MOTOR = "-- engine:"
MOTORES = ("pg", "sqlite")


class SQLNoEncontrada(LookupError):
    """El identificador no corresponde a ningún fichero o sección."""


@lru_cache(maxsize=None)
def _fichero(ruta: str) -> str:
    destino = (SQL_DIR / f"{ruta}.sql").resolve()
    # `ruta` viene siempre de una constante del código, nunca de una petición;
    # la comprobación es contra el error de tipeo, no contra un atacante.
    if not destino.is_relative_to(SQL_DIR) or not destino.is_file():
        raise SQLNoEncontrada(f"No existe el fichero SQL {ruta!r}")
    return destino.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _parsear(ruta: str) -> tuple[dict[str, str], dict[str, str]]:
    """(cuerpo por sección, motor declarado por sección)."""
    secciones: dict[str, list[str]] = {}
    motores: dict[str, str] = {}
    actual: str | None = None
    for linea in _fichero(ruta).splitlines(keepends=True):
        if linea.startswith(_MARCA):
            nombre = linea[len(_MARCA) :].strip()
            if nombre in secciones:
                raise ValueError(f"Sección SQL duplicada: {ruta}:{nombre}")
            secciones[nombre] = []
            actual = nombre
            continue
        if actual is None:
            continue
        if linea.startswith(_MARCA_MOTOR):
            # Metadato, no SQL: se queda fuera del cuerpo que va al motor.
            motor = linea[len(_MARCA_MOTOR) :].strip()
            if motor not in MOTORES:
                raise ValueError(
                    f"Motor desconocido en {ruta}:{actual}: {motor!r}; "
                    f"admitidos {MOTORES}"
                )
            motores[actual] = motor
            continue
        secciones[actual].append(linea)
    cuerpos = {n: "".join(c).strip() for n, c in secciones.items()}
    return cuerpos, motores


def _secciones(ruta: str) -> dict[str, str]:
    return _parsear(ruta)[0]


@lru_cache(maxsize=None)
def sql(identificador: str) -> str:
    """SQL con ese identificador: ``ruta`` o ``ruta:seccion``."""
    ruta, _, seccion = identificador.partition(":")
    if not seccion:
        return _fichero(ruta)
    secciones = _secciones(ruta)
    try:
        return secciones[seccion]
    except KeyError:
        raise SQLNoEncontrada(
            f"{ruta}.sql no tiene la sección {seccion!r}; "
            f"tiene {sorted(secciones)}"
        ) from None


def secciones_de(ruta: str) -> dict[str, str]:
    """Todas las secciones de un fichero. Para los tests y para recorrerlas."""
    return dict(_secciones(ruta))


def motores_de(ruta: str) -> dict[str, str]:
    """Motor declarado con `-- engine:` en cada sección que lo lleve."""
    return dict(_parsear(ruta)[1])
