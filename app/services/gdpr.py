"""GDPR service: exportación de datos del usuario (Artículo 20)."""
from __future__ import annotations

import io
import json
import zipfile

from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.storage.db import open_db
from app.utils import flog


async def export_user_data(username: str) -> io.BytesIO:
    """Recopila todos los datos del usuario y devuelve un ZIP en un BytesIO."""
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        async with open_db() as conn:
            identity = await conn.fetchone(
                "SELECT id FROM users WHERE id = ? OR username = ?",
                (username, username),
            )
            user_id = identity["id"] if identity else username

            # 1. Perfil (sin password_hash)
            row = await conn.fetchone(
                "SELECT username, email, display_name, birth_date, gender, country, "
                "phone, role, created_at, preferences FROM users WHERE id = ?",
                (user_id,),
            )
            if row:
                profile = dict(row)
                if profile.get("preferences"):
                    try:
                        profile["preferences"] = json.loads(profile["preferences"])
                    except Exception:
                        pass
                zf.writestr("profile.json", json.dumps(profile, ensure_ascii=False, indent=2))

            # 2. Conexiones (con API keys cifradas — son datos del usuario)
            rows = await conn.fetchall("SELECT * FROM connections WHERE owner_id = ?", (user_id,))
            connections = []
            for r in rows:
                c = dict(r)
                if isinstance(c.get("data"), str):
                    try:
                        c["data"] = json.loads(c["data"])
                    except Exception:
                        pass
                connections.append(c)
            zf.writestr("connections.json", json.dumps(connections, ensure_ascii=False, indent=2))

            # 3. Knowledge (documentos y URLs)
            rows = await conn.fetchall("SELECT * FROM knowledge_items WHERE owner_id = ?", (user_id,))
            zf.writestr("knowledge.json", json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))

            # 4. Conversaciones + mensajes (un fichero por conversación)
            convs = await conn.fetchall(
                "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            )
            for conv in convs:
                conv_dict = dict(conv)
                msgs = await conn.fetchall(
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                    (conv_dict["id"],),
                )
                conv_dict["messages"] = [dict(m) for m in msgs]
                safe_title = (conv_dict.get("title") or conv_dict["id"])[:40].replace("/", "_").replace("\\", "_")
                zf.writestr(
                    f"conversations/{conv_dict['id']}_{safe_title}.json",
                    json.dumps(conv_dict, ensure_ascii=False, indent=2),
                )

            # 5. Uso de tokens por día
            rows = await conn.fetchall(
                "SELECT day, tokens FROM token_daily WHERE owner_id = ? ORDER BY day DESC",
                (user_id,),
            )
            zf.writestr(
                "token_usage.json",
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
            )

            # 6. Groups donde es miembro
            rows = await conn.fetchall(
                "SELECT w.id, w.name, w.created_at, wm.role, wm.joined_at "
                "FROM groups w JOIN group_members wm ON w.id = wm.group_id WHERE wm.username = ?",
                (user_id,),
            )
            zf.writestr(
                "groups.json",
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
            )

            # 7. Cuentas externas (solo metadatos, sin claves)
            rows = await conn.fetchall("SELECT provider, linked_at FROM accounts WHERE owner_id = ?", (user_id,))
            zf.writestr(
                "accounts.json",
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
            )

        # 8. Agentes (ficheros)
        agents = _collect_file_owned(AGENTS_DIR, user_id)
        zf.writestr("agents.json", json.dumps(agents, ensure_ascii=False, indent=2))

        # 9. Skills (ficheros)
        skills = _collect_file_owned(SKILLS_DIR, user_id)
        zf.writestr("skills.json", json.dumps(skills, ensure_ascii=False, indent=2))

    flog.ok(f"[gdpr] Exportación generada para {username}")
    buf.seek(0)
    return buf


def _collect_file_owned(base_dir, username: str) -> list:
    items = []
    for scope in ("private", "public"):
        scope_dir = base_dir / scope
        if not scope_dir.exists():
            continue
        for item_dir in scope_dir.iterdir():
            cfg = item_dir / "config.json"
            if not cfg.exists():
                continue
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                if data.get("owner_id") == username:
                    items.append(data)
            except Exception:
                pass
    return items
