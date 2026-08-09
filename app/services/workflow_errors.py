"""Errores públicos y serialización segura para ejecuciones workflow por SSE."""

from __future__ import annotations

from typing import Any

from app.errors import APIError
from app.utils import flog


class WorkflowPublicError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


def workflow_error_event(exc: Exception, *, context: str) -> dict[str, Any]:
    if isinstance(exc, WorkflowPublicError):
        return {"type": "error", "code": exc.code, "message": exc.public_message}
    if isinstance(exc, APIError) and isinstance(exc.detail, dict):
        return {
            "type": "error",
            "code": str(exc.detail.get("code") or "internal_error"),
            "message": str(exc.detail.get("message") or "Error en la orquestación."),
        }
    flog.error(
        f"[{context}] fallo no controlado: {type(exc).__name__}: {exc}",
        exc_info=True,
    )
    return {
        "type": "error",
        "code": "internal_error",
        "message": "Error interno de la orquestación.",
    }
