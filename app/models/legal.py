"""Contrato público de aceptación de términos y privacidad."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LegalDocumentAcceptance(BaseModel):
    document_type: Literal["terms", "privacy"]
    version: str = Field(min_length=1, max_length=80)
    content_sha256: str = Field(min_length=64, max_length=64)
    document_url: str = Field(min_length=1, max_length=500)


class LegalAcceptancePayload(BaseModel):
    accepted: Literal[True]
    locale: str = Field(default="es", min_length=2, max_length=12)
    documents: list[LegalDocumentAcceptance] = Field(min_length=2, max_length=2)
