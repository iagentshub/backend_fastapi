"""DTOs de entrada compartidos por los routers HTTP.

Los recursos editables admiten extensiones por compatibilidad con versiones
anteriores del frontend. Aun en esos casos el cuerpo raíz queda tipado y los
campos conocidos aparecen en OpenAPI.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RequestBody(BaseModel):
    # El helper JSON anterior ignoraba claves desconocidas. Mantenerlo evita
    # romper clientes antiguos durante la migración progresiva a DTOs.
    model_config = ConfigDict(extra="ignore")

    def payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class ExtensibleRequestBody(RequestBody):
    model_config = ConfigDict(extra="allow")


class AccountBody(RequestBody):
    provider: str | None = None
    api_key: str | None = None
    host: str | None = None
    url: str | None = None
    username: str | None = None
    name: str | None = None


class AccountSyncBody(RequestBody):
    models: list[str] | None = None


class DeviceCodeBody(RequestBody):
    device_code: str | None = None


class TokenBody(RequestBody):
    token: str | None = None


class PatCreateBody(RequestBody):
    name: str | None = None
    # La ruta conserva su conversión manual y su error 400 histórico.
    expires_in_days: Any = Field(default=90)


class VSCodeAuthorizeBody(RequestBody):
    state: str | None = None


class VSCodeExchangeBody(RequestBody):
    code: str | None = None
    state: str | None = None


class AdminUserPatchBody(RequestBody):
    is_active: bool | None = None
    role: str | None = None
    password: str | None = None


class AdminUserCreateBody(RequestBody):
    username: str | None = None
    email: str | None = None
    password: str | None = None
    role: str | None = "standard"
    display_name: str | None = None


class StatusBody(RequestBody):
    status: str | None = None


class VerificationBody(RequestBody):
    verified: bool = False


class ResourceOwnerBody(RequestBody):
    username: str | None = None
    owner_id: str | None = None


class ConversationBody(RequestBody):
    title: str | None = None


class UsernameBody(RequestBody):
    username: str | None = None


class KnowledgeTextBody(RequestBody):
    title: str | None = None
    content: str | None = None
    source: str | None = None
    labels: list[Any] | None = None


class KnowledgeUrlBody(RequestBody):
    url: str | None = None
    title: str | None = None
    labels: list[Any] | None = None


class MemoryBody(RequestBody):
    content: str | None = None


class GroupShareBody(RequestBody):
    group_id: str | None = None


class OllamaModelsBody(RequestBody):
    host: str | None = None
    api_key: str | None = None


class ConnectionTestsBody(RequestBody):
    ids: list[str] | None = None


class AgentChatBody(RequestBody):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    conversation_id: str | None = None
    attached_knowledge_ids: list[str] = Field(default_factory=list)


class ResourcePayload(ExtensibleRequestBody):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    labels: list[Any] | None = None
    tags: Any = None
    scope: str | None = None
    is_active: bool | None = None


class AgentPayload(ResourcePayload):
    connection_id: str | None = None
    system_prompt: str | None = None
    skills: list[str] | None = None
    knowledge: list[str] | None = None
    prompts: list[str] | None = None
    tools: list[str] | None = None


class ConnectionPayload(ResourcePayload):
    type: str | None = None
    api_key: str | None = None
    model: str | None = None
    url: str | None = None
    host: str | None = None
    username: str | None = None


class CatalogResourcePayload(ResourcePayload):
    content: str | None = None
    language: str | None = None
