"""GDPR service: exportación de datos del usuario (Artículo 20)."""
from __future__ import annotations

import io
import json
import zipfile

from app.sql import sql
from app.storage.db import open_db
from app.utils import flog

# Columnas que la base de datos guarda como texto JSON. Sin deserializarlas, el
# ZIP del artículo 20 entrega el agente como una cadena escapada dentro de otra:
# formalmente son los datos, pero no son portables a ningún sitio.
_JSON_COLUMNS = ("data", "definition", "labels", "agents", "payload")

# Un fichero del ZIP por consulta. El orden es el del ZIP y el nombre es el que
# ve el usuario; la consulta toma siempre el id de usuario como único parámetro.
# Los identificadores van enteros y literales: la guarda de secciones huérfanas
# los busca por su forma en el código, y uno compuesto con un f-string daría por
# muerta la consulta.
_RESOURCE_FILES = (
    ("agents.json", "queries/gdpr_export:agents"),
    ("skills.json", "queries/gdpr_export:skills"),
    ("prompts.json", "queries/gdpr_export:prompts"),
    ("tools.json", "queries/gdpr_export:tools"),
    ("workflows.json", "queries/gdpr_export:workflows"),
    ("knowledge_packs.json", "queries/gdpr_export:knowledge_packs"),
    ("memory.json", "queries/gdpr_export:memory_files"),
    ("stars.json", "queries/gdpr_export:stars"),
    ("sessions.json", "queries/gdpr_export:sessions"),
    ("agent_preferences.json", "queries/gdpr_export:agent_preferences"),
    ("personal_access_tokens.json", "queries/gdpr_export:personal_access_tokens"),
    ("subscriptions.json", "queries/gdpr_export:subscriptions"),
    ("workflow_runs.json", "queries/gdpr_export:workflow_runs"),
    ("workflow_run_events.json", "queries/gdpr_export:workflow_run_events"),
)


async def export_user_data(username: str) -> io.BytesIO:
    """Recopila todos los datos del usuario y devuelve un ZIP en un BytesIO."""
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        async with open_db() as conn:
            identity = await conn.fetchone(
                sql("queries/gdpr_export:user_exists"),
                (username, username),
            )
            user_id = identity["id"] if identity else username

            # 1. Perfil (sin password_hash)
            row = await conn.fetchone(
                sql("queries/gdpr_export:profile"),
                (user_id,),
            )
            if row:
                profile = dict(row)
                if profile.get("preferences"):
                    try:
                        profile["preferences"] = json.loads(profile["preferences"])
                    except (json.JSONDecodeError, TypeError) as exc:
                        flog.warning(
                            f"[gdpr] Preferencias no normalizadas para {username}: {exc}"
                        )
                zf.writestr("profile.json", json.dumps(profile, ensure_ascii=False, indent=2))

            # 2. Conexiones (con API keys cifradas — son datos del usuario)
            rows = await conn.fetchall(sql("queries/gdpr_export:connections"), (user_id,))
            connections = []
            for r in rows:
                c = dict(r)
                if isinstance(c.get("data"), str):
                    try:
                        c["data"] = json.loads(c["data"])
                    except (json.JSONDecodeError, TypeError) as exc:
                        flog.warning(
                            f"[gdpr] Conexión {c.get('id', '?')} no normalizada "
                            f"para {username}: {exc}"
                        )
                connections.append(c)
            zf.writestr("connections.json", json.dumps(connections, ensure_ascii=False, indent=2))

            # 3. Knowledge (documentos y URLs)
            rows = await conn.fetchall(sql("queries/gdpr_export:knowledge_items"), (user_id,))
            zf.writestr("knowledge.json", json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))

            # 4. Conversaciones + mensajes (un fichero por conversación)
            convs = await conn.fetchall(
                sql("queries/gdpr_export:conversations"),
                (user_id,),
            )
            for conv in convs:
                conv_dict = dict(conv)
                msgs = await conn.fetchall(
                    sql("queries/gdpr_export:messages_of_conversation"),
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
                sql("queries/gdpr_export:token_daily"),
                (user_id,),
            )
            zf.writestr(
                "token_usage.json",
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
            )

            # 6. Groups donde es miembro
            rows = await conn.fetchall(
                sql("queries/gdpr_export:groups_of_user"),
                (user_id,),
            )
            zf.writestr(
                "groups.json",
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
            )

            # 7. Cuentas externas (solo metadatos, sin claves)
            rows = await conn.fetchall(sql("queries/gdpr_export:accounts"), (user_id,))
            zf.writestr(
                "accounts.json",
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
            )

            # 8. Recursos propios: agentes, skills, prompts, tools, workflows,
            #    packs de knowledge, memoria de los agentes y favoritos.
            for filename, query in _RESOURCE_FILES:
                rows = await conn.fetchall(sql(query), (user_id,))
                zf.writestr(
                    filename,
                    json.dumps(
                        [_decode_row(r) for r in rows], ensure_ascii=False, indent=2
                    ),
                )

            # Las licencias propias y las que cuelgan de una suscripción del
            # usuario se solapan para el asiento del comprador. Se deduplican
            # por su clave primaria antes de escribir un único fichero.
            assignments = {}
            for query in (
                "queries/gdpr_export:subscription_license_assignments",
                "queries/gdpr_export:subscription_assignments_owned",
            ):
                rows = await conn.fetchall(sql(query), (user_id,))
                for row in rows:
                    item = dict(row)
                    assignments[(item["subscription_id"], item["username"])] = item
            zf.writestr(
                "subscription_license_assignments.json",
                json.dumps(
                    list(assignments.values()), ensure_ascii=False, indent=2
                ),
            )

            # 9. Seguimientos, en las dos direcciones: a quién sigue y quién le
            #    sigue son datos suyos por igual.
            rows = await conn.fetchall(
                sql("queries/gdpr_export:follows"), (user_id, user_id)
            )
            zf.writestr(
                "follows.json",
                    json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
                )

            artifact_rows = await conn.fetchall(
                sql("queries/gdpr_export:tool_artifacts"), (user_id,)
            )
            artifact_manifest = []
            for row in artifact_rows:
                item = dict(row)
                binary = bytes(item.pop("binary_data"))
                zf.writestr(f"tool_artifacts/{item['sha256']}.bin", binary)
                artifact_manifest.append(item)
            zf.writestr(
                "tool_artifacts/manifest.json",
                json.dumps(artifact_manifest, ensure_ascii=False, indent=2),
            )

    flog.ok(f"[gdpr] Exportación generada para {username}")
    buf.seek(0)
    return buf


def _decode_row(row) -> dict:
    """Fila a dict, deserializando las columnas que guardan JSON como texto."""
    item = dict(row)
    for column in _JSON_COLUMNS:
        value = item.get(column)
        if not isinstance(value, str):
            continue
        try:
            item[column] = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            # Se conserva el texto crudo: es el dato del usuario y el artículo 20
            # obliga a entregarlo, aunque no se pueda normalizar.
            flog.warning(
                f"[gdpr] Columna {column} no normalizada en "
                f"{item.get('id', '?')}: {exc}"
            )
    return item
