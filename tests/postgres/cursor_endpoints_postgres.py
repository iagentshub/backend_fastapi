"""Smoke test aislado de los cuatro listados cursor sobre PostgreSQL real.

No lo descubre la suite SQLite: el workflow PostgreSQL lo ejecuta como script
para que ``tests/conftest.py`` no fuerce ``DATABASE_URL=''`` durante colección.
La base temporal se crea y elimina dentro del propio test.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _database_url(dsn: str, database: str) -> str:
    parsed = urlsplit(dsn)
    return urlunsplit(parsed._replace(path=f"/{database}"))


async def _create_database(admin_dsn: str, database: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


async def _drop_database(admin_dsn: str, database: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    finally:
        await conn.close()


def main() -> None:
    source_dsn = os.environ.get("GAIA_TEST_PG_DSN", "").strip()
    if not source_dsn:
        raise SystemExit("GAIA_TEST_PG_DSN es obligatorio")

    database = f"cursor_api_{uuid4().hex}"
    admin_dsn = _database_url(source_dsn, "postgres")
    test_dsn = _database_url(source_dsn, database)
    data_dir = Path(tempfile.mkdtemp(prefix="gaia_cursor_pg_"))
    (data_dir / "settings.json").write_text(
        json.dumps(
            {
                "jwt_secret": "postgres-cursor-test-secret-minimum-32-bytes",
                "billing_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    asyncio.run(_create_database(admin_dsn, database))
    try:
        os.environ["DATABASE_URL"] = test_dsn
        os.environ["GAIA_DATA_DIR"] = str(data_dir)
        os.environ["GAIA_ADMIN_USERNAME"] = "cursoradmin"
        os.environ["GAIA_ADMIN_EMAIL"] = "cursoradmin@example.test"
        os.environ["GAIA_BCRYPT_ROUNDS"] = "4"
        os.environ["GAIA_EMAIL_VERIFY"] = "false"
        os.environ.pop("GAIA_SCHEMA_MIGRATED", None)

        # Las importaciones deben ocurrir después de fijar DATABASE_URL: db.py
        # selecciona el motor al importarse.
        from fastapi.testclient import TestClient

        from app.api.app import create_app
        from app.auth.auth import create_token
        from app.utils import flog

        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            token = create_token("cursoradmin")
            client.cookies.set("ga_token", token)

            endpoints = (
                "/api/v2/feed",
                "/api/v2/connections",
                "/api/v2/admin/explore",
                "/api/v2/admin/metadata/tables/users/data",
            )
            for endpoint in endpoints:
                response = client.get(
                    endpoint,
                    params={"limit": 2, "include_total": "true"},
                )
                assert response.status_code == 200, (endpoint, response.text)
                body = response.json()
                assert "items" in body, endpoint
                assert body["page"]["limit"] == 2, endpoint
                assert isinstance(body["page"]["has_more"], bool), endpoint
                assert body["page"]["total"] is not None, endpoint

            filtered = client.get(
                "/api/v2/admin/explore",
                params={
                    "type": "user",
                    "role": "admin",
                    "active": "true",
                    "verified": "true",
                    "include_counts": "true",
                },
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["counts"]["user"] == 1

        # El logger PostgreSQL usa un hilo propio. Vaciarlo antes de eliminar la
        # base evita que su cierre atexit intente persistir el último mensaje en
        # una base temporal que ya no existe.
        flog.flush()
        print("cursor-postgres-endpoints: 4/4 OK")
    finally:
        asyncio.run(_drop_database(admin_dsn, database))
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
