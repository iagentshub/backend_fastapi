<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/config.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Configuration

All settings are read at startup from environment variables. No config file needs to be modified for most deployments.

| Variable | Default | Required | Description |
|---|---|---|---|
| `GAIA_DATA_DIR` | `./data` | No | Absolute path to the data directory |
| `GAIA_HOST` | `0.0.0.0` | No | Bind address |
| `GAIA_PORT` | `8765` | No | Listen port |
| `GAIA_RELOAD` | `true` | No | Enable hot-reload. Set to `false` in production |
| `GAIA_AGENTS_SECRET` | — | **Yes (prod)** | JWT signing secret. Falls back to `settings.json#jwt_secret` |
| `GAIA_CORS_ORIGINS` | `*` | No | Comma-separated list of allowed origins |
| `GAIA_JWT_EXPIRE_HOURS` | `12` | No | Token lifetime in hours |

---

## settings.json

Located at `$GAIA_DATA_DIR/settings.json`. Stores the admin account and a fallback JWT secret.

```json
{
  "admin_username": "admin",
  "admin_password_hash": "$2b$12$...",
  "jwt_secret": "change-me-in-production"
}
```

> `GAIA_AGENTS_SECRET` takes priority over `jwt_secret` in `settings.json`. Always use the environment variable in production.

---

## CORS

For a single frontend origin:

```bash
GAIA_CORS_ORIGINS=https://app.example.com
```

For multiple origins:

```bash
GAIA_CORS_ORIGINS=https://app.example.com,http://localhost:3000
```

Use `*` only in local development.
