"""Chequeo fail-safe del contrato legal que puede bloquear toda la API."""

from __future__ import annotations

from app.config.startup_checks._model import ConfigCheck


def _check_legal_contract(settings: dict) -> ConfigCheck:
    legal = settings.get("legal") if isinstance(settings.get("legal"), dict) else {}
    if not legal.get("required", False):
        return ConfigCheck(
            key="legal_contract",
            feature="Consentimiento legal versionado",
            severity="ok",
            detail="No exigido; configuración adecuada para autoalojamiento.",
            variables=(),
        )

    from app.services.legal_consent import legal_contract

    contract = legal_contract()
    errors = contract["configuration_errors"]
    if errors:
        return ConfigCheck(
            key="legal_contract",
            feature="Consentimiento legal versionado",
            severity="error",
            detail="No se puede exigir consentimiento: " + "; ".join(errors),
            variables=(),
        )
    return ConfigCheck(
        key="legal_contract",
        feature="Consentimiento legal versionado",
        severity="ok",
        detail="Versiones ES/EN y hashes legales configurados.",
        variables=(),
    )
