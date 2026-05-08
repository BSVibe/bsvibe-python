"""On-disk profile store (``~/.bsvibe/config.yaml``).

Persists :class:`~bsvibe_cli_base.config.CliConfig` via PyYAML's ``safe_*``
APIs. Writes go through a sibling tempfile + ``Path.replace`` so a partial
write never corrupts the prior state.

Path resolution order (matches XDG Base Directory spec):

1. Explicit ``path=`` argument.
2. ``$XDG_CONFIG_HOME/bsvibe/config.yaml`` if ``XDG_CONFIG_HOME`` is set.
3. ``~/.bsvibe/config.yaml`` otherwise.

This module owns no authentication logic — JWT verification, token
introspection, and refresh flows live in ``bsvibe-authz``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import ValidationError as PydanticValidationError

from bsvibe_core.exceptions import ConfigurationError, NotFoundError, ValidationError

from bsvibe_cli_base.config import CliConfig, Profile

logger = structlog.get_logger(__name__)


class ProfileNotFoundError(NotFoundError):
    """Raised when a requested profile name has no matching entry."""


class ProfileExistsError(ValidationError):
    """Raised when adding a profile whose name is already in use."""


def _default_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "bsvibe" / "config.yaml"
    return Path(os.path.expanduser("~")) / ".bsvibe" / "config.yaml"


class ProfileStore:
    """CRUD facade over the YAML config file.

    The store is stateless — every call re-reads from disk so concurrent
    edits from a sibling process are picked up. This trades a few extra
    syscalls for the absence of a stale-cache class of bug, which matters
    because users often edit the YAML by hand.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self.path: Path = path if path is not None else _default_path()

    def list_profiles(self) -> list[Profile]:
        return list(self._load().profiles)

    def get_profile(self, name: str) -> Profile:
        for profile in self._load().profiles:
            if profile.name == name:
                return profile
        raise ProfileNotFoundError(f"Profile not found: {name}", context={"name": name})

    def add_profile(self, profile: Profile) -> None:
        cfg = self._load()
        if any(existing.name == profile.name for existing in cfg.profiles):
            raise ProfileExistsError(
                f"Profile already exists: {profile.name}",
                context={"name": profile.name},
            )
        cfg.profiles.append(profile)
        self._save(cfg)
        logger.info("profile_added", name=profile.name)

    def remove_profile(self, name: str) -> None:
        cfg = self._load()
        kept = [p for p in cfg.profiles if p.name != name]
        if len(kept) == len(cfg.profiles):
            raise ProfileNotFoundError(f"Profile not found: {name}", context={"name": name})
        cfg.profiles = kept
        self._save(cfg)
        logger.info("profile_removed", name=name)

    def set_active(self, name: str) -> None:
        cfg = self._load()
        if not any(p.name == name for p in cfg.profiles):
            raise ProfileNotFoundError(f"Profile not found: {name}", context={"name": name})
        for profile in cfg.profiles:
            profile.default = profile.name == name
        self._save(cfg)
        logger.info("profile_active_set", name=name)

    def get_active(self) -> Profile | None:
        for profile in self._load().profiles:
            if profile.default:
                return profile
        return None

    def _load(self) -> CliConfig:
        if not self.path.exists():
            return CliConfig()
        try:
            raw: Any = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"Invalid YAML at {self.path}: {exc}",
                context={"path": str(self.path)},
            ) from exc
        if raw is None:
            return CliConfig()
        if not isinstance(raw, dict):
            raise ConfigurationError(
                f"Config root must be a mapping at {self.path}",
                context={"path": str(self.path)},
            )
        try:
            return CliConfig.model_validate(raw)
        except PydanticValidationError as exc:
            raise ConfigurationError(
                f"Invalid config schema at {self.path}: {exc}",
                context={"path": str(self.path)},
            ) from exc

    def _save(self, cfg: CliConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = cfg.model_dump(mode="python")
        fd, tmp_name = tempfile.mkstemp(
            prefix=".bsvibe-config-",
            suffix=".yaml",
            dir=str(self.path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False)
            tmp_path.replace(self.path)
        except BaseException:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise


__all__ = [
    "ProfileStore",
    "ProfileNotFoundError",
    "ProfileExistsError",
]
