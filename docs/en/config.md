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
| LLM concurrency | Maximum simultaneous provider calls per worker. |
| Log writes | Batch size and flush interval for the activity log. |

## Log writes

Log records are written to the database **in batches**, not one by one: they are
grouped and flushed in a single transaction when the batch fills up or when the
interval elapses, whichever comes first.

| Variable | Default | Description |
|---|---|---|
| `GAIA_LOG_BATCH_SIZE` | `50` | Records per transaction. `1` restores immediate writes. |
| `GAIA_LOG_FLUSH_INTERVAL` | `1.0` | Maximum seconds a record may wait in memory. |
| `GAIA_LOG_HEALTH` | *(disabled)* | Set to `1` to log successful health checks again. |

Liveness probes (`/api/health`) are **not logged when they succeed**: the
container `HEALTHCHECK` fires every 30 s and, with several workers, they filled
the table with identical lines. Failing health checks *are* logged.

`ERROR` level messages are always written immediately, without waiting for the
batch. The `/api/admin/logs` viewer forces a flush before querying, so it always
shows complete activity.

Lower `GAIA_LOG_BATCH_SIZE` only if you need strict per-line durability: with the
default, an abrupt process crash may lose at most the last second of diagnostic
logs.

## LLM concurrency

`GAIA_LLM_MAX_THREADS` controls the dedicated executor used for LLM provider
streaming. It defaults to `16` per worker. Once the limit is reached, new chats
receive HTTP 429 with `Retry-After` instead of occupying the general executor or
growing an unbounded queue. Increase it only after measuring memory, file
descriptors, and provider limits.

## Session secret

Must be generated randomly before the first startup and not changed while sessions are active. If not configured, the system uses a value stored in the platform data — acceptable in development, not in production.

This secret also serves as the master key for encrypting API keys stored in the database (derived via PBKDF2-SHA256). **Changing it after API keys have been saved will make those keys unreadable** — users will need to re-enter their credentials.
