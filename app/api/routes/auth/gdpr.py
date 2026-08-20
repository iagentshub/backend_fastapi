"""Endpoints RGPD del propio usuario: estado, solicitud/cancelación de borrado, export."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.routes.auth.dependencies import require_auth
from app.auth.auth import get_user_by_id
from app.auth.gdpr import cancel_user_deletion, get_owned_groups, schedule_user_deletion
from app.errors import APIError
from app.middleware.locale import get_locale
from app.models.request_bodies import TokenBody
from app.utils import flog

router = APIRouter()


@router.get("/me/deletion-status")
async def get_deletion_status(username: str = Depends(require_auth)) -> dict[str, Any]:
    user = await get_user_by_id(username)
    if not user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    return {
        "scheduled": user.get("deletion_requested_at") is not None,
        "deletion_date": user.get("deletion_requested_at"),
    }


@router.post("/me/request-deletion")
async def request_account_deletion(
    username: str = Depends(require_auth),
) -> dict[str, Any]:
    owned = await get_owned_groups(username)
    if owned:
        raise APIError(
            409,
            "owned_groups_exist",
            "Transfiere o elimina tus grupos antes de borrar la cuenta",
            extra={"groups": owned},
        )
    await schedule_user_deletion(username, lang=get_locale())
    flog.audit(
        "account.deletion.requested",
        resource_type="user",
        resource_id=username,
        summary=f"{username} programó la eliminación de su cuenta",
        username=username,
    )
    return {"ok": True, "message": "Cuenta programada para eliminación en 30 días"}


@router.post("/me/cancel-deletion")
async def cancel_account_deletion(body: TokenBody) -> dict[str, Any]:
    body = body.payload()
    token = str(body.get("token", "")).strip()
    if not token or not await cancel_user_deletion(token):
        raise APIError(400, "invalid_deletion_token", "Token inválido o expirado")
    return {"ok": True}


@router.get("/me/export")
async def export_my_data(username: str = Depends(require_auth)):
    from datetime import datetime, timezone

    from fastapi.responses import StreamingResponse

    from app.services.gdpr import export_user_data

    buf = await export_user_data(username)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    user = await get_user_by_id(username)
    safe_name = (user.get("username") if user else "account").replace(" ", "_")
    filename = f"export_{safe_name}_{date_str}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
