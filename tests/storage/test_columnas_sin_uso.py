"""Ninguna columna del esquema se queda sin que nadie la nombre.

`resource_social` arrastró `fork_of_user` y `fork_of_id` durante toda su vida:
venían de un «fork» que nunca se implementó, ninguna consulta las escribía ni
las leía y en toda instalación estaban a NULL. No fallaba nada —una columna de
más no rompe una consulta— y por eso sobrevivieron a varias revisiones del
catálogo, viajando en cada `SELECT *` y en cada reconstrucción de la tabla.

El criterio es deliberadamente laxo: basta con que **alguien la nombre** en una
consulta, en código o en una migración. Una columna de solo escritura
—trazabilidad, auditoría— pasa, porque su `INSERT` la nombra; lo que no pasa es
la que no aparece en ninguna parte, que es la que ya nadie sabe por qué está.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
SCHEMA = APP / "sql" / "schema"

_NO_ES_COLUMNA = re.compile(r"(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT)\b", re.I)


def _columnas_declaradas() -> dict[str, list[str]]:
    tablas: dict[str, list[str]] = {}
    for fichero in sorted(SCHEMA.glob("*.sql")):
        creacion = re.search(
            r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);",
            fichero.read_text(encoding="utf-8"),
            re.S,
        )
        if not creacion:
            continue
        columnas = []
        for linea in creacion.group(2).split("\n"):
            linea = linea.strip()
            if not linea or linea.startswith("--") or _NO_ES_COLUMNA.match(linea):
                continue
            nombre = re.match(r"(\w+)\s", linea)
            if nombre:
                columnas.append(nombre.group(1))
        tablas[creacion.group(1)] = columnas
    return tablas


def _todo_lo_que_puede_nombrarlas() -> str:
    partes = [f.read_text(encoding="utf-8") for f in APP.rglob("*.py")]
    partes += [
        f.read_text(encoding="utf-8")
        for f in (APP / "sql").rglob("*.sql")
        if f.parent.name != "schema"
    ]
    return "\n".join(partes)


def test_ninguna_columna_del_esquema_esta_sin_usar():
    corpus = _todo_lo_que_puede_nombrarlas()
    huerfanas = [
        f"{tabla}.{columna}"
        for tabla, columnas in _columnas_declaradas().items()
        for columna in columnas
        if not re.search(rf"\b{re.escape(columna)}\b", corpus)
    ]

    assert huerfanas == [], (
        "Estas columnas están declaradas y no las nombra ninguna consulta, "
        "ningún código y ninguna migración. Retíralas del esquema y añade el "
        "paso que las quita de las bases que ya existen:\n"
        + "\n".join(sorted(huerfanas))
    )
