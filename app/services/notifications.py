"""notify() — el único punto que llaman los productores de avisos.

Junta los dos canales: la fila que enciende la campana de la app y el correo
que sale a la vez. Vive en `services/` y no en `storage/` porque orquesta
almacenamiento *y* correo; que el almacenamiento importara el servicio de email
invertiría las capas y dejaría a `app.storage.notifications` sin poder probarse
sin SMTP.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Coroutine, Dict, Set

from app.models.notification_kinds import categoria_de
from app.services.email import send_notification_email
from app.services.push import send_push
from app.storage.notifications import insert_notification
from app.utils import flog

# Idioma de los correos cuando el usuario no ha elegido ninguno. Es el mismo
# valor por defecto que usan las preferencias (`settings/_shared.py:_DEFAULTS`).
_IDIOMA_POR_DEFECTO = "es"

# Referencias fuertes a los envíos en vuelo. `asyncio` solo guarda una débil
# a la tarea, así que sin esto el recolector puede llevarse un push a medio
# camino, y el fallo sería intermitente y no reproducible.
_EN_VUELO: Set[asyncio.Task] = set()


async def notify(*, user_id: str, kind: str, **data: Any) -> None:
    """Reparte el aviso por los tres canales. **No lanza nunca.**

    El orden no es casual: primero la fila, que es el canal que no se pierde
    —el usuario la verá en la campana aunque falle todo lo demás—, y después
    push y correo, que dependen de terceros.

    Ese contrato de no lanzar es lo que permite que cada productor sea una sola
    línea sin `try` alrededor: un fallo al avisar no puede tumbar la invitación
    —ni la baja, ni el traspaso— que lo provocó. El precio es que un aviso
    puede perderse en silencio, y por eso queda registrado.

    ponytail: los canales se apagan enteros, no por tipo de aviso. Quien no
    quiera correo de un cambio de rol tiene que renunciar también al de una
    invitación. Afinar por `kind` es ampliar `_preferencias` y la pantalla que
    la edita; hoy nadie lo ha pedido y son dos interruptores en vez de doce.
    """
    try:
        await insert_notification(user_id=user_id, kind=kind, data=data)
    except Exception as exc:  # noqa: BLE001
        flog.warning(f"[notify] No se pudo guardar el aviso {kind!r}: {exc}")
        return

    # Los otros dos canales van después y aparte: si fallan, la campana ya
    # tiene el aviso, que es el que nunca se pierde.
    try:
        from app.auth.user_lookup import get_user_by_id

        user = await get_user_by_id(user_id)
        if not user:
            return
        prefs = _preferencias(user.get("preferences"), kind)
    except Exception as exc:  # noqa: BLE001
        flog.warning(f"[notify] No se pudo resolver al destinatario: {exc}")
        return

    if prefs["push"]:
        _en_segundo_plano(send_push(user_id=user_id, kind=kind, data=data), kind)

    if prefs["email"] and user.get("email"):
        try:
            send_notification_email(
                email=user["email"],
                kind=kind,
                data=data,
                lang=prefs["language"],
            )
        except Exception as exc:  # noqa: BLE001
            flog.warning(f"[notify] No se pudo enviar el correo de {kind!r}: {exc}")


def _preferencias(preferences: Any, kind: str) -> Dict[str, Any]:
    """Idioma y canales efectivos **para este tipo de aviso**.

    Dos niveles, y el orden importa: el interruptor general de cada canal manda
    sobre el de la categoría. Apagar el correo entero no puede dejar pasar el
    de una categoría que quedó encendida hace meses, que es justo la sorpresa
    que hace que alguien deje de fiarse de los ajustes.

    Todo viene activado de fábrica: una cuenta recién creada tiene que
    enterarse de que la han invitado a un grupo sin haber configurado nada.

    El push, además, solo llega a donde el usuario haya dado permiso
    explícitamente en el navegador; que la preferencia esté activa no basta.
    """
    prefs = _json_dict(preferences)
    categoria = categoria_de(kind)
    por_categoria = _json_dict(prefs.get("notifications", {})).get(categoria, {})
    if not isinstance(por_categoria, dict):
        por_categoria = {}

    def activo(canal: str, maestro: str) -> bool:
        if prefs.get(maestro, True) is False:
            return False
        return por_categoria.get(canal, True) is not False

    return {
        "language": str(prefs.get("language") or _IDIOMA_POR_DEFECTO),
        "email": activo("email", "notify_email"),
        "push": activo("push", "notify_push"),
    }


def _json_dict(valor: Any) -> Dict[str, Any]:
    """`users.preferences` es un blob de texto; sus ramas ya son diccionarios."""
    if isinstance(valor, dict):
        return valor
    try:
        datos = json.loads(valor or "{}")
    except (TypeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _en_segundo_plano(tarea: Coroutine, kind: str) -> None:
    """Lanza el envío sin que la petición lo espere.

    El correo ya se comportaba así —`_send_smtp` encola en un pool de hilos y
    vuelve—, y el push no: recorría los dispositivos con `await` dentro del
    handler. Alguien con tres navegadores suscritos y un servicio push lento
    convertía «invitar a un compañero» en treinta segundos de reloj de arena
    para quien invita, por un aviso que no le afecta.

    Lo que se pierde a cambio es la entrega de los envíos que estén en vuelo si
    el proceso se apaga en ese instante. Es aceptable: la fila de la campana ya
    está guardada, que es el canal que no se pierde.
    """
    try:
        pendiente = asyncio.create_task(tarea)
    except RuntimeError:
        # Sin bucle en marcha —un script suelto, un test síncrono— no hay dónde
        # programarla. Cerrar la corrutina evita el aviso de «never awaited».
        tarea.close()
        return
    _EN_VUELO.add(pendiente)
    pendiente.add_done_callback(_EN_VUELO.discard)
    pendiente.add_done_callback(lambda t: _registrar_fallo(t, kind))


def _registrar_fallo(tarea: asyncio.Task, kind: str) -> None:
    if tarea.cancelled():
        return
    error = tarea.exception()
    if error is not None:
        flog.warning(f"[notify] No se pudo empujar {kind!r}: {error}")
