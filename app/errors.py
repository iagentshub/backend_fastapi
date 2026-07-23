"""Errores de API con código estable para i18n en el cliente.

`APIError` es una `HTTPException` cuyo `detail` es un dict `{code, message, ...extra}`
en vez de un string plano. El `code` es un identificador estable que el frontend
traduce con su propio sistema de i18n según el idioma del usuario; `message` es el
texto en español, usado como fallback si el cliente no reconoce el código.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException


class APIError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        detail: Dict[str, Any] = {"code": code, "message": message}
        if extra:
            detail.update(extra)
        super().__init__(status_code=status_code, detail=detail, headers=headers)
