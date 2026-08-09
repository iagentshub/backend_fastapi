"""Storage efímero en memoria para sesiones guest."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.utils.generators import generate_id

TTL = 43200  # 12 h — igual que max_age del cookie de sesión
MAX_SESSIONS = int(os.getenv("GAIA_MAX_GUEST_SESSIONS", "200"))

_sessions: Dict[str, "GuestSession"] = {}


@dataclass
class GuestSession:
    created_at: float = field(default_factory=time.time)
    connections: List[Dict[str, Any]] = field(default_factory=list)
    agents: List[Dict[str, Any]] = field(default_factory=list)
    memory: Dict[str, str] = field(default_factory=dict)
    skills: List[Dict[str, Any]] = field(default_factory=list)
    knowledge: List[Dict[str, Any]] = field(default_factory=list)
    prompts: List[Dict[str, Any]] = field(default_factory=list)


class GuestMemoryAdapter:
    """Adaptador mínimo de MemoryStorage para pasar a stream_chat/auto_update_memory."""

    def __init__(self, session: GuestSession) -> None:
        self._s = session

    async def get(self, filename: str, owner_id: str = "guest") -> str | None:
        return self._s.memory.get(filename)

    async def save(
        self, filename: str, content: str, owner_id: str = "guest"
    ) -> Dict[str, Any]:
        self._s.memory[filename] = content
        return {"filename": filename}


class GuestKnowledgeAdapter:
    """Adaptador mínimo de KnowledgeStorage para sesiones guest (items en memoria)."""

    def __init__(self, session: GuestSession) -> None:
        self._s = session

    async def get(self, item_id: str, owner_id: Any = None) -> Dict[str, Any] | None:
        return next((i for i in self._s.knowledge if i["id"] == item_id), None)


def is_guest(user: str) -> bool:
    return user.startswith("guest:")


def get_session(guest_id: str) -> GuestSession:
    """Devuelve la sesión en memoria del guest, creándola si no existe. Limpia las expiradas.

    Ojo: `_sessions` es un dict de ESTE proceso y producción arranca varios
    workers (ver el aviso de arranque en main.py). Sin sticky sessions en el
    proxy, el mismo invitado puede caer en otro worker y encontrarse una sesión
    vacía.
    """
    from app.errors import APIError

    now = time.time()
    # Limpiar siempre, no solo cuando hay demasiadas sesiones
    expired = [k for k, v in list(_sessions.items()) if now - v.created_at > TTL]
    for k in expired:
        del _sessions[k]
    if guest_id not in _sessions:
        if len(_sessions) >= MAX_SESSIONS:
            raise APIError(
                503,
                "server_busy",
                "Servidor saturado. Inténtalo más tarde.",
            )
        _sessions[guest_id] = GuestSession()
    return _sessions[guest_id]


def new_guest_id() -> str:
    return f"guest:{generate_id()}"
