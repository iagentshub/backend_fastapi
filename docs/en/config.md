<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/config.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Configuration

Infrastructure and startup configuration is supplied through environment
variables. Operational settings that must change without a restart are stored
from the admin panel. Neither mechanism requires code changes.

---

## What can be configured

| Setting | Description |
|---|---|
| Session secret | Key used to sign session tokens. Required in production. |
| Data directory | Path for the SQLite database, settings, operational state, and legacy data. |
| Port and host | Where the server listens. |
| Allowed origins | Domains permitted to access the API. |
| Session duration | How long in hours a session remains active. |
| LLM concurrency | Maximum simultaneous provider calls per worker. |
| Log writes | Batch size and flush interval for the activity log. |
| Internal Ollama origins | Exact local/LAN servers the backend may contact. |

## Runtime admin settings

`GET /api/settings/platform` and `PUT /api/settings/platform` let an admin read
and change persistent operational settings. They include
`max_request_bytes`, the single global size limit for requests and artifacts;
`0` means unlimited. There is no separate limit for each resource type.

`GET /api/settings/platform/public` exposes the non-sensitive part of this
configuration to clients. It includes `tool_runtimes`, the effective Tool
catalog with API codes, extensions, binary requirements, and native targets.
Clients must use that catalog instead of duplicating fixed lists.

### Structural directory import limits

These limits protect normalization and cataloguing from pathological trees.
They do not limit bytes or replace `max_request_bytes`: even when it is `0`
—unlimited—, a maliciously deep path is still rejected. They are startup
configuration and require a backend restart.

| Variable | Default | Description |
|---|---:|---|
| `GAIA_DIRECTORY_IMPORT_MAX_FILES` | `5000` | Maximum files in one imported directory. |
| `GAIA_DIRECTORY_IMPORT_MAX_DEPTH` | `32` | Maximum segments in a relative path. |
| `GAIA_DIRECTORY_IMPORT_MAX_PATH_LENGTH` | `500` | Maximum normalized relative-path length. |

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

## Push notifications

Bell notifications can also pop up as system notifications —outside the app,
with the tab closed— using **Web Push**. This needs a VAPID key pair, which
identifies this installation to each browser's push service. This command prints
the three variables ready to copy:

```bash
python - <<'EOF'
import base64
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01

v = Vapid01(); v.generate_keys()
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
print("GAIA_VAPID_PUBLIC_KEY=" + b64(v.public_key.public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)))
print("GAIA_VAPID_PRIVATE_KEY=" + b64(
    v.private_key.private_numbers().private_value.to_bytes(32, "big")))
print("GAIA_VAPID_SUBJECT=mailto:CHANGE@THIS.com")
EOF
```

`python -m py_vapid --gen` also works, but **it does not produce the keys in the
format the variables expect**: it writes `private_key.pem` and `public_key.pem`.
In that case paste the private PEM into `GAIA_VAPID_PRIVATE_KEY` and the output
of `python -m py_vapid --applicationServerKey` into the public one. The backend
accepts both forms of the private key, with escaped newlines or without.

| Variable | Default | Description |
|---|---|---|
| `GAIA_VAPID_PUBLIC_KEY` | *(empty)* | Public key. The browser receives it when subscribing. |
| `GAIA_VAPID_PRIVATE_KEY` | *(empty)* | Private key used to sign every delivery. |
| `GAIA_VAPID_SUBJECT` | *(empty)* | Operator contact (`mailto:` or `https://`). Required by RFC 8292; some services reject deliveries without it. |

Without these variables push stays disabled and the app does not offer the
toggle; the bell and email keep working as usual.

**The pair is not rotated lightly.** The browser checks that the delivery key
matches the one it subscribed with, so changing it invalidates every existing
subscription at once and each user has to turn it on again.

### Notification retention

Notifications are swept automatically with **two separate windows**: a read one
has already done its job, while an unread one may be all the user has left of
the fact that something happened —the invitation that caused it disappears from
`group_invitations` as soon as it is accepted—.

| Setting (admin panel) | Default | What it sweeps |
|---|---|---|
| `notification_retention_days` | 90 | **Read** notifications older than that |
| `notification_unread_retention_days` | 365 | **Unread** notifications older than that |

| Variable | Default | Description |
|---|---|---|
| `GAIA_NOTIFICATION_PURGE_HOURS` | 24 | How often the broom passes. Not the policy: raising it leaves more rubbish between sweeps, it does not change what a user sees. |

Push subscriptions need no purge: the push service answers 404 or 410 once the
browser has dropped them and the row is deleted right then. Delivery itself does
the cleaning.

### What each user receives

Two levels, and the general one wins over the specific one:

1. **Per-channel switch** (`notify_email`, `notify_push`): turns off the whole
   channel.
2. **Per-category, per-channel switch**: fine-tunes inside a channel that is on.

Categories are declared in `app/models/notification_kinds.py` and **published by
the server** at `/api/settings`; the client renders whatever it receives. That
way adding an event type never leaves the client with a missing switch.
`tests/api/test_notification_kinds.py` checks that every emitted type, and every
type with an email template, belongs to a category: an orphan would silently
ignore the user's preferences.

**The bell cannot be turned off.** It is the record of what happened, not an
interruption, and without it the user would have no way to find out at all.

### Push retries

A delivery is retried up to **3 times** with exponential backoff (1 s, 2 s), and
only for what can fix itself: 408, 429 and the 5xx. A 400, 401 or 403 is the
message's or the signature's fault and repeating it yields the same error. A 404
or 410 means the subscription is gone, so it is deleted rather than retried.

If the service sends `Retry-After` it is honoured —it knows when to come back,
and jumping the gun turns a 429 into a block— capped at 60 seconds: a
notification is not worth a task sleeping for half an hour, and it is still in
the bell anyway.

### What it covers and what it does not

| Where | Does it pop with the app closed? |
|---|---|
| Android (Chrome) and desktop | Yes, as long as the browser stays alive in the background |
| macOS Safari | Yes |
| iPhone, regular Safari tab | **No.** Apple does not allow it |
| iPhone, app added to the Home Screen | Yes, since iOS 16.4 |

That last case is the only gap and it does not depend on configuration: the app
detects visitors on an iPhone who have not installed it and explains the
missing step.

Native Android and iOS would use FCM and APNs, which are a different channel.
The `push_subscriptions` table already tells the type apart in its `kind`
column, so adding them the day the apps ship touches neither the schema nor the
notification producers.
