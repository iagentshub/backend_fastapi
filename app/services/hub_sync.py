"""Sincronización de recursos desde una instancia remota de iAgents Hub."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.errors import APIError
from app.services.credentials import assert_readable
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.skill_storage import SkillStorage
from app.utils.safe_http import safe_urlopen

_storage = ConnectionStorage()
_agent_storage = AgentStorage(AGENTS_DIR)
_skill_storage = SkillStorage(SKILLS_DIR)
_know_storage = KnowledgeStorage()
_REMOTE_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class _RemotePage:
    payload: Any
    has_more: bool
    next_cursor: str | None


async def _get_remote_json(base_url: str, path: str, headers: dict[str, str]) -> Any:
    def _read() -> Any:
        request = urllib.request.Request(f"{base_url}{path}", headers=headers)
        with safe_urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(_read)


async def _get_remote_cursor_page(
    base_url: str, path: str, headers: dict[str, str]
) -> _RemotePage:
    decoded = await _get_remote_json(base_url, path, headers)
    if not isinstance(decoded, dict):
        raise ValueError("El hub remoto no devolvió el envelope cursor v2")
    payload = decoded.get("items")
    page = decoded.get("page")
    if not isinstance(payload, list) or not isinstance(page, dict):
        raise ValueError("El hub remoto no devolvió el envelope cursor v2")
    has_more = page.get("has_more")
    if not isinstance(has_more, bool):
        raise ValueError("El hub remoto devolvió metadatos cursor no válidos")
    next_cursor = page.get("next_cursor")
    if has_more and not isinstance(next_cursor, str):
        raise ValueError("El hub remoto indicó más resultados sin cursor")
    return _RemotePage(
        payload=payload,
        has_more=has_more,
        next_cursor=next_cursor if isinstance(next_cursor, str) else None,
    )


async def _get_all_remote_cursor_pages(
    base_url: str, path: str, headers: dict[str, str]
) -> list[dict[str, Any]]:
    """Consume un listado remoto cursor-only sin interpretar sus cursores."""

    parts = urlsplit(path)
    base_query = dict(parse_qsl(parts.query, keep_blank_values=True))
    items: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    while True:
        query = {**base_query, "limit": str(_REMOTE_PAGE_SIZE)}
        if cursor is not None:
            query["cursor"] = cursor
        page_path = urlunsplit(("", "", parts.path, urlencode(query), ""))
        remote_page = await _get_remote_cursor_page(base_url, page_path, headers)
        if not isinstance(remote_page.payload, list):
            raise ValueError("El hub remoto no devolvió un listado")
        page = [item for item in remote_page.payload if isinstance(item, dict)]
        items.extend(page)
        if not remote_page.has_more:
            return items
        cursor = remote_page.next_cursor
        if not cursor:
            raise ValueError("El hub remoto indicó más resultados sin cursor")
        if cursor in seen_cursors:
            raise ValueError("El hub remoto repitió el cursor de paginación")
        seen_cursors.add(cursor)


def _safe_name(name: str, taken: Set[str], hub_label: str) -> str:
    if name not in taken:
        return name
    candidate = f"{name} ({hub_label})"
    if candidate not in taken:
        return candidate
    index = 2
    while f"{candidate} {index}" in taken:
        index += 1
    return f"{candidate} {index}"


async def run_hub_sync(
    conn_id: str, conn: Dict[str, Any], owner: str
) -> Dict[str, Any]:
    """Lógica de sincronización con un hub remoto, reutilizable tanto desde la
    ruta `/{conn_id}/hub-sync` (conexión tipo iagentshub) como desde
    `accounts.sync_account` (cuenta de proveedor tipo iagentshub)."""
    assert_readable(conn)
    url = (conn.get("url") or "").rstrip("/")
    username = conn.get("username") or ""
    password = conn.get("api_key") or ""
    hub_label = conn.get("name") or "Hub"

    # C1: validar URL contra SSRF antes de hacer cualquier petición HTTP
    from app.config.security import assert_safe_url as _assert_safe_hub_url

    try:
        _assert_safe_hub_url(url)
    except ValueError as _ssrf_err:
        raise APIError(400, "unsafe_url", str(_ssrf_err))

    from app.connections.iagentshub import _login

    try:
        token = _login(url, username, password)
    except Exception as e:  # noqa: BLE001
        # Ancho a propósito: `_login` habla con un hub remoto arbitrario y puede
        # fallar por red, TLS, JSON o credenciales. No es un silencio — el
        # motivo se traduce a un 502 con `reason` para el usuario.
        raise APIError(
            502,
            "hub_auth_error",
            f"Error de autenticación: {e}",
            extra={"reason": str(e)},
        )

    headers = {"Cookie": f"ga_token={token}"}
    result: Dict[str, Any] = {
        "agents": 0,
        "skills": 0,
        "knowledge": 0,
        "connections": 0,
        "updated": 0,
        "errors": [],
    }

    async def _get(path: str) -> Any:
        return await _get_remote_json(url, path, headers)

    async def _get_all_cursor(path: str) -> list[dict[str, Any]]:
        return await _get_all_remote_cursor_pages(url, path, headers)

    # ── 1. Conexiones (solo estructura, sin credenciales) ──────────────
    try:
        remote_conns = await _get_all_cursor("/api/v2/connections")

        local_conns = await _storage.list(owner)
        local_conn_names: Set[str] = {c["name"] for c in local_conns}
        by_src = {c.get("_hub_source"): c for c in local_conns if c.get("_hub_source")}
        conns_created = 0
        conns_updated = 0
        for rc in remote_conns:
            src_key = f"{conn_id}:{rc.get('id', '')}"
            data: Dict[str, Any] = {
                "type": rc.get("type", ""),
                "model": rc.get("model") or "",
                "url": rc.get("url") or "",
                "host": rc.get("host") or "",
                "_imported": True,
                "_hub_source": src_key,
            }
            if src_key in by_src:
                data["id"] = by_src[src_key]["id"]
                data["name"] = by_src[src_key]["name"]
                await _storage.save(data, owner_id=owner)
                conns_updated += 1
            else:
                name = _safe_name(
                    rc.get("name", "Conexión"), local_conn_names, hub_label
                )
                data["name"] = name
                local_conn_names.add(name)
                await _storage.save(data, owner_id=owner)
                conns_created += 1
        result["connections"] += conns_created
        result["updated"] += conns_updated
    except Exception as e:  # noqa: BLE001
        # Ancho a propósito: cada bloque sincroniza un tipo de recurso y el
        # fallo de uno no debe impedir los otros tres. No es un silencio —
        # el motivo se acumula en result["errors"], que viaja al usuario.
        result["errors"].append(f"conexiones: {e}")

    # ── 2. Agentes ────────────────────────────────────────────────────
    try:
        summaries = await _get_all_cursor("/api/v2/agents?scope=private")
        local_agents = await _agent_storage.list("private")
        local_a_names: Set[str] = {a["name"] for a in local_agents}
        by_src = {a.get("_hub_source"): a for a in local_agents if a.get("_hub_source")}

        for summary in summaries:
            ra_id = summary.get("id", "")
            src_key = f"{conn_id}:{ra_id}"
            try:
                ra = await _get(f"/api/agents/{ra_id}")
            except Exception as exc:  # noqa: BLE001
                # Un agente que no se puede traer no debe abortar la sincronía
                # de los demás, pero sí tiene que aparecer en el parte: sin
                # esto, el usuario ve "3 agentes sincronizados" de 5 y no hay
                # nada que explique los otros dos.
                result["errors"].append(f"agente {ra_id}: {exc}")
                continue

            data = {
                k: v
                for k, v in ra.items()
                if k
                not in (
                    "id",
                    "owner_id",
                    "created_at",
                    "updated_at",
                    "tokens_in",
                    "tokens_out",
                )
            }
            data["_hub_source"] = src_key
            data["_hub_conn_id"] = conn_id

            if src_key in by_src:
                data["id"] = by_src[src_key]["id"]
                await _agent_storage.save(data, "private", owner_id=owner)
                result["updated"] += 1
            else:
                name = _safe_name(ra.get("name", "Agente"), local_a_names, hub_label)
                data["name"] = name
                local_a_names.add(name)
                await _agent_storage.save(data, "private", owner_id=owner)
                result["agents"] += 1
    except Exception as e:  # noqa: BLE001
        # Ancho a propósito: cada bloque sincroniza un tipo de recurso y el
        # fallo de uno no debe impedir los otros tres. No es un silencio —
        # el motivo se acumula en result["errors"], que viaja al usuario.
        result["errors"].append(f"agentes: {e}")

    # ── 3. Skills ────────────────────────────────────────────────────
    try:
        remote_skills = await _get_all_cursor("/api/v2/skills?scope=private")
        local_skills = await _skill_storage.list("private")
        local_s_names: Set[str] = {s["name"] for s in local_skills}
        by_src = {s.get("_hub_source"): s for s in local_skills if s.get("_hub_source")}

        for rs in remote_skills:
            rs_id = rs.get("id", "")
            src_key = f"{conn_id}:{rs_id}"
            data = {
                k: v
                for k, v in rs.items()
                if k not in ("id", "owner_id", "created_at", "updated_at")
            }
            data["_hub_source"] = src_key
            data["_hub_conn_id"] = conn_id

            if src_key in by_src:
                data["id"] = by_src[src_key]["id"]
                await _skill_storage.save("private", data, owner_id=owner)
                result["updated"] += 1
            else:
                name = _safe_name(rs.get("name", "Skill"), local_s_names, hub_label)
                data["name"] = name
                local_s_names.add(name)
                await _skill_storage.save("private", data, owner_id=owner)
                result["skills"] += 1
    except Exception as e:  # noqa: BLE001
        # Ancho a propósito: cada bloque sincroniza un tipo de recurso y el
        # fallo de uno no debe impedir los otros tres. No es un silencio —
        # el motivo se acumula en result["errors"], que viaja al usuario.
        result["errors"].append(f"skills: {e}")

    # ── 4. Conocimiento ───────────────────────────────────────────────
    try:
        remote_know = await _get_all_cursor("/api/v2/knowledge")

        local_know = await _know_storage.list(owner)
        local_k_titles: Set[str] = {k["title"] for k in local_know}
        synced_srcs = {
            k.get("source", "")
            for k in local_know
            if k.get("source", "").startswith(f"hub:{conn_id}:")
        }
        know_created = 0
        know_updated = 0
        for rk in remote_know:
            rk_id = rk.get("id", "")
            src_tag = f"hub:{conn_id}:{rk_id}"
            if src_tag in synced_srcs:
                know_updated += 1
                continue
            title = _safe_name(rk.get("title", ""), local_k_titles, hub_label)
            local_k_titles.add(title)
            try:
                await _know_storage.save(
                    type=rk.get("type", "url"),
                    title=title,
                    source=src_tag,
                    content=rk.get("content", ""),
                    owner_id=owner,
                )
                know_created += 1
            except Exception as exc:  # noqa: BLE001 — el motivo va a result["errors"], que se devuelve al usuario;
            # ancho a propósito: el fallo de un recurso no debe parar los demás.
                result["errors"].append(f"conocimiento {rk_id}: {exc}")
        result["knowledge"] += know_created
        result["updated"] += know_updated
    except Exception as e:  # noqa: BLE001
        # Ancho a propósito: cada bloque sincroniza un tipo de recurso y el
        # fallo de uno no debe impedir los otros tres. No es un silencio —
        # el motivo se acumula en result["errors"], que viaja al usuario.
        result["errors"].append(f"conocimiento: {e}")

    result["ok"] = not result["errors"]
    return result
