"""Tests for :mod:`bsvibe_cli_base.config` and :mod:`bsvibe_cli_base.profile`.

The Profile / ProfileStore pair are the persistent identity layer for every
BSVibe CLI. They must:

* validate Profile fields (name, url required; tenant_id / token_ref optional)
* resolve the config path from ``$XDG_CONFIG_HOME/bsvibe/config.yaml`` with
  fallback to ``~/.bsvibe/config.yaml``
* round-trip through YAML safely (safe_load / safe_dump only)
* write atomically — failure during persistence MUST leave the prior file
  on disk intact
* surface ``ProfileNotFoundError`` / ``ProfileExistsError`` (subclasses of
  the bsvibe-core base hierarchy) instead of generic ``ValueError``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from bsvibe_cli_base.config import CliConfig, Profile
from bsvibe_cli_base.profile import (
    ProfileExistsError,
    ProfileNotFoundError,
    ProfileStore,
)


class TestProfile:
    def test_minimum_fields(self) -> None:
        p = Profile(name="dev", url="https://api.dev")
        assert p.name == "dev"
        assert p.url == "https://api.dev"
        assert p.tenant_id is None
        assert p.default is False
        assert p.token_ref is None

    def test_all_fields(self) -> None:
        p = Profile(
            name="prod",
            url="https://api.prod",
            tenant_id="t1",
            default=True,
            token_ref="keyring:bsvibe:prod",
        )
        assert p.tenant_id == "t1"
        assert p.default is True
        assert p.token_ref == "keyring:bsvibe:prod"

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Profile(name="", url="https://api.dev")

    def test_blank_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Profile(name="dev", url="")

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Profile(name="dev", url="https://api.dev", unknown="x")  # type: ignore[call-arg]


class TestCliConfig:
    def test_empty_default(self) -> None:
        cfg = CliConfig()
        assert cfg.profiles == []

    def test_profiles_round_trip(self) -> None:
        cfg = CliConfig(profiles=[Profile(name="a", url="https://a")])
        as_dict = cfg.model_dump(mode="python")
        rebuilt = CliConfig.model_validate(as_dict)
        assert rebuilt.profiles[0].name == "a"


class TestProfileStorePath:
    def test_xdg_config_home_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        store = ProfileStore()
        assert store.path == tmp_path / "bsvibe" / "config.yaml"

    def test_home_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        store = ProfileStore()
        assert store.path == tmp_path / ".bsvibe" / "config.yaml"

    def test_explicit_path_overrides(self, tmp_path: Path) -> None:
        explicit = tmp_path / "custom.yaml"
        store = ProfileStore(path=explicit)
        assert store.path == explicit


class TestProfileStoreCRUD:
    @pytest.fixture
    def store(self, tmp_path: Path) -> ProfileStore:
        return ProfileStore(path=tmp_path / "bsvibe" / "config.yaml")

    def test_list_empty_when_file_missing(self, store: ProfileStore) -> None:
        assert store.list_profiles() == []

    def test_add_then_get(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="dev", url="https://api.dev"))
        got = store.get_profile("dev")
        assert got.name == "dev"
        assert got.url == "https://api.dev"

    def test_add_persists_to_disk(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="dev", url="https://api.dev"))
        assert store.path.exists()
        # Fresh store reads the same data back.
        fresh = ProfileStore(path=store.path)
        assert fresh.get_profile("dev").url == "https://api.dev"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "config.yaml"
        store = ProfileStore(path=deep)
        store.add_profile(Profile(name="x", url="https://x"))
        assert deep.exists()

    def test_list_after_add(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="a", url="https://a"))
        store.add_profile(Profile(name="b", url="https://b"))
        names = [p.name for p in store.list_profiles()]
        assert names == ["a", "b"]

    def test_add_duplicate_raises(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="dev", url="https://api.dev"))
        with pytest.raises(ProfileExistsError):
            store.add_profile(Profile(name="dev", url="https://other"))

    def test_get_missing_raises(self, store: ProfileStore) -> None:
        with pytest.raises(ProfileNotFoundError):
            store.get_profile("ghost")

    def test_remove(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="a", url="https://a"))
        store.add_profile(Profile(name="b", url="https://b"))
        store.remove_profile("a")
        assert [p.name for p in store.list_profiles()] == ["b"]

    def test_remove_missing_raises(self, store: ProfileStore) -> None:
        with pytest.raises(ProfileNotFoundError):
            store.remove_profile("ghost")

    def test_set_active_makes_one_default(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="a", url="https://a", default=True))
        store.add_profile(Profile(name="b", url="https://b"))
        store.set_active("b")
        assert store.get_profile("a").default is False
        assert store.get_profile("b").default is True

    def test_set_active_idempotent(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="a", url="https://a", default=True))
        store.set_active("a")
        assert store.get_profile("a").default is True

    def test_set_active_missing_raises(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="a", url="https://a"))
        with pytest.raises(ProfileNotFoundError):
            store.set_active("ghost")

    def test_get_active_returns_default(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="a", url="https://a"))
        store.add_profile(Profile(name="b", url="https://b", default=True))
        assert store.get_active() is not None
        assert store.get_active().name == "b"  # type: ignore[union-attr]

    def test_get_active_none_when_no_default(self, store: ProfileStore) -> None:
        store.add_profile(Profile(name="a", url="https://a"))
        assert store.get_active() is None


class TestProfileStorePersistence:
    def test_uses_safe_dump(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        store = ProfileStore(path=path)
        store.add_profile(Profile(name="dev", url="https://api.dev", tenant_id="t1"))
        # Plain text YAML — no Python object tags.
        text = path.read_text()
        assert "!!python" not in text
        # safe_load round-trip stays a dict.
        loaded = yaml.safe_load(text)
        assert loaded["profiles"][0]["name"] == "dev"

    def test_atomic_write_preserves_prior_file_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "config.yaml"
        store = ProfileStore(path=path)
        store.add_profile(Profile(name="a", url="https://a"))
        before = path.read_bytes()

        original_replace = Path.replace

        def boom(self: Path, target):  # type: ignore[no-untyped-def]
            if Path(target) == path:
                raise OSError("simulated rename failure")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", boom)
        with pytest.raises(OSError):
            store.add_profile(Profile(name="b", url="https://b"))

        # Original file untouched.
        assert path.read_bytes() == before
        # No leftover temp files in the parent dir.
        leftovers = [p for p in path.parent.iterdir() if p.name != path.name]
        assert leftovers == []

    def test_corrupt_yaml_raises_configuration_error(self, tmp_path: Path) -> None:
        from bsvibe_core.exceptions import ConfigurationError

        path = tmp_path / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a: b: c: : :\n  - not: valid", encoding="utf-8")
        store = ProfileStore(path=path)
        with pytest.raises(ConfigurationError):
            store.list_profiles()

    def test_invalid_schema_raises_configuration_error(self, tmp_path: Path) -> None:
        from bsvibe_core.exceptions import ConfigurationError

        path = tmp_path / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        # profiles must be a list.
        path.write_text("profiles: not-a-list\n", encoding="utf-8")
        store = ProfileStore(path=path)
        with pytest.raises(ConfigurationError):
            store.list_profiles()

    def test_list_at_root_raises_configuration_error(self, tmp_path: Path) -> None:
        from bsvibe_core.exceptions import ConfigurationError

        path = tmp_path / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- a\n- b\n", encoding="utf-8")
        store = ProfileStore(path=path)
        with pytest.raises(ConfigurationError):
            store.list_profiles()

    def test_empty_file_is_treated_as_empty_config(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        store = ProfileStore(path=path)
        assert store.list_profiles() == []
