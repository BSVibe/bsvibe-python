# E2E Checklist — bsvibe-alerts (Phase A)

Non-web Python library. Each item verified by direct execution against
the editable install in this worktree.

## Public API surface

- [x] `from bsvibe_alerts import AlertClient, Alert, AlertSeverity, AlertSettings, AlertRouter, AlertChannel, StructlogChannel, TelegramChannel, SlackChannel` succeeds
- [x] `AlertClient.from_settings(AlertSettings())` returns a usable client
  (structlog channel always present, telegram/slack only when creds are set)
- [x] `__version__ == "0.1.0"`

## Routing behaviour

- [x] Default INFO → `["structlog"]`
- [x] Default WARNING → `["structlog", "slack"]`
- [x] Default CRITICAL → `["structlog", "slack", "telegram"]`
- [x] Env override (`ALERT_CRITICAL_CHANNELS=structlog,slack`) reflected in
  router; telegram NOT dispatched
- [x] Custom routing tables override defaults entirely

## Channel dispatch

- [x] StructlogChannel emits a structured log with the alert event,
  severity, message, and context keys merged
- [x] TelegramChannel calls `https://api.telegram.org/bot<token>/sendMessage`
  with `{chat_id, text, parse_mode: Markdown}`
- [x] SlackChannel POSTs `{text}` to the configured webhook URL
- [x] Channel that raises does not block sibling channels (failure isolation)
- [x] `AlertClient.publish()` return dict captures per-channel status
  (`True | "missing" | error_repr`)

## Settings / env contract

- [x] `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` together enable telegram
- [x] `TELEGRAM_BOT_TOKEN` alone does NOT enable telegram (chat_id missing)
- [x] `SLACK_WEBHOOK_URL` non-empty enables slack
- [x] `ALERT_SERVICE_NAME` populates `Alert.service` via emit()
- [x] CSV env vars accept legacy `os.environ.get(...).split(",")` shape
  without JSON encoding (BSupervisor §M18 pattern)

## Quality gates

- [x] `uv run pytest packages/bsvibe-alerts` — 60 tests pass
- [x] Coverage ≥ 80% (current: 99.15%)
- [x] `uv run ruff check packages/bsvibe-alerts` — All checks passed
- [x] `uv run ruff format --check packages/bsvibe-alerts` — formatted
- [x] All external HTTP calls mocked (no hits to api.telegram.org /
  hooks.slack.com)
