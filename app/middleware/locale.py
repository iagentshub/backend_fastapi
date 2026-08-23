"""Locale middleware — extracts Accept-Language and stores in context var."""
from __future__ import annotations

from contextvars import ContextVar

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

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


class LocaleMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        lang = _parse_lang(request.headers.get("Accept-Language", DEFAULT_LOCALE))
        token = current_locale.set(lang)
        try:
            await self.app(scope, receive, send)
        finally:
            current_locale.reset(token)


def get_locale() -> str:
    return current_locale.get()
