"""El invitado es un usuario efímero en la base de datos.

Esto era un `dict` del proceso con la sesión de demo dentro. Con
`GAIA_WORKERS>1` —el default en producción— y sin afinidad de sesión en el
proxy, dos peticiones del mismo invitado caían en workers distintos y la
segunda se encontraba una sesión vacía: el agente recién creado desaparecía sin
error. El tope tampoco era el declarado, sino `workers × MAX_SESSIONS`.

Ahora el invitado tiene fila en `users` con `role='guest'` y usa exactamente el
mismo almacenamiento que cualquier otro usuario. Lo que lo distingue no es
dónde guarda, sino **cuánto dura**: se borra entero —con `purge_user_data`, la
misma rutina del RGPD— al cerrar sesión, y por expiración cuando se queda sin
sesiones vivas. Ver docs/adr/012-el-invitado-es-un-usuario-efimero.md.

La identidad no cambió al hacer el movimiento: sigue siendo `guest:<id>`, y ese
mismo string es a la vez `id` y `username` de la fila. Eso mantiene válido
`is_guest()` —una comprobación de prefijo, sin consultar— en el camino caliente
de las dependencias de autorización.
"""

from __future__ import annotations

import os

from app.sql import sql
from app.storage.db import open_db
from app.utils.generators import generate_date, generate_id

# Tope de invitados simultáneos en el clúster, no en el proceso: el contador es
# una consulta a `users`. A 0 el modo invitado queda desactivado — el alta
# responde 503 siempre.
MAX_SESSIONS = int(os.getenv("GAIA_MAX_GUEST_SESSIONS", "200"))

def is_guest(user: str) -> bool:
    return user.startswith("guest:")


def new_guest_id() -> str:
    return f"guest:{generate_id()}"


async def create_guest_user() -> str:
    """Da de alta un invitado y devuelve su id, o 503 si no cabe.

    Solo purga **al topar**, no en cada alta. Purgar primero era lo evidente
    —así el tope nunca lo consumen las sesiones muertas que el bucle de fondo
    todavía no ha barrido— pero pone un borrado RGPD por invitado abandonado en
    el camino de la primera petición de la demo: medido, 146 ms con 10
    abandonados, 608 ms con 150. La limpieza solo la paga quien se encuentra la
    puerta cerrada, que es quien se llevaría un 503 si no se hiciera.

    Lo que cambió es cómo se comprueba: el intento de alta *es* la comprobación,
    así que el caso normal cuesta una sentencia en vez de un COUNT y un INSERT
    por conexiones distintas. Ese razonamiento sobre coste ya estaba hecho; el
    de concurrencia no.
    """
    from app.auth.gdpr import purge_expired_guests
    from app.errors import APIError

    guest_id = new_guest_id()
    if await _alta(guest_id):
        return guest_id

    # Solo al topar: la limpieza la paga quien se encuentra la puerta cerrada.
    await purge_expired_guests()
    if await _alta(guest_id):
        return guest_id

    raise APIError(503, "server_busy", "Servidor saturado. Inténtalo más tarde.")


async def _alta(guest_id: str) -> bool:
    """Da de alta al invitado si cabe. False si el tope ya estaba lleno.

    Una sola sentencia. Antes el COUNT y el INSERT viajaban por conexiones
    distintas con todo el hueco en medio, así que N altas simultáneas leían el
    mismo recuento y entraban todas; y el tercer bloque, que parecía comprobar
    junto al INSERT, releía la variable de Python que ya estaba en memoria:
    tenía la forma de un chequeo transaccional sin serlo.

    Con MAX_SESSIONS a 0 la condición nunca es cierta y el 503 sale siempre, que
    es como se desactiva el modo invitado.

    ponytail: en SQLite la sentencia es atómica de verdad —el escritor es
    exclusivo—. En PostgreSQL la subconsulta ve la instantánea de READ
    COMMITTED, así que dos altas exactamente simultáneas podrían colarse; la
    ventana pasa de tres viajes de red a una sentencia. Si algún día importa, un
    advisory lock sobre el recuento lo cierra del todo.
    """
    async with open_db() as conn:
        fila = await conn.fetchone(
            sql("queries/guest:insert_guest_si_cabe"),
            (
                guest_id,
                guest_id,
                # `email` es UNIQUE NOT NULL y el invitado no da ninguno. El
                # dominio es reservado (RFC 6761), así que no colisiona con el
                # correo real de nadie ni es enrutable si algo intentara
                # escribir a él.
                f"{guest_id}@invalid",
                generate_date(),
                MAX_SESSIONS,
            ),
        )
        await conn.commit()
    return fila is not None
