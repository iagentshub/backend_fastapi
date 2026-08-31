"""Rutas de etiquetas — enlaces entre objetos de cualquier tipo por etiqueta."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.errors import APIError
from app.storage.labels import resources_with_label
from app.storage.skill_storage import SKILL_ASSIGNABLE_LABELS, SKILL_LABELS

_VISIBILIDAD = frozenset({"private", "public"})
_ENTORNOS = frozenset({"production", "staging", "development", "test"})


def validate_labels(
    raw_labels: Any,
    *,
    role: str,
    scope: str,
    recurso: str,
    extra_permitidas: frozenset = frozenset(),
) -> Optional[List[str]]:
    """Etiquetas saneadas y comprobadas contra el catálogo del sistema.

    Devuelve `None` cuando no venían, para que el llamante distinga «no las
    tocó» de «las dejó vacías».

    Estaba escrita cuatro veces —skills, prompts, tools y workflows—, y el
    recurso que llegó sin ella fue el agente: solo miraba si alguien se
    autoproclamaba `official`, mientras su mensaje de error describía una
    comprobación contra el catálogo que nunca se escribió. Lo importante estaba
    defendido, pero el campo dejaba de ser el conjunto cerrado que todo lo demás
    da por hecho: ese valor llega intacto al índice transversal `resource_labels`
    y el importador de fuentes oficiales ya filtra por catálogo en dos sitios.

    Con una sola función, el recurso que llegue mañana no puede quedarse fuera
    por olvido, que es exactamente como se quedó este.
    """
    if raw_labels is None:
        return None
    if not isinstance(raw_labels, list):
        raise APIError(
            422,
            "invalid_field",
            "Las labels deben ser una lista del catálogo del sistema",
            extra={"field": "labels"},
        )
    permitidas = (
        SKILL_LABELS
        if role == "admin"
        else SKILL_ASSIGNABLE_LABELS | {"community", "fork"} | extra_permitidas
    )
    labels = list(
        dict.fromkeys(str(label).strip() for label in raw_labels if str(label).strip())
    )
    invalidas = [label for label in labels if label not in permitidas]
    if invalidas:
        raise APIError(
            422,
            "invalid_field",
            f"{recurso} contiene labels que no existen en el catálogo del sistema",
            extra={"field": "labels", "invalid": invalidas},
        )
    if (
        len([x for x in labels if x in _VISIBILIDAD]) > 1
        or len([x for x in labels if x in _ENTORNOS]) > 1
    ):
        raise APIError(
            422,
            "invalid_field",
            f"{recurso} contiene labels mutuamente excluyentes",
            extra={"field": "labels"},
        )
    if not any(x in _VISIBILIDAD for x in labels):
        labels.insert(0, scope if scope in _VISIBILIDAD else "private")
    return labels

router = APIRouter(prefix="/api/labels", tags=["labels"])


@router.get("/{label}")
async def list_resources_with_label(
    label: str, ctx: GroupContext = Depends(require_group_session)
) -> List[Dict[str, Any]]:
    """Devuelve los recursos (de cualquier tipo) que llevan una etiqueta.

    El admin ve todos; el resto solo los suyos (por owner_id de group activo).
    """
    owner_id = None if await get_user_role(ctx.user) == "admin" else ctx.group_id
    return await resources_with_label(label, owner_id=owner_id)
