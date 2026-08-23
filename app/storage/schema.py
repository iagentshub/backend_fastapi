"""DDL base para SQLite y PostgreSQL.

Una sola definición, dos dialectos. Antes eran dos constantes de 331 líneas
cada una, idénticas al 91%: mismas tablas, mismas columnas, mismos índices, y
solo 29 líneas de diferencia — siempre por lo mismo (el tipo de los booleanos,
el autoincremento, el flotante y una expresión de fecha por defecto). El coste
no era el tamaño sino que cada cambio había que escribirlo dos veces sin que
nada lo comprobara: la columna `labels` de `knowledge_items` llegó a estar
añadida a mano en los dos bloques, y una divergencia en el de PostgreSQL no la
detecta la suite, que corre siempre en SQLite.

El DDL ya no vive en este fichero sino en `app/sql/schema/`, un `.sql` por
tabla con sus índices al lado. Aquí queda lo que no es SQL: qué tablas hay, en
qué orden se crean (las claves ajenas lo exigen) y cómo se traduce cada
marcador a cada dialecto.

`tests/storage/test_schema_dialectos.py` fija el resultado.

Los marcadores son `@NOMBRE@` y no `{NOMBRE}` porque el DDL lleva llaves
propias y `str.format` las interpretaría.

Ojo al tocar los dialectos: `migrate_schema` parte el DDL de PostgreSQL por
`";"` (ver db.py), así que ninguna sustitución puede meter un punto y coma
dentro de un literal.

Las migraciones incrementales permanecen en :mod:`app.storage.migrations`.
"""

from __future__ import annotations

from app.sql import sql

_DIALECTOS: dict[str, dict[str, str]] = {
    "sqlite": {
        "BOOL": "INTEGER",
        "SERIAL": "INTEGER PRIMARY KEY AUTOINCREMENT",
        # El relleno mantiene la alineación de la columna; SQL lo ignora, pero
        # así el DDL generado se lee igual que cuando estaba escrito a mano.
        "FLOAT": "REAL   ",
        "NOW": "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
    },
    "pg": {
        "BOOL": "SMALLINT",
        "SERIAL": "BIGSERIAL PRIMARY KEY",
        "FLOAT": "DOUBLE PRECISION",
        "NOW": "(NOW()::TEXT)",
    },
}

# El orden importa: `messages` referencia `conversations`, `resource_source_links`
# y los borradores de importación referencian `official_sources`, y
# `workflow_run_events` referencia `workflow_runs`. Añadir una tabla es añadir
# su `.sql` y su nombre aquí, detrás de aquello a lo que apunte.
TABLAS: tuple[str, ...] = (
    "agents",
    "skills",
    "prompts",
    "tools",
    "memory_files",
    "connections",
    "accounts",
    "conversations",
    "messages",
    "knowledge_items",
    "knowledge_packs",
    "official_sources",
    "resource_source_links",
    "official_import_drafts",
    "official_import_components",
    "official_source_mappings",
    "users",
    "resource_group_shares",
    "groups",
    "group_members",
    "token_daily",
    "group_invitations",
    "subscriptions",
    "subscription_license_assignments",
    "stripe_events",
    "app_logs",
    "rate_limit_windows",
    "user_agent_preferences",
    "personal_access_tokens",
    "sessions",
    "vscode_auth_codes",
    "resource_versions",
    "agent_workflows",
    "workflow_runs",
    "workflow_run_events",
    "resource_executions",
    "llm_orchestrations",
    "llm_orchestration_bindings",
)


def _traducir(ddl: str, dialecto: str) -> str:
    try:
        sustituciones = _DIALECTOS[dialecto]
    except KeyError:
        raise ValueError(f"Dialecto de esquema desconocido: {dialecto!r}") from None
    for marcador, valor in sustituciones.items():
        ddl = ddl.replace(f"@{marcador}@", valor)
    return ddl


def tabla_ddl(tabla: str, dialecto: str) -> str:
    """DDL de una sola tabla con sus índices, ya traducido.

    `flog` crea `app_logs` por su cuenta porque su handler se construye al
    importar, antes de `init_db`; esto le da esa tabla sin tener que filtrar el
    esquema entero por substring, que es lo que hacía.
    """
    if tabla not in TABLAS:
        raise ValueError(f"Tabla desconocida en el esquema: {tabla!r}")
    return _traducir(sql(f"schema/{tabla}"), dialecto)


def schema_for(dialecto: str) -> str:
    """DDL completo para ``sqlite`` o ``pg``."""
    _traducir("", dialecto)  # valida el dialecto antes de leer 36 ficheros
    return "\n" + "".join(tabla_ddl(tabla, dialecto) for tabla in TABLAS)


SCHEMA_SQLITE = schema_for("sqlite")
SCHEMA_PG = schema_for("pg")
