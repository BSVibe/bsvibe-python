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
