<div align="center">
  <a href="docs/en/index.md">🇬🇧 English</a> &nbsp;·&nbsp;
  <a href="docs/es/index.md">🇪🇸 Español</a>
</div>

<br>

<h1 align="center">iAgentsHub — Backend</h1>

<p align="center">The service that powers agents, skills, memory, and AI provider connections.</p>

---

## Quick deploy

**Docker**

```bash
docker build -t iagentshub-backend .
docker run -p 8765:8765 \
  -e GAIA_AGENTS_SECRET=your-secret \
  -v /path/to/data:/data \
  iagentshub-backend
```

**Python**

```bash
pip install -r requirements.txt
GAIA_AGENTS_SECRET=your-secret python3 main.py
```

API available at `http://localhost:8765`.

> For the full stack (backend + frontend + skills), deploy from [iAgentsHub](https://github.com/iagentshub/iAgents).

---

| | |
|---|---|
| 🇪🇸 Español | [docs/es/index.md](docs/es/index.md) |
| 🇬🇧 English | [docs/en/index.md](docs/en/index.md) |

---

[MIT](LICENSE)
