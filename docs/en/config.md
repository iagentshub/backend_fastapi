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
| Internal Ollama origins | Exact local/LAN servers the backend may contact. |

### Internal Ollama destinations

| Variable | Default | Description |
|---|---|---|
| `GAIA_OLLAMA_ALLOWED_ORIGINS` | `http://localhost:11434,http://127.0.0.1:11434,http://[::1]:11434,http://host.docker.internal:11434` | Comma-separated exact internal origins authorized for Ollama. Public destinations still use validation and DNS pinning. |

Authorization includes scheme, host, and port. For example, allowing
`http://localhost:11434` does not allow `http://localhost:5432`.
This variable only opens exceptions for internal destinations. The official
`https://ollama.com` option and public custom URLs require no additional
configuration and retain SSRF validation and DNS pinning.

## Startup configuration audit

A missing variable breaks nothing: it turns a feature off. Without
`GAIA_SMTP_HOST` verification emails never leave, and with a typo in
`STRIPE_WEBHOOK_SECRET` the server starts just fine and simply never charges. So
that this stops happening quietly, startup audits the configuration and logs
**which feature is disabled and because of which variable**.

There are two levels:

| Level | Meaning | Example |
|---|---|---|
| Warning | A feature is off because configuration is missing. May well be deliberate. | No `GAIA_SMTP_HOST`, so no email is sent. |
| Error | The configuration contradicts itself: something is enabled that cannot work. | Email verification on with no SMTP server: nobody ever gets in. |

| Variable | Default | Description |
|---|---|---|
| `GAIA_STRICT_CONFIG` | *(off)* | Set to `true` so **errors** abort startup instead of only warning. |

Errors do not abort by default: tightening that would leave an installation that
works today —degraded— unable to start. In a production deployment, set
`GAIA_STRICT_CONFIG=true` once and startup warns you forever.

The same report lives in the admin panel under **Configuration → Configuration
diagnostics**, and in `GET /api/admin/config-audit`. It shows variable **names**
only, never their values.

## Subscription tax

Advertised prices are **net**: checkout asks for the billing country before the
subscription is created, and Stripe Tax adds the VAT that country requires. A
business in another member state that supplies a valid EU VAT number pays no
VAT (reverse charge).

The country cannot be asked for at the end. `payment_behavior="default_incomplete"`
drafts the invoice at the same moment the subscription is created, so with no
location Stripe answers `customer_tax_location_invalid` and there is no sign-up.
That is why `POST /api/billing/subscribe` requires `country` (ISO 3166-1 alpha-2),
accepts an optional `tax_id`, and returns `subtotal_cents`, `tax_cents` and
`total_cents` taken from that invoice — which is what the customer sees before
paying.

| Variable | Default | Description |
|---|---|---|
| `STRIPE_TAX` | `true` | Set to `false` to charge the net amount with no VAT. |

Enabling it here is not enough: dashboard.stripe.com needs **Tax enabled**, the
country's tax registrations declared, a `tax_code` on the `STRIPE_PRODUCT_SEATS`
product, and `tax_behavior` on the self-hosted add-on prices, which are fixed and
not created from code. If any of those four is missing, sign-up fails when the
subscription is created; the startup report is the reminder.

Only EU VAT numbers (`eu_vat`) are registered. A customer outside the EU pays as
a consumer, which is correct even for a business; widening it means adding types
in `app/services/billing_tax.py`.

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

When that happens the failure is visible: the affected resource is returned with `credentials_unreadable: true` (plus `unreadable_fields` listing the exact fields), the client flags it as *needs attention* in the listing, and any action that would have used the credential — chat, test, model import, sync — responds with the `credential_unreadable` code instead of sending the encrypted value to the provider. The ciphertext is kept intact: restore the correct secret and the keys become readable again on their own.
