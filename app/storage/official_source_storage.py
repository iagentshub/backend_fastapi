"""Persistencia de las fuentes oficiales.

Solo la fuente: lo que trae se guarda como recurso normal en agents/skills/…
con ``official_source_id`` apuntando aquí (ver services/official_source_sync).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.models.official_source import INTERNAL_SOURCE_ID, OfficialSource
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


class OfficialSourceStorage:
    async def list_sources(self) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM official_sources ORDER BY lower(name)"
            )
        return [OfficialSource(**dict(row)).as_dict() for row in rows]

    async def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_sources WHERE id=?", (source_id,)
            )
        return OfficialSource(**dict(row)).as_dict() if row else None

    async def find_by_repository(self, repository_url: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_sources WHERE repository_url=?",
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
                    "UPDATE official_sources SET name=?, description=?, "
                    "repository_owner=?, repository_name=?, provider=?, repository_path=?, "
                    "owner_id=COALESCE(owner_id, ?), default_branch=?, tracking_mode=?, "
                    "tracking_ref=?, license=?, updated_at=? WHERE id=?",
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
                        data.get("license", ""),
                        now,
                        source_id,
                    ),
                )
            else:
                await conn.execute(
                    "INSERT INTO official_sources "
                    "(id,name,description,repository_url,repository_owner,"
                    "repository_name,provider,repository_path,owner_id,default_branch,"
                    "tracking_mode,tracking_ref,license,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "SELECT id FROM official_sources WHERE repository_url=? AND id<>?",
                (data["repository_url"], source_id),
            )
            if duplicate:
                raise ValueError("repository_already_registered")
            await conn.execute(
                "UPDATE official_sources SET name=?, description=?, repository_url=?, "
                "repository_owner=?, repository_name=?, provider=?, repository_path=?, "
                "default_branch=?, tracking_mode=?, tracking_ref=?, license=?, "
                "updated_at=? WHERE id=?",
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
                "UPDATE official_sources SET latest_checked_at=?, last_sync_error=?, "
                "last_version=COALESCE(?, last_version), "
                "last_commit_sha=COALESCE(?, last_commit_sha), sync_state=?, "
                "updated_at=? WHERE id=?",
                (now, error, version, commit_sha, state, now, source_id),
            )
            await conn.commit()

    async def acquire_sync_lock(self, source_id: str, base_commit_sha: str) -> bool:
        """Bloqueo compare-and-set; también rechaza un borrador obsoleto."""
        async with open_db() as conn:
            row = await conn.fetchone(
                "UPDATE official_sources SET sync_state='applying',updated_at=? "
                "WHERE id=? AND sync_state<>'applying' "
                "AND COALESCE(last_commit_sha,'')=? RETURNING id",
                (now_iso(), source_id, base_commit_sha),
            )
            await conn.commit()
        return row is not None

    async def delete_source(self, source_id: str) -> bool:
        if not await self.get_source(source_id):
            return False
        async with open_db() as conn:
            await conn.execute("DELETE FROM official_sources WHERE id=?", (source_id,))
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
                "INSERT INTO official_sources "
                "(id,name,description,repository_url,provider,repository_path,"
                "tracking_mode,tracking_ref,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                "INSERT INTO resource_source_links "
                "(source_id,component_key,resource_type,resource_id,resource_owner_id,"
                "source_path,content_hash,commit_sha,explicitly_selected,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_id,component_key) DO UPDATE SET "
                "resource_type=excluded.resource_type,resource_id=excluded.resource_id,"
                "resource_owner_id=excluded.resource_owner_id,"
                "source_path=excluded.source_path,content_hash=excluded.content_hash,"
                "commit_sha=excluded.commit_sha,"
                "explicitly_selected=excluded.explicitly_selected,"
                "updated_at=excluded.updated_at",
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
                "DELETE FROM resource_source_links "
                "WHERE resource_type=? AND resource_id=? AND resource_owner_id=?",
                (resource_type, resource_id, owner_id),
            )

    async def find_resource(
        self, source_id: str, component_id: str
    ) -> Optional[Dict[str, Any]]:
        """Recurso ya materializado para un componente, si sigue existiendo."""
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT resource_type,resource_id,resource_owner_id,source_path,"
                "content_hash,commit_sha,explicitly_selected FROM resource_source_links "
                "WHERE source_id=? AND component_key=?",
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
                "SELECT component_key,resource_type,resource_id,resource_owner_id,"
                "source_path,content_hash,commit_sha,explicitly_selected,"
                "created_at,updated_at "
                "FROM resource_source_links WHERE source_id=? ORDER BY component_key",
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
                "UPDATE official_sources SET owner_id=?,updated_at=? WHERE id=?",
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
                        "UPDATE resource_labels SET owner_id=? "
                        "WHERE resource_type=? AND resource_id=? AND owner_id=?",
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                    await conn.execute(
                        "UPDATE resource_social SET owner=? "
                        "WHERE resource_type=? AND resource_id=? AND owner=?",
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                    await conn.execute(
                        "UPDATE resource_versions SET owner_id=? "
                        "WHERE resource_type=? AND resource_id=? AND owner_id=?",
                        (owner_id, resource_type, resource_id, old_owner),
                    )
                await conn.execute(
                    "UPDATE resource_source_links SET resource_owner_id=? "
                    "WHERE source_id=?",
                    (owner_id, source_id),
                )
                await conn.execute(
                    "UPDATE official_sources SET owner_id=?,updated_at=? WHERE id=?",
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
                "INSERT INTO official_import_drafts "
                "(id,source_id,owner_id,repository_url,provider,repository_path,"
                "tracking_mode,tracking_ref,resolved_version,commit_sha,source_payload,"
                "errors,security_warnings,status,expires_at,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    "INSERT INTO official_import_components "
                    "(draft_id,component_key,payload,selected,explicitly_selected,"
                    "forced_type,forced_language,security_accepted,state) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            draft_id,
                            str(item["component_id"]),
                            json.dumps(item, ensure_ascii=False),
                            int(bool(item.get("selected", False))),
                            int(bool(item.get("explicitly_selected", False))),
                            item.get("forced_type"),
                            item.get("forced_language"),
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
                "SELECT * FROM official_import_drafts WHERE id=?", (draft_id,)
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM official_import_components WHERE draft_id=?",
                (draft_id,),
            )
        if not row:
            return None
        result = dict(row)
        result["source"] = json.loads(result.pop("source_payload"))
        result["errors"] = json.loads(result["errors"])
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
                "UPDATE official_import_drafts SET source_id=?,source_payload=?,"
                "updated_at=? WHERE id=?",
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
                    "SELECT payload FROM official_import_components "
                    "WHERE draft_id=? AND component_key=?",
                    (draft_id, component_key),
                )
                if row:
                    payload = json.loads(row["payload"])
                    payload["dependencies"] = list(dependencies)
                    await conn.execute(
                        "UPDATE official_import_components SET payload=? "
                        "WHERE draft_id=? AND component_key=?",
                        (
                            json.dumps(payload, ensure_ascii=False),
                            draft_id,
                            component_key,
                        ),
                    )
            await conn.execute(
                "UPDATE official_import_drafts SET updated_at=? WHERE id=?",
                (now_iso(), draft_id),
            )
            await conn.commit()
        return await self.get_draft_component(draft_id, component_key)

    async def get_draft_component(
        self, draft_id: str, component_key: str
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_import_components "
                "WHERE draft_id=? AND component_key=?",
                (draft_id, component_key),
            )
        return self._draft_component_from_row(row) if row else None

    async def get_all_draft_components(self, draft_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM official_import_components WHERE draft_id=? "
                "ORDER BY component_key",
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
                "SELECT component_key FROM official_import_components WHERE draft_id=?",
                (draft_id,),
            )
            await conn.executemany(
                "UPDATE official_import_components SET selected=?,"
                "explicitly_selected=? WHERE draft_id=? AND component_key=?",
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
                "UPDATE official_import_drafts SET updated_at=? WHERE id=?",
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
        dependencies: List[str],
        ignored: bool = False,
    ) -> None:
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO official_source_mappings "
                "(source_id,source_path,forced_type,forced_language,ignored,"
                "dependencies,updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(source_id,source_path) DO UPDATE SET "
                "forced_type=excluded.forced_type,"
                "forced_language=excluded.forced_language,ignored=excluded.ignored,"
                "dependencies=excluded.dependencies,updated_at=excluded.updated_at",
                (
                    source_id,
                    source_path,
                    forced_type,
                    forced_language,
                    ignored,
                    json.dumps(dependencies, ensure_ascii=False),
                    now_iso(),
                ),
            )
            await conn.commit()

    async def list_mappings(self, source_id: str) -> Dict[str, Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM official_source_mappings WHERE source_id=?",
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
                "UPDATE official_import_drafts SET status=?,updated_at=? WHERE id=?",
                (status, now_iso(), draft_id),
            )
            await conn.commit()

    async def delete_expired_drafts(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with open_db() as conn:
            count = int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM official_import_drafts WHERE expires_at<?",
                    (now,),
                )
                or 0
            )
            await conn.execute(
                "DELETE FROM official_import_drafts WHERE expires_at<?", (now,)
            )
            await conn.commit()
        return count

    @staticmethod
    def _draft_component_from_row(row: Any) -> Dict[str, Any]:
        payload = json.loads(row["payload"])
        return {
            **payload,
            "component_id": row["component_key"],
            "selected": bool(row["selected"]),
            "explicitly_selected": bool(row["explicitly_selected"]),
            "forced_type": row["forced_type"],
            "forced_language": row["forced_language"],
            "security_accepted": bool(row["security_accepted"]),
            "state": row["state"],
        }
