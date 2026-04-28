# bsvibe-alerts

Multi-channel alert publisher shared by every BSVibe product.

The package provides a single `AlertClient` that routes alerts to one or
more sinks (structlog / slack / telegram) based on severity. Routing
rules are configurable via env vars so deployers can adjust per-severity
fan-out without redeploying any product.

## Status

Phase A baseline. Implements the D18 client contract from
`BSVibe_Shared_Library_Roadmap.md` §4 — local channel dispatch only. The
central `POST /api/alerts/dispatch` model (alerts dispatched through
BSVibe-Auth) is a future enhancement; today every product runs its own
`AlertClient` instance.

## Install

Inside a product's `pyproject.toml`:

```toml
[project]
dependencies = [
    "bsvibe-alerts @ git+https://github.com/BSVibe/bsvibe-python.git@v0.1.0#subdirectory=packages/bsvibe-alerts",
]
```

## Quick start

```python
from bsvibe_alerts import AlertClient, AlertSettings

settings = AlertSettings()                  # picks env vars up
alerts = AlertClient.from_settings(settings)

await alerts.emit(
    event="rate_limit_exceeded",
    message="quota hit for tenant t-1",
    severity="warning",
    context={"tenant_id": "t-1"},
)
```

## Public API

- `Alert` — dataclass (`event`, `message`, `severity`, `context`, `service`).
- `AlertSeverity` — `INFO < WARNING < CRITICAL` (string-backed enum).
- `AlertSettings` — pydantic-settings; reads telegram/slack creds and
  per-severity routing tables from env.
- `AlertChannel` — runtime-checkable `Protocol`. Implement `name` +
  `async send(alert)` to add a custom sink.
- `StructlogChannel`, `TelegramChannel`, `SlackChannel` — concrete sinks.
- `AlertRouter` — pure data routing rule (severity → channel names).
- `AlertClient` — top-level publisher. `publish(alert)` /
  `emit(event=..., message=...)` / `from_settings(settings)`.

## Env contract

| Env var | Effect |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Both required to enable telegram channel. |
| `SLACK_WEBHOOK_URL` | Enables slack channel when non-empty. |
| `ALERT_SERVICE_NAME` | Tagged on every emitted alert. |
| `ALERT_INFO_CHANNELS` | CSV. Default: `structlog`. |
| `ALERT_WARNING_CHANNELS` | CSV. Default: `structlog,slack`. |
| `ALERT_CRITICAL_CHANNELS` | CSV. Default: `structlog,slack,telegram`. |

CSV env vars follow the BSupervisor §M18 `Annotated[list[str], NoDecode]`
pattern — drop them in raw, no JSON encoding required.

## Production-safety guarantees

- The `structlog` sink is always-on; an `AlertClient` never silently
  swallows alerts even when slack/telegram credentials are absent.
- A failing channel logs via the always-on fallback and is reflected in
  `publish()`'s return dict, but never blocks other channels — slack
  outages cannot silence telegram critical alerts.
- Routing rules referencing channels that were not registered (because
  their credentials were missing at startup) are skipped silently and
  logged at `warning` level.

## Testing

```bash
uv run pytest packages/bsvibe-alerts --cov=bsvibe_alerts --cov-fail-under=80
```

External HTTP calls (telegram / slack) are mocked at the
`httpx.AsyncClient.post` boundary — no test ever hits the real APIs.
