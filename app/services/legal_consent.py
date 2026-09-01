"""Política única para publicar, validar y exigir consentimiento legal."""

from __future__ import annotations

import json
import re
from typing import Any

from app.errors import APIError
from app.models.legal import LegalAcceptancePayload
from app.services.platform_settings import _read_platform_cfg
from app.storage.legal_acceptances import LegalAcceptanceStorage

LEGAL_DOCUMENT_TYPES = ("terms", "privacy")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDERS = (
    "[razón social]",
    "[fecha de publicación]",
    "[nif]",
    "todo:",
)
_EXEMPT_PATHS = frozenset(
    {
        "/api/auth/me/cancel-deletion",
        "/api/auth/me/deletion-status",
        "/api/auth/me/export",
        "/api/auth/me/request-deletion",
        "/api/auth/me",
        "/api/auth/legal-acceptances",
        "/api/auth/logout",
        "/api/auth/logout-all",
        "/api/auth/refresh",
    }
)

_storage = LegalAcceptanceStorage()


def legal_contract() -> dict[str, Any]:
    raw = _read_platform_cfg().get("legal")
    legal = raw if isinstance(raw, dict) else {}
    documents_raw = legal.get("documents")
    documents_source = documents_raw if isinstance(documents_raw, dict) else {}
    documents: dict[str, Any] = {}
    for document_type in LEGAL_DOCUMENT_TYPES:
        raw_document = documents_source.get(document_type)
        document = raw_document if isinstance(raw_document, dict) else {}
        locales_raw = document.get("locales")
        locales_source = locales_raw if isinstance(locales_raw, dict) else {}
        locales: dict[str, dict[str, str]] = {}
        for locale, raw_locale in locales_source.items():
            if not isinstance(raw_locale, dict):
                continue
            locales[str(locale).lower()] = {
                "url": str(raw_locale.get("url") or "").strip(),
                "sha256": str(raw_locale.get("sha256") or "").strip().lower(),
            }
        documents[document_type] = {
            "version": str(document.get("version") or "").strip(),
            "locales": locales,
        }
    contract = {
        "required": bool(legal.get("required", False)),
        "ready": bool(legal.get("ready", False)),
        "accept_url": str(legal.get("accept_url") or "/app/legal-acceptance"),
        "documents": documents,
    }
    contract["configuration_errors"] = legal_configuration_errors(contract)
    return contract


def legal_configuration_errors(contract: dict[str, Any] | None = None) -> list[str]:
    current = contract or legal_contract()
    errors: list[str] = []
    if not current.get("ready"):
        errors.append("legal.ready no está activado")
    for document_type in LEGAL_DOCUMENT_TYPES:
        document = current.get("documents", {}).get(document_type, {})
        if not document.get("version"):
            errors.append(f"{document_type}.version no está configurado")
        locales = document.get("locales", {})
        for locale in ("es", "en"):
            localized = locales.get(locale, {})
            url = str(localized.get("url") or "")
            digest = str(localized.get("sha256") or "")
            if not url.startswith(("/", "https://")):
                errors.append(f"{document_type}.{locale}.url no es válida")
            if not _SHA256.fullmatch(digest):
                errors.append(f"{document_type}.{locale}.sha256 no es válido")
    serialized = json.dumps(current, ensure_ascii=False).lower()
    accept_url = str(current.get("accept_url") or "")
    if not accept_url.startswith(("/", "https://")):
        errors.append("legal.accept_url no es válida")
    if any(marker in serialized for marker in _PLACEHOLDERS):
        errors.append("la configuración legal contiene placeholders")
    return errors


def public_legal_contract() -> dict[str, Any]:
    contract = legal_contract()
    return {
        "required": contract["required"],
        "ready": contract["ready"] and not contract["configuration_errors"],
        "accept_url": contract["accept_url"],
        "documents": contract["documents"],
    }


def current_documents(locale: str) -> list[dict[str, str]]:
    contract = legal_contract()
    if contract["configuration_errors"]:
        raise APIError(
            503,
            "legal_contract_unavailable",
            "El contrato legal vigente no está configurado correctamente.",
        )
    selected_locale = locale.lower().split("-", 1)[0]
    result: list[dict[str, str]] = []
    for document_type in LEGAL_DOCUMENT_TYPES:
        document = contract["documents"][document_type]
        localized = (
            document["locales"].get(selected_locale) or document["locales"]["es"]
        )
        result.append(
            {
                "document_type": document_type,
                "version": document["version"],
                "locale": selected_locale
                if selected_locale in document["locales"]
                else "es",
                "content_sha256": localized["sha256"],
                "document_url": localized["url"],
            }
        )
    return result


def validate_acceptance(payload: LegalAcceptancePayload) -> list[dict[str, str]]:
    expected = current_documents(payload.locale)
    received = {
        document.document_type: document.model_dump() for document in payload.documents
    }
    if set(received) != set(LEGAL_DOCUMENT_TYPES):
        raise APIError(
            422,
            "legal_contract_mismatch",
            "Deben aceptarse términos y privacidad en su versión vigente.",
        )
    for document in expected:
        candidate = received[document["document_type"]]
        if any(
            candidate[field] != document[field]
            for field in ("version", "content_sha256", "document_url")
        ):
            raise APIError(
                409,
                "legal_contract_mismatch",
                "La versión legal cambió; revisa y acepta los documentos vigentes.",
                extra={"legal": public_legal_contract()},
            )
    return expected


async def acceptance_required(user_id: str, role: str) -> bool:
    if role in {"admin", "guest"}:
        return False
    contract = legal_contract()
    if not contract["required"]:
        return False
    documents = current_documents("es")
    return not await _storage.has_current(user_id, documents)


async def assert_legal_access(user_id: str, role: str, path: str) -> None:
    if path in _EXEMPT_PATHS or path.startswith("/api/auth/vscode/"):
        return
    if not await acceptance_required(user_id, role):
        return
    contract = public_legal_contract()
    raise APIError(
        428,
        "legal_acceptance_required",
        "Debes aceptar la versión vigente de los términos y la privacidad.",
        extra={
            "accept_url": contract["accept_url"],
            "documents": contract["documents"],
        },
    )
