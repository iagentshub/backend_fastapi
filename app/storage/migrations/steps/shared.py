"""Los pasos cuyo SQL es idéntico en los dos motores.

Estaban escritos dos veces, byte a byte, en `sqlite.py` y en `postgres.py`. Son
todos DDL de índices, que es donde los dos dialectos coinciden. Al vivir una
sola vez, añadir un índice ya no se puede hacer «solo en uno».
"""



from __future__ import annotations

from typing import Any

# Migración 28 (`gdpr_orphan_resources`): la lista de tablas y la de dueños que
# no son cuentas eran idénticas en los dos motores, escritas dos veces.
_TABLAS_CON_HUÉRFANOS = (
    ("prompts", "owner_id"),
    ("tools", "owner_id"),
    ("memory_files", "owner_id"),
    ("knowledge_packs", "owner_id"),
    ("resource_versions", "owner_id"),
    ("resource_source_links", "resource_owner_id"),
)

# `__public__` y `admin` no son cuentas: nunca están en `users`, así que
# "no está en users" no puede significar huérfano para ellos.
_DUEÑOS_SIN_CUENTA = ("__public__", "admin")


async def _resource_social_origin_index(conn: Any) -> None:
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsoc_link_origin ON resource_social("
        "owner,linked_to_user,linked_to_id,resource_type) "
        "WHERE linked_to_id IS NOT NULL"
    )

async def _pagination_indexes(conn: Any) -> None:
    """Índices compuestos alineados con filtros y órdenes de páginas."""
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_agents_owner_page "
        "ON agents(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_skills_owner_page "
        "ON skills(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_prompts_owner_page "
        "ON prompts(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tools_owner_page "
        "ON tools(owner_id,scope,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_owner_page "
        "ON knowledge_items(owner_id,type,created_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_connections_owner_page "
        "ON connections(owner_id,is_active,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_conv_user_agent_page "
        "ON conversations(user_id,agent_id,updated_at DESC,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_msg_conv_page "
        "ON messages(conversation_id,created_at DESC,id DESC)",
    )
    for statement in statements:
        await conn.execute(statement)

async def _resource_social_page_index(conn: Any) -> None:
    """Índice del catálogo público, la página más consultada del producto.

    `idx_rsoc_public` cubre el filtro pero no el orden, así que Explorar y el
    feed ordenaban el resultado en memoria en cada consulta.
    """
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsoc_public_page ON resource_social("
        "is_public,resource_type,updated_at DESC,stars_count DESC,resource_id)"
    )

async def _app_logs_index_diet(conn: Any) -> None:
    """De seis índices a dos en la tabla más escrita del sistema.

    El visor de logs siempre ordena por `ts DESC` con LIMIT, y filtra `ip` y
    `username` con LIKE '%x%' — el comodín inicial impide usar un B-tree, así
    que esos dos índices no se podían elegir nunca. Los de `level` y `source`
    sí se elegían, y por eso hacían daño: al entrar por ellos se pierde el
    orden de `ts` y hay que ordenar todo el resultado.

    Medido sobre 200.000 filas con ERROR al 1%: filtrar por fuente pasa de
    18,66 ms a 0,06 ms, insertar 200.000 filas de 1.685 ms a 565 ms, y la base
    ocupa un 27% menos.
    """
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_al_level_ts ON app_logs(level, ts DESC)")
    for indice in ("idx_al_date", "idx_al_level", "idx_al_username", "idx_al_ip", "idx_al_source"):
        await conn.execute(f"DROP INDEX IF EXISTS {indice}")

async def _drop_redundant_indexes(conn: Any) -> None:
    """Siete índices que repiten un UNIQUE o una PRIMARY KEY.

    Los dos motores crean su propio índice para esas restricciones, así que
    estos nunca aportaron un camino de acceso nuevo: comparados los planes de
    las 97 consultas que tocan estas seis tablas, 14 cambian de índice y
    ninguna a peor —pasan al implícito, con el mismo tipo de acceso—. En cuatro
    casos el planificador ya prefería el implícito teniendo ambos.

    Lo que sí costaban, medido: entre un 11% y un 20% del tamaño de esas tablas
    y en torno a un 25% del tiempo de inserción.
    """
    for indice in (
        "idx_users_email",
        "idx_users_username",
        "idx_pat_hash",
        "idx_group_share_resource",
        "idx_resource_source_resource",
        "idx_resource_versions_lookup",
        "idx_workflow_run_events_run",
    ):
        await conn.execute(f"DROP INDEX IF EXISTS {indice}")
