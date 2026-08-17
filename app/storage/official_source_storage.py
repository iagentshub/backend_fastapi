"""Persistencia de las fuentes oficiales.

Solo la fuente: lo que trae se guarda como recurso normal en agents/skills/…
con ``official_source_id`` apuntando aquí (ver services/official_source_sync).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config.content_languages import CONTENT_LANGUAGE_LABELS
from app.models.official_source import INTERNAL_SOURCE_ID, OfficialSource
from app.sql import sql
from app.storage.db import AsyncConn, open_db
from app.utils import now_iso
from app.utils.generators import generate_id

# Tablas de recurso que pueden llevar contenido oficial, con su tipo lógico.
OFFICIAL_RESOURCE_TABLES: Dict[str, str] = {
    "agent": "agents",
    "skill": "skills",
    "prompt": "prompts",
    "tool": "tools",
    "knowledge": "knowledge_items",
    "workflow": "agent_workflows",
}
SOURCE_RESOURCE_TYPES = frozenset({*OFFICIAL_RESOURCE_TABLES, "memory"})
_INVALID_LANGUAGE_ERROR = re.compile(
    r"^[^:]+: etiquetas no válidas \(([^)]+)\)$"
)


def _is_legacy_invalid_language_error(value: Any) -> bool:
    match = _INVALID_LANGUAGE_ERROR.fullmatch(str(value))
    if not match:
        return False
    labels = {item.strip() for item in match.group(1).split(",")}
    return bool(labels) and all(
        label.startswith("lang_") and label not in CONTENT_LANGUAGE_LABELS
        for label in labels
    )


class OfficialSourceStorage:
    async def list_sources(self) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/official_sources:list_all")
            )
        return [OfficialSource(**dict(row)).as_dict() for row in rows]

    async def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_by_id"), (source_id,)
            )
        return OfficialSource(**dict(row)).as_dict() if row else None

    async def find_by_repository(self, repository_url: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_by_url"),
                (repository_url,),
            )
        return OfficialSource(**dict(row)).as_dict() if row else None

    async def save_source(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Alta o actualización por repositorio, que es la clave natural."""
        existing = await self.find_by_repository(str(data["repository_url"]))
        source_id = str((existing or {}).get("id") or data.get("id") or generate_id())
        now = now_iso()
        async with open_db() as conn:
            if existing:
                await conn.execute(
                    sql("queries/official_sources:update_from_repo"),
                    (
                        data["name"],
                        data.get("description", ""),
                        data.get("repository_owner", ""),
                        data.get("repository_name", ""),
                        data.get("provider", "github"),
                        data.get("repository_path", ""),
                        data.get("owner_id"),
                        data.get("default_branch", "main"),
                        data.get("tracking_mode", "release"),
                        data.get("tracking_ref", "main"),
                        data.get("import_mode", "deterministic"),
                        data.get("llm_connection_id"),
                        data.get("license", ""),
                        now,
                        source_id,
                    ),
                )
            else:
                await conn.execute(
                    sql("queries/official_sources:insert_full"),
                    (
                        source_id,
                        data["name"],
                        data.get("description", ""),
                        data["repository_url"],
                        data.get("repository_owner", ""),
                        data.get("repository_name", ""),
                        data.get("provider", "github"),
                        data.get("repository_path", ""),
                        data.get("owner_id"),
                        data.get("default_branch", "main"),
                        data.get("tracking_mode", "release"),
                        data.get("tracking_ref", "main"),
                        data.get("import_mode", "deterministic"),
                        data.get("llm_connection_id"),
                        data.get("license", ""),
                        now,
                        now,
                    ),
                )
            await conn.commit()
        result = await self.get_source(source_id)
        assert result is not None
        return result

    async def update_source(
        self, source_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not await self.get_source(source_id):
            return None
        async with open_db() as conn:
            duplicate = await conn.fetchone(
                sql("queries/official_sources:url_taken_by_other"),
                (data["repository_url"], source_id),
            )
            if duplicate:
                raise ValueError("repository_already_registered")
            await conn.execute(
                sql("queries/official_sources:update_fields"),
                (
                    data["name"],
                    data.get("description", ""),
                    data["repository_url"],
                    data.get("repository_owner", ""),
                    data.get("repository_name", ""),
                    data.get("provider", "github"),
                    data.get("repository_path", ""),
                    data.get("default_branch", "main"),
                    data.get("tracking_mode", "release"),
                    data.get("tracking_ref", "main"),
                    data.get("license", ""),
                    now_iso(),
                    source_id,
                ),
            )
            await conn.commit()
        return await self.get_source(source_id)

    async def mark_sync(
        self,
        source_id: str,
        *,
        version: Optional[str] = None,
        commit_sha: Optional[str] = None,
        error: Optional[str] = None,
        state: str = "idle",
    ) -> None:
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:update_sync_result"),
                (now, error, version, commit_sha, state, now, source_id),
            )
            await conn.commit()

    async def acquire_sync_lock(self, source_id: str, base_commit_sha: str) -> bool:
        """Bloqueo compare-and-set; también rechaza un borrador obsoleto."""
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:claim_applying"),
                (now_iso(), source_id, base_commit_sha),
            )
            await conn.commit()
        return row is not None

    async def delete_source(self, source_id: str) -> bool:
        if not await self.get_source(source_id):
            return False
        async with open_db() as conn:
            await conn.execute(sql("queries/official_sources:delete_source"), (source_id,))
            await conn.commit()
        return True

    async def ensure_internal_source(self) -> Dict[str, Any]:
        """Fuente para lo que un admin marca como oficial sin repositorio."""
        existing = await self.get_source(INTERNAL_SOURCE_ID)
        if existing:
            return existing
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:insert_minimal"),
                (
                    INTERNAL_SOURCE_ID,
                    "iAgents Hub",
                    "Contenido marcado como oficial desde el panel",
                    f"internal://{INTERNAL_SOURCE_ID}",
                    "internal",
                    INTERNAL_SOURCE_ID,
                    "branch",
                    "main",
                    now,
                    now,
                ),
            )
            await conn.commit()
        result = await self.get_source(INTERNAL_SOURCE_ID)
        assert result is not None
        return result

    async def mark_resource(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        *,
        source_id: Optional[str],
        component_id: Optional[str] = None,
        source_path: str = "",
        content_hash: str = "",
        commit_sha: str = "",
        explicitly_selected: bool = True,
        conn: Optional[AsyncConn] = None,
    ) -> None:
        """Marca (o desmarca, con ``source_id=None``) un recurso como oficial.

        Las columnas se escriben aquí y no en el storage del recurso: son
        metadatos de gobierno del catálogo, no del objeto, y así ningún
        guardado normal puede inventárselos.
        """
        if resource_type not in SOURCE_RESOURCE_TYPES:
            raise ValueError(f"tipo de recurso sin tabla oficial: {resource_type!r}")
        if source_id and not component_id:
            raise ValueError("component_id_required")
        if conn is not None:
            await self._mark_resource_conn(
                conn,
                resource_type,
                resource_id,
                owner_id,
                source_id=source_id,
                component_id=component_id,
                source_path=source_path,
                content_hash=content_hash,
                commit_sha=commit_sha,
                explicitly_selected=explicitly_selected,
            )
            return
        async with open_db() as own_conn:
            await self._mark_resource_conn(
                own_conn,
                resource_type,
                resource_id,
                owner_id,
                source_id=source_id,
                component_id=component_id,
                source_path=source_path,
                content_hash=content_hash,
                commit_sha=commit_sha,
                explicitly_selected=explicitly_selected,
            )
            await own_conn.commit()

    async def _mark_resource_conn(
        self,
        conn: AsyncConn,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        *,
        source_id: Optional[str],
        component_id: Optional[str],
        source_path: str,
        content_hash: str,
        commit_sha: str,
        explicitly_selected: bool,
    ) -> None:
        table = OFFICIAL_RESOURCE_TABLES.get(resource_type)
        if table:
            await conn.execute(
                f"UPDATE {table} SET official_source_id=?, official_component_id=? "
                "WHERE id=? AND owner_id=?",
                (source_id, component_id if source_id else None, resource_id, owner_id),
            )
        if source_id:
            now = now_iso()
            await conn.execute(
                sql("queries/official_sources:upsert_link"),
                (
                    source_id,
                    component_id,
                    resource_type,
                    resource_id,
                    owner_id,
                    source_path,
                    content_hash,
                    commit_sha,
                    int(explicitly_selected),
                    now,
                    now,
                ),
            )
        else:
            await conn.execute(
                sql("queries/official_sources:delete_link_by_resource"),
                (resource_type, resource_id, owner_id),
            )

    async def find_resource(
        self, source_id: str, component_id: str
    ) -> Optional[Dict[str, Any]]:
        """Recurso ya materializado para un componente, si sigue existiendo."""
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_link"),
                (source_id, component_id),
            )
        if not row:
            return None
        result = dict(row)
        result["owner_id"] = result.pop("resource_owner_id")
        return result

    async def list_resources(self, source_id: str) -> List[Dict[str, Any]]:
        """Recursos materializados por una fuente, de todos los tipos."""
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/official_sources:list_links"),
                (source_id,),
            )
        return [
            {
                **dict(row),
                "component_id": row["component_key"],
                "owner_id": row["resource_owner_id"],
            }
            for row in rows
        ]

    async def get_origin(
        self, resource_type: str, resource_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        params: tuple[Any, ...] = (resource_type, resource_id)
        owner_filter = ""
        if owner_id is not None:
            owner_filter = " AND l.resource_owner_id=?"
            params = (*params, owner_id)
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT l.*,s.name AS source_name,s.repository_url,s.provider,"
                "s.repository_path,s.owner_id AS source_owner_id,s.last_commit_sha,"
                "s.sync_state FROM resource_source_links l "
                "JOIN official_sources s ON s.id=l.source_id "
                "WHERE l.resource_type=? AND l.resource_id=?" + owner_filter,
                params,
            )
        return dict(row) if row else None

    async def set_owner(self, source_id: str, owner_id: str) -> bool:
        if not await self.get_source(source_id):
            return False
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:set_owner"),
                (owner_id, now_iso(), source_id),
            )
            await conn.commit()
        return True

    async def transfer_owner(self, source_id: str, owner_id: str) -> bool:
        """Transfiere fuente y objetos originales; nunca toca copias ajenas."""
        source = await self.get_source(source_id)
        if not source:
            return False
        links = await self.list_resources(source_id)
        table_by_type = {
            **OFFICIAL_RESOURCE_TABLES,
            "memory": "memory_files",
        }
        async with open_db() as conn:
            async with conn.transaction():
                for item in links:
                    table = table_by_type[str(item["resource_type"])]
                    conflict = await conn.fetchone(
                        f"SELECT 1 FROM {table} WHERE id=? AND owner_id=?",
                        (item["resource_id"], owner_id),
                    )
                    if conflict and item["owner_id"] != owner_id:
                        raise ValueError("owner_transfer_resource_conflict")
                for item in links:
                    old_owner = str(item["owner_id"])
                    resource_type = str(item["resource_type"])
                    resource_id = str(item["resource_id"])
                    table = table_by_type[resource_type]
                    await conn.execute(
                        f"UPDATE {table} SET owner_id=? WHERE id=? AND owner_id=?",
                        (owner_id, resource_id, old_owner),
                    )
                    await conn.execute(
                        sql("queries/official_sources:relabel_labels_owner"),
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                    await conn.execute(
                        sql("queries/official_sources:relabel_social_owner"),
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                    await conn.execute(
                        sql("queries/official_sources:relabel_versions_owner"),
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                await conn.execute(
                    sql("queries/official_sources:relabel_links_owner"),
                    (owner_id, source_id),
                )
                await conn.execute(
                    sql("queries/official_sources:set_owner"),
                    (owner_id, now_iso(), source_id),
                )
        return True

    async def create_draft(
        self,
        *,
        owner_id: str,
        source: Dict[str, Any],
        components: List[Dict[str, Any]],
        source_id: Optional[str] = None,
        errors: Optional[List[Any]] = None,
        security_warnings: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        draft_id = generate_id()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(hours=24)).isoformat()
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:insert_draft"),
                (
                    draft_id,
                    source_id,
                    owner_id,
                    source["repository_url"],
                    source.get("provider", "github"),
                    source.get("repository_path", ""),
                    source.get("tracking_mode", "branch"),
                    source.get("tracking_ref", source.get("default_branch", "main")),
                    source.get("resolved_version", ""),
                    source.get("commit_sha", ""),
                    json.dumps(source, ensure_ascii=False),
                    json.dumps(errors or [], ensure_ascii=False),
                    json.dumps(security_warnings or [], ensure_ascii=False),
                    "pending",
                    expires_at,
                    now,
                    now,
                ),
            )
            if components:
                await conn.executemany(
                    sql("queries/official_sources:insert_draft_component"),
                    [
                        (
                            draft_id,
                            str(item["component_id"]),
                            json.dumps(item, ensure_ascii=False),
                            int(bool(item.get("selected", False))),
                            int(bool(item.get("explicitly_selected", False))),
                            item.get("forced_type"),
                            item.get("forced_language"),
                            item.get("forced_tool_language"),
                            int(bool(item.get("security_accepted", False))),
                            item.get("state", "new"),
                        )
                        for item in components
                    ],
                )
            await conn.commit()
        draft = await self.get_draft(draft_id)
        assert draft is not None
        return draft

    async def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_draft"), (draft_id,)
            )
            count = await conn.fetchval(
                sql("queries/official_sources:count_draft_components"),
                (draft_id,),
            )
        if not row:
            return None
        result = dict(row)
        result["source"] = json.loads(result.pop("source_payload"))
        result["errors"] = [
            error
            for error in json.loads(result["errors"])
            if not _is_legacy_invalid_language_error(error)
        ]
        result["security_warnings"] = json.loads(result["security_warnings"])
        result["component_count"] = int(count or 0)
        result["expired"] = (
            result["expires_at"] <= datetime.now(timezone.utc).isoformat()
        )
        return result

    async def attach_draft_source(
        self, draft_id: str, source: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        payload = dict(source)
        payload["base_commit_sha"] = source.get("last_commit_sha") or ""
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:update_draft_source"),
                (
                    source["id"],
                    json.dumps(payload, ensure_ascii=False),
                    now_iso(),
                    draft_id,
                ),
            )
            await conn.commit()
        return await self.get_draft(draft_id)

    async def list_draft_components(
        self,
        draft_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        component_type: Optional[str] = None,
        state: Optional[str] = None,
        query: str = "",
    ) -> Dict[str, Any]:
        clauses = ["draft_id=?"]
        params: List[Any] = [draft_id]
        if state:
            clauses.append("state=?")
            params.append(state)
        rows_query = "SELECT * FROM official_import_components WHERE " + " AND ".join(
            clauses
        )
        async with open_db() as conn:
            rows = await conn.fetchall(rows_query, tuple(params))
        items = [self._draft_component_from_row(row) for row in rows]
        if component_type:
            items = [item for item in items if item["component_type"] == component_type]
        if query:
            needle = query.casefold()
            items = [
                item
                for item in items
                if needle in str(item.get("name", "")).casefold()
                or needle in str(item.get("source_path", "")).casefold()
            ]
        total = len(items)
        safe_limit = max(1, min(limit, 500))
        safe_offset = max(0, offset)
        return {
            "items": items[safe_offset : safe_offset + safe_limit],
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
        }

    async def update_draft_component(
        self, draft_id: str, component_key: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "selected",
            "explicitly_selected",
            "forced_type",
            "forced_language",
            "forced_tool_language",
            "security_accepted",
        }
        values = {key: value for key, value in updates.items() if key in allowed}
        for boolean_key in (
            "selected",
            "explicitly_selected",
            "security_accepted",
        ):
            if boolean_key in values:
                values[boolean_key] = int(bool(values[boolean_key]))
        dependencies = updates.get("dependencies")
        if not values and dependencies is None:
            return await self.get_draft_component(draft_id, component_key)
        async with open_db() as conn:
            if values:
                assignments = ",".join(f"{key}=?" for key in values)
                await conn.execute(
                    f"UPDATE official_import_components SET {assignments} "
                    "WHERE draft_id=? AND component_key=?",
                    (*values.values(), draft_id, component_key),
                )
            if dependencies is not None:
                row = await conn.fetchone(
                    sql("queries/official_sources:get_component_payload"),
                    (draft_id, component_key),
                )
                if row:
                    payload = json.loads(row["payload"])
                    payload["dependencies"] = list(dependencies)
                    await conn.execute(
                        sql("queries/official_sources:update_component_payload"),
                        (
                            json.dumps(payload, ensure_ascii=False),
                            draft_id,
                            component_key,
                        ),
                    )
            await conn.execute(
                sql("queries/official_sources:touch_draft"),
                (now_iso(), draft_id),
            )
            await conn.commit()
        return await self.get_draft_component(draft_id, component_key)

    async def get_draft_component(
        self, draft_id: str, component_key: str
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/official_sources:get_component"),
                (draft_id, component_key),
            )
        return self._draft_component_from_row(row) if row else None

    async def get_all_draft_components(self, draft_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/official_sources:list_components"),
                (draft_id,),
            )
        return [self._draft_component_from_row(row) for row in rows]

    async def replace_draft_selection(
        self,
        draft_id: str,
        *,
        selected: set[str],
        explicit: set[str],
    ) -> None:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/official_sources:list_component_keys"),
                (draft_id,),
            )
            await conn.executemany(
                sql("queries/official_sources:set_component_selection"),
                [
                    (
                        int(key in selected),
                        int(key in explicit),
                        draft_id,
                        key,
                    )
                    for key in (str(row["component_key"]) for row in rows)
                ],
            )
            await conn.execute(
                sql("queries/official_sources:touch_draft"),
                (now_iso(), draft_id),
            )
            await conn.commit()

    async def save_mapping(
        self,
        source_id: str,
        source_path: str,
        *,
        forced_type: Optional[str],
        forced_language: Optional[str],
        forced_tool_language: Optional[str],
        dependencies: List[str],
        ignored: bool = False,
    ) -> None:
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:upsert_mapping"),
                (
                    source_id,
                    source_path,
                    forced_type,
                    forced_language,
                    forced_tool_language,
                    ignored,
                    json.dumps(dependencies, ensure_ascii=False),
                    now_iso(),
                ),
            )
            await conn.commit()

    async def list_mappings(self, source_id: str) -> Dict[str, Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/official_sources:list_mappings"),
                (source_id,),
            )
        return {
            str(row["source_path"]): {
                **dict(row),
                "ignored": bool(row["ignored"]),
                "dependencies": json.loads(row["dependencies"]),
            }
            for row in rows
        }

    async def mark_draft_status(self, draft_id: str, status: str) -> None:
        async with open_db() as conn:
            await conn.execute(
                sql("queries/official_sources:set_draft_status"),
                (status, now_iso(), draft_id),
            )
            await conn.commit()

    async def delete_expired_drafts(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with open_db() as conn:
            count = int(
                await conn.fetchval(
                    sql("queries/official_sources:count_expired_drafts"),
                    (now,),
                )
                or 0
            )
            await conn.execute(
                sql("queries/official_sources:delete_expired_drafts"), (now,)
            )
            await conn.commit()
        return count

    @staticmethod
    def _draft_component_from_row(row: Any) -> Dict[str, Any]:
        payload = json.loads(row["payload"])
        payload["labels"] = [
            label
            for label in payload.get("labels", [])
            if not str(label).startswith("lang_")
            or label in CONTENT_LANGUAGE_LABELS
        ]
        if (
            payload.get("language")
            and payload["language"] not in CONTENT_LANGUAGE_LABELS
        ):
            payload["language"] = ""
        return {
            **payload,
            "component_id": row["component_key"],
            "selected": bool(row["selected"]),
            "explicitly_selected": bool(row["explicitly_selected"]),
            "forced_type": row["forced_type"],
            "forced_language": row["forced_language"],
            "forced_tool_language": row["forced_tool_language"],
            "security_accepted": bool(row["security_accepted"]),
            "state": row["state"],
        }
