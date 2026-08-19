"""`router` compartido del catálogo social — cada submódulo le registra sus rutas.

Lleva también el limitador, que comparten estas rutas y las de `explore`: es
una sola cuota para todo lo social.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config.session import RATE_IP_FACTOR
from app.middleware.ratelimit import RateLimiter, principal_key
from app.models.resource_types import SOCIAL_RESOURCE_TYPES

router = APIRouter(tags=["social"])

# A4: tipos de recurso válidos para star/unstar y endpoints sociales
_VALID_SOCIAL_RESOURCE_TYPES = SOCIAL_RESOURCE_TYPES

# N2: rate limiting para endpoints sociales (star, follow). Todos exigen
# sesión, así que la cuota va por cuenta: quien infla contadores lo hace desde
# una cuenta, y cambiar de IP no le devuelve el cupo.
_social_limiter = RateLimiter(
    calls=30,
    window=60,
    key_func=principal_key,
    shared=True,
    name="social",
    ip_calls=30 * RATE_IP_FACTOR,
)
