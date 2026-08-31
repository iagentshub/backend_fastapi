"""E2E fixtures: real uvicorn server on a random TCP port."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).parent.parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/api/v2/skills", timeout=0.5)
            return
        except Exception:  # noqa: BLE001
            # Sondeo de arranque: cualquier fallo significa 'aún no listo'.
            time.sleep(0.15)
    raise RuntimeError(f"Server at {base_url} did not become ready in {timeout}s")


@pytest.fixture(scope="module")
def live_server():
    """Start a real uvicorn server in a subprocess with an isolated data dir."""
    data_dir = Path(tempfile.mkdtemp(prefix="gaia_e2e_"))
    for d in ("agents", "skills", "memory", "connections"):
        (data_dir / d).mkdir()
    (data_dir / "connections" / "connections.json").write_text("[]", encoding="utf-8")
    (data_dir / "settings.json").write_text(
        json.dumps({"jwt_secret": "e2e-secret-key"}), encoding="utf-8"
    )

    admin_email = "e2eadmin@example.com"
    port = _free_port()
    env = {
        **os.environ,
        "GAIA_DATA_DIR": str(data_dir),
        "DATABASE_URL": "",  # force SQLite
        "GAIA_ADMIN_EMAIL": admin_email,  # server creates/promotes this user on startup
        "PYTHONPATH": str(BACKEND_DIR),
    }

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base_url)
    except RuntimeError:
        proc.terminate()
        raise

    yield base_url, data_dir, admin_email

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="module")
def auth_client(live_server):
    """httpx Client pre-authenticated as admin against the live server.

    The server writes the admin password to {DATA_DIR}/.admin_pass on first boot.
    """
    base, data_dir, admin_email = live_server

    # Read the admin password written by ensure_admin_user() on startup
    pass_file = data_dir / ".admin_pass"
    deadline = time.monotonic() + 5.0
    while not pass_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pass_file.exists(), (
        f".admin_pass not found in {data_dir} — server may not have started correctly"
    )
    password = pass_file.read_text(encoding="utf-8").strip()

    with httpx.Client(base_url=base, follow_redirects=True) as client:
        r = client.post(
            "/api/auth/login", json={"email": admin_email, "password": password}
        )
        assert r.status_code == 200, f"Login failed: {r.text}"
        # CsrfMiddleware exige X-CSRF-Token en toda mutación autenticada por
        # cookie; React y Flutter la leen de `ga_csrf` (no HttpOnly) y la
        # reenvían así, así que este cliente hace lo mismo una sola vez.
        csrf = client.cookies.get("ga_csrf")
        assert csrf, "login no emitió la cookie ga_csrf"
        client.headers["X-CSRF-Token"] = csrf
        yield client


# ── Shared helpers ────────────────────────────────────────────────────────────


def create_skill(
    client: httpx.Client, name: str, description: str, content: str
) -> dict:
    r = client.post(
        "/api/skills/private",
        json={
            "name": name,
            "description": description,
            "content": content,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def create_agent(client: httpx.Client, extra: dict | None = None) -> dict:
    payload = {
        "name": "E2E Agent",
        "description": "An agent for E2E testing",
        "system_prompt": "You are a test assistant.",
        "model": "gpt-4o",
        "temperature": 0.5,
        **(extra or {}),
    }
    r = client.post("/api/agents", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def zip_names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.namelist()


def zip_read(content: bytes, path: str) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.read(path).decode()
