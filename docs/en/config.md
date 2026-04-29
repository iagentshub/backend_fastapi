<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/config.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Configuration

All configuration is done through environment variables. These are set by the deployment orchestrator ([iAgentsHub](https://github.com/iagentshub/iagentshub)) and do not require modifying any code files.

---

## What can be configured

| Setting | Description |
|---|---|
| Session secret | Key used to sign session tokens. Required in production. |
| Data directory | Path where agents, connections, skills, and memory are stored. |
| Port and host | Where the server listens. |
| Allowed origins | Domains permitted to access the API. |
| Session duration | How long in hours a session remains active. |
| Google OAuth | Credentials to enable Google Sign-In. |
| Access restriction | Limit access to specific Google emails or domains. |

---

## Google Sign-In

Google login requires registering the application in Google Cloud Console and configuring the obtained credentials. Once set up, any Google account can access the platform unless access is restricted to specific emails or domains.

---

## Session secret

Must be generated randomly before the first startup and not changed while sessions are active. If not configured, the system uses a value stored in the platform data — acceptable in development, not in production.
