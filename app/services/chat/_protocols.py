"""Los almacenes que usa el chat, declarados como Protocol.

Existen para no importar `app.storage.skill_storage` y compañía desde aquí: el
import es circular. Son solo anotaciones.
"""


from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Protocol,
)

if TYPE_CHECKING:
    pass



# Estos Protocol existen para no importar los storages de recurso desde aquí
# (app.storage.skill_storage y compañía; el import es circular). Son solo
# anotaciones: se les quitó @runtime_checkable
# porque ninguno se usaba nunca en un isinstance, y el decorador hacía creer
# que había una comprobación en tiempo de ejecución que no existe.
class _SkillStorage(Protocol):
    async def get(
        self, scope: str, skill_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...

class _ToolStorage(Protocol):
    async def get(
        self, scope: str, tool_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...

class _KnowledgeStorage(Protocol):
    async def get(
        self, item_id: str, owner_id: Any = None
    ) -> Optional[Dict[str, Any]]: ...

class _PromptStorage(Protocol):
    async def get_any(
        self, prompt_id: str, owner_id: Any = None
    ) -> Optional[Dict[str, Any]]: ...

    async def find_by_alias(
        self, alias: str, owner_id: Any = None
    ) -> Optional[Dict[str, Any]]: ...

class _MemoryStorage(Protocol):
    # Declaraban get/save síncronos y el código los llama con await desde el
    # primer día: las implementaciones reales (MemoryStorage) son async.
    async def get(self, filename: str, owner_id: str = "admin") -> Optional[str]: ...
    async def save(
        self, filename: str, content: str, owner_id: str = "admin"
    ) -> Dict[str, Any]: ...

class _ChatStorage(Protocol):
    async def list_memory_messages(
        self,
        user_id: str,
        agent_id: str,
        exclude_conversation_id: str | None = None,
        *,
        limit: int = 200,
        chars_per_message: int = 2_000,
    ) -> List[Dict[str, Any]]: ...
