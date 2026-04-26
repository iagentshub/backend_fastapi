<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/deployment.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Deployment

---

## Local development

```bash
git clone https://github.com/iagentshub/backend.git
cd backend
pip install -r requirements.txt

# Optional: point to a custom data directory
export GAIA_DATA_DIR=/path/to/data

python3 main.py
```

API available at `http://localhost:8765`. Auto-reload is enabled by default (`GAIA_RELOAD=true`).

Default credentials: `admin` / `admin` — change them at `data/settings.json` after the first login.

---

## Docker

```bash
docker build -t iagentshub-backend .
docker run -p 8765:8765 \
  -e GAIA_RELOAD=false \
  -e GAIA_AGENTS_SECRET=your-secret \
  -v /path/to/data:/data \
  iagentshub-backend
```

---

## Via iAgentsHub (recommended)

This service is designed to be deployed as part of the [iagentshub](https://github.com/iagentshub/iagentshub) stack, which handles data initialization, skills provisioning, and reverse proxying through a single command:

```bash
docker compose up --build
```

---

## Production checklist

- Set `GAIA_RELOAD=false`
- Set `GAIA_AGENTS_SECRET` to a strong random value (e.g. `openssl rand -hex 32`)
- Set `GAIA_CORS_ORIGINS` to the exact frontend origin
- Mount `data/` as a persistent volume
- Change the default admin password
