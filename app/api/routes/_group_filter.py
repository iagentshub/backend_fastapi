"""Helpers de filtrado por grupo de workspace."""
from __future__ import annotations

from typing import List, Optional


def get_group_shared_ids(username: str, resource_type: str, workspace_id: str) -> Optional[List[str]]:
    """IDs de recursos compartidos con grupos del usuario en el workspace actual.

    Devuelve None si el usuario no pertenece a ningún grupo (sin restriccion adicional).
    Devuelve lista vacía si pertenece a grupos pero ningún recurso está compartido con ellos.
    """
    from app.config.data import DB_FILE
    from app.storage.groups import GroupStorage

    gs = GroupStorage(DB_FILE)
    shared = gs.get_user_shared_resource_ids(username, resource_type, workspace_id)

    # Si la lista está vacía pero el usuario podría ser miembro de grupos sin recursos,
    # devolvemos None para no restringir (el workspace ya filtra por owner_id).
    # Los grupos son un mecanismo de sharing adicional, no de restriccion.
    return shared if shared else None
