"""Borradores: lo que se ha detectado de una fuente y aún no se ha aplicado.

Un borrador caduca (`delete_expired_drafts`); lo que sobrevive al aplicarse son
recursos normales, no filas de aquí.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config.content_languages import CONTENT_LANGUAGE_LABELS
from app.pagination.cursor import cursor_context_signature
from app.pagination.models import CursorPage, CursorParams
from app.sql import sql
from app.storage.composite_cursor_page import (
    KeysetColumn,
    fetch_composite_cursor_page,
)
from app.storage.db import open_db

# Tablas de recurso que pueden llevar contenido oficial, con su tipo lógico.
from app.storage.official_source_storage._shared import (
    _is_legacy_invalid_language_error,
)
from app.utils import now_iso
from app.utils.generators import generate_id


class _DraftsMixin:
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
                            str(item.get("component_type") or ""),
                            str(item.get("name") or ""),
                            str(item.get("source_path") or ""),
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

    async def list_draft_components_cursor(
        self,
        draft_id: str,
        *,
        page: CursorParams,
        component_type: Optional[str] = None,
        state: Optional[str] = None,
        query: str = "",
    ) -> CursorPage[Dict[str, Any]]:
        clauses = ["draft_id=?"]
        params: List[Any] = [draft_id]
        if state:
            clauses.append("state=?")
            params.append(state)
        if component_type:
            clauses.append("component_type=?")
            params.append(component_type)
        if query:
            clauses.append(
                "(LOWER(name) LIKE LOWER(?) OR LOWER(source_path) LIKE LOWER(?))"
            )
            pattern = f"%{query}%"
            params.extend((pattern, pattern))
        where = " AND ".join(clauses)
        context = cursor_context_signature(
            {
                "resource": "official_import_component",
                "draft_id": draft_id,
                "component_type": component_type,
                "state": state,
                "query": query,
            }
        )
        async with open_db() as conn:
            return await fetch_composite_cursor_page(
                conn,
                count_sql=(
                    f"SELECT COUNT(*) FROM official_import_components WHERE {where}"
                ),
                select_sql=(f"SELECT * FROM official_import_components WHERE {where}"),
                params=tuple(params),
                columns=(
                    KeysetColumn("component_key", "component_key", descending=False),
                ),
                context=context,
                resource="official_import_component",
                page=page,
                decode=self._draft_component_from_row,
            )

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
            if not str(label).startswith("lang_") or label in CONTENT_LANGUAGE_LABELS
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
