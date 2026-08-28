"""Catálogo de avisos: qué tipos hay y en qué categoría los agrupa el usuario.

Un interruptor por tipo serían doce —seis tipos por dos canales— y crecerían
con cada evento nuevo. La unidad que entiende quien configura no es
`group_role_changed`, es «lo de los grupos»: por eso los canales se apagan por
**categoría**, que es como lo resuelven GitHub o Slack.

Que el catálogo esté aquí y no repartido tiene dos consecuencias buenas:

- `tests/api/test_notification_kinds.py` comprueba que **todo tipo con
  plantilla de correo pertenece a una categoría**. Un tipo huérfano no se
  colaría en silencio ignorando las preferencias del usuario, que es
  exactamente el fallo que nadie reporta porque no se ve.
- El backend lo publica en `/api/settings` y el cliente pinta lo que reciba, en
  vez de llevar su propia copia que se desincroniza al añadir un evento.
"""

from __future__ import annotations

from typing import Dict, Tuple

# Categoría -> tipos que agrupa. El orden es el que verá el usuario.
CATEGORIAS: Dict[str, Tuple[str, ...]] = {
    "groups": (
        "group_invite",
        "group_member_added",
        "group_member_removed",
        "group_role_changed",
        "group_ownership_received",
    ),
    "billing": ("license_assigned",),
}

# Índice inverso, que es como se consulta en el camino caliente.
_DE_TIPO: Dict[str, str] = {
    kind: categoria for categoria, kinds in CATEGORIAS.items() for kind in kinds
}

# Dónde cae un tipo que aún no está clasificado. Existe para que añadir un
# evento y olvidar el catálogo no lo deje fuera de las preferencias: cae en la
# categoría general, el usuario puede apagarlo, y el test avisa del olvido.
CATEGORIA_POR_DEFECTO = "general"


def categoria_de(kind: str) -> str:
    return _DE_TIPO.get(kind, CATEGORIA_POR_DEFECTO)


def categorias_publicas() -> Tuple[str, ...]:
    """Las que el cliente debe ofrecer, en orden."""
    return (*CATEGORIAS.keys(), CATEGORIA_POR_DEFECTO)
