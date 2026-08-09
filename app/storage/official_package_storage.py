"""Persistencia del catálogo oficial, sus versiones y las copias de usuario."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from app.models.official_package import OfficialPackage, PackageComponent
from app.storage.db import open_db
from app.utils import now_iso
from app.utils.generators import generate_id


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


class OfficialPackageStorage:
    async def list_packages(self, *, published_only: bool = False) -> List[Dict[str, Any]]:
        where = "WHERE published_version IS NOT NULL" if published_only else ""
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM official_packages " + where + " ORDER BY lower(name)"
            )
        return [OfficialPackage(**dict(row)).as_dict() for row in rows]

    async def get_package(self, package_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_packages WHERE id=?", (package_id,)
            )
        return OfficialPackage(**dict(row)).as_dict() if row else None

    async def find_by_repository(self, repository_url: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_packages WHERE repository_url=?",
                (repository_url,),
            )
        return OfficialPackage(**dict(row)).as_dict() if row else None

    async def save_package(self, data: Dict[str, Any]) -> Dict[str, Any]:
        existing = await self.find_by_repository(str(data["repository_url"]))
        package_id = str((existing or {}).get("id") or data.get("id") or generate_id())
        now = now_iso()
        created_at = str((existing or {}).get("created_at") or now)
        async with open_db() as conn:
            if existing:
                await conn.execute(
                    "UPDATE official_packages SET name=?, description=?, repository_owner=?, "
                    "repository_name=?, tracking_mode=?, tracking_ref=?, license=?, updated_at=? "
                    "WHERE id=?",
                    (
                        data["name"], data.get("description", ""),
                        data["repository_owner"], data["repository_name"],
                        data.get("tracking_mode", "release"), data.get("tracking_ref", "main"),
                        data.get("license", ""), now, package_id,
                    ),
                )
            else:
                await conn.execute(
                    "INSERT INTO official_packages "
                    "(id,name,description,repository_url,repository_owner,repository_name,"
                    "tracking_mode,tracking_ref,license,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        package_id, data["name"], data.get("description", ""),
                        data["repository_url"], data["repository_owner"],
                        data["repository_name"], data.get("tracking_mode", "release"),
                        data.get("tracking_ref", "main"), data.get("license", ""),
                        created_at, now,
                    ),
                )
            await conn.commit()
        result = await self.get_package(package_id)
        assert result is not None
        return result

    async def mark_sync(
        self, package_id: str, *, error: Optional[str] = None
    ) -> None:
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                "UPDATE official_packages SET latest_checked_at=?, last_sync_error=?, updated_at=? "
                "WHERE id=?",
                (now, error, now, package_id),
            )
            await conn.commit()

    async def delete_package(self, package_id: str) -> bool:
        if not await self.get_package(package_id):
            return False
        async with open_db() as conn:
            async with conn.transaction():
                # Las copias no tienen FK porque sus recursos privados deben
                # sobrevivir; solo se elimina su registro de procedencia.
                await conn.execute(
                    "DELETE FROM official_package_copies WHERE package_id=?",
                    (package_id,),
                )
                await conn.execute(
                    "DELETE FROM official_package_components WHERE package_id=?",
                    (package_id,),
                )
                await conn.execute(
                    "DELETE FROM official_package_versions WHERE package_id=?",
                    (package_id,),
                )
                await conn.execute(
                    "DELETE FROM official_packages WHERE id=?", (package_id,)
                )
        return True

    async def save_version(
        self,
        package_id: str,
        version: str,
        commit_sha: str,
        manifest: Dict[str, Any],
        components: Iterable[PackageComponent],
        validation_errors: List[str],
    ) -> Dict[str, Any]:
        status = "pending_review" if not validation_errors else "draft"
        now = now_iso()
        existing = await self.get_version(package_id, version)
        if existing and existing.get("status") in {"published", "superseded"}:
            return existing
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM official_package_components WHERE package_id=? AND version=?",
                    (package_id, version),
                )
                await conn.execute(
                    "DELETE FROM official_package_versions WHERE package_id=? AND version=? "
                    "AND status IN ('draft','pending_review','rejected')",
                    (package_id, version),
                )
                await conn.execute(
                    "INSERT INTO official_package_versions "
                    "(package_id,version,commit_sha,status,manifest,validation_errors,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        package_id, version, commit_sha, status,
                        _json(manifest), _json(validation_errors), now,
                    ),
                )
                rows = [
                    (
                        c.package_id, c.version, c.component_id, c.component_type,
                        c.name, c.description, c.source_path, c.content,
                        _json(c.files), _json(c.targets), c.content_hash,
                    )
                    for c in components
                ]
                if rows:
                    await conn.executemany(
                        "INSERT INTO official_package_components "
                        "(package_id,version,component_id,component_type,name,description,"
                        "source_path,content,files,targets,content_hash) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        rows,
                    )
        result = await self.get_version(package_id, version)
        assert result is not None
        return result

    async def list_versions(self, package_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM official_package_versions WHERE package_id=? "
                "ORDER BY created_at DESC",
                (package_id,),
            )
        return [self._version_row(row) for row in rows]

    async def get_version(
        self, package_id: str, version: str, *, include_content: bool = False
    ) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM official_package_versions WHERE package_id=? AND version=?",
                (package_id, version),
            )
            if not row:
                return None
            component_rows = await conn.fetchall(
                "SELECT * FROM official_package_components WHERE package_id=? AND version=? "
                "ORDER BY component_type,name",
                (package_id, version),
            )
        result = self._version_row(row)
        result["components"] = [
            self._component_row(item, include_content=include_content)
            for item in component_rows
        ]
        return result

    async def get_published(self, package_id: str, *, include_content: bool = False) -> Optional[Dict[str, Any]]:
        package = await self.get_package(package_id)
        if not package or not package.get("published_version"):
            return None
        version = await self.get_version(
            package_id, str(package["published_version"]), include_content=include_content
        )
        if version is None:
            return None
        return {**package, "version": version}

    async def review_version(
        self, package_id: str, version: str, *, publish: bool, reviewer: str
    ) -> Dict[str, Any]:
        current = await self.get_version(package_id, version)
        if not current:
            raise KeyError("version_not_found")
        if publish and current["validation_errors"]:
            raise ValueError("No se puede publicar una versión con errores de validación")
        now = now_iso()
        next_status = "published" if publish else "rejected"
        async with open_db() as conn:
            async with conn.transaction():
                if publish:
                    await conn.execute(
                        "UPDATE official_package_versions SET status='superseded' "
                        "WHERE package_id=? AND status='published' AND version<>?",
                        (package_id, version),
                    )
                await conn.execute(
                    "UPDATE official_package_versions SET status=?,reviewed_at=?,reviewed_by=? "
                    "WHERE package_id=? AND version=?",
                    (next_status, now, reviewer, package_id, version),
                )
                if publish:
                    await conn.execute(
                        "UPDATE official_packages SET published_version=?,updated_at=? WHERE id=?",
                        (version, now, package_id),
                    )
        result = await self.get_version(package_id, version)
        assert result is not None
        return result

    async def save_copy(self, data: Dict[str, Any]) -> Dict[str, Any]:
        copy_id = generate_id()
        now = now_iso()
        async with open_db() as conn:
            await conn.execute(
                "INSERT INTO official_package_copies "
                "(id,owner_id,package_id,source_version,component_id,resource_type,"
                "resource_id,name,content,source_content_hash,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    copy_id, data["owner_id"], data["package_id"], data["source_version"],
                    data["component_id"], data["resource_type"], data.get("resource_id"),
                    data["name"], data.get("content", ""), data["source_content_hash"],
                    now, now,
                ),
            )
            await conn.commit()
        return {
            "id": copy_id,
            "source_package_id": data["package_id"],
            "source_version": data["source_version"],
            "source_component_id": data["component_id"],
            "resource_type": data["resource_type"],
            "resource_id": data.get("resource_id"),
            "name": data["name"],
            "content_hash": data["source_content_hash"],
            "status": "Sin cambios",
            "created_at": now,
            "updated_at": now,
            "is_official": False,
        }

    async def list_copies(self, owner_id: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM official_package_copies WHERE owner_id=? "
                "ORDER BY created_at DESC",
                (owner_id,),
            )
        return [dict(row) for row in rows]

    @staticmethod
    def _version_row(row: Any) -> Dict[str, Any]:
        result = dict(row)
        result["manifest"] = _loads(result.get("manifest"), {})
        result["validation_errors"] = _loads(result.get("validation_errors"), [])
        return result

    @staticmethod
    def _component_row(row: Any, *, include_content: bool) -> Dict[str, Any]:
        result = dict(row)
        result["targets"] = _loads(result.get("targets"), [])
        result["files"] = _loads(result.get("files"), {}) if include_content else {}
        if not include_content:
            result.pop("content", None)
        return result
