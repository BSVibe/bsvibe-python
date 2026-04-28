"""LLM-specific settings, layered on top of :class:`BsvibeSettings`.

The default routing target is **BSGateway** (Lockin Decision #11). A
deployer who wants direct vendor calls must opt in per call via
``LlmClient.complete(..., direct=True)`` — this settings object only
defines the **default** route for the process.
"""

from __future__ import annotations

from typing import Annotated, Literal

from bsvibe_core import BsvibeSettings, csv_list_field, parse_csv_list
from pydantic import Field, field_validator
from pydantic_settings import NoDecode

RouteDefault = Literal["bsgateway", "direct"]


class LlmSettings(BsvibeSettings):
    """Per-process LLM defaults.

    Attributes:
        bsgateway_url: BSGateway base URL. When non-empty and
            ``route_default == "bsgateway"`` (the default), every
            ``complete()`` sets ``api_base`` to this URL so LiteLLM
            talks to BSGateway instead of the vendor directly.
        model: Default LiteLLM model identifier. Empty allowed; a
            ``complete(model=...)`` override always wins.
        fallback_models: Ordered list of vendor fallbacks. Tried in
            order if the primary model raises a transient error.
        retry_max_attempts: Maximum attempts per model in the fallback
            chain (1 means no retry).
        retry_base_delay_s: Initial backoff seconds for transient
            failures. Doubles each attempt.
        route_default: ``"bsgateway"`` (default) or ``"direct"``.
            Switches the implicit ``api_base`` plumbing.
    """

    bsgateway_url: str = Field(default="", validation_alias="bsgateway_url")
    model: str = Field(default="", validation_alias="model")

    fallback_models: Annotated[list[str], NoDecode] = csv_list_field(
        default=[],
        alias="fallback_models",
        description="Ordered fallback model list (CSV env var)",
    )

    retry_max_attempts: int = Field(default=3, validation_alias="retry_max_attempts", ge=1)
    retry_base_delay_s: float = Field(default=0.5, validation_alias="retry_base_delay_s", gt=0)

    route_default: RouteDefault = Field(default="bsgateway", validation_alias="route_default")

    @field_validator("fallback_models", mode="before")
    @classmethod
    def _parse_fallback_models(cls, v):
        return parse_csv_list(v)


__all__ = ["LlmSettings", "RouteDefault"]
