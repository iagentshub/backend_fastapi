"""Personal access tokens.

Credencial para clientes que no son un navegador (extensión de VS Code,
scripts, CI). Se gestionan desde el perfil, con la sesión web ya iniciada.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.routes.auth.dependencies import _login_limiter, _tokens, require_auth
from app.errors import APIError
from app.models.request_bodies import PatCreateBody
from app.storage.tokens import DEFAULT_EXPIRY_DAYS, VALID_EXPIRY_DAYS
from app.utils import flog

router = APIRouter()


@router.get("/tokens")
async def list_tokens(username: str = Depends(require_auth)) -> list[dict[str, Any]]:
    """Metadatos de los PATs del usuario. El secreto no se devuelve nunca."""
    return await _tokens.list_for_user(username)


@router.post("/tokens")
async def create_pat(
    request: Request, body: PatCreateBody, username: str = Depends(require_auth)
) -> dict[str, Any]:
    """Crea un PAT. El token en claro viaja en esta respuesta y en ninguna más."""
    from app.storage.guest import is_guest as _is_guest

    if _is_guest(username):
        raise APIError(
            403,
            "guest_cannot_create_tokens",
            "Las sesiones de invitado no pueden crear tokens.",
        )
    await _login_limiter(request)

    body = body.payload()
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 100:
        raise APIError(
            400, "token_name_required", "Nombre requerido (máximo 100 caracteres)"
        )

    # Ausente → 90 días. Presente y null → sin caducidad. Son casos distintos.
    expires = body.get("expires_in_days", DEFAULT_EXPIRY_DAYS)
    if expires is not None:
        try:
            expires = int(expires)
        except (TypeError, ValueError) as exc:
            raise APIError(
                400,
                "invalid_field",
                "expires_in_days inválido",
                extra={"field": "expires_in_days"},
            ) from exc
    if expires not in VALID_EXPIRY_DAYS:
        raise APIError(
            400,
            "invalid_field",
            "expires_in_days debe ser 30, 90, 180 o null",
            extra={"field": "expires_in_days"},
        )

    token, meta = await _tokens.create(username, name, expires)
    flog.info(
        f"PAT creado: {meta['id']} {name!r} ({meta['prefix']}…)", username=username
    )
    return {**meta, "token": token}


@router.delete("/tokens/{token_id}")
async def revoke_pat(
    token_id: str, username: str = Depends(require_auth)
) -> dict[str, Any]:
    """Revoca un PAT. Irreversible: deja de autenticar de inmediato."""
    if not await _tokens.revoke(token_id, username):
        raise APIError(
            404, "not_found", "Token no encontrado", extra={"resource": "token"}
        )
    flog.info(f"PAT revocado: {token_id}", username=username)
    return {"ok": True}
