"""`OfficialSourceLLMAnalyzer`: pide al LLM el manifiesto de un repositorio.

Es el camino alternativo al detector por convenciones: cuando el repositorio no
sigue ninguna estructura reconocible, se le pregunta a un modelo.
"""


from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

import yaml

from app.config.content_languages import language_label
from app.config.providers import OPENAI_COMPAT_URLS
from app.models.agent import Agent
from app.models.official_source import PackageComponent
from app.services.chat import stream_chat
from app.services.official_source_importer import (
    unique_import_notices,
    validate_components,
)
from app.services.official_source_llm._filters import (
    _llm_path_priority,
    _llm_relevant_files,
    _pack_files,
    _slug,
)
from app.services.official_source_llm.models import (
    LLMManifestComponent,
    LLMManifestRelation,
    LLMRepositoryManifest,
    ProgressCallback,
)
from app.services.official_source_llm.prompts import (
    _extract_json,
    _repair_prompt,
    _system_prompt,
    _user_packet,
)
from app.storage.connection_storage import ConnectionStorage
from app.storage.skill_storage import ensure_origin_label

_SUPPORTED_CONNECTIONS = frozenset({*OPENAI_COMPAT_URLS, "claude", "ollama"})

class OfficialSourceLLMAnalyzer:
    def __init__(self, connections: Optional[ConnectionStorage] = None) -> None:
        self.connections = connections or ConnectionStorage()

    async def analyze(
        self,
        snapshot: Dict[str, Any],
        connection_id: str,
        deterministic_components: List[PackageComponent],
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        connection = await self.connections.get(connection_id, None)
        if not connection or not connection.get("is_active", True):
            raise ValueError("La conexión LLM seleccionada no está disponible")
        connection_type = str(connection.get("type") or "").lower()
        if connection_type not in _SUPPORTED_CONNECTIONS:
            raise ValueError("La conexión seleccionada no es una conexión LLM compatible")

        relevant_files = _llm_relevant_files(
            snapshot["files"], deterministic_components
        )
        packets = _pack_files(relevant_files)
        if progress:
            await progress(
                {
                    "stage": "llm_preparing",
                    "current": 0,
                    "total": len(packets),
                    "files": len(relevant_files),
                    "components": 0,
                }
            )
        catalog = [
            {
                "id": item.component_id,
                "type": item.component_type,
                "path": item.source_path,
                "name": item.name,
            }
            for item in deterministic_components
        ]
        partials: List[LLMRepositoryManifest] = []
        successful_chunks = 0
        reported_candidate_hashes: set[tuple[str, str]] = set()
        for index, packet in enumerate(packets, start=1):
            if progress:
                await progress(
                    {
                        "stage": "llm_analyzing",
                        "current": index,
                        "total": len(packets),
                        "files": len(packet),
                        "paths": [path for path, _content in packet[:5]],
                        "components": sum(
                            len(manifest.components) for manifest in partials
                        ),
                    }
                )
            packet_prompt = _user_packet(
                packet,
                index=index,
                total=len(packets),
                repository=str(snapshot["source"]["repository_url"]),
                commit=str(snapshot["commit_sha"]),
                catalog=catalog,
            )
            reply = await self._invoke(connection, packet_prompt)
            try:
                manifest = _extract_json(reply)
            except ValueError:
                if progress:
                    await progress(
                        {
                            "stage": "llm_retrying",
                            "current": index,
                            "total": len(packets),
                            "files": len(packet),
                            "components": sum(
                                len(manifest.components) for manifest in partials
                            ),
                        }
                    )
                repaired = await self._invoke(
                    connection, _repair_prompt(packet_prompt)
                )
                try:
                    manifest = _extract_json(repaired)
                except ValueError:
                    message = (
                        f"Fragmento {index}/{len(packets)} omitido: el modelo no "
                        "devolvió un manifiesto JSON válido tras dos intentos"
                    )
                    partials.append(
                        LLMRepositoryManifest(warnings=[message])
                    )
                    if progress:
                        await progress(
                            {
                                "stage": "llm_chunk_failed",
                                "current": index,
                                "total": len(packets),
                                "files": len(packet),
                                "components": sum(
                                    len(item.components) for item in partials
                                ),
                            }
                        )
                    continue
            packet_paths = {path for path, _content in packet}
            local_components = [
                component
                for component in manifest.components
                if component.source_path in packet_paths
            ]
            local_ids = {component.id for component in local_components}
            manifest = manifest.model_copy(
                update={
                    "components": local_components,
                    "relations": [
                        relation
                        for relation in manifest.relations
                        if relation.from_id in local_ids
                    ],
                }
            )
            partials.append(manifest)
            successful_chunks += 1
            if progress:
                detected = []
                for component in manifest.components:
                    if component.resource_type == "ignore":
                        continue
                    candidate_key = (
                        component.resource_type,
                        hashlib.sha256(
                            snapshot["files"][component.source_path].encode()
                        ).hexdigest(),
                    )
                    if candidate_key in reported_candidate_hashes:
                        continue
                    reported_candidate_hashes.add(candidate_key)
                    detected.append(
                        {
                            "name": component.name,
                            "resource_type": component.resource_type,
                            "source_path": component.source_path,
                        }
                    )
                await progress(
                    {
                        "stage": "llm_chunk_complete",
                        "current": index,
                        "total": len(packets),
                        "files": len(packet),
                        "components": sum(
                            len(manifest.components) for manifest in partials
                        ),
                        "chunk_components": len(detected),
                        "chunk_relations": len(manifest.relations),
                        "findings": detected[:8],
                    }
                )
        if progress:
            await progress(
                {
                    "stage": "validating",
                    "current": len(packets),
                    "total": len(packets),
                    "files": len(relevant_files),
                    "components": sum(len(item.components) for item in partials),
                }
            )
        if successful_chunks == 0:
            errors, security = validate_components(deterministic_components)
            return {
                "source": {
                    **snapshot["source"],
                    "import_mode": "llm",
                    "llm_connection_id": connection_id,
                    "analysis_manifest": {
                        "schema_version": "1",
                        "chunks": len(packets),
                        "fallback": "deterministic",
                        "components": len(deterministic_components),
                    },
                },
                "version": snapshot["version"],
                "commit_sha": snapshot["commit_sha"],
                "components": deterministic_components,
                "errors": errors,
                "security_warnings": unique_import_notices(
                    [
                        "El análisis LLM no produjo fragmentos válidos; se usó "
                        "el detector manual como respaldo",
                        *security,
                    ]
                ),
            }
        return self._materialize_manifest(snapshot, partials, connection_id)

    async def _invoke(self, connection: Dict[str, Any], prompt: str) -> str:
        agent = Agent(
            id="_official_repository_analyzer",
            name="Analizador de repositorios oficiales",
            model=str(connection.get("model") or ""),
            system_prompt=_system_prompt(),
            temperature=0,
            max_tokens=12_000,
            timeout=180,
        )
        reply = ""
        partial = ""
        error = ""
        async for chunk in stream_chat(
            agent,
            connection,
            [{"role": "user", "content": prompt}],
            None,
        ):
            if not chunk.startswith("data: "):
                continue
            try:
                event = json.loads(chunk[6:].strip())
            except json.JSONDecodeError:
                continue
            if event.get("type") == "token":
                partial += str(event.get("token") or "")
            elif event.get("type") == "done":
                reply = str(event.get("reply") or "")
            elif event.get("type") == "error":
                error = str(event.get("message") or "")
        if error:
            raise ValueError(f"La conexión LLM no pudo analizar el repositorio: {error}")
        if not (reply or partial):
            raise ValueError("La conexión LLM no devolvió respuesta")
        return reply or partial

    def _materialize_manifest(
        self,
        snapshot: Dict[str, Any],
        manifests: List[LLMRepositoryManifest],
        connection_id: str,
    ) -> Dict[str, Any]:
        files: Dict[str, str] = snapshot["files"]
        proposed: Dict[str, LLMManifestComponent] = {}
        warnings: List[str] = []
        ignored = 0
        relations: List[LLMManifestRelation] = []
        for manifest in manifests:
            warnings.extend(manifest.warnings)
            relations.extend(manifest.relations)
            for item in manifest.components:
                if item.source_path not in files:
                    warnings.append(f"El LLM propuso una ruta inexistente: {item.source_path}")
                    continue
                if item.resource_type == "ignore":
                    ignored += 1
                    continue
                proposed.setdefault(item.source_path, item)

        components: List[PackageComponent] = []
        used_ids: set[str] = set()
        aliases: Dict[str, str] = {}
        variants_by_hash: Dict[tuple[str, str], PackageComponent] = {}
        for path, item in sorted(
            proposed.items(), key=lambda pair: _llm_path_priority(pair[0])
        ):
            kind = "workflow" if item.resource_type == "orchestrator" else item.resource_type
            content = files[path]
            variant_key = (
                kind,
                hashlib.sha256(content.encode()).hexdigest(),
            )
            canonical = variants_by_hash.get(variant_key)
            if canonical is not None:
                canonical.variants.append(path)
                aliases[item.id] = canonical.component_id
                continue
            base_id = _slug(item.id)
            component_id = base_id
            number = 2
            while component_id in used_ids:
                component_id = f"{base_id}-{number}"
                number += 1
            used_ids.add(component_id)
            aliases[item.id] = component_id
            related = [value for value in item.related_paths if value in files and value != path]
            if kind == "workflow":
                try:
                    definition = yaml.safe_load(content) or {}
                except yaml.YAMLError:
                    definition = {}
                if not isinstance(definition, dict) or not {
                    "nodes",
                    "edges",
                }.issubset(definition):
                    warnings.append(
                        f"{path}: la orquestación propuesta no usa el formato "
                        "compatible de iAgentsHub y no se importará"
                    )
                    continue
            digest = content + "".join(value + files[value] for value in sorted(related))
            aliases = {
                "english": "en",
                "spanish": "es",
                "español": "es",
                "french": "fr",
                "german": "de",
                "portuguese": "pt",
                "italian": "it",
                "chinese": "zh",
                "japanese": "ja",
                "arabic": "ar",
            }
            language = aliases.get(item.language.strip().lower(), item.language)
            content_language = language_label(language) or ""
            labels = ensure_origin_label(item.labels, "official")
            if content_language:
                labels.append(content_language)
            inferred_tool_language = {
                ".py": "python",
                ".sh": "shell",
                ".cpp": "cpp",
            }.get(PurePosixPath(path).suffix.lower(), "")
            tool_language = (
                item.tool_language.strip().lower() or inferred_tool_language
            )
            if tool_language not in {"", "python", "shell", "cpp"}:
                tool_language = inferred_tool_language
            executable = kind == "tool"
            blocked = executable and (
                PurePosixPath(path).suffix.lower() not in {".py", ".sh", ".cpp"}
                or any(
                    marker in content.lower()
                    for marker in ("rm -rf", "invoke-expression")
                )
            )
            component = PackageComponent(
                source_id=str(snapshot["source"].get("id") or "draft"),
                component_id=component_id,
                component_type=kind,
                name=item.name,
                description=item.description,
                source_path=path,
                content_hash=hashlib.sha256(digest.encode()).hexdigest(),
                content=content,
                files={value: files[value] for value in related},
                labels=list(dict.fromkeys(labels)),
                    language=content_language,
                    tool_language=tool_language if kind == "tool" else "",
                detected_by="llm_manifest",
                executable=executable,
                security_blocked=blocked,
                security_review_required=executable,
            )
            components.append(component)
            variants_by_hash[variant_key] = component

        by_id = {item.component_id: item for item in components}
        for relation in relations:
            source_id = aliases.get(relation.from_id, relation.from_id)
            target_id = aliases.get(relation.to_id, relation.to_id)
            source = by_id.get(source_id)
            target = by_id.get(target_id)
            if not source or not target or source_id == target_id:
                continue
            source.relations.append(
                {
                    "target_id": target_id,
                    "relation_type": relation.relation_type,
                    "evidence_path": relation.evidence_path,
                    "evidence": relation.evidence,
                }
            )
            if relation.relation_type in {"uses", "depends_on"}:
                source.dependencies = list(dict.fromkeys([*source.dependencies, target_id]))

        errors, security = validate_components(components)
        source = {
            **snapshot["source"],
            "import_mode": "llm",
            "llm_connection_id": connection_id,
            "analysis_manifest": {
                "schema_version": "1",
                "chunks": len(manifests),
                "ignored": ignored,
                "components": len(components),
                "relations": sum(len(item.relations) for item in components),
            },
        }
        return {
            "source": source,
            "version": snapshot["version"],
            "commit_sha": snapshot["commit_sha"],
            "components": components,
            "errors": errors,
            "security_warnings": unique_import_notices([*warnings, *security]),
        }
