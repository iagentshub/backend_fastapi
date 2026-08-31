"""Ningún listado nuevo nace sin cota.

Cuando se paginaron los listados del producto, el panel de administración se
quedó entero fuera: once `GET` devolvían la tabla completa, y el único sitio
donde el resultado no lo acota lo que tiene un usuario sino lo que tiene la
instalación era justo ese. Nada avisó, porque no había nada que avisara.

Esta guarda es lo que faltaba. Un endpoint `GET` que devuelve `list[...]` sin
`page`, `limit` ni `cursor` entre sus parámetros se trae la tabla entera, y a
partir de ahora tiene que declararse aquí para pasar.
"""

from __future__ import annotations

import ast
from pathlib import Path

RUTAS = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"

# Listados sin cota que ya existían. **La lista solo puede encoger.**
#
# Los once del panel salieron de aquí al retirarse sus `GET` v1: no los pedía
# ningún cliente —ni Flutter, ni la extensión—, así que no había bundle
# cacheado al que esperar, y dejarlos publicados habría sido conservar
# exactamente el camino que este trabajo vino a cerrar.
DEUDA = {
    # Acotados por el usuario o el grupo que pregunta: lo que devuelven no
    # crece con el número de clientes de la instalación, que es lo que hacía
    # del panel un caso distinto.
    "accounts.py:list_accounts",
    "auth/pat_tokens.py:list_tokens",
    "connections.py:list_connections_raw",
    "connections.py:get_tokens_daily",
    "groups/crud.py:list_groups",
    "groups/invitations.py:my_invitations",
    "groups/invitations.py:list_group_invitations",
    "groups/members.py:list_members",
    "labels.py:list_resources_with_label",
    "llm_orchestrations.py:list_llm_orchestrations",
    "memory.py:list_memory",
    "resource_executions.py:list_resource_executions",
    "resource_management.py:versions",
    "resource_management.py:list_workflows",
    # Catálogos y agregados de tamaño fijo, no tablas que crezcan.
    "admin/official_sources.py:admin_list_official_sources",
    "connection_catalog.py:list_providers",
    "explore/official_packs.py:explore_official_packs",
    "logs.py:logs_summary",
    "settings/banners.py:list_notification_banners",
    "settings/banners.py:get_active_notification_banners",
    # Este sí crece con lo que publique una persona, y no está acotado. Es el
    # candidato claro a la próxima tanda; se anota para que no se pierda.
    "explore/profile.py:user_resources",
}

PAGINACION = {"page", "limit", "cursor"}


def _listados_sin_cota() -> set[str]:
    encontrados: set[str] = set()
    for fichero in sorted(RUTAS.rglob("*.py")):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            es_get = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "get"
                for d in nodo.decorator_list
            )
            if not es_get or nodo.returns is None:
                continue
            devuelve = ast.unparse(nodo.returns)
            # `list[...]` y `List[...]` de typing son lo mismo. Mirar solo la
            # minúscula dejaba trece listados fuera del barrido, que es la
            # forma de fallar de una guarda: pasa, y no está mirando.
            if not devuelve.startswith(("list[", "List[")):
                continue
            parametros = {a.arg for a in nodo.args.args + nodo.args.kwonlyargs}
            if PAGINACION & parametros:
                continue
            encontrados.add(f"{fichero.relative_to(RUTAS)}:{nodo.name}")
    return encontrados


def test_ningun_listado_nuevo_sin_cota():
    nuevos = sorted(_listados_sin_cota() - DEUDA)
    assert nuevos == [], (
        "Estos GET devuelven una lista sin limit/cursor, así que traen la "
        "tabla entera:\n  " + "\n  ".join(nuevos) + "\n\nPagínalos con el "
        "contrato v2 (app/pagination) o, si lo que devuelven ya está acotado "
        "por el usuario, anótalos en DEUDA con el motivo."
    )


def test_la_deuda_no_se_queda_con_entradas_muertas():
    """Al retirar un listado, su línea sale de la lista: si no, la lista deja
    de decir la verdad y la próxima migración no sabe qué queda."""
    fantasmas = sorted(DEUDA - _listados_sin_cota())
    assert fantasmas == [], (
        "Estas entradas de DEUDA ya no corresponden a ningún listado sin "
        "cota; bórralas:\n  " + "\n  ".join(fantasmas)
    )
