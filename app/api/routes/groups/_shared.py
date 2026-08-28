"""`router`, almacén y guardas comunes a los tres submódulos.

`_assert_not_personal_group` está aquí porque la comparten casi todas las
rutas: el grupo personal no es un grupo de verdad y no admite miembros,
invitaciones ni traspaso.
"""


from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.auth.user_lookup import get_user_by_id
from app.errors import APIError
from app.storage.groups import GroupStorage
from app.storage.guest import is_guest

router = APIRouter(prefix="/api/groups", tags=["groups"])

_groups = GroupStorage()

_PERMISSION_ACTIONS = {
    "agents": {"use"},
    "connections": {"direct", "via_agent"},
    "knowledge": {"view"},
}

async def _nombre_visible(user_id: str) -> str:
    """El username público de quien actúa.

    `ctx.user` es el id interno, y es lo que llega a los avisos; enseñarlo tal
    cual en una notificación o en un correo sería enseñar un identificador.
    """
    user = await get_user_by_id(user_id)
    return str((user or {}).get("username") or "")

def _assert_not_guest(user: str) -> None:
    if is_guest(user):
        raise APIError(403, "forbidden", "Los invitados no pueden gestionar grupos")

def _assert_not_personal_group(group_id: str, username: str) -> None:
    if group_id == username:
        raise APIError(
            400,
            "personal_group_single_user",
            "El grupo Personal solo puede contener a su propietario",
            extra={"resource": "group"},
        )

def _validate_permissions(permissions: Dict[str, Any]) -> None:
    for section, config in permissions.items():
        allowed_actions = _PERMISSION_ACTIONS.get(section)
        if allowed_actions is None or not isinstance(config, dict):
            raise APIError(
                422,
                "invalid_field",
                "Permisos inválidos",
                extra={"field": "permissions"},
            )
        if "default" in config and not isinstance(config["default"], bool):
            raise APIError(
                422,
                "invalid_field",
                "Permisos inválidos",
                extra={"field": "permissions"},
            )
        items = config.get("items", {})
        if not isinstance(items, dict):
            raise APIError(
                422,
                "invalid_field",
                "Permisos inválidos",
                extra={"field": "permissions"},
            )
        for resource_id, actions in items.items():
            if not resource_id or not isinstance(actions, dict):
                raise APIError(
                    422,
                    "invalid_field",
                    "Permisos inválidos",
                    extra={"field": "permissions"},
                )
            if any(
                action not in allowed_actions or not isinstance(value, bool)
                for action, value in actions.items()
            ):
                raise APIError(
                    422,
                    "invalid_field",
                    "Permisos inválidos",
                    extra={"field": "permissions"},
                )
