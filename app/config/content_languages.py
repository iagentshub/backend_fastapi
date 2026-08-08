"""Closed catalog of languages used by textual resources."""

from __future__ import annotations

from collections.abc import Iterable

CONTENT_LANGUAGE_CODES: tuple[str, ...] = (
    "es",
    "en",
    "fr",
    "de",
    "pt",
    "it",
    "zh",
    "ja",
    "ar",
)
CONTENT_LANGUAGE_SET = frozenset(CONTENT_LANGUAGE_CODES)
CONTENT_LANGUAGE_LABELS = frozenset(
    f"lang_{code}" for code in CONTENT_LANGUAGE_CODES
)


def language_label(code: str) -> str | None:
    """Return the canonical label for a supported ISO-like language code."""
    normalized = str(code or "").strip().lower().replace("-", "_")
    if normalized.startswith("lang_"):
        normalized = normalized[5:]
    return f"lang_{normalized}" if normalized in CONTENT_LANGUAGE_SET else None


def language_codes_from_labels(labels: Iterable[str]) -> list[str]:
    """Return supported language codes in stable catalog order."""
    selected = {str(label) for label in labels}
    return [
        code for code in CONTENT_LANGUAGE_CODES if f"lang_{code}" in selected
    ]


def normalize_language_labels(values: Iterable[str]) -> list[str]:
    """Normalize codes or language-label keys, dropping unsupported values."""
    normalized = {label for value in values if (label := language_label(value))}
    return [
        f"lang_{code}"
        for code in CONTENT_LANGUAGE_CODES
        if f"lang_{code}" in normalized
    ]
