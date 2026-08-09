"""Sincronización de recursos desde una instancia remota de iAgents Hub."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any, Dict, Set

from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.errors import APIError
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.skill_storage import SkillStorage
from app.utils.safe_http import safe_urlopen

_storage = ConnectionStorage()
_agent_storage = AgentStorage(AGENTS_DIR)
_skill_storage = SkillStorage(SKILLS_DIR)
_know_storage = KnowledgeStorage()


async def _get_remote_json(base_url: str, path: str, headers: dict[str, str]) -> Any:
    def _read() -> Any:
        request = urllib.request.Request(f"{base_url}{path}", headers=headers)
        with safe_urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(_read)


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
    except Exception as e:
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

    # ── 1. Conexiones (solo estructura, sin credenciales) ──────────────
    try:
        remote_conns = await _get("/api/connections")

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
    except Exception as e:
        result["errors"].append(f"conexiones: {e}")

    # ── 2. Agentes ────────────────────────────────────────────────────
    try:
        summaries = await _get("/api/agents?scope=private")
        local_agents = await _agent_storage.list("private")
        local_a_names: Set[str] = {a["name"] for a in local_agents}
        by_src = {a.get("_hub_source"): a for a in local_agents if a.get("_hub_source")}

        for summary in summaries:
            ra_id = summary.get("id", "")
            src_key = f"{conn_id}:{ra_id}"
            try:
                ra = await _get(f"/api/agents/{ra_id}")
            except Exception:
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
    except Exception as e:
        result["errors"].append(f"agentes: {e}")

    # ── 3. Skills ────────────────────────────────────────────────────
    try:
        remote_skills = await _get("/api/skills?scope=private")
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
    except Exception as e:
        result["errors"].append(f"skills: {e}")

    # ── 4. Conocimiento ───────────────────────────────────────────────
    try:
        remote_know = await _get("/api/knowledge")

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
            except Exception as exc:
                result["errors"].append(f"conocimiento {rk_id}: {exc}")
        result["knowledge"] += know_created
        result["updated"] += know_updated
    except Exception as e:
        result["errors"].append(f"conocimiento: {e}")

    result["ok"] = not result["errors"]
    return result
