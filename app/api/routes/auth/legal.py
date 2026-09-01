"""Aceptación autenticada de la versión legal vigente."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.routes.auth.dependencies import require_session
from app.auth.auth import get_user_role
from app.models.legal import LegalAcceptancePayload
from app.services.legal_consent import acceptance_required, validate_acceptance
from app.storage.legal_acceptances import LegalAcceptanceStorage

router = APIRouter()
_storage = LegalAcceptanceStorage()


@router.post("/legal-acceptances")
async def accept_current_legal_documents(
    body: LegalAcceptancePayload,
    user_id: str = Depends(require_session),
) -> dict[str, bool]:
    documents = validate_acceptance(body)
    await _storage.record(user_id, documents, source="in_session")
    role = await get_user_role(user_id)
    return {
        "ok": True,
        "legal_acceptance_required": await acceptance_required(user_id, role),
    }
