"""Rutas que responden sin credencial de ningún tipo.

Todo lo demás en `app/api/routes/` empieza por una dependency de autorización;
esto no, y por eso vive en su propio fichero: el prefijo `/api/public/` avisa
de lo que hay al otro lado y hace que la lista de endpoints abiertos se pueda
leer de un vistazo en vez de deducirla buscando handlers sin `Depends`.

Lo único que hay aquí es el formulario de contacto de la web pública, que es
el camino de conversión de los planes sin checkout directo (Tropa, Legión y
las peticiones de formación). Antes apuntaba a `/api/admin/contact-requests`,
que no existía y que —de haber existido— habría respondido 401 a un visitante
anónimo, con lo que el cliente lo mandaba a la pantalla de login y perdía por
el camino lo que acababa de escribir.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.config.session import RATE_CONTACT_CALLS, RATE_CONTACT_WINDOW
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.services.email import send_contact_notification
from app.storage.contact import save_contact_request
from app.utils.net import client_ip
from app.utils.validation import is_valid_email

router = APIRouter(prefix="/api/public", tags=["public"])

# Los `ctaType` de los planes de la página de precios, más el de formación, que
# tiene título propio en el locale. Se valida contra la lista porque el valor
# acaba en el asunto del correo del operador: sin ella, quien envíe el
# formulario elige qué texto le llega.
TIPOS = frozenset({"free", "plan_dev", "plan_biz", "plan_ent", "training"})

_contact_limiter = RateLimiter(
    calls=RATE_CONTACT_CALLS,
    window=RATE_CONTACT_WINDOW,
    key_func=client_ip,
    shared=True,
    name="public-contact",
)


class ContactBody(BaseModel):
    type: str = Field(max_length=32)
    name: str = Field(max_length=120)
    email: str = Field(max_length=254)
    message: str = Field(default="", max_length=4000)
    # Campo trampa: se pinta oculto en el formulario, así que una persona nunca
    # lo rellena y casi todos los bots sí. Se acepta la petición como si nada
    # —decir «te he pillado» es enseñarle al bot cómo evitarlo la próxima— y no
    # se guarda ni se envía nada.
    website: str = Field(default="", max_length=200)


@router.post("/contact")
async def create_contact_request(
    body: ContactBody, request: Request, _: None = Depends(_contact_limiter)
) -> dict:
    """Recoge una petición de contacto de la web pública. Sin sesión."""
    if body.website.strip():
        return {"ok": True}

    tipo = body.type.strip().lower()
    if tipo not in TIPOS:
        raise APIError(
            400, "invalid_field", "Tipo de petición desconocido.", extra={"field": "type"}
        )

    nombre = body.name.strip()
    email = body.email.strip().lower()
    mensaje = body.message.strip()
    if not nombre:
        raise APIError(
            400, "invalid_field", "El nombre es obligatorio.", extra={"field": "name"}
        )
    if not is_valid_email(email):
        raise APIError(
            400, "invalid_field", "El email no es válido.", extra={"field": "email"}
        )

    # Guardar primero: el correo se encola en un hilo y puede no salir nunca
    # —SMTP sin configurar es el caso por defecto—, y un lead perdido no se
    # recupera. La fila es lo que garantiza que la petición existe.
    await save_contact_request(
        kind=tipo,
        name=nombre,
        email=email,
        message=mensaje,
        ip=_ip_o_none(request),
    )
    enviado = send_contact_notification(
        kind=tipo, name=nombre, email=email, message=mensaje
    )
    return {"ok": True, "notified": enviado}


def _ip_o_none(request: Request) -> Optional[str]:
    return client_ip(request) or None
