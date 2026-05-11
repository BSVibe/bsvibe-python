"""Tests for :func:`bsvibe_cli_base.cli.cli_app`.

The factory mints a :class:`typer.Typer` with the BSVibe global flag set
already wired:

  --profile / -p          BSVIBE_PROFILE
  --output / -o           BSVIBE_OUTPUT  (json|yaml|tsv|table)
  --tenant                BSVIBE_TENANT
  --token                 BSVIBE_TOKEN
  --url                   BSVIBE_URL
  --dry-run               (boolean, default False)

Subcommands attached to the returned app receive a :class:`CliContext`
through ``ctx.obj`` containing the resolved profile, the OutputFormatter
ready for ``emit()``, and the runtime overrides (token / tenant / url /
dry_run). This keeps subcommands free of plumbing — they call
``ctx.obj.formatter.emit(result)`` and the output respects every flag.

Profile resolution order: ``--profile`` flag → ``BSVIBE_PROFILE`` env →
``ProfileStore.get_active()``. If none of those resolve, subcommands
that need a profile must surface a typed error themselves; the factory
simply leaves ``ctx.obj.profile`` as ``None``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from bsvibe_cli_base.cli import CliContext, cli_app
from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.profile import ProfileStore


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(path=tmp_path / "config.yaml")


@pytest.fixture
def store_with_dev_active(store: ProfileStore) -> ProfileStore:
    store.add_profile(Profile(name="dev", url="https://api.dev", tenant_id="t-dev", default=True))
    return store


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


class TestFactoryShape:
    def test_returns_typer_app(self) -> None:
        import typer

        app = cli_app(name="demo")
        assert isinstance(app, typer.Typer)

    def test_global_flags_appear_in_help(self) -> None:
        app = cli_app(name="demo")

        @app.command()
        def noop(ctx: typer.Context) -> None:
            pass

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0, result.output
        for flag in ("--profile", "--output", "--tenant", "--token", "--url", "--dry-run"):
            assert flag in result.output, f"missing {flag} in --help"


# ---------------------------------------------------------------------------
# Context wiring
# ---------------------------------------------------------------------------


class TestContextWiring:
    def test_profile_flag_resolves_named_profile(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["--profile", "dev", "show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].profile is not None
        assert captured["obj"].profile.name == "dev"

    def test_active_profile_used_when_flag_omitted(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].profile is not None
        assert captured["obj"].profile.name == "dev"

    def test_env_profile_used_when_flag_omitted(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store_with_dev_active.add_profile(Profile(name="prod", url="https://api.prod"))
        captured: dict[str, CliContext] = {}
        monkeypatch.setenv("BSVIBE_PROFILE", "prod")

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].profile is not None
        assert captured["obj"].profile.name == "prod"

    def test_no_profile_does_not_crash(self, store: ProfileStore, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].profile is None

    def test_url_flag_overrides_profile_url(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["--url", "https://override", "show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].url == "https://override"

    def test_url_falls_back_to_profile(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].url == "https://api.dev"

    def test_tenant_flag_overrides_profile(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["--tenant", "t-other", "show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].tenant_id == "t-other"

    def test_token_flag_overrides_resolution(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)
        monkeypatch.delenv("BSVIBE_TOKEN", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["--token", "explicit-token", "show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].token == "explicit-token"

    def test_dry_run_default_false(self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0
        assert captured["obj"].dry_run is False

    def test_dry_run_flag_sets_true(self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["--dry-run", "show"])
        assert result.exit_code == 0
        assert captured["obj"].dry_run is True


# ---------------------------------------------------------------------------
# OutputFormatter wiring
# ---------------------------------------------------------------------------


class TestFormatterWiring:
    def test_explicit_output_flag_sets_format(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)
        monkeypatch.delenv("BSVIBE_OUTPUT", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["--output", "yaml", "show"])
        assert result.exit_code == 0
        assert captured["obj"].formatter.format == "yaml"

    def test_unknown_output_format_rejected(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            pass

        runner = CliRunner()
        result = runner.invoke(app, ["--output", "xml", "show"])
        assert result.exit_code != 0
        assert "xml" in result.output.lower() or "output" in result.output.lower()

    def test_default_format_is_json_when_non_tty(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)
        monkeypatch.delenv("BSVIBE_OUTPUT", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()  # CliRunner streams are non-TTY.
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0
        assert captured["obj"].formatter.format == "json"


# ---------------------------------------------------------------------------
# Auto-wired login + profile subapps
# ---------------------------------------------------------------------------


class TestAutoWiredSubapps:
    """``cli_app()`` registers ``login`` and ``profile`` subapps by default
    so every product CLI exposes them without having to import or wire
    each one — the ``library-fix-doesnt-cascade-when-callers-rewrap``
    trap explicitly avoided.
    """

    def test_login_subapp_present_by_default(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)
        app = cli_app(name="demo", profile_store=store_with_dev_active)
        runner = CliRunner()
        result = runner.invoke(app, ["login", "--help"])
        assert result.exit_code == 0, result.output
        assert "--auth-url" in result.output

    def test_profile_subapp_present_by_default(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)
        app = cli_app(name="demo", profile_store=store_with_dev_active)
        runner = CliRunner()
        result = runner.invoke(app, ["profile", "--help"])
        assert result.exit_code == 0, result.output
        for needle in ("add", "list", "use", "remove"):
            assert needle in result.output, f"missing 'profile {needle}' in --help"

    def test_auto_login_false_omits_subapps(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)
        app = cli_app(name="demo", profile_store=store_with_dev_active, auto_login=False)
        runner = CliRunner()
        result = runner.invoke(app, ["login", "--help"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# keyring_persist_callback wiring
# ---------------------------------------------------------------------------


class TestKeyringPersistCallback:
    def test_callback_present_when_profile_resolved(
        self, store_with_dev_active: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store_with_dev_active)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0, result.output
        assert captured["obj"].keyring_persist_callback is not None

    def test_callback_absent_when_no_profile(self, store: ProfileStore, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, CliContext] = {}
        monkeypatch.delenv("BSVIBE_PROFILE", raising=False)

        app = cli_app(name="demo", profile_store=store)

        @app.command()
        def show(ctx: typer.Context) -> None:
            captured["obj"] = ctx.obj

        runner = CliRunner()
        result = runner.invoke(app, ["show"])
        assert result.exit_code == 0
        assert captured["obj"].keyring_persist_callback is None


class TestStdoutCleanForJsonPipelines:
    """Phase 8 dogfood (2026-05-11) finding #7 — structlog logs went to
    stdout, interleaving with `--output json` payloads and breaking
    downstream `jq` / `python -c json.loads(…)` pipelines. Pin the
    invariant: stdout carries the subcommand payload; structlog carries
    the operator-facing log noise to stderr."""

    def test_structlog_goes_to_stderr_not_stdout(
        self, store: ProfileStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """End-to-end check via a real subprocess — pytest's capfd/capsys
        can't see structlog's PrintLogger output reliably because the
        factory binds to ``sys.stderr`` at import time, before pytest
        installs its capture. A subprocess with redirected fds is the
        only way to observe what an operator's shell would see."""
        import subprocess
        import sys as _sys
        import textwrap

        # Tiny runner: import cli_app, register a command, invoke it.
        # Stdout is the formatter payload; stderr is the structlog logs.
        runner = tmp_path / "runner.py"
        runner.write_text(
            textwrap.dedent(
                """
                import sys, structlog, typer
                from pathlib import Path
                from bsvibe_cli_base.cli import cli_app
                from bsvibe_cli_base.profile import ProfileStore

                app = cli_app(name="demo", profile_store=ProfileStore(path=Path(sys.argv[1])))

                @app.command()
                def show(ctx: typer.Context) -> None:
                    typer.echo("PAYLOAD_LINE")
                    structlog.get_logger("demo").warning("background_event", x=1)

                app(["show"])
                """
            )
        )
        cfg_path = tmp_path / "config.yaml"
        result = subprocess.run(
            [_sys.executable, str(runner), str(cfg_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        assert "PAYLOAD_LINE" in result.stdout, (
            f"stdout missing subcommand payload — formatter output regressed: {result.stdout!r}"
        )
        assert "background_event" not in result.stdout, (
            f"structlog leaked to stdout (breaks `--output json | jq`): {result.stdout!r}"
        )
        assert "background_event" in result.stderr, (
            f"structlog warning vanished entirely; should be on stderr: {result.stderr!r}"
        )
        # Sanity: the subcommand payload didn't leak into stderr either.
        assert "PAYLOAD_LINE" not in result.stderr
