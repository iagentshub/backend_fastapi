"""El backend entrega relaciones; el grafo lo arma el cliente.

Armar un grafo —recorrer un recurso y convertirlo en nodos y aristas— llegó a
estar escrito ocho veces: cuatro en el cliente Flutter y cuatro aquí. No eran
ocho grafos distintos: cuatro repetían «un agente usa una skill, un prompt, una
tool…» y tres el recorrido de carpetas de un pack, ya divergidos entre sí.

Ahora el backend aporta solo hechos (`app/services/resource_relations.py`) y el
ensamblado vive en `app_flutter/lib/shared/graph/resource_graph_builder.dart`.
Estas guardas existen para que no vuelva a repartirse.
"""

from __future__ import annotations

import ast
from pathlib import Path

RUTAS = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"
SERVICIOS = Path(__file__).resolve().parents[2] / "app" / "services"
CONSTRUCTOR = "resource_relations.py"


def _ficheros_de_ruta() -> list[Path]:
    return sorted(RUTAS.rglob("*.py"))


def _diccionarios_con(fichero: Path, claves_buscadas: set[str]) -> list[int]:
    """Líneas donde un diccionario literal declara todas esas claves.

    Se mira el diccionario entero y no cada línea suelta porque `source_id` es
    también el id de una fuente oficial y `nodes`/`edges` son la definición de
    una orquestación: por separado no significan nada, juntos son un grafo.
    """
    arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    encontrados: list[int] = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Dict):
            continue
        claves = {
            clave.value
            for clave in nodo.keys
            if isinstance(clave, ast.Constant) and isinstance(clave.value, str)
        }
        if claves_buscadas <= claves:
            encontrados.append(nodo.lineno)
    return encontrados


def test_ninguna_ruta_devuelve_un_grafo_montado():
    """Un `root_id` junto a sus `nodes` es un grafo ya armado."""
    infractores = [
        f"{fichero.name}:{linea}"
        for fichero in _ficheros_de_ruta()
        for linea in _diccionarios_con(fichero, {"root_id", "nodes"})
    ]

    assert infractores == [], (
        "Devuelve relaciones (app/services/resource_relations.py) en vez de un "
        f"grafo ya montado: {infractores}"
    )


def test_solo_el_servicio_de_relaciones_construye_aristas():
    """Una arista —`source_id` con `target_id`— se declara en un solo sitio."""
    infractores = [
        f"{fichero.relative_to(SERVICIOS.parent)}:{linea}"
        for fichero in [*SERVICIOS.rglob("*.py"), *RUTAS.rglob("*.py")]
        if fichero.name != CONSTRUCTOR
        for linea in _diccionarios_con(fichero, {"source_id", "target_id"})
    ]

    assert infractores == [], (
        "Las aristas solo se construyen en resource_relations.to_graph, y su "
        f"único consumidor es el cliente: {infractores}"
    )


def test_las_rutas_de_grafo_ya_no_existen():
    """Los cuatro `/graph` se retiraron a favor de `/relations`."""
    contrato = (Path(__file__).parent / "contrato_rutas.txt").read_text(
        encoding="utf-8"
    )
    rutas_de_grafo = [
        linea for linea in contrato.splitlines() if linea.endswith("/graph")
    ]
    assert rutas_de_grafo == [], (
        "Un endpoint que devuelve un grafo montado vuelve a repartir el "
        f"ensamblado entre cliente y servidor: {rutas_de_grafo}"
    )
