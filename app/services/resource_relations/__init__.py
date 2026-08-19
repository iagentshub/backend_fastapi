"""Relaciones de un recurso: el único sitio que las resuelve.

Antes cada pantalla que necesitaba un grafo lo montaba por su cuenta —cuatro
constructores en el cliente y cuatro en el backend, con la misma frase «un
agente usa una skill, un prompt, una tool…» escrita cuatro veces y ya
divergida—. Aquí el servidor aporta solo **hechos**: qué cuelga de qué, con
qué nombre y con qué relación. La forma (qué es raíz, qué nodos de carpeta
hacen falta, qué arista se dibuja punteada) la decide el cliente.

`to_graph` traduce esos hechos al formato `nodes`/`edges`, y es el único sitio
donde se construye una arista.

Partido en paquete porque el módulo único llegó a 1301 líneas, de las que dos
tercios eran la vista de Admin —que no filtra por visibilidad— pegada a la del
marketplace, que sí:

    _shared.py      la forma de un hecho (`item`, `payload`) y el recorrido de packs.
    graph.py        `to_graph`: la única traducción a nodos y aristas.
    public.py       relaciones de un recurso publicado.
    admin.py        entrada de la vista de Admin.
    admin_uses.py   qué usa un agente y quién lo usa.
    admin_owned.py  de quién es y qué hay en su espacio.

Sigue siendo «un solo sitio»: el sitio es ahora este paquete. Ver
`docs/adr/010-el-grafo-se-arma-en-el-cliente.md`.
"""

from __future__ import annotations

from app.services.resource_relations._shared import item, payload
from app.services.resource_relations.admin import admin_labels, admin_relations
from app.services.resource_relations.graph import to_graph
from app.services.resource_relations.public import (
    official_pack_relations,
    public_names,
    public_relations,
)

__all__ = [
    "item",
    "payload",
    "to_graph",
    "public_names",
    "public_relations",
    "official_pack_relations",
    "admin_labels",
    "admin_relations",
]
