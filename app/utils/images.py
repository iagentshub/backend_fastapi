"""Validación mínima de imágenes mediante firmas binarias confiables."""

from __future__ import annotations


def detect_avatar_mime(data: bytes) -> str | None:
    """Devuelve el MIME de un avatar permitido o ``None`` si no es una imagen válida."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
