"""DTOs de entrada compartidos por los routers HTTP.

Los recursos editables admiten extensiones por compatibilidad con versiones
anteriores del frontend. Aun en esos casos el cuerpo raíz queda tipado y los
campos conocidos aparecen en OpenAPI.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestBody(BaseModel):
    # El helper JSON anterior ignoraba claves desconocidas. Mantenerlo evita
    # romper clientes antiguos durante la migración progresiva a DTOs.
    model_config = ConfigDict(extra="ignore")

    def payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class ExtensibleRequestBody(RequestBody):
    model_config = ConfigDict(extra="allow")


# Los cinco cuerpos de `groups/`, que se quedaron como `Dict[str, Any]` cuando
# el resto del backend pasó a pydantic. No es «parsear a mano» —FastAPI ya
# rechaza un cuerpo que no sea objeto— pero producía los mismos efectos: en el
# esquema OpenAPI eran un objeto libre, o sea que estos cinco endpoints no
# estaban ni en el único contrato que se genera solo.
#
# Los campos van sin `Literal` ni `max_length` a propósito: las comprobaciones
# de dominio se quedan en el handler porque cada una tiene su código de error
# —`name_too_long`, `invalid_field`, `role_or_permissions_required`— y pasarlas
# a pydantic las convertiría a todas en el 422 genérico, cambiando el contrato
# que los clientes ya leen.

# El tope del nombre vivía escrito dos veces dentro de los handlers de crear y
# de renombrar, que es donde peor se encuentra.
GROUP_NAME_MAX_LENGTH = 80
GROUP_ROLES = ("owner", "admin", "member")


class GroupBody(RequestBody):
    """Alta y renombrado de un grupo."""

    name: str | None = None


class GroupMemberBody(RequestBody):
    """Alta directa de un miembro."""

    username: str | None = None
    role: str | None = None


class GroupMemberUpdateBody(RequestBody):
    """Cambio de rol, de permisos, o de los dos.

    `model_fields_set` distingue «no lo mandó» de «lo mandó vacío», que es lo
    que antes hacía el `"role" in body`.
    """

    role: str | None = None
    permissions: dict[str, Any] | None = None


class GroupInvitationBody(RequestBody):
    """Invitación por nombre de usuario."""

    username: str | None = None


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


class LabelsBody(RequestBody):
    labels: list[Any] | None = None


class KnowledgeEditBody(RequestBody):
    name: str | None = None
    labels: list[Any] | None = None


class KnowledgePackEditBody(KnowledgeEditBody):
    description: str | None = None


class KnowledgePackUploadSessionBody(RequestBody):
    name: str | None = None
    description: str | None = None
    labels: list[Any] | None = None
    source_mode: str | None = "upload"
    total_files: int | None = None


class KnowledgePackManifestFile(RequestBody):
    relative_path: str
    size_bytes: int = 0
    checksum: str
    mime_type: str = ""
    modified_at: int | None = None


class KnowledgePackManifestBody(RequestBody):
    files: list[KnowledgePackManifestFile] = Field(default_factory=list)


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
    # El catálogo cerrado tiene 26 entradas, así que 64 sobra para cualquier
    # recurso legítimo. La cota va aquí y no en cada router porque es el sitio
    # barato —pydantic rechaza antes de tocar la base de datos— y porque
    # `sync_labels` hace un INSERT por etiqueta distinta: sin cota, un solo POST
    # con cien mil etiquetas son cien mil inserciones dentro de la transacción
    # del guardado, y el techo de cuerpo que lo frenaría (`max_request_bytes`)
    # vale 0 por defecto.
    labels: list[Any] | None = Field(default=None, max_length=64)
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
    publish_dependencies: list[str] | None = None


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
    instructions: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    target_os: str | None = None
    target_arch: str | None = None


class ToolSecurityBody(BaseModel):
    state: Literal["approved", "review", "quarantine"]
