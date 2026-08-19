"""La única traducción de relaciones a `nodes`/`edges`.

`tests/api/test_grafo_en_un_sitio.py` comprueba que ningún otro fichero declara
una arista (`source_id` junto a `target_id`): repartir el ensamblado entre
cliente y servidor es exactamente lo que este servicio vino a deshacer.
"""


from __future__ import annotations

from typing import Any, Dict, List

from app.services.resource_relations._shared import node_id


def to_graph(relations: Dict[str, Any]) -> Dict[str, Any]:
    """Serializa las relaciones como el grafo que servían los `/graph`.

    Incluye los nodos de carpeta que el cliente construye a partir de `path`:
    el formato viejo los llevaba dentro y hay clientes desplegados que lo
    esperan así.
    """
    root = relations["root"]
    root_node_id = node_id(root["type"], root["id"])
    nodes: List[Dict[str, Any]] = [
        {
            "id": root_node_id,
            "label": root["label"],
            "type": root["type"],
            "description": root.get("description", ""),
        }
    ]
    edges: List[Dict[str, Any]] = []
    seen = {root_node_id}
    directories: Dict[str, str] = {}

    for relation_item in relations["items"]:
        target_id = node_id(relation_item["type"], relation_item["id"])
        via = relation_item.get("via")
        parent_id = node_id(via["type"], via["id"]) if via else root_node_id

        path = relation_item.get("path") or ""
        parts = [part for part in path.split("/") if part]
        if len(parts) > 1:
            # Las carpetas no son recursos: son nodos sintéticos derivados de
            # la ruta relativa del fichero dentro de su pack.
            accumulated: List[str] = []
            # El id conserva el formato que servía el endpoint anterior:
            # knowledge_directory:{id del pack}:{ruta}.
            owner_raw_id = via["id"] if via else root["id"]
            for directory in parts[:-1]:
                accumulated.append(directory)
                key = f"{owner_raw_id}:{'/'.join(accumulated)}"
                directory_id = directories.get(key)
                if directory_id is None:
                    directory_id = f"knowledge_directory:{key}"
                    directories[key] = directory_id
                    nodes.append(
                        {
                            "id": directory_id,
                            "label": directory,
                            "type": "knowledge_directory",
                            "description": "/".join(accumulated),
                        }
                    )
                    edges.append(
                        {
                            "source_id": parent_id,
                            "target_id": directory_id,
                            "relation": "contains",
                        }
                    )
                parent_id = directory_id

        if target_id not in seen:
            seen.add(target_id)
            nodes.append(
                {
                    "id": target_id,
                    "label": relation_item["label"],
                    "type": relation_item["type"],
                    "description": relation_item.get("description", ""),
                }
            )
        source, target = (
            (target_id, parent_id)
            if relation_item.get("inverse")
            else (parent_id, target_id)
        )
        edge: Dict[str, Any] = {
            "source_id": source,
            "target_id": target,
            "relation": relation_item["relation"],
        }
        if relation_item["relation"] in ("flow_loop", "shared", "depends"):
            edge["dashed"] = True
        edges.append(edge)

    return {"root_id": root_node_id, "nodes": nodes, "edges": edges}
