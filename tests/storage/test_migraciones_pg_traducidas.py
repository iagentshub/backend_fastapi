"""Las migraciones de PostgreSQL reciben asyncpg en crudo, no `AsyncConn`.

`migrate_schema` abre la conexión con `asyncpg.connect()` y se la pasa tal cual
a `run_postgres_migrations` (ver db.py), así que ahí no existen las comodidades
del envoltorio: ni `fetchall`, ni `fetchone`, ni marcadores `?` traducidos.

El riesgo es concreto y ya se materializó: `_connection_provider_accounts`
estaba copiada de `sqlite.py` sin traducir —`fetchall`, `fetchone` y `?` con
una tupla— y reventaba con `AttributeError`, dejando sin arrancar cualquier
instalación nueva sobre PostgreSQL. No lo vio nadie porque la suite corre en
SQLite; apareció al ejecutar las migraciones contra una base real.

Este test no necesita PostgreSQL: mira la forma del código, que es donde está
el error.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PG = Path(__file__).resolve().parents[2] / "app/storage/migrations/postgres.py"

# asyncpg.Connection ofrece fetch/fetchrow/fetchval/execute/executemany.
# fetchall y fetchone son del envoltorio AsyncConn y de aiosqlite.
METODOS_PROHIBIDOS = {"fetchall", "fetchone", "execute_fetchall", "execute_insert"}


def _llamadas_sobre_conn() -> list[tuple[str, int]]:
    arbol = ast.parse(PG.read_text(encoding="utf-8"))
    llamadas = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        f = nodo.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "conn"
        ):
            llamadas.append((f.attr, nodo.lineno))
    return llamadas


def test_no_usa_metodos_que_asyncpg_no_tiene():
    intrusos = [
        f"línea {linea}: conn.{metodo}()"
        for metodo, linea in _llamadas_sobre_conn()
        if metodo in METODOS_PROHIBIDOS
    ]
    assert intrusos == [], (
        "migrations/postgres.py usa métodos del envoltorio sobre una conexión "
        f"asyncpg en crudo (usa fetch/fetchrow/fetchval): {intrusos}"
    )


def test_no_quedan_marcadores_de_sqlite():
    """asyncpg numera los parámetros: `?` es un error de sintaxis, no un hueco.

    No vale con buscar el carácter: en PostgreSQL `?` también es el operador
    JSONB "contiene esta clave", y `(data::jsonb -> 'labels') ? 'public'` es
    SQL legítimo que usa una de estas migraciones. Se busca el `?` en posición
    de parámetro —pegado a un `=`, una coma o un paréntesis—, que es como se
    escribe un marcador y no como se escribe el operador.
    """
    posicion_de_parametro = re.compile(r"[=,(]\s*\?|\?\s*[,)]")
    arbol = ast.parse(PG.read_text(encoding="utf-8"))
    con_interrogante = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call) or not nodo.args:
            continue
        f = nodo.func
        if not (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "conn"
        ):
            continue
        primero = nodo.args[0]
        texto = primero.value if isinstance(primero, ast.Constant) else None
        if isinstance(texto, str) and posicion_de_parametro.search(texto):
            con_interrogante.append(f"línea {nodo.lineno}: {' '.join(texto.split())[:70]}")

    assert con_interrogante == [], (
        f"Marcadores `?` en migraciones de PostgreSQL (usa $1, $2…): {con_interrogante}"
    )


def test_los_parametros_no_van_en_una_tupla():
    """asyncpg recibe los parámetros sueltos; una tupla se toma como un valor."""
    arbol = ast.parse(PG.read_text(encoding="utf-8"))
    culpables = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call) or len(nodo.args) < 2:
            continue
        f = nodo.func
        if not (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "conn"
            and f.attr in {"execute", "fetch", "fetchrow", "fetchval"}
        ):
            continue
        if isinstance(nodo.args[1], ast.Tuple):
            culpables.append(f"línea {nodo.lineno}: conn.{f.attr}(…, (tupla))")

    assert culpables == [], (
        f"Parámetros en tupla sobre asyncpg (pásalos sueltos): {culpables}"
    )
