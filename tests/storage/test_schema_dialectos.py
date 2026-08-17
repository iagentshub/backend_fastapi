"""Una plantilla, dos dialectos (mejora #09).

`schema.py` tenía dos constantes de 331 líneas idénticas al 91%, así que cada
cambio había que escribirlo dos veces sin que nada lo comprobara — y la suite
corre siempre en SQLite, de modo que una divergencia en el bloque de PostgreSQL
no la ve nadie hasta el despliegue.

Estos tests son la red que faltaba: comparan los dos dialectos entre sí para
que ninguna tabla, columna o índice pueda existir solo en uno.
"""

from __future__ import annotations

import re

import pytest

from app.sql import SQL_DIR
from app.storage.schema import SCHEMA_PG, SCHEMA_SQLITE, schema_for


def _tablas(ddl: str) -> dict[str, list[str]]:
    """{nombre de tabla: [columnas]} a partir del DDL."""
    tablas: dict[str, list[str]] = {}
    for bloque in ddl.split(";"):
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*)\)", bloque, re.S)
        if not m:
            continue
        columnas = []
        for linea in m.group(2).splitlines():
            linea = linea.strip().rstrip(",")
            if not linea or linea.upper().startswith(
                ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE(", "CONSTRAINT", "CHECK")
            ):
                continue
            columnas.append(linea.split()[0])
        tablas[m.group(1)] = columnas
    return tablas


def _indices(ddl: str) -> set[str]:
    return set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", ddl))


def test_las_mismas_tablas_en_los_dos_dialectos():
    sqlite, pg = _tablas(SCHEMA_SQLITE), _tablas(SCHEMA_PG)
    assert set(sqlite) == set(pg), (
        f"solo en SQLite: {sorted(set(sqlite) - set(pg))}; "
        f"solo en PG: {sorted(set(pg) - set(sqlite))}"
    )


def test_las_mismas_columnas_en_cada_tabla():
    sqlite, pg = _tablas(SCHEMA_SQLITE), _tablas(SCHEMA_PG)
    for tabla in sorted(sqlite):
        assert sqlite[tabla] == pg[tabla], f"{tabla} difiere entre dialectos"


def test_los_mismos_indices():
    assert _indices(SCHEMA_SQLITE) == _indices(SCHEMA_PG)


def test_no_quedan_marcadores_sin_sustituir():
    for ddl in (SCHEMA_SQLITE, SCHEMA_PG):
        assert not re.search(r"@[A-Z_]+@", ddl), "marcador de dialecto sin resolver"


def test_los_tipos_propios_de_cada_dialecto():
    assert "SMALLINT NOT NULL DEFAULT 1" in SCHEMA_PG
    assert "BIGSERIAL PRIMARY KEY" in SCHEMA_PG
    assert "DOUBLE PRECISION" in SCHEMA_PG
    assert "(NOW()::TEXT)" in SCHEMA_PG

    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in SCHEMA_SQLITE
    assert "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')" in SCHEMA_SQLITE
    assert "SMALLINT" not in SCHEMA_SQLITE


def test_el_ddl_de_pg_se_puede_partir_por_punto_y_coma():
    """migrate_schema parte el DDL de PG por ';' (db.py): ningún literal puede
    llevar uno dentro o la sentencia se cortaría por la mitad."""
    for sentencia in SCHEMA_PG.split(";"):
        assert sentencia.count("'") % 2 == 0, (
            f"comillas desparejadas tras partir por ';': {sentencia[:120]!r}"
        )


def test_ningun_comentario_del_esquema_lleva_punto_y_coma():
    """Un ';' dentro de un `--` parte la sentencia siguiente por la mitad.

    Pasó de verdad: un comentario que decía "trazabilidad de solo escritura; se
    lee por…" cortó el CREATE TABLE de `skills` en dos, y PostgreSQL abortaba
    el arranque con "syntax error at end of input". La suite no lo vio porque
    en SQLite el DDL se aplica con `executescript`, que no parte nada; solo
    apareció al aplicar el esquema contra una base PostgreSQL real.
    """
    culpables = [
        f"{ruta.name}:{numero}: {linea.strip()}"
        for ruta in sorted((SQL_DIR / "schema").glob("*.sql"))
        for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1)
        if linea.strip().startswith("--") and ";" in linea
    ]
    assert culpables == [], f"Comentarios con ';' en el esquema: {culpables}"


def test_cada_sentencia_del_ddl_de_pg_esta_completa():
    """Tras partir por ';', cada trozo tiene que ser una sentencia entera.

    Los paréntesis desbalanceados son la señal de que el corte cayó dentro de
    un CREATE TABLE, que es como se manifestó el fallo de arriba.
    """
    for sentencia in SCHEMA_PG.split(";"):
        util = "\n".join(
            linea
            for linea in sentencia.splitlines()
            if not linea.strip().startswith("--")
        ).strip()
        if not util:
            continue
        assert util.count("(") == util.count(")"), (
            f"sentencia cortada por la mitad: {util[:120]!r}"
        )
        assert re.match(r"(?i)^(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE)\b", util), (
            f"trozo que no empieza por una sentencia: {util[:120]!r}"
        )


def test_dialecto_desconocido():
    with pytest.raises(ValueError, match="Dialecto"):
        schema_for("mysql")
