"""El SQL estático vive en app/sql/, no incrustado en los módulos (mejora #43).

Sacarlo a ficheros solo sirve si no vuelve a colarse por otro lado, y cambia el
modo de fallo: antes una consulta rota era un error de sintaxis SQL a la vista;
ahora un identificador mal escrito no se nota hasta que esa rama se ejecuta.
Los tres tests de aquí cubren las tres formas de que eso pase: SQL que vuelve al
Python, un identificador que no resuelve, y una sección que ya no usa nadie.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.config.tool_runtimes import TOOL_RUNTIMES
from app.sql import SQL_DIR, motores_de, secciones_de, sql

APP = Path(__file__).resolve().parents[2] / "app"

EJECUTORES = {
    "execute",
    "executemany",
    "executescript",
    "fetchall",
    "fetchone",
    "fetchval",
    "fetch",
    "execute_fetchall",
    "execute_insert",
}
EMPIEZA_SQL = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP)\b", re.I
)

# Lo que legítimamente sigue escrito en Python:
#   - db.py ejecuta los PRAGMA de configuración de la conexión, que no son
#     consultas sino ajustes del motor, y son solo de SQLite.
#   - las migraciones son una secuencia histórica: cada una describe el paso de
#     un esquema al siguiente y se lee junto al código que transforma los datos.
EXENTOS = {"storage/db.py"}
EXENTOS_DIR = {"storage/migrations"}


def _modulos():
    for ruta in sorted(APP.rglob("*.py")):
        rel = ruta.relative_to(APP).as_posix()
        if rel in EXENTOS or any(rel.startswith(d) for d in EXENTOS_DIR):
            continue
        yield ruta, rel


# Bases de consultas que se completan en ejecución: el módulo les añade el
# filtro opcional y el ORDER BY antes de ejecutarlas, así que no son sentencias
# terminadas y no pueden vivir en un fichero. Se listan una a una para que
# añadir una nueva sea deliberado.
FRAGMENTOS_DINAMICOS = {
    ("storage/chat.py", "_CONVERSATION_TOKENS_SELECT"),
    ("storage/chat.py", "_CONVERSATION_TOKENS_GROUP_BY"),
    ("storage/knowledge.py", "query"),
    ("storage/knowledge_packs.py", "query"),
}


def test_no_hay_sql_estatica_asignada_a_variables():
    """La SQL tampoco puede entrar por una variable.

    El primer guard miraba solo el argumento de `conn.execute(...)`, así que un
    `query = "INSERT …"` en la línea de antes pasaba de largo: por ahí seguía
    viviendo el UPSERT del rate limiter, la única consulta que quedó fuera del
    catálogo sin que nadie lo notara.
    """
    intrusos = []
    for ruta, rel in _modulos():
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Assign):
                destino, valor = nodo.targets[0], nodo.value
            elif isinstance(nodo, ast.AnnAssign) and nodo.value:
                destino, valor = nodo.target, nodo.value
            else:
                continue
            if not (
                isinstance(valor, ast.Constant)
                and isinstance(valor.value, str)
                and EMPIEZA_SQL.match(valor.value)
            ):
                continue
            nombre = ast.unparse(destino)
            if (rel, nombre) in FRAGMENTOS_DINAMICOS:
                continue
            intrusos.append(f"{rel}:{nodo.lineno} ({nombre})")

    assert intrusos == [], (
        "SQL estática asignada a una variable. Muévela a app/sql/queries/, o "
        f"añádela a FRAGMENTOS_DINAMICOS si se completa en ejecución: {intrusos}"
    )


def test_no_hay_sql_estatica_incrustada():
    """Ninguna llamada a la BD recibe una consulta constante escrita a mano."""
    intrusos = []
    for ruta, rel in _modulos():
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call) or not nodo.args:
                continue
            f = nodo.func
            nombre = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if nombre not in EJECUTORES:
                continue
            arg = nodo.args[0]
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and EMPIEZA_SQL.match(arg.value)
            ):
                intrusos.append(f"{rel}:{nodo.lineno}")

    assert intrusos == [], (
        "SQL estática incrustada en el código. Muévela a app/sql/queries/ y "
        f'pídela con sql("queries/fichero:nombre"): {intrusos}'
    )


def _referencias() -> set[str]:
    """Todos los identificadores que el código pide.

    Busca la forma del identificador y no la llamada `sql(...)`: varias
    llamadas eligen la sección con un condicional —`sql(… if _db.IS_PG else …)`—
    y anclar en el paréntesis dejaba fuera la segunda rama, que es justo la que
    solo se ejecuta en el motor que la suite no prueba.
    """
    patron = re.compile(r'"((?:queries|schema)/[a-z0-9_]+(?::[a-z0-9_]+)?)"')
    referencias: set[str] = set()
    for ruta in APP.rglob("*.py"):
        # El cargador se documenta a sí mismo con ejemplos; no es un consumidor.
        if ruta == SQL_DIR / "__init__.py":
            continue
        referencias.update(patron.findall(ruta.read_text(encoding="utf-8")))
    return referencias


def test_toda_referencia_del_codigo_resuelve():
    """Un identificador con una errata falla en ejecución, no al importar."""
    rotas = []
    for referencia in sorted(_referencias()):
        archivo, _, seccion = referencia.partition(":")
        destino = SQL_DIR / f"{archivo}.sql"
        if not destino.is_file():
            rotas.append(f"{referencia} (no existe {archivo}.sql)")
        elif seccion and seccion not in secciones_de(archivo):
            rotas.append(f"{referencia} (sección inexistente)")

    assert rotas == [], f"Identificadores SQL que no resuelven: {rotas}"


def test_ninguna_seccion_queda_sin_usar():
    """El catálogo no acumula consultas muertas."""
    referencias = _referencias()
    huerfanas = [
        f"{ruta.stem}:{seccion}"
        for ruta in sorted((SQL_DIR / "queries").glob("*.sql"))
        for seccion in secciones_de(f"queries/{ruta.stem}")
        if f"queries/{ruta.stem}:{seccion}" not in referencias
    ]

    assert huerfanas == [], f"Secciones SQL sin ningún consumidor: {huerfanas}"


def test_listados_de_tools_no_arrastran_contenido_ni_binario():
    for section in ("list_public", "list_private_by_owner", "list_private", "list_all"):
        query = sql(f"queries/tools:{section}").lower()
        select = query.partition("from tools")[0]
        assert "binary_b64" not in select
        # Consultar si existe texto no lo transporta. Lo prohibido es incluir
        # la columna completa en la proyección de los listados.
        assert not re.search(r"(?:select|,)\s*content\s*(?:,|$)", select)


def test_upsert_de_tools_no_reescribe_columnas_binarias():
    for section in ("upsert_pg", "upsert_sqlite"):
        query = sql(f"queries/tools:{section}").lower()
        assert "binary_b64" not in query
        assert "binary_filename" not in query
        assert "binary_size" not in query
        assert "binary_uploaded_at" not in query


# Sintaxis que solo entiende uno de los dos motores. Clasificar por lo que la
# consulta dice y no por cómo se llama: un nombre acabado en `_pg` es una
# convención, y la convención es justo lo que se olvida.
SOLO_SQLITE = (
    r"\bINSERT\s+OR\s+(IGNORE|REPLACE)\b",
    r"\bdatetime\s*\(\s*'now'",
    r"\bstrftime\s*\(",
    r"\bsqlite_master\b",
    r"\bdbstat\b",
    r"\browid\b",
)
SOLO_PG = (
    r"\bnow\s*\(\s*\)",
    r"\binformation_schema\b",
    r"\bpg_[a-z_]+\b",
    r"::\w+",
    r"\bto_regclass\b",
    r"\bEXCLUDED\.",
    r"\bFOR\s+UPDATE\b",
)


def _motor_por_sintaxis(cuerpo: str) -> str | None:
    sqlite = any(re.search(p, cuerpo, re.I | re.M) for p in SOLO_SQLITE)
    pg = any(re.search(p, cuerpo, re.M) for p in SOLO_PG)
    if sqlite and not pg:
        return "sqlite"
    if pg and not sqlite:
        return "pg"
    return None


def _motor_de_cada_seccion() -> dict[str, str]:
    """Motor de cada sección, según lo que declara `-- engine:`."""
    motores = {}
    for ruta in sorted((SQL_DIR / "queries").glob("*.sql")):
        for seccion, motor in motores_de(f"queries/{ruta.stem}").items():
            motores[f"queries/{ruta.stem}:{seccion}"] = motor
    return motores


def test_toda_consulta_dialectal_declara_su_motor():
    """La sintaxis de un solo motor tiene que llevar `-- engine:`.

    Es la mitad que la convención de nombres no cubre: un `_pg` en el nombre es
    una costumbre, y basta olvidarlo una vez para que la consulta quede sin
    vigilancia. Aquí manda lo que dice la consulta, no cómo se llama.
    """
    sin_declarar, contradictorias = [], []
    for ruta in sorted((SQL_DIR / "queries").glob("*.sql")):
        declarados = motores_de(f"queries/{ruta.stem}")
        for seccion, cuerpo in secciones_de(f"queries/{ruta.stem}").items():
            detectado = _motor_por_sintaxis(cuerpo)
            declarado = declarados.get(seccion)
            identificador = f"{ruta.stem}:{seccion}"
            if detectado and not declarado:
                sin_declarar.append(f"{identificador} (parece de {detectado})")
            elif detectado and declarado != detectado:
                contradictorias.append(
                    f"{identificador} declara {declarado} y su sintaxis es de {detectado}"
                )

    assert sin_declarar == [], (
        f"Consultas con sintaxis de un solo motor y sin `-- engine:`: {sin_declarar}"
    )
    assert contradictorias == [], f"`-- engine:` que no cuadra: {contradictorias}"


def test_el_motor_declarado_no_llega_al_motor():
    """`-- engine:` es metadato: no puede acabar dentro de la consulta."""
    con_metadato = [
        f"{ruta.stem}:{seccion}"
        for ruta in sorted((SQL_DIR / "queries").glob("*.sql"))
        for seccion, cuerpo in secciones_de(f"queries/{ruta.stem}").items()
        if "-- engine:" in cuerpo
    ]
    assert con_metadato == [], f"El marcador se coló en el cuerpo: {con_metadato}"


class _RamasIsPg(ast.NodeVisitor):
    """Anota cada identificador SQL con la rama de `IS_PG` que lo cubre."""

    def __init__(self) -> None:
        self.pila: list[str | None] = []
        self.usos: list[tuple[str, str | None, int]] = []

    def _es_is_pg(self, test: ast.expr) -> bool:
        return re.search(r"\bIS_PG\b", ast.unparse(test)) is not None

    def visit_If(self, nodo: ast.If) -> None:
        negada = ast.unparse(nodo.test).strip().startswith("not ")
        for cuerpo, es_pg in ((nodo.body, not negada), (nodo.orelse, negada)):
            rama = ("pg" if es_pg else "sqlite") if self._es_is_pg(nodo.test) else None
            self.pila.append(rama)
            for hijo in cuerpo:
                self.visit(hijo)
            self.pila.pop()

    def visit_IfExp(self, nodo: ast.IfExp) -> None:
        if not self._es_is_pg(nodo.test):
            self.generic_visit(nodo)
            return
        for sub, rama in ((nodo.body, "pg"), (nodo.orelse, "sqlite")):
            self.pila.append(rama)
            self.visit(sub)
            self.pila.pop()

    def visit_Constant(self, nodo: ast.Constant) -> None:
        if isinstance(nodo.value, str) and nodo.value.startswith("queries/"):
            ramas = [r for r in self.pila if r]
            self.usos.append((nodo.value, ramas[-1] if ramas else None, nodo.lineno))


def test_las_consultas_dialectales_solo_corren_en_su_motor():
    """Una consulta de un solo motor tiene que estar bajo su rama de `IS_PG`.

    La suite corre siempre en SQLite, así que una consulta de PostgreSQL mal
    situada no la ve nadie hasta el despliegue — y al revés es peor: un
    `INSERT OR IGNORE` sin rama es un error de sintaxis en PostgreSQL que un
    `except` cercano puede degradar a un warning. Le pasaba a la migración
    legacy de memory_files.
    """
    motores = _motor_de_cada_seccion()  # lo que declara `-- engine:`
    fuera_de_sitio = []
    for ruta, rel in _modulos():
        visitante = _RamasIsPg()
        visitante.visit(ast.parse(ruta.read_text(encoding="utf-8")))
        for identificador, rama, linea in visitante.usos:
            esperado = motores.get(identificador)
            if esperado and rama != esperado:
                donde = "sin rama de IS_PG" if rama is None else f"en la rama {rama}"
                fuera_de_sitio.append(
                    f"{rel}:{linea} usa {identificador} ({esperado}) {donde}"
                )

    assert fuera_de_sitio == [], (
        "Consultas de un solo motor fuera de su rama: " + "; ".join(fuera_de_sitio)
    )


def test_el_esquema_tiene_un_fichero_por_tabla():
    from app.storage.schema import TABLAS

    ficheros = {ruta.stem for ruta in (SQL_DIR / "schema").glob("*.sql")}
    assert ficheros == set(TABLAS), (
        f"solo en disco: {sorted(ficheros - set(TABLAS))}; "
        f"solo en TABLAS: {sorted(set(TABLAS) - ficheros)}"
    )


def test_los_runtimes_de_tools_coinciden_con_la_restriccion_sql():
    schema = (SQL_DIR / "schema" / "tools.sql").read_text(encoding="utf-8")
    match = re.search(
        r"language\s+TEXT.*?CHECK\s*\(language\s+IN\s*\(([^)]*)\)",
        schema,
        re.S,
    )
    assert match is not None, "tools.language debe conservar su CHECK explícito"
    schema_values = set(re.findall(r"'([^']+)'", match.group(1)))
    assert schema_values == set(TOOL_RUNTIMES)
