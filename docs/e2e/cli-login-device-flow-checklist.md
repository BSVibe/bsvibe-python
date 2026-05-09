# E2E Checklist — `bsvibe-cli-base` device-flow login + profile

PR3 of Phase 8.0. The Typer subapps land here in `bsvibe-cli-base` and
auto-wire into all 4 product CLIs via `cli_app()`. End-to-end checks
that hit a real auth backend are deferred until PR1 (BSVibe-Auth
`/oauth/device/{code,token}` handlers) and PR2 (bsvibe-site
`/auth/device` verification UI) ship — those rows are tagged
`[deferred]` and verified during integration deploy.

## Library-level (verifiable in this PR)

- [x] `Profile.refresh_token_ref` field accepts `str | None`, defaults to `None`, rejects unknown fields (extra=forbid).
- [x] `keyring.set_refresh_token` / `get_refresh_token` / `delete_refresh_token` round-trip when an in-memory keyring backend is installed.
- [x] `keyring.set_refresh_token` swallows backend failure (no raise) on a headless host — same fail-soft contract as `set_token`.
- [x] `keyring.make_persist_callback(profile_name)` returns a callable that, given a `DeviceTokenGrant`, writes both access + refresh tokens to keyring under the `bsvibe` service.
- [x] `DeviceFlowClient.request_code(scope=..., audience=...)` includes the `audience` field in the JSON body when supplied.
- [x] `do_login()` happy path: device flow approved → keyring populated (access + refresh) → profile upserted → profile becomes active.
- [x] `do_login()` for new profile name: creates profile with provided url + tenant + sets default=true.
- [x] `do_login()` for existing profile name: updates token refs in keyring, leaves profile fields intact.
- [x] `do_login()` propagates `DeviceFlowError` from `/oauth/device/code` 4xx — caller exits non-zero.
- [x] `do_login()` propagates `DeviceFlowError` on `access_denied` polling response.
- [x] `profile add NAME --url URL [--tenant T] [--default]` persists a Profile row.
- [x] `profile add` with duplicate name fails with non-zero exit + "exists" in stderr.
- [x] `profile list` emits one row per profile with name / url / tenant / default flag.
- [x] `profile use NAME` flips `default` to that name.
- [x] `profile remove NAME` deletes the profile; missing name → non-zero exit.
- [x] `cli_app()` auto-registers `login` and `profile` subapps; their commands appear in `--help`.
- [x] `cli_app(auto_login=False)` opts out — neither subapp is registered.
- [x] `CliContext.keyring_persist_callback` is non-None when a profile is resolved; callback persists a `DeviceTokenGrant` to keyring.
- [x] Coverage ≥ 80% on the new modules (achieved 93% overall, 64% on `login_cmd.py` Typer wrapper / 98%+ on the rest).
- [x] `uv run ruff check packages/bsvibe-cli-base/` GREEN.
- [x] `uv run ruff format --check packages/bsvibe-cli-base/` GREEN.

## Cross-product (verified during PR3 → 4 product redeploy)

- [ ] After 4 product CLIs bump `bsvibe-cli-base`, each exposes `<product> login` and `<product> profile` (gateway / nexus / sage / supervisor).
- [ ] None of the existing product subcommands regress (`<product> --help` still shows product subapps).

## End-to-end against real BSVibe-Auth (deferred — needs PR1+PR2)

- [ ] [deferred] Run `bsgateway login --auth-url https://auth.bsvibe.dev --client-id cli --scope 'gateway:* sage:* nexus:* supervisor:*'` → CLI prints user code + verification URL.
- [ ] [deferred] Open the URL in browser, log in, click Approve → CLI prints "Saved PAT to keyring".
- [ ] [deferred] After login, `bsgateway profile list` shows the new profile with `default: true`.
- [ ] [deferred] `python -c "from bsvibe_cli_base import keyring; print(keyring.get_token('default'))"` returns the issued access token (`bsv_sk_*`).
- [ ] [deferred] `keyring.get_refresh_token('default')` returns the refresh token (`bsv_rt_*`).
- [ ] [deferred] Issue a real call (`bsgateway model list`) — succeeds against api-gateway.bsvibe.dev with the keyring-backed token.
- [ ] [deferred] Force token expiry → next `bsgateway` call refreshes silently and updates keyring (callback fires).
- [ ] [deferred] BSage REST probe with the issued PAT: `curl -H "Authorization: Bearer $(python -c '...')" https://api-sage.bsvibe.dev/api/knowledge/catalog` → 200 (or surface handoff §사전 발견 #1).

## Verification commands

```bash
# inside the worktree
cd ~/Works/bsvibe-python/wt/feat-cli-login-device-flow
uv run pytest packages/bsvibe-cli-base/ --cov=packages/bsvibe-cli-base/src/bsvibe_cli_base --cov-fail-under=80 -v
uv run ruff check packages/bsvibe-cli-base/
uv run ruff format --check packages/bsvibe-cli-base/
```
