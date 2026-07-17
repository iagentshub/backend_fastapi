<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/config.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Configuration

All configuration is done through environment variables. These are set by the deployment orchestrator ([iAgentsHub](https://github.com/iagentshub/iAgents)) and do not require modifying any code files.

---

## What can be configured

| Setting | Description |
|---|---|
| Session secret | Key used to sign session tokens. Required in production. |
| Data directory | Path where agents, connections, skills, and memory are stored. |
| Port and host | Where the server listens. |
| Allowed origins | Domains permitted to access the API. |
| Session duration | How long in hours a session remains active. |
## Session secret

Must be generated randomly before the first startup and not changed while sessions are active. If not configured, the system uses a value stored in the platform data — acceptable in development, not in production.

This secret also serves as the master key for encrypting API keys stored in the database (derived via PBKDF2-SHA256). **Changing it after API keys have been saved will make those keys unreadable** — users will need to re-enter their credentials.
