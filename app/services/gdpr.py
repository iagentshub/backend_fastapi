"""GDPR service: exportación de datos del usuario (Artículo 20)."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from app.config.data import AGENTS_DIR, DB_FILE, SKILLS_DIR
from app.storage.db import PH, close_db, open_db
from app.utils import flog


def export_user_data(username: str) -> io.BytesIO:
    """Recopila todos los datos del usuario y devuelve un ZIP en un BytesIO."""
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        conn = open_db(DB_FILE)
        try:
            cur = conn.cursor()

            # 1. Perfil (sin password_hash)
            cur.execute(
                "SELECT username, email, display_name, birth_date, gender, country, "
                f"phone, role, created_at, preferences FROM users WHERE username = {PH}",
                (username,),
            )
            row = cur.fetchone()
            if row:
                profile = dict(row)
                if profile.get("preferences"):
                    try:
                        profile["preferences"] = json.loads(profile["preferences"])
                    except Exception:
                        pass
                zf.writestr("profile.json", json.dumps(profile, ensure_ascii=False, indent=2))

            # 2. Conexiones (con API keys cifradas — son datos del usuario)
            cur.execute(f"SELECT * FROM connections WHERE owner_id = {PH}", (username,))
            connections = []
            for r in cur.fetchall():
                c = dict(r)
                if isinstance(c.get("data"), str):
                    try:
                        c["data"] = json.loads(c["data"])
                    except Exception:
                        pass
                connections.append(c)
            zf.writestr("connections.json", json.dumps(connections, ensure_ascii=False, indent=2))

            # 3. Knowledge (documentos y URLs)
            cur.execute(f"SELECT * FROM knowledge_items WHERE owner_id = {PH}", (username,))
            knowledge = [dict(r) for r in cur.fetchall()]
            zf.writestr("knowledge.json", json.dumps(knowledge, ensure_ascii=False, indent=2))

            # 4. Conversaciones + mensajes (un fichero por conversación)
            cur.execute(
                f"SELECT * FROM conversations WHERE user_id = {PH} ORDER BY updated_at DESC",
                (username,),
            )
            conversations = [dict(r) for r in cur.fetchall()]
            for conv in conversations:
                cur.execute(
                    f"SELECT * FROM messages WHERE conversation_id = {PH} ORDER BY created_at ASC",
                    (conv["id"],),
                )
                conv["messages"] = [dict(m) for m in cur.fetchall()]
                safe_title = (conv.get("title") or conv["id"])[:40].replace("/", "_").replace("\\", "_")
                zf.writestr(
                    f"conversations/{conv['id']}_{safe_title}.json",
                    json.dumps(conv, ensure_ascii=False, indent=2),
                )

            # 5. Uso de tokens por día
            cur.execute(
                f"SELECT day, tokens FROM token_daily WHERE owner_id = {PH} ORDER BY day DESC",
                (username,),
            )
            zf.writestr(
                "token_usage.json",
                json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2),
            )

            # 6. Workspaces donde es miembro
            cur.execute(
                "SELECT w.id, w.name, w.created_at, wm.role, wm.joined_at "
                f"FROM workspaces w JOIN workspace_members wm ON w.id = wm.workspace_id WHERE wm.username = {PH}",
                (username,),
            )
            zf.writestr(
                "workspaces.json",
                json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2),
            )

            # 7. Cuentas externas (solo metadatos, sin claves)
            cur.execute(f"SELECT provider, linked_at FROM accounts WHERE owner_id = {PH}", (username,))
            zf.writestr(
                "accounts.json",
                json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2),
            )

        finally:
            close_db(conn)

        # 8. Agentes (ficheros)
        agents = _collect_file_owned(AGENTS_DIR, username)
        zf.writestr("agents.json", json.dumps(agents, ensure_ascii=False, indent=2))

        # 9. Skills (ficheros)
        skills = _collect_file_owned(SKILLS_DIR, username)
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
