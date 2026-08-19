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

No analiza el fichero `postgres.py` —que hoy solo tiene el runner— sino **cada
función registrada como la variante PostgreSQL de un paso**, esté donde esté.
Los pasos viven agrupados por dominio, con su pareja de SQLite al lado, así que
buscarlos por fichero dejaría el guard mirando al sitio equivocado; buscarlos
por su papel en `MIGRATION_PAIRS` no se puede eludir moviendo código.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

from app.storage.migrations.steps import MIGRATION_PAIRS

# asyncpg.Connection ofrece fetch/fetchrow/fetchval/execute/executemany.
# fetchall y fetchone son del envoltorio AsyncConn y de aiosqlite.
METODOS_PROHIBIDOS = {"fetchall", "fetchone", "execute_fetchall", "execute_insert"}


def _funciones_de_postgres() -> list[tuple[str, ast.AST]]:
    """El AST de cada paso que se ejecuta contra PostgreSQL.

    Se saltan los que comparten función con SQLite: su SQL es idéntico en los
    dos motores y no hay dialecto que traducir.
    """
    funciones = []
    for par in MIGRATION_PAIRS:
        if par.postgres is par.sqlite:
            continue
        fuente = textwrap.dedent(inspect.getsource(par.postgres))
        funciones.append((par.postgres.__name__, ast.parse(fuente)))
    return funciones


def _nodos_conn(arbol: ast.AST):
    """Llamadas de la forma `conn.algo(...)`."""
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        f = nodo.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "conn"
        ):
            yield nodo, f


def test_hay_pasos_que_auditar():
    """Si el guard deja de encontrar pasos, pasa en verde sin mirar nada."""
    funciones = _funciones_de_postgres()
    assert len(funciones) >= 20, (
        "El guard no está encontrando las migraciones de PostgreSQL: "
        f"solo ve {len(funciones)}. Comprueba `MIGRATION_PAIRS`."
    )


def test_no_usa_metodos_que_asyncpg_no_tiene():
    intrusos = [
        f"{nombre}: conn.{f.attr}() en línea {nodo.lineno}"
        for nombre, arbol in _funciones_de_postgres()
        for nodo, f in _nodos_conn(arbol)
        if f.attr in METODOS_PROHIBIDOS
    ]
    assert intrusos == [], (
        "Un paso de PostgreSQL usa métodos del envoltorio sobre una conexión "
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
    con_interrogante = []
    for nombre, arbol in _funciones_de_postgres():
        for nodo, _f in _nodos_conn(arbol):
            if not nodo.args:
                continue
            primero = nodo.args[0]
            texto = primero.value if isinstance(primero, ast.Constant) else None
            if isinstance(texto, str) and posicion_de_parametro.search(texto):
                con_interrogante.append(
                    f"{nombre}: {' '.join(texto.split())[:70]}"
                )

    assert con_interrogante == [], (
        f"Marcadores `?` en migraciones de PostgreSQL (usa $1, $2…): {con_interrogante}"
    )


def test_los_parametros_no_van_en_una_tupla():
    """asyncpg recibe los parámetros sueltos; una tupla se toma como un valor."""
    culpables = [
        f"{nombre}: conn.{f.attr}(…, (tupla))"
        for nombre, arbol in _funciones_de_postgres()
        for nodo, f in _nodos_conn(arbol)
        if len(nodo.args) >= 2
        and f.attr in {"execute", "fetch", "fetchrow", "fetchval"}
        and isinstance(nodo.args[1], ast.Tuple)
    ]
    assert culpables == [], (
        f"Parámetros en tupla sobre asyncpg (pásalos sueltos): {culpables}"
    )
