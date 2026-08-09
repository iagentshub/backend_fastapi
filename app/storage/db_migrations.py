"""Fachada compatible para imports anteriores de las migraciones legacy.

El código nuevo debe registrar pasos en :mod:`app.storage.migrations`.
"""

from __future__ import annotations

import sys

from app.storage.migrations import legacy as _legacy

# Mantener identidad de módulo es importante: algunos tests y extensiones
# parchean constantes privadas antes de invocar los helpers históricos.
sys.modules[__name__] = _legacy
