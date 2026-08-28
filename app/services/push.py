"""Entrega de avisos por Web Push (RFC 8030 / 8291 / 8292).

Un push no viaja como un JSON cualquiera: el payload va cifrado **para cada
navegador** con la clave que ese navegador entregó al suscribirse, y la
petición va firmada con el par VAPID de la instalación. Aquí se juntan las tres
piezas —`http_ece` cifra, `py_vapid` firma, `httpx` manda— y se decide qué
hacer con lo que responde el servicio push.

Lo que NO hace y es deliberado: no reintenta. Un aviso es útil ahora o no lo
es, y una cola de reintentos es infraestructura que este producto todavía no
necesita. Lo que sí hace es limpiar: un 404 o un 410 significa que el navegador
tiró la suscripción, y esa fila se borra en el acto.

`kind` prepara el terreno para FCM y APNs cuando se publiquen las apps
nativas: serían otra rama de `_enviar_uno`, no otra tabla ni otro productor.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Dict

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid01

from app.storage import push_subscriptions as _subs
from app.utils import flog

# Cuánto vive la firma VAPID. Doce horas es el máximo que aceptan los servicios
# push; más corto solo obliga a refirmar sin ganar nada.
_VIGENCIA_FIRMA = 12 * 3600

# El servicio push guarda el aviso si el navegador está apagado. Un día es de
# sobra: pasado ese plazo la invitación se ve igual al abrir la aplicación.
_TTL = 24 * 3600

# Cuánto se espera a un servicio push. Diez segundos es de sobra para un POST
# de doscientos bytes; lo que protege es del servicio que acepta la conexión
# y no responde.
_ESPERA = 10.0

# Cuántas veces se intenta un mismo destino. Tres es el equilibrio conocido:
# cubre el corte de red y el pico de 5xx del servicio push sin convertir una
# caída larga en una cola de tareas colgadas durante minutos.
_INTENTOS = 3

# Retroceso exponencial entre intentos: 1 s y 2 s.
_ESPERA_BASE = 1.0

# Techo para el `Retry-After` que mande el servicio. Un aviso no vale tener una
# tarea dormida media hora; si pide más, se abandona y queda en la campana.
_ESPERA_MAXIMA = 60.0

# Lo que merece otro intento: el servicio está caído, saturado o limitando. El
# resto de 4xx son culpa del mensaje y repetirlos da el mismo error.
_REINTENTABLES = frozenset({408, 429, 500, 502, 503, 504})

# Tamaño máximo que garantiza el estándar. Los textos de estos avisos son de
# una línea, así que solo protege de un `data` inesperadamente grande.
_MAX_PAYLOAD = 3800


def push_disponible() -> bool:
    """Las tres variables, no dos.

    `VAPID_SUBJECT` parece opcional y no lo es: `py_vapid.sign()` lanza
    `VapidException` si a los claims les falta `sub`, así que sin contacto no
    se firma ni un envío. Comprobarlo aquí es lo que hace que la ausencia se
    traduzca en «push desactivado» —igual que sin SMTP no hay correo— en vez de
    en un interruptor que el usuario activa y que no entrega nada.
    """
    from app.config.session import (
        VAPID_PRIVATE_KEY,
        VAPID_PUBLIC_KEY,
        VAPID_SUBJECT,
    )

    return bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY and VAPID_SUBJECT)


def clave_publica() -> str:
    """La que el navegador necesita para suscribirse. Vacía si no hay push."""
    from app.config.session import VAPID_PUBLIC_KEY

    return VAPID_PUBLIC_KEY


async def send_push(*, user_id: str, kind: str, data: Dict[str, Any]) -> int:
    """Empuja el aviso a todos los destinos del usuario. Devuelve cuántos.

    No lanza: la llama `notify`, que tampoco puede lanzar. Un servicio push
    caído no puede impedir que se cree una invitación.
    """
    if not push_disponible():
        return 0

    try:
        destinos = await _subs.list_for_user(user_id)
    except Exception as exc:  # noqa: BLE001
        flog.warning(f"[push] No se pudieron leer las suscripciones: {exc}")
        return 0
    if not destinos:
        return 0

    cuerpo = _payload(kind, data)
    # En paralelo, no en fila: los destinos de una persona son su portátil,
    # su móvil y el del trabajo, y son servicios push distintos. En serie, el
    # que esté lento marca el tiempo de todos.
    async with httpx.AsyncClient(timeout=_ESPERA) as cliente:
        resultados = await asyncio.gather(
            *(_enviar_uno(cliente, destino, cuerpo) for destino in destinos),
            return_exceptions=True,
        )
    return sum(1 for r in resultados if r is True)


def _payload(kind: str, data: Dict[str, Any]) -> bytes:
    """Lo que recibe el service worker.

    Viaja el `kind` y el `data` en crudo, no la frase montada: el texto lo
    compone el cliente con sus traducciones, igual que hace la campana. Así un
    usuario en inglés no recibe el push en español porque el servidor lo
    escribió antes.
    """
    cuerpo = json.dumps({"kind": kind, "data": data}, ensure_ascii=False).encode()
    if len(cuerpo) > _MAX_PAYLOAD:
        # Antes que fallar el envío entero, va el tipo sin los huecos: el
        # cliente enseñará el texto genérico de ese `kind`.
        cuerpo = json.dumps({"kind": kind, "data": {}}).encode()
    return cuerpo


async def _enviar_uno(
    cliente: httpx.AsyncClient, destino: Dict[str, Any], cuerpo: bytes
) -> bool:
    endpoint = destino["endpoint"]
    try:
        cifrado = _cifrar(cuerpo, destino["p256dh"], destino["auth"])
        cabeceras = _cabeceras(endpoint)
    except Exception as exc:  # noqa: BLE001
        # Claves corruptas o ilegibles: la suscripción ya no sirve para nada.
        flog.warning(f"[push] Suscripción ilegible, se descarta: {type(exc).__name__}")
        await _subs.unsubscribe(endpoint)
        return False

    for intento in range(_INTENTOS):
        try:
            respuesta = await cliente.post(
                endpoint, content=cifrado, headers=cabeceras
            )
        except Exception as exc:  # noqa: BLE001
            # Corte de red, DNS, TLS: transitorio por definición, se reintenta.
            if await _esperar_antes_de_reintentar(intento):
                continue
            flog.warning(
                f"[push] Error de red hacia el servicio push: {type(exc).__name__}"
            )
            return False

        codigo = respuesta.status_code
        if codigo in (404, 410):
            # El navegador desinstaló la app, limpió sus datos o revocó el
            # permiso. Es el mecanismo estándar de limpieza, no un error, y
            # reintentarlo solo repetiría el mismo 410.
            await _subs.unsubscribe(endpoint)
            return False
        if codigo < 400:
            await _subs.touch(destino["id"])
            return True
        if codigo not in _REINTENTABLES:
            # 400, 401, 403: el mensaje o la firma están mal. Repetirlos da
            # exactamente el mismo error y gasta cuota.
            flog.warning(f"[push] El servicio push respondió {codigo}")
            return False
        if not await _esperar_antes_de_reintentar(intento, respuesta):
            flog.warning(
                f"[push] El servicio push respondió {codigo} tras {_INTENTOS} intentos"
            )
            return False

    return False


async def _esperar_antes_de_reintentar(
    intento: int, respuesta: "httpx.Response | None" = None
) -> bool:
    """Espera lo que toque y dice si queda otro intento.

    Respeta `Retry-After` cuando el servicio lo manda —es él quien sabe cuándo
    volver, y adelantarse a su plazo es lo que convierte un 429 en un bloqueo—.
    Sin cabecera, retroceso exponencial.
    """
    if intento >= _INTENTOS - 1:
        return False

    espera = _ESPERA_BASE * (2**intento)
    if respuesta is not None:
        cabecera = respuesta.headers.get("retry-after", "")
        try:
            # Solo la forma en segundos: la variante con fecha HTTP la mandan
            # muy pocos servicios y parsearla no compensa aquí.
            espera = max(espera, min(float(cabecera), _ESPERA_MAXIMA))
        except (TypeError, ValueError):
            pass

    await asyncio.sleep(espera)
    return True


def _cifrar(cuerpo: bytes, p256dh: str, auth: str) -> bytes:
    """Cifra el payload para un navegador concreto (aes128gcm, RFC 8291)."""
    import http_ece

    return http_ece.encrypt(
        cuerpo,
        private_key=ec.generate_private_key(ec.SECP256R1()),
        dh=_unb64u(p256dh),
        auth_secret=_unb64u(auth),
        version="aes128gcm",
    )


def _cabeceras(endpoint: str) -> Dict[str, str]:
    """Firma VAPID para el origen de este endpoint.

    El `aud` es el origen del servicio push, no la URL completa: firmar la ruta
    entera hace que el servicio rechace el envío.
    """
    from urllib.parse import urlsplit

    from app.config.session import VAPID_PRIVATE_KEY, VAPID_SUBJECT

    partes = urlsplit(endpoint)
    origen = f"{partes.scheme}://{partes.netloc}"

    vapid = _vapid(VAPID_PRIVATE_KEY)
    # `sub` es obligatorio para py_vapid; `push_disponible()` garantiza que
    # existe antes de llegar aquí.
    reclamos: Dict[str, Any] = {
        "aud": origen,
        "exp": _expira(),
        "sub": VAPID_SUBJECT,
    }

    cabeceras = vapid.sign(reclamos)
    cabeceras["Content-Encoding"] = "aes128gcm"
    cabeceras["TTL"] = str(_TTL)
    # Sin urgencia declarada algunos servicios agrupan y retrasan el aviso.
    cabeceras["Urgency"] = "normal"
    return cabeceras


def _vapid(privada: str) -> Vapid01:
    """Carga la clave privada en cualquiera de las formas que genera py_vapid.

    `Vapid01.from_string` solo entiende la clave cruda o el DER en base64, pero
    el comando que la documentación manda usar —`python -m py_vapid --gen`—
    escribe un **PEM con cabeceras**. Quien copie ese fichero tal cual en la
    variable de entorno se encuentra con «Could not deserialize key data» y
    ninguna pista de qué formato se esperaba.

    Aceptar las dos formas cuesta seis líneas y evita ese callejón.
    """
    if "BEGIN" in privada:
        from cryptography.hazmat.primitives import serialization

        # Un `.env` o un compose guardan el PEM con los saltos escapados; sin
        # deshacerlos, `load_pem_private_key` no reconoce las cabeceras.
        pem = privada.replace("\\n", "\n").encode()
        clave = serialization.load_pem_private_key(pem, password=None)
        cruda = clave.private_numbers().private_value.to_bytes(32, "big")
        return Vapid01.from_string(
            private_key=base64.urlsafe_b64encode(cruda).rstrip(b"=").decode()
        )
    return Vapid01.from_string(private_key=privada.strip())


def _expira() -> int:
    import time

    return int(time.time()) + _VIGENCIA_FIRMA


def _unb64u(valor: str) -> bytes:
    return base64.urlsafe_b64decode(valor + "=" * (-len(valor) % 4))
