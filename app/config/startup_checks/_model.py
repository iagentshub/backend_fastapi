"""Qué es un check y cómo se decide si la configuración es un error.

`strict_mode` es lo que separa «arranca degradado y dilo» de «no arranques»:
dejar una instalación que ya funcionaba sin poder arrancar es el peor fallo de
los dos, así que por defecto no aborta.
"""


from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal

# Los módulos se importan enteros, no sus símbolos: `DATA_DIR` y compañía se
# resuelven al importar, y los tests (y el propio panel de admin, que puede
# reescribir settings.json en caliente) necesitan ver el valor de ahora.
import app.config.data as _data

Severity = Literal["ok", "warning", "error"]

_STRICT_ENV = "GAIA_STRICT_CONFIG"

@dataclass(frozen=True)
class ConfigCheck:
    """Resultado de una comprobación. `variables` son nombres, nunca valores."""

    key: str
    feature: str
    severity: Severity
    detail: str
    variables: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "feature": self.feature,
            "severity": self.severity,
            "detail": self.detail,
            "variables": list(self.variables),
        }

class ConfigError(RuntimeError):
    """Configuración incoherente con `GAIA_STRICT_CONFIG` activo."""

def strict_mode() -> bool:
    """Se lee en cada llamada para que los tests puedan cambiarla."""
    return os.getenv(_STRICT_ENV, "").lower() in ("1", "true", "yes")

def _platform_settings() -> dict:
    """`settings.json` — la fuente real de billing_enabled, email_verify y registration.

    El panel de admin los escribe ahí, así que el entorno no basta para saber
    si una función está activa. Un fichero ausente o ilegible no es asunto de
    este módulo: se trata como «sin overrides» y el resto de checks siguen.
    """
    try:
        data = json.loads(_data.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
