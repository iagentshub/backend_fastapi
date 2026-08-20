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
    abandonados, 608 ms con 150. Comprobar el hueco primero cuesta un COUNT, y
    la limpieza solo la paga quien se encuentra la puerta cerrada, que es quien
    se llevaría un 503 si no se hiciera.
    """
    from app.auth.gdpr import purge_expired_guests
    from app.errors import APIError

    guest_id = new_guest_id()
    async with open_db() as conn:
        activos = await conn.fetchval(sql("queries/guest:count_guests")) or 0

    if activos >= MAX_SESSIONS:
        await purge_expired_guests()
        async with open_db() as conn:
            activos = await conn.fetchval(sql("queries/guest:count_guests")) or 0

    async with open_db() as conn:
        if activos >= MAX_SESSIONS:
            raise APIError(
                503,
                "server_busy",
                "Servidor saturado. Inténtalo más tarde.",
            )
        await conn.execute(
            sql("queries/auth:insert_user_with_role"),
            (
                guest_id,
                guest_id,
                # `email` es UNIQUE NOT NULL y el invitado no da ninguno. El
                # dominio es reservado (RFC 6761), así que no colisiona con el
                # correo real de nadie ni es enrutable si algo intentara
                # escribir a él.
                f"{guest_id}@invalid",
                None,
                "guest",
                1,
                1,
                generate_date(),
            ),
        )
        await conn.commit()
    return guest_id
