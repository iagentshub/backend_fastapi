"""Reparto de asientos del servicio gestionado entre usuarios de la cuenta.

La ruta conserva ``/licenses`` por compatibilidad con clientes existentes. En
la interfaz y en los errores hablamos de asientos para no confundirlos con la
licencia AGPL-3.0 del software.
"""


from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends

from app.api.routes.auth import require_auth
from app.api.routes.billing._shared import (
    _billing,
    _license_error,
    router,
)
from app.auth.auth import get_user_by_id, get_user_by_username
from app.errors import APIError
from app.services.notifications import notify


@router.get("/licenses")
async def get_licenses(user: str = Depends(require_auth)) -> Dict[str, Any]:
    return await _billing.license_summary_for_owner(user)

@router.post("/licenses/{username}")
async def assign_license(
    username: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(
            404, "no_active_subscription", "No tienes una suscripción activa"
        )
    if row["tier"] != "business":
        raise APIError(
            400,
            "business_tier_required",
            "Solo el plan Business permite asignar asientos",
            extra={"action": "assign_licenses"},
        )
    target = await get_user_by_username(username)
    if not target:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    try:
        await _billing.assign_license(
            subscription_id=row["id"], target_username=target["id"], assigned_by=user
        )
    except ValueError as exc:
        raise _license_error(exc)
    actor = await get_user_by_id(user)
    await notify(
        user_id=target["id"],
        kind="license_assigned",
        actor=str((actor or {}).get("username") or ""),
    )
    return await _billing.license_summary_for_owner(user)

@router.delete("/licenses/{username}")
async def revoke_license(
    username: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    row = await _billing.get_active_by_username(user)
    if not row:
        raise APIError(
            404, "no_active_subscription", "No tienes una suscripción activa"
        )
    if row["tier"] != "business":
        raise APIError(
            400,
            "business_tier_required",
            "Solo el plan Business permite quitar asientos",
            extra={"action": "revoke_licenses"},
        )
    target = await get_user_by_username(username)
    if not target:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    target_id = target["id"]
    if target_id == user:
        raise APIError(
            400, "cannot_revoke_own_license", "No puedes quitar tu propio asiento"
        )
    if not await _billing.revoke_license(
        subscription_id=row["id"], target_username=target_id
    ):
        raise APIError(
            404, "not_found", "Asiento no encontrado", extra={"resource": "license"}
        )
    return await _billing.license_summary_for_owner(user)
