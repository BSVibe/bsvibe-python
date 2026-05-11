"""Tests for :mod:`bsvibe_cli_base.profile_cmd` — ``profile`` CRUD subapp.

The ``profile`` subapp wraps :class:`ProfileStore` for end-user use:

* ``profile add NAME --url URL [--tenant T] [--default]``
* ``profile list``
* ``profile use NAME``
* ``profile remove NAME``

Each command takes its store from ``ctx.obj.profile_store`` so the
factory's ``profile_store=`` override flows through to the subcommands
under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bsvibe_cli_base.cli import cli_app
from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.profile import ProfileStore


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(path=tmp_path / "config.yaml")


@pytest.fixture
def app_factory(store: ProfileStore, monkeypatch: pytest.MonkeyPatch):
    """Build a fresh cli_app each invocation so option parsing isn't shared."""
    monkeypatch.delenv("BSVIBE_PROFILE", raising=False)
    monkeypatch.delenv("BSVIBE_TOKEN", raising=False)
    monkeypatch.delenv("BSVIBE_OUTPUT", raising=False)

    def _make():
        return cli_app(name="demo", profile_store=store)

    return _make


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


class TestProfileAdd:
    def test_add_persists_profile(self, store: ProfileStore, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "https://api.prod"],
        )
        assert result.exit_code == 0, result.output
        prof = store.get_profile("prod")
        assert prof.url == "https://api.prod"
        assert prof.default is False

    def test_add_with_tenant_and_default(self, store: ProfileStore, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "https://api.prod", "--tenant", "t-1", "--default"],
        )
        assert result.exit_code == 0, result.output
        prof = store.get_profile("prod")
        assert prof.tenant_id == "t-1"
        assert prof.default is True

    def test_add_duplicate_fails(self, store: ProfileStore, app_factory) -> None:
        store.add_profile(Profile(name="prod", url="https://api.prod"))
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "https://api.prod"],
        )
        assert result.exit_code != 0
        assert "exists" in result.output.lower() or "exist" in result.output.lower()

    def test_add_bare_host_auto_prefixes_https(self, store: ProfileStore, app_factory) -> None:
        # Operator slip-up: dropping the scheme. Pre-fix profile add wrote
        # "//api.prod" to disk and the next CliHttpClient invocation
        # crashed with UnsupportedProtocol. Now auto-prefixed to https://.
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "api.prod"],
        )
        assert result.exit_code == 0, result.output
        assert store.get_profile("prod").url == "https://api.prod"

    def test_add_bare_host_with_port_auto_prefixes(self, store: ProfileStore, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "dev", "--url", "localhost:8000"],
        )
        assert result.exit_code == 0, result.output
        assert store.get_profile("dev").url == "https://localhost:8000"

    def test_add_rejects_protocol_relative_url(self, store: ProfileStore, app_factory) -> None:
        # Most often produced by shell quoting bugs (`'gw:https://x'` →
        # `${var##*:}` → `//api.prod`). Reject explicitly so the bad
        # value never lands in config.yaml.
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "//api.prod"],
        )
        assert result.exit_code != 0
        assert "explicit scheme" in result.output

    def test_add_rejects_unknown_scheme(self, store: ProfileStore, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "ftp://api.prod"],
        )
        assert result.exit_code != 0
        assert "scheme" in result.output

    def test_add_rejects_empty_url(self, store: ProfileStore, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "   "],
        )
        assert result.exit_code != 0

    def test_add_preserves_path_after_auto_prefix(self, store: ProfileStore, app_factory) -> None:
        # Don't strip path components when auto-prefixing.
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "prod", "--url", "api.prod/api/v1"],
        )
        assert result.exit_code == 0, result.output
        assert store.get_profile("prod").url == "https://api.prod/api/v1"

    def test_add_keeps_http_for_localhost(self, store: ProfileStore, app_factory) -> None:
        # Don't force https on http://localhost — dev servers commonly
        # run plain http. Only auto-prefix when scheme is missing.
        runner = CliRunner()
        result = runner.invoke(
            app_factory(),
            ["profile", "add", "dev", "--url", "http://localhost:18100"],
        )
        assert result.exit_code == 0, result.output
        assert store.get_profile("dev").url == "http://localhost:18100"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestProfileList:
    def test_list_empty(self, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(app_factory(), ["profile", "list"])
        assert result.exit_code == 0, result.output

    def test_list_shows_profiles(self, store: ProfileStore, app_factory) -> None:
        store.add_profile(Profile(name="prod", url="https://api.prod", default=True))
        store.add_profile(Profile(name="dev", url="https://api.dev"))
        runner = CliRunner()
        result = runner.invoke(app_factory(), ["profile", "list"])
        assert result.exit_code == 0, result.output
        assert "prod" in result.output
        assert "dev" in result.output


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------


class TestProfileUse:
    def test_use_flips_default(self, store: ProfileStore, app_factory) -> None:
        store.add_profile(Profile(name="prod", url="https://api.prod", default=True))
        store.add_profile(Profile(name="dev", url="https://api.dev"))

        runner = CliRunner()
        result = runner.invoke(app_factory(), ["profile", "use", "dev"])
        assert result.exit_code == 0, result.output

        assert store.get_profile("dev").default is True
        assert store.get_profile("prod").default is False

    def test_use_unknown_fails(self, store: ProfileStore, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(app_factory(), ["profile", "use", "ghost"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestProfileRemove:
    def test_remove_deletes_profile(self, store: ProfileStore, app_factory) -> None:
        store.add_profile(Profile(name="prod", url="https://api.prod"))
        runner = CliRunner()
        result = runner.invoke(app_factory(), ["profile", "remove", "prod"])
        assert result.exit_code == 0, result.output
        assert store.list_profiles() == []

    def test_remove_unknown_fails(self, store: ProfileStore, app_factory) -> None:
        runner = CliRunner()
        result = runner.invoke(app_factory(), ["profile", "remove", "ghost"])
        assert result.exit_code != 0
