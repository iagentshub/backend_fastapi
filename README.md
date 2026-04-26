<div align="center">
  <a href="docs/en/index.md">🇬🇧 Read in English</a> &nbsp;·&nbsp;
  <a href="docs/es/index.md">🇪🇸 Ver en Español</a>
</div>

<br>

<div align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/license-MIT-22c55e?style=flat-square" alt="License">
</div>

<br>

<h1 align="center">iAgentsHub — Backend</h1>

<p align="center">REST API for iAgentsHub. Manages agents, skills, memory, connections to AI providers, and authentication. Built with FastAPI and designed to run as a stateless service backed by a mounted data directory.</p>

---

## Features

| Feature | Details |
|---|---|
| **Auth** | JWT via HTTP-only cookies, multi-user registration, rate limiting |
| **Agents** | Create, configure, and chat with agents via SSE streaming |
| **Skills** | Load and serve skill definitions from the data directory |
| **Memory** | Per-agent persistent memory stored as Markdown files |
| **Connections** | Manage API keys for multiple AI providers |
| **Providers** | Anthropic, OpenAI, Google Gemini, Grok (xAI), Qwen (Alibaba), Ollama (local) |
| **Admin** | User management endpoints restricted to the admin role |

---

## Quick Start

```bash
git clone https://github.com/iagentshub/backend.git
cd backend
pip install -r requirements.txt
python3 main.py
```

API available at `http://localhost:8765`. Default credentials: `admin` / `admin`.

For Docker or production deployment, see the [full documentation](docs/en/index.md).

---

## Documentation

| | English | Español |
|---|---|---|
| Full docs | [docs/en/index.md](docs/en/index.md) | [docs/es/index.md](docs/es/index.md) |

---

## License

[MIT](LICENSE)