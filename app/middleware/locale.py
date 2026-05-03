"""Locale middleware — extracts Accept-Language and stores in context var."""
from __future__ import annotations

from contextvars import ContextVar
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

SUPPORTED_LOCALES = ("es", "en")
DEFAULT_LOCALE = "es"

current_locale: ContextVar[str] = ContextVar("locale", default=DEFAULT_LOCALE)


def _parse_lang(header: str) -> str:
    """Return best-match locale from Accept-Language header."""
    for part in header.split(","):
        lang = part.strip().split(";")[0].strip()[:2].lower()
        if lang in SUPPORTED_LOCALES:
            return lang
    return DEFAULT_LOCALE


class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        lang = _parse_lang(request.headers.get("Accept-Language", DEFAULT_LOCALE))
        token = current_locale.set(lang)
        try:
            response = await call_next(request)
        finally:
            current_locale.reset(token)
        return response


def get_locale() -> str:
    return current_locale.get()
